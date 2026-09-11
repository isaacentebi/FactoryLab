"""Bounded Base USDC authorizations and wallet authentication never expose signing keys.

``eth_account`` is already a transitive dependency of hyperliquid-python-sdk.
No web3 or x402 SDK is needed. Money crosses this boundary as integer micro-USD
(USDC base units); native ETH balances are integer wei.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any
from urllib import error, parse, request

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

BASE_RPC = "https://mainnet.base.org"
VENICE_URL = "https://api.venice.ai/api/v1"
BASE_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
BASE_NETWORK = "eip155:8453"
TOP_UP_MICRO = 5_000_000


class X402Error(Exception):
    """Failures contain local explanations and status codes, never response bodies or keys."""


@dataclass(frozen=True)
class HTTPResponse:
    """Status and headers remain available even for a payment-required response."""

    status: int
    body: dict[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)


Transport = Callable[[str, str, dict | None, dict[str, str]], HTTPResponse]


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """No redirect can forward authentication or replay a signed payment."""
        return None


def http_request(method: str, url: str, payload: dict | None, headers: dict) -> HTTPResponse:
    """One HTTP attempt preserves 402 headers and parses decimal numbers without floats."""
    req = request.Request(
        url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **headers},
        method=method,
    )
    try:
        response = request.build_opener(_NoRedirect()).open(req, timeout=60)
    except error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        try:
            decoded = json.loads(body, parse_float=Decimal) if body else {}
        except (ValueError, UnicodeError):
            if 200 <= response.status < 300:
                raise X402Error("Invalid JSON response") from None
            decoded = {}
        if not isinstance(decoded, dict):
            raise X402Error("Expected a JSON object")
        return HTTPResponse(response.status, decoded, dict(response.headers.items()))


def usd_micro(value: Any, *, round_up: bool = False) -> int:
    """Finite nonnegative USD becomes integer micro-USD, down for balances, up for costs."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        exact = Fraction(amount) * 1_000_000
        quotient, remainder = divmod(exact.numerator, exact.denominator)
        return quotient + int(round_up and remainder != 0)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise X402Error("Invalid USD amount") from None


def redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """Response data cannot echo any supplied secret, including unprefixed key hex."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                pattern = re.escape(secret.removeprefix("0x"))
                value = re.sub(r"(?:0x)?" + pattern, "[REDACTED]", value, flags=re.IGNORECASE)
        return value
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    return value


def _account(private_key: str | None = None):
    key = private_key or os.environ.get("RESERVE_PRIVATE_KEY")
    if not key:
        raise X402Error("RESERVE_PRIVATE_KEY is not set; run factorylab reserve init")
    try:
        return Account.from_key(key)
    except Exception:
        raise X402Error("Invalid reserve private key") from None


def reserve_address(private_key: str | None = None) -> str:
    """Only the checksummed public address leaves the key-loading boundary."""
    return _account(private_key).address


def siwe_header(account: Any, resource_url: str) -> str:
    """Each header signs a fresh EIP-4361 nonce, Base chain and exact request URI.

    Envelope follows https://docs.venice.ai/guides/integrations/x402-venice-api:
    address, message, signature, millisecond timestamp, and numeric chainId.
    """
    url = parse.urlsplit(resource_url)
    if url.scheme not in {"https", "http"} or not url.netloc or url.username or url.password:
        raise X402Error("Invalid authentication URL")
    timestamp = time.time_ns() // 1_000_000
    now = datetime.fromtimestamp(timestamp // 1000, UTC).replace(
        microsecond=(timestamp % 1000) * 1000,
    )
    issued = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    expires = (now + timedelta(minutes=5)).isoformat(timespec="milliseconds")
    message = (
        f"{url.netloc} wants you to sign in with your Ethereum account:\n"
        f"{account.address}\n\nSign in to Venice AI\n\n"
        f"URI: {resource_url}\nVersion: 1\nChain ID: 8453\n"
        f"Nonce: {os.urandom(16).hex()}\nIssued At: {issued}\n"
        f"Expiration Time: {expires.replace('+00:00', 'Z')}"
    )
    signature = account.sign_message(encode_defunct(text=message)).signature
    return _encode(
        {
            "address": account.address,
            "message": message,
            "signature": "0x" + signature.hex(),
            "timestamp": timestamp,
            "chainId": 8453,
        }
    )


def _encode(value: dict) -> str:
    return base64.b64encode(json.dumps(value, separators=(",", ":")).encode()).decode("ascii")


def _decode(value: str) -> dict:
    try:
        decoded = json.loads(base64.b64decode(value, validate=True), parse_float=Decimal)
        if not isinstance(decoded, dict):
            raise ValueError
        return decoded
    except Exception:
        raise X402Error("Invalid base64 JSON payment header") from None


def _header(headers: Mapping[str, str], *names: str) -> str | None:
    return next((v for k, v in headers.items() if k.lower() in names), None)


def _address(address: str) -> str:
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise X402Error("Invalid EVM address")
    return address


def _requirements(accepted: dict, amount_micro: int) -> None:
    if type(amount_micro) is not int or amount_micro != TOP_UP_MICRO:
        raise X402Error("Only a $5 Venice top-up is supported")
    if (
        accepted.get("scheme") != "exact"
        or accepted.get("network") != BASE_NETWORK
        or str(accepted.get("asset", "")).lower() != BASE_USDC
        or accepted.get("amount") != str(amount_micro)
    ):
        raise X402Error("Quote must offer exactly $5 in canonical USDC on Base")
    _address(accepted.get("payTo"))
    if int(accepted["payTo"], 16) == 0:
        raise X402Error("Zero payment recipient is not supported")
    timeout = accepted.get("maxTimeoutSeconds")
    if type(timeout) is not int or not 0 < timeout < 2**256:
        raise X402Error("Invalid payment timeout")
    extra = accepted.get("extra") or {}
    if not isinstance(extra, dict) or (
        extra.get("name", "USD Coin") != "USD Coin"
        or extra.get("version", "2") != "2"
        or extra.get("assetTransferMethod", "eip3009") != "eip3009"
    ):
        raise X402Error("Unsupported USDC authorization domain or transfer method")


@dataclass(frozen=True)
class PaymentQuote:
    """The selected v2 requirements retain the server's resource and extension metadata."""

    accepted: dict
    resource: dict | None = None
    extensions: dict | None = None


