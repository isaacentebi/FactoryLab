"""The population sells a registered program's output over x402; the mirror of the buyer.

``world/x402.py`` is the buyer: it validates a seller's quote, signs an EIP-3009
authorization for exactly the quoted USDC and checks the settlement it gets back.
This module is the seller. It issues the same v2 quote shape the buyer accepts,
verifies a payment by reconstructing exactly the typed data the buyer signs
(``authorization_typed_data``) and recovering its signer, hands the signed
authorization to a facilitator for on-chain settlement, and only then runs the
program in the population jail and returns its output.

Nothing here holds a key. The reserve address comes from the manifest; the
payer signs, the facilitator submits. A paid call is money the wallet's pots
received, so it is written down before the program runs: the runtime books
each receipt as ``income.earned`` (in-process through ``Treasury.earn``, or
from the receipt spool the wake host's server appends to, since only the
runtime may append to the ledger). The spool itself lives in ``world/income.py``,
beside the treasury that reads it; this module lives in ``runtime`` because a
seller runs registered programs and consumes registry contracts.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data

from factorylab.cortex.tools import PopulationTool, ToolRunner
from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds

# The spool is re-exported: the wake host's server and the tests reach it here.
from factorylab.world.income import IncomeSpool, read_income_spool  # noqa: F401
from factorylab.world.x402 import (
    BASE_NETWORK,
    BASE_USDC,
    Transport,
    X402Error,
    _address,
    _decode,
    _encode,
    _header,
    _requirements,
    authorization_typed_data,
    http_request,
)

#: Coinbase's public x402 facilitator; ``FACTORYLAB_FACILITATOR_URL`` overrides it at
#: launch only (``configured_facilitator``), after which the ledgered value binds.
FACILITATOR_URL = "https://x402.org/facilitator"
FACILITATOR_ENV = "FACTORYLAB_FACILITATOR_URL"


def configured_facilitator() -> str:
    """The facilitator this launch binds: the environment's URL, or the public default.

    Read once, by the runtime at construction, and ledgered in the ``Launch``
    event beside the release digest. Nothing else reads the variable: the seller
    settles through the ledgered value (``deploy/serve.py``) and a resume under a
    different one is refused (``facilitator_mismatch``), so the facilitator is
    not a lever the environment can pull on a running world.
    """
    url = os.environ.get(FACILITATOR_ENV, "").strip()
    if not url:
        return FACILITATOR_URL
    if not re.match(r"^https?://[^\s\"'\\]+$", url):
        raise ValueError("facilitator URL must be a plain http(s) URL")
    return url
#: A buyer's authorization is valid for at most this long; the buyer clamps to 600.
QUOTE_TIMEOUT_S = 300
MAX_BODY_BYTES = 65_536
#: Settled nonces the server remembers so a replayed header is refused before settlement.
MAX_SEEN_NONCES = 4096
PAYMENT_HEADERS = ("payment-signature", "x-payment", "x-402-payment")
_HEX32 = re.compile(r"0x[0-9a-fA-F]{64}")
_HEX65 = re.compile(r"0x[0-9a-fA-F]{130}")
_UINT = re.compile(r"0|[1-9][0-9]{0,77}")


@dataclass(frozen=True)
class Service:
    """A paid endpoint: one frozen program version at one price."""

    id: str
    program_id: str
    price_micro: int
    description: str
    version: int
    tool: PopulationTool

    def public(self) -> dict:
        """Catalogue row: never the source."""
        return {"id": self.id, "description": self.description,
                "price_micro": self.price_micro, "version": self.version,
                "args_schema": self.tool.args_schema}


def service_contract(prop: Any, version: int, *, timeout_s: int) -> Contract:
    """The registry contract a service proposal consumes its novelty trial for."""
    return Contract(
        id=f"service:{prop.program_id}", version=version, kind="service",
        description=prop.description,
        input_schema={"type": "object", "program_id": prop.program_id,
                      "price_micro": prop.price_micro},
        output_schema={"type": "object"},
        price=PriceSpec({"call": prop.price_micro}),
        permissions=frozenset({"sandbox.run", "service.sell"}),
        resource_bounds=ResourceBounds(max_duration_ns=timeout_s * 1_000_000_000),
    )


def requirements(service: Service, pay_to: str) -> dict:
    """The one accepted payment: exact canonical Base USDC to the reserve, at the price."""
    accepted = {
        "scheme": "exact", "network": BASE_NETWORK, "amount": str(service.price_micro),
        "asset": BASE_USDC, "payTo": _address(pay_to), "maxTimeoutSeconds": QUOTE_TIMEOUT_S,
        "extra": {"name": "USD Coin", "version": "2"},
    }
    _requirements(accepted, service.price_micro)  # the buyer's own check, applied first
    return accepted


def quote(service: Service, pay_to: str, resource_url: str, *, error: str | None = None) -> dict:
    """A v2 PaymentRequired body the buyer's ``parse_quote`` accepts as is."""
    body = {
        "x402Version": 2,
        "accepts": [requirements(service, pay_to)],
        "resource": {"url": resource_url, "description": service.description},
    }
    if error:
        body["error"] = error
    return body


def verify_payment(header: str, *, pay_to: str, amount_micro: int,
                   now: int | None = None) -> dict:
    """Accept a payment header only if it is the authorization the buyer would have signed.

    The seller rebuilds the buyer's typed data with ``authorization_typed_data``
    from the payload's own parameters, so the recovered signer signed exactly a
    transfer of the quoted amount to the reserve, and nothing else. Every refusal
    is an ``X402Error`` with a local reason; the header body is never echoed.
    """
    envelope = _decode(header)
    if envelope.get("x402Version") != 2:
        raise X402Error("Expected an x402 v2 payment payload")
    accepted, payload = envelope.get("accepted"), envelope.get("payload")
    if not isinstance(accepted, dict) or not isinstance(payload, dict):
        raise X402Error("Payment payload must carry accepted requirements and a payload")
    _requirements(accepted, amount_micro)
    pay_to = _address(pay_to)
    if accepted["payTo"].lower() != pay_to.lower():
        raise X402Error("Payment is not addressed to the reserve")
    authorization, signature = payload.get("authorization"), payload.get("signature")
    if not isinstance(authorization, dict) or not isinstance(signature, str):
        raise X402Error("Payment payload must carry an authorization and a signature")
    if not _HEX65.fullmatch(signature):
        raise X402Error("Invalid authorization signature encoding")
    fields = ("from", "to", "value", "validAfter", "validBefore", "nonce")
    if any(not isinstance(authorization.get(name), str) for name in fields):
        raise X402Error("Authorization fields must be strings")
    payer = _address(authorization["from"])
    if authorization["to"].lower() != pay_to.lower():
        raise X402Error("Authorization is not addressed to the reserve")
    for name in ("value", "validAfter", "validBefore"):
        if not _UINT.fullmatch(authorization[name]):
            raise X402Error("Authorization amounts must be uint256 decimal strings")
    if int(authorization["value"]) != amount_micro or int(authorization["validAfter"]) != 0:
        raise X402Error("Authorization does not match the quoted amount")
    if not _HEX32.fullmatch(authorization["nonce"]):
        raise X402Error("Authorization nonce must be 32 bytes")
    valid_before = int(authorization["validBefore"])
    signed_at = valid_before - min(accepted["maxTimeoutSeconds"], 600)
    if signed_at < 0:
        raise X402Error("Authorization validity is outside the quoted timeout")
    typed = authorization_typed_data(
        accepted, payer, now=signed_at, nonce=bytes.fromhex(authorization["nonce"][2:]),
    )
    now = time.time_ns() // 1_000_000_000 if now is None else now
    if not int(authorization["validAfter"]) <= now < valid_before:
        raise X402Error("Authorization is not valid at this time")
    try:
        recovered = Account.recover_message(
            encode_typed_data(full_message=typed), signature=bytes.fromhex(signature[2:]),
        )
    except Exception:
        raise X402Error("Authorization signature cannot be recovered") from None
    if recovered.lower() != payer.lower():
        raise X402Error("Authorization was not signed by its payer")
    return {"envelope": envelope, "accepted": accepted, "payer": payer,
            "nonce": authorization["nonce"].lower(), "amount_micro": amount_micro}