def parse_quote(response: HTTPResponse, *, amount_micro: int = TOP_UP_MICRO) -> PaymentQuote:
    """Only a v2 402 quote for the explicitly requested Base USDC amount is accepted."""
    if response.status != 402:
        raise X402Error(f"Expected a 402 quote; received HTTP {response.status}")
    encoded = _header(response.headers, "payment-required", "x-payment-required")
    quote = _decode(encoded) if encoded is not None else response.body
    if (
        type(quote.get("x402Version")) is not int or quote["x402Version"] != 2
        or not isinstance(quote.get("accepts"), list)
    ):
        raise X402Error("Expected x402 v2 payment requirements")
    for accepted in quote["accepts"]:
        if not isinstance(accepted, dict):
            continue
        try:
            _requirements(accepted, amount_micro)
        except X402Error:
            continue
        for field_name in ("resource", "extensions"):
            if field_name in quote and not isinstance(quote[field_name], dict):
                raise X402Error("Invalid payment metadata")
        return PaymentQuote(dict(accepted), quote.get("resource"), quote.get("extensions"))
    raise X402Error("No exact $5 canonical Base USDC quote is available")


def authorization_typed_data(
    accepted: dict,
    address: str,
    *,
    now: int | None = None,
    nonce: bytes | None = None,
) -> dict:
    """The EIP-3009 signature can spend only $5 USDC to this quote's recipient on Base."""
    _requirements(accepted, TOP_UP_MICRO)
    _address(address)
    now = time.time_ns() // 1_000_000_000 if now is None else now
    nonce = os.urandom(32) if nonce is None else nonce
    if type(now) is not int or now < 0 or now + accepted["maxTimeoutSeconds"] >= 2**256:
        raise X402Error("Invalid authorization time")
    if not isinstance(nonce, bytes) or len(nonce) != 32:
        raise X402Error("Authorization nonce must be 32 bytes")
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": "USD Coin",
            "version": "2",
            "chainId": 8453,
            "verifyingContract": accepted["asset"],
        },
        "message": {
            "from": address,
            "to": accepted["payTo"],
            "value": int(accepted["amount"]),
            "validAfter": 0,
            "validBefore": now + accepted["maxTimeoutSeconds"],
            "nonce": "0x" + nonce.hex(),
        },
    }


def payment_header(account: Any, quote: PaymentQuote) -> str:
    """Standard base64 of UTF-8 v2 JSON retains accepted requirements and signed payload.

    Specification files read from https://github.com/coinbase/x402 (2026-09-11):
    ``specs/x402-specification-v2.md`` section 5.2 (PaymentPayload),
    ``specs/schemes/exact/scheme_exact_evm.md`` section 1 (EIP-3009), and
    ``specs/transports-v2/http.md`` (base64 JSON transport).
    Venice uses ``X-402-Payment`` for the v2 envelope that the standard sends in
    ``PAYMENT-SIGNATURE``. The uint256 authorization values are decimal strings.
    """
    typed = authorization_typed_data(quote.accepted, account.address)
    signed = account.sign_message(encode_typed_data(full_message=typed))
    authorization = {
        k: str(v) if k in {"value", "validAfter", "validBefore"} else v
        for k, v in typed["message"].items()
    }
    envelope = {
        "x402Version": 2,
        "accepted": quote.accepted,
        "payload": {"signature": "0x" + signed.signature.hex(), "authorization": authorization},
    }
    if quote.resource is not None:
        envelope["resource"] = quote.resource
    if quote.extensions is not None:
        envelope["extensions"] = quote.extensions
    return _encode(envelope)