def settle_payment(verified: dict, *, facilitator: str = FACILITATOR_URL,
                   transport: Transport | None = None) -> dict:
    """Submit the verified authorization once and accept only an explicit, matching success.

    The checks are the buyer's settlement checks from ``X402Client.top_up``,
    applied to the facilitator's answer: success must be stated, the network must
    be Base, the payer must be the recovered signer, and a transaction reference
    must exist. A failure after submission is unknown, never retried here.
    """
    try:
        response = (transport or http_request)(
            "POST", facilitator.rstrip("/") + "/settle",
            {"x402Version": 2, "paymentPayload": verified["envelope"],
             "paymentRequirements": verified["accepted"]},
            {},
        )
    except Exception:
        raise X402Error("Facilitator request failed; settlement outcome unknown") from None
    if not 200 <= response.status < 300:
        raise X402Error(f"Facilitator settlement returned HTTP {response.status}")
    settlement = response.body
    data = settlement.get("data", settlement)
    if not isinstance(data, dict):
        raise X402Error("Invalid settlement response")
    if settlement.get("success") is False or data.get("success") is not True:
        raise X402Error("Facilitator did not settle the payment")
    if data.get("network", BASE_NETWORK) != BASE_NETWORK:
        raise X402Error("Unexpected settlement network")
    if str(data.get("payer", verified["payer"])).lower() != verified["payer"].lower():
        raise X402Error("Unexpected settlement payer")
    transaction = data.get("transaction")
    if not isinstance(transaction, str) or not transaction:
        raise X402Error("Settlement carries no transaction reference")
    return {"success": True, "transaction": transaction, "network": BASE_NETWORK,
            "payer": verified["payer"]}


# --- receipts -----------------------------------------------------------------

# --- catalogue ----------------------------------------------------------------

def services_from_items(items) -> dict[str, Service]:
    """Rebuild the catalogue from ``service.registered`` items; the latest version wins."""
    services: dict[str, Service] = {}
    for item in items:
        if item.get("kind") != "service.registered":
            continue
        try:
            tool = PopulationTool(item["program_id"], item["description"],
                                  dict(item["args_schema"]), item["code"],
                                  int(item["timeout_s"]), str(item.get("handle")))
            service = Service(item["id"], item["program_id"], int(item["price_micro"]),
                              item["description"], int(item["version"]), tool)
        except (KeyError, TypeError, ValueError):
            continue
        current = services.get(service.id)
        if current is None or service.version >= current.version:
            services[service.id] = service
    return services


def facilitator_from_items(items) -> str | None:
    """The facilitator the ``Launch`` event ledgered, or None for a world launched before
    the pin. The host serves through this value and never through the environment."""
    for item in items:
        if item.get("kind") != "event":
            continue
        event = item.get("event") or {}
        if event.get("kind") == "Launch":
            url = (event.get("payload") or {}).get("facilitator_url")
            return url if isinstance(url, str) and url else None
    return None


def services_from_runtime(rt) -> dict[str, Service]:
    """The registry's current service contracts, bound to the programs the runtime holds."""
    services = {}
    for contract in rt.registry.available("service"):
        program_id = contract.input_schema["program_id"]
        tool = rt.population_tools.get(program_id)
        if tool is None:
            continue
        services[program_id] = Service(program_id, program_id, contract.price.units["call"],
                                       contract.description, contract.version, tool)
    return services


# --- the seller ---------------------------------------------------------------

Earn = Callable[[Service, int, str, str, int], Any]


class Seller:
    """Serve one catalogue: unpaid calls get the quote, paid calls get the program's output."""

    def __init__(self, services: Mapping[str, Service], *, pay_to: str, runner: Any,
                 earn: Earn, facilitator: str | None = None,
                 transport: Transport | None = None,
                 clock_ns: Callable[[], int] = time.time_ns,
                 live: Callable[[], bool] | None = None) -> None:
        self.services = dict(services)
        # Whether the world that owns these services is alive. A dead world sells
        # nothing -- no quote, no settlement, no program run -- and a seller that
        # cannot tell fails closed: the caller's ``live`` answers False when its
        # own view of the world is stale.
        self.live = live
        self.pay_to = _address(pay_to)
        self.runner = runner
        self.earn = earn
        # Never the environment: the host passes the value the Launch event ledgered.
        self.facilitator = facilitator or FACILITATOR_URL
        self.transport = transport
        self.clock_ns = clock_ns
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def refresh(self, services: Mapping[str, Service]) -> None:
        self.services = dict(services)

    def catalogue(self) -> list[dict]:
        return [service.public() for _, service in sorted(self.services.items())]

    def _replayed(self, nonce: str) -> bool:
        with self._lock:
            if nonce in self._seen:
                return True
            self._seen[nonce] = None
            while len(self._seen) > MAX_SEEN_NONCES:
                self._seen.popitem(last=False)
            return False

    def handle(self, service_id: str, body: bytes, headers: Mapping[str, str],
               resource_url: str) -> tuple[int, dict[str, str], dict]:
        """One request, one answer: status, response headers, JSON body."""
        if not self._alive():
            return 503, {}, {"error": "the world is not live"}
        service = self.services.get(service_id)
        if service is None:
            return 404, {}, {"error": "unknown service"}
        if len(body) > MAX_BODY_BYTES:
            return 413, {}, {"error": "request body too large"}
        try:
            args = json.loads(body or b"{}")
        except ValueError:
            return 400, {}, {"error": "request body must be a JSON object"}
        if not isinstance(args, dict):
            return 400, {}, {"error": "request body must be a JSON object"}
        header = _header(headers, *PAYMENT_HEADERS)
        if header is None:
            return self._challenge(service, resource_url, None)
        try:
            verified = verify_payment(header, pay_to=self.pay_to,
                                      amount_micro=service.price_micro,
                                      now=self.clock_ns() // 1_000_000_000)
        except X402Error as exc:
            return self._challenge(service, resource_url, str(exc))
        if self._replayed(verified["nonce"]):
            return self._challenge(service, resource_url, "Authorization already used")
        if not self._alive():
            # Checked again at the last moment before money moves: the world may
            # have died while this request was being verified.
            return 503, {}, {"error": "the world is not live"}
        try:
            settlement = settle_payment(verified, facilitator=self.facilitator,
                                        transport=self.transport)
        except X402Error as exc:
            return self._challenge(service, resource_url, str(exc))
        # The money arrived: it is written down before the program runs, so a
        # program that fails still leaves the receipt it was paid for.
        served_ns = self.clock_ns()
        self.earn(service, service.price_micro, settlement["transaction"],
                  settlement["payer"], served_ns)
        output = self.runner.run(service.tool, args)
        return 200, {"PAYMENT-RESPONSE": _encode(settlement)}, output

    def _alive(self) -> bool:
        if self.live is None:
            return True
        try:
            return bool(self.live())
        except Exception:  # noqa: BLE001 - a liveness that cannot answer is not alive
            return False

    def _challenge(self, service: Service, resource_url: str,
                   error: str | None) -> tuple[int, dict[str, str], dict]:
        body = quote(service, self.pay_to, resource_url, error=error)
        return 402, {"PAYMENT-REQUIRED": _encode(body)}, body


def seller_from_runtime(rt, **options) -> Seller:
    """Bind a seller to a running world: its registry, its jail, its treasury."""
    pay_to = getattr(rt.m.treasury, "reserve_address", None)
    if pay_to is None:
        raise ValueError("the manifest names no reserve address to be paid at")

    def earn(service: Service, micro: int, tx: str, payer: str, served_ns: int):
        """Book one paid call with the payment's full identity, in process.

        The receipt carries chain, asset and recipient as well as the
        transaction, so it deduplicates against the same payment arriving by any
        other route (the host's spool, say) rather than only against itself. This
        path books directly: the runtime performed the settlement itself, through
        the facilitator, on an authorization it verified against the quoted
        amount and the reserve address. The spool path has no such evidence and
        books a claim instead.
        """
        item = rt.treasury.earn(service.id, micro, tx, payer=payer, program=service.program_id,
                                version=service.version, served_ns=served_ns,
                                chain="base", asset="USDC", recipient=pay_to)
        if item is not None:
            rt._book_income(item)  # A repeated receipt never credits money twice.
        return item

    def live() -> bool:
        """Production alive, the world not final, the wallet not dead."""
        from factorylab.runtime.winddown import KILLED

        return (getattr(rt, "production_state", None) != KILLED
                and not rt.termination.final and not rt.wallet.dead)

    options.setdefault("live", live)
    return Seller(services_from_runtime(rt), pay_to=pay_to, runner=rt.tool_runner,
                  earn=earn, clock_ns=lambda: rt.clock.now_ns, **options)


def world_is_dead(items) -> bool:
    """Whether a diary records the world's death: a production kill or ``Terminated``."""
    for item in items:
        if item.get("kind") == "kill.production":
            return True
        if item.get("kind") == "event" and (item.get("event") or {}).get("kind") == "Terminated":
            return True
    return False


def spool_earn(spool: IncomeSpool, *, pay_to: str | None = None) -> Earn:
    """An ``earn`` that leaves a receipt for the runtime instead of touching its ledger.

    The receipt carries the payment's identity -- chain, asset and the recipient
    it was paid to -- so the runtime can ask the chain about it rather than
    taking the file's word. Until it does, the runtime books it as a claim.
    """
    def earn(service: Service, micro: int, tx: str, payer: str, served_ns: int):
        return spool.append({"service": service.id, "micro": micro, "tx": tx, "payer": payer,
                             "program": service.program_id, "version": service.version,
                             "ts": served_ns, "chain": "base", "asset": "USDC",
                             "recipient": pay_to})
    return earn


# --- HTTP ---------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "FactoryLabSeller/1"
    sys_version = ""
    seller: Seller

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        """Requests are not logged: a body or a header must not reach a log."""

    def _send(self, status: int, headers: Mapping[str, str], body: dict) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if self.path == "/services":
            self._send(200, {}, {"services": self.seller.catalogue()})
        else:
            self._send(404, {}, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        prefix = "/service/"
        if not self.path.startswith(prefix) or "/" in self.path[len(prefix):]:
            self._send(404, {}, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {}, {"error": "invalid content length"})
            return
        if length > MAX_BODY_BYTES:
            self._send(413, {}, {"error": "request body too large"})
            return
        body = self.rfile.read(length) if length > 0 else b""
        host = self.headers.get("Host") or "localhost"
        try:
            status, headers, answer = self.seller.handle(
                self.path[len(prefix):], body, dict(self.headers.items()),
                f"http://{host}{self.path}",
            )
        except Exception:
            # No exception text leaves the process: it can carry a header or a body.
            status, headers, answer = 500, {}, {"error": "service failed"}
        self._send(status, headers, answer)


def serve(seller: Seller, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Bind the seller on a local socket; the caller runs ``serve_forever`` or a thread."""
    handler = type("SellerHandler", (_Handler,), {"seller": seller})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def default_runner() -> ToolRunner:
    return ToolRunner()