def _rpc_balance(rpc: str, method: str, params: list, transport: Transport | None) -> int:
    try:
        response = (transport or http_request)(
            "POST",
            rpc,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            {},
        )
        result = response.body.get("result")
        if (
            response.status != 200
            or response.body.get("error") is not None
            or response.body.get("id") != 1
            or not isinstance(result, str)
            or not re.fullmatch(r"0x[0-9a-fA-F]+", result)
        ):
            raise ValueError
        balance = int(result, 16)
        if balance >= 2**256:
            raise ValueError
        return balance
    except Exception:
        raise X402Error("Base RPC balance request failed") from None


def usdc_balance(address: str, *, rpc: str = BASE_RPC, transport: Transport | None = None) -> int:
    """Canonical Base USDC balance is an integer in six-decimal base units (micro-USD)."""
    address = _address(address)
    data = "0x70a08231" + address[2:].lower().zfill(64)  # balanceOf(address)
    return _rpc_balance(rpc, "eth_call", [{"to": BASE_USDC, "data": data}, "latest"], transport)


def eth_balance(address: str, *, rpc: str = BASE_RPC, transport: Transport | None = None) -> int:
    """Native Base ETH is returned in wei using eth_getBalance, without a transaction."""
    return _rpc_balance(rpc, "eth_getBalance", [_address(address), "latest"], transport)


class X402Client:
    """Only an explicit top_up signs payment; status and inference auth cannot move USDC."""

    def __init__(
        self,
        *,
        private_key: str | None = None,
        base_url: str = VENICE_URL,
        rpc: str = BASE_RPC,
        transport: Transport | None = None,
    ) -> None:
        self._account = _account(private_key)
        self.address = self._account.address
        self.base_url = base_url.rstrip("/")
        self.rpc = rpc
        self._transport = transport or http_request
        self._secrets = (private_key or os.environ.get("RESERVE_PRIVATE_KEY", ""),)

    def auth_headers(self, path: str) -> dict[str, str]:
        """The request gets a fresh signed SIWE header for its own URI."""
        return {"X-Sign-In-With-X": siwe_header(self._account, self.base_url + path)}

    def _request(self, method: str, path: str, payload: dict | None = None, **headers):
        try:
            return self._transport(
                method,
                self.base_url + path,
                payload,
                {**self.auth_headers(path), **headers},
            )
        except Exception:
            raise X402Error("Venice request failed; payment outcome may be unknown") from None

    def usdc_balance(self, address: str | None = None) -> int:
        """The reserve's USDC balance is integer micro-USD."""
        return usdc_balance(address or self.address, rpc=self.rpc, transport=self._transport)

    def eth_balance(self, address: str | None = None) -> int:
        """The reserve's native ETH balance is integer wei."""
        return eth_balance(address or self.address, rpc=self.rpc, transport=self._transport)

    def venice_balance(self, address: str | None = None) -> int:
        """A SIWE-authenticated wallet balance is rounded down to integer micro-USD."""
        address = _address(address or self.address)
        response = self._request("GET", f"/x402/balance/{address}")
        if response.status != 200:
            raise X402Error(f"Venice balance failed (HTTP {response.status})")
        # Venice's official client unwraps data.balanceUsd; direct bodies also occur in fakes.
        data = response.body.get("data", response.body)
        if not isinstance(data, dict):
            raise X402Error("Invalid Venice balance response")
        return usd_micro(data.get("balanceUsd"))

    def top_up(self, amount_micro: int = TOP_UP_MICRO) -> dict:
        """A validated $5 quote is signed once and submitted once, with no automatic retry.

        A timeout after submission is an unknown outcome. Inspect wallet balances
        and Venice transactions before authorizing another top-up.
        """
        if type(amount_micro) is not int or amount_micro != TOP_UP_MICRO:
            raise X402Error("Only a $5 Venice top-up is supported")
        if self.usdc_balance() < amount_micro:
            raise X402Error("Insufficient Base USDC for a $5 top-up")
        path = "/x402/top-up"
        quote = parse_quote(self._request("POST", path, {}), amount_micro=amount_micro)
        encoded = payment_header(self._account, quote)
        response = self._request("POST", path, {}, **{"X-402-Payment": encoded})
        if not 200 <= response.status < 300:
            raise X402Error(
                f"Top-up submission returned HTTP {response.status}; check settlement before retry",
            )
        header = _header(response.headers, "payment-response", "x-payment-response")
        settlement = _decode(header) if header is not None else response.body
        data = settlement.get("data", settlement)
        if not isinstance(data, dict):
            raise X402Error("Invalid settlement response; check balances before retry")
        if settlement.get("success") is False or data.get("success") is False:
            raise X402Error("Top-up settlement failed; check balances before retry")
        if data.get("network", BASE_NETWORK) != BASE_NETWORK:
            raise X402Error("Unexpected settlement network; check balances before retry")
        if str(data.get("payer", self.address)).lower() != self.address.lower():
            raise X402Error("Unexpected settlement payer; check balances before retry")
        return redact(settlement, self._secrets)


def venice_balance(
    address: str,
    *,
    private_key: str | None = None,
    base_url: str = VENICE_URL,
    transport: Transport | None = None,
) -> int:
    """Wallet-bound Venice credits require fresh SIWE and return integer micro-USD."""
    return X402Client(
        private_key=private_key,
        base_url=base_url,
        transport=transport,
    ).venice_balance(address)
