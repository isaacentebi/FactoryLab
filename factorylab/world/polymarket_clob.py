"""Polymarket orders on the live CLOB: signed V2 orders, their auth, and the pot's reads.

The venue is the world (AGENTS.md: a venue is not architecture). This module is the
seam between the runtime's order intents (``runtime/polymarket.py``) and Polymarket's
central limit order book on Polygon. It adds no decision: it builds, signs, sends and
reads back exactly the orders the runtime has already ledgered as intents, and it
reports what the venue says in the same shapes the simulated venue
(``world/polymarket.py``, ``FakePolymarket``) uses.

Protocol facts, each read 2026-09-29 (Polymarket moved to CLOB V2 on 2026-04-28):

* **Order struct and domain.** EIP-712 ``Order(uint256 salt,address maker,address
  signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8
  signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)`` under domain
  ``{name: "Polymarket CTF Exchange", version: "2", chainId: 137, verifyingContract}``;
  V2 dropped ``taker``, ``expiration``, ``nonce`` and ``feeRateBps`` from the signed
  struct (``expiration`` still travels unsigned in the body).
  https://docs.polymarket.com/v2-migration, https://docs.polymarket.com/trading/place-orders,
  https://github.com/Polymarket/ctf-exchange-v2 (``src/exchange/libraries/Structs.sol``,
  ``mixins/Hashing.sol``). The digest ``order_hash`` computes was checked against the
  exchange's own ``hashOrder`` by an ``eth_call`` on Polygon (``tests/world``).
* **Signature types.** 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE, 3 POLY_1271 (a Deposit
  Wallet, ERC-1271, the default for accounts made since 2026-05-04; an EOA trades only
  if Polymarket allowlisted it). For 3 the EOA signs an ERC-7739 ``TypedDataSign``
  wrapper. https://docs.polymarket.com/trading/wallets-auth,
  https://github.com/Polymarket/py-clob-client-v2 (``order_utils``).
* **Contracts (Polygon 137).** CTF Exchange ``0xE111180000d2663C0091e4f400237545B87B996B``,
  Neg Risk CTF Exchange ``0xe2222d279d744050d28e00520010520000310F59``, Conditional
  Tokens ``0x4D97DCd97eC945f40cF65F87097ACe5EA0476045``, pUSD (6 decimals)
  ``0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB``, CtfCollateralAdapter
  ``0xAdA100Db00Ca00073811820692005400218FcE1f``, NegRiskCtfCollateralAdapter
  ``0xadA2005600Dec949baf300f4C6120000bDB6eAab``, CollateralOnramp
  ``0x93070a847efEf7F70739046A929D47a521F5B8ee``.
  https://docs.polymarket.com/resources/contracts, https://docs.polymarket.com/concepts/pusd
* **Amounts.** Collateral and outcome tokens both carry 6 decimals. A BUY's maker
  amount is ``price x size`` USD and its taker amount ``size`` tokens; a SELL the
  reverse. Rounding per tick: ``ROUNDING``. https://docs.polymarket.com/trading/place-orders
* **Auth.** L1: EIP-712 ``ClobAuth(address address,string timestamp,uint256 nonce,string
  message)`` under ``{name: "ClobAuthDomain", version: "1", chainId: 137}``; ``GET
  /auth/derive-api-key`` or ``POST /auth/api-key`` answer ``{apiKey, secret,
  passphrase}``. L2: ``POLY_SIGNATURE`` is HMAC-SHA256 over ``timestamp + METHOD + path +
  body`` (the query is not signed), keyed by the base64url-decoded secret, encoded as
  padded urlsafe base64. https://docs.polymarket.com/getting-started/api
* **Tick and minimum.** Ticks 0.1, 0.01, 0.005, 0.0025, 0.001, 0.0001; an off-tick
  price is rejected, never rounded; a size below ``orderMinSize`` is rejected.
  https://docs.polymarket.com/market-data/market-details,
  https://docs.polymarket.com/resources/error-codes
* **Fees.** ``fee = shares x rate x (p (1 - p))^exponent``, takers only, rounded to 5
  decimals, set by the operator at match time from the market's ``feeSchedule``; a BUY
  taker pays it in collateral on top of the notional, a SELL taker out of proceeds.
  https://docs.polymarket.com/trading/fees
* **Order types.** GTC and GTD rest; FOK and FAK do not. Every order here is GTC.
  ``POST /order`` answers ``{success, errorMsg, orderID, status: live | matched |
  delayed | unmatched, makingAmount, takingAmount}``; ``DELETE /order {orderID}``
  answers ``{canceled, not_canceled}``. The CLOB has no client order id: an order's
  identity is its hash, and re-posting it is rejected as ``Duplicated``.
  https://docs.polymarket.com/trading/place-orders, https://docs.polymarket.com/trading/manage-orders
* **Fills and positions.** ``GET /data/order/{hash}`` (``status`` LIVE, MATCHED,
  CANCELED, CANCELED_MARKET_RESOLVED, INVALID; ``original_size``, ``size_matched``);
  ``GET /data/orders``; ``GET /data/trades`` (a trade is MATCHED, MINED, CONFIRMED,
  RETRYING or FAILED; pages end at ``next_cursor == "LTE="``);
  ``GET /balance-allowance``; the Data API's ``/positions``.
  https://docs.polymarket.com/concepts/order-lifecycle, https://docs.polymarket.com/api-spec/clob-openapi.yaml
* **Resolution and redemption.** UMA's optimistic oracle (a 2 h challenge window, days
  on a disputed vote); Gamma shows ``closed``, ``umaResolutionStatus: resolved`` and the
  payout in ``outcomePrices``. A holder redeems with ``redeemPositions(pUSD, 0x0,
  conditionId, [1, 2])`` on the collateral adapter, an on-chain transaction paid in POL.
  https://docs.polymarket.com/concepts/resolution, https://docs.polymarket.com/trading/positions/manage
* **Rate limits.** ``POST /order`` 5,000 per 10 s, ``/data/orders`` and ``/data/trades``
  500, ``/balance-allowance`` 200; Gamma ``/markets`` 300.
  https://docs.polymarket.com/api-reference/rate-limits
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib import error, parse, request

from factorylab.world.polymarket import (
    MAX_BODY_BYTES,
    PolymarketReader,
    PolymarketRefused,
    PolymarketUnavailable,
    parse_book,
    payout,
)

CHAIN_ID = 137
DATA_API_URL = "https://data-api.polymarket.com"
CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_CTF_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_CTF_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
#: Both collateral and outcome tokens carry six decimals on the exchange.
TOKEN_DECIMALS = 6
UNIT = 10 ** TOKEN_DECIMALS

#: The signature types the exchange verifies (wallets-auth): 0 EOA, 1 POLY_PROXY,
#: 2 POLY_GNOSIS_SAFE, 3 POLY_1271 (a Deposit Wallet).
SIGNATURE_TYPES = {0: "EOA", 1: "POLY_PROXY", 2: "POLY_GNOSIS_SAFE", 3: "POLY_1271"}

#: The published limit on the tightest endpoint the pot's own requests reach apart from
#: Gamma ``/markets`` (whose 300 per 10 s it shares with the public reads, checked at
#: load): ``/balance-allowance``, 200 per sliding 10 s (rate-limits, read 2026-09-29).
PUBLISHED_ORDER_REQUESTS_PER_10S = 200
#: Every Polymarket limit is counted over a sliding 10 s.
BUDGET_WINDOW_NS = 10_000_000_000

ORDER_TYPE = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,"
    "uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,"
    "bytes32 builder)")
DOMAIN_TYPE = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
TYPED_DATA_SIGN_TYPE = (
    "TypedDataSign(Order contents,string name,string version,uint256 chainId,"
    "address verifyingContract,bytes32 salt)" + ORDER_TYPE)
AUTH_DOMAIN_TYPE = "EIP712Domain(string name,string version,uint256 chainId)"
AUTH_TYPE = "ClobAuth(address address,string timestamp,uint256 nonce,string message)"
AUTH_MESSAGE = "This message attests that I control the given wallet"
EXCHANGE_NAME, EXCHANGE_VERSION = "Polymarket CTF Exchange", "2"
ZERO32 = "0x" + "00" * 32

#: ROUNDING_CONFIG of the official clients: decimals of price, size and USD amount per tick.
ROUNDING = {
    "0.1": (1, 2, 3), "0.01": (2, 2, 4), "0.005": (3, 2, 5),
    "0.0025": (4, 2, 6), "0.001": (3, 2, 5), "0.0001": (4, 2, 6),
}
#: A salt travels as a JSON number, so it stays within JavaScript's safe integers.
MAX_SALT = 2 ** 53 - 1
#: The page cursor that ends a CLOB listing.
END_CURSOR = "LTE="
FIRST_CURSOR = "MA=="
#: A trade's settlement states: only CONFIRMED is final, FAILED never settles.
TRADE_FINAL, TRADE_FAILED = "CONFIRMED", "FAILED"
#: Seconds a fill poll re-reads before the newest trade it has seen, for a trade the
#: venue lists after a later one; the seen set keeps each one booked once.
TRADE_OVERLAP_S = 600
#: Positions a Data API page is asked for (its own maximum is 500).
POSITIONS_PAGE = 500
#: Pages one poll reads at most; more leaves the stream incomplete for the next poll.
MAX_TRADE_PAGES = 5


def _keccak(data: bytes) -> bytes:
    from eth_utils import keccak

    return keccak(data)


def _word(value: int) -> bytes:
    return int(value).to_bytes(32, "big")


def _address_word(address: str) -> bytes:
    raw = bytes.fromhex(address.removeprefix("0x"))
    if len(raw) != 20:
        raise ValueError("an address is 20 bytes")
    return bytes(12) + raw


def _bytes32(value: str) -> bytes:
    raw = bytes.fromhex(value.removeprefix("0x"))
    if len(raw) != 32:
        raise ValueError("a bytes32 is 32 bytes")
    return raw


def exchange_for(neg_risk: bool) -> str:
    """The exchange that verifies an order: the Neg Risk CTF Exchange for a neg-risk market."""
    return NEG_RISK_CTF_EXCHANGE if neg_risk else CTF_EXCHANGE


def domain_separator(neg_risk: bool) -> bytes:
    """The V2 exchange's EIP-712 domain separator, for its verifying contract."""
    return _keccak(_keccak(DOMAIN_TYPE.encode()) + _keccak(EXCHANGE_NAME.encode())
                   + _keccak(EXCHANGE_VERSION.encode()) + _word(CHAIN_ID)
                   + _address_word(exchange_for(neg_risk)))


def _struct_hash(order: dict[str, Any]) -> bytes:
    return _keccak(
        _keccak(ORDER_TYPE.encode()) + _word(order["salt"]) + _address_word(order["maker"])
        + _address_word(order["signer"]) + _word(int(order["tokenId"]))
        + _word(int(order["makerAmount"])) + _word(int(order["takerAmount"]))
        + _word(order["side"]) + _word(order["signatureType"]) + _word(int(order["timestamp"]))
        + _bytes32(order["metadata"]) + _bytes32(order["builder"]))


def order_hash(order: dict[str, Any], neg_risk: bool) -> str:
    """The order's EIP-712 digest, which the CLOB answers as its ``orderID``.

    Guarantees the exchange's own ``hashOrder`` for the same fields: the digest is a
    function of the signed fields alone (never the signature), so it is known before the
    order is sent and is the order's durable identity.
    """
    return "0x" + _keccak(b"\x19\x01" + domain_separator(neg_risk) + _struct_hash(order)).hex()


def order_amounts(is_buy: bool, size: Decimal, price: Decimal, tick: Decimal) -> tuple[int, int]:
    """(makerAmount, takerAmount) in six-decimal units for a limit order, or raise.

    Guarantees the order the exchange would read is exactly the one asked for: a price
    off the market's tick, a size past two decimals, or an amount past the tick's
    decimals is refused (``PolymarketRefused``), never rounded into another order.
    """
    rounding = ROUNDING.get(format(tick.normalize(), "f"))
    if rounding is None:
        raise PolymarketRefused(f"tick size {tick} is not one Polymarket publishes")
    price_places, size_places, amount_places = rounding
    if not tick <= price <= 1 - tick or price % tick:
        raise PolymarketRefused(f"price is not on the market's {tick} tick inside (0, 1)")
    if size <= 0 or size != size.quantize(Decimal(1).scaleb(-size_places), rounding=ROUND_DOWN):
        raise PolymarketRefused(f"size must be positive with at most {size_places} decimals")
    usd = price * size
    if usd != usd.quantize(Decimal(1).scaleb(-amount_places), rounding=ROUND_DOWN):
        raise PolymarketRefused(f"notional has more than {amount_places} decimals")
    shares, cash = int(size * UNIT), int(usd * UNIT)
    if Decimal(shares) != size * UNIT or Decimal(cash) != usd * UNIT:
        raise PolymarketRefused("amounts are not exact six-decimal units")
    return (cash, shares) if is_buy else (shares, cash)


def order_price_size(side: int, maker_amount: int, taker_amount: int) -> tuple[Decimal, Decimal]:
    """(price, size) an order's amounts state: the inverse of ``order_amounts``."""
    if side == 0:
        return Decimal(maker_amount) / Decimal(taker_amount), Decimal(taker_amount) / UNIT
    return Decimal(taker_amount) / Decimal(maker_amount), Decimal(maker_amount) / UNIT


def salt_of(identity: str) -> int:
    """A salt fixed by an identity string, within JavaScript's safe integers.

    The runtime's identity is the world's namespace, its launch nonce and the intent's
    client id: the same intent always builds the same order and so the same hash, and
    no two launches or intents share one.
    """
    return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") & MAX_SALT


def taker_fee(size: Decimal, price: Decimal, rate: Decimal, exponent: Decimal) -> Decimal:
    """The fee a taker pays: ``size x rate x (p (1 - p))^exponent``, to five decimals.

    Exact decimal arithmetic only (money is never a float): an exponent that is not a
    nonnegative integer is refused rather than approximated.
    """
    if exponent != exponent.to_integral_value() or exponent < 0:
        raise PolymarketRefused("a fee exponent that is not a whole number is not priced")
    value = size * rate * (price * (1 - price)) ** int(exponent)
    return value.quantize(Decimal("0.00001"))


def execution_fee(size: Decimal, price: Decimal, taker: bool, fee_bps: Any,
                  exponent: Any) -> Decimal | None:
    """The fee one execution charged, from what the execution itself states, or None.

    Astra P0 on #177: the operator sets the fee at match time, so a fill's fee is read
    from its own trade (``fee_rate_bps``, get-trades), never from the schedule admission
    saw. A maker leg is charged nothing (fees: "Makers are never charged fees"). A taker
    leg's fee is ``size x fee_rate_bps / 10,000 x p (1 - p)`` to five decimals (the
    documented formula) when the trade states its rate and the market's schedule is the
    documented one (exponent 1); otherwise the execution does not establish it, and
    None is returned: the fee stays open until the custodian's balance settles it
    (``runtime/polymarket.py``, ``reconcile``), never a debit made up here.
    """
    if not taker:
        try:
            return Decimal(0) if fee_bps is None or _dec(fee_bps) == 0 else None
        except (ValueError, ArithmeticError):
            return None
    try:
        rate, power = _dec(fee_bps) / 10_000, _dec(exponent)
    except (ValueError, ArithmeticError, TypeError):
        return None
    if power != 1 or rate < 0:
        return None
    return taker_fee(size, price, rate, power)


# --- signing ------------------------------------------------------------------------------


class Signer:
    """The pot's signing key, held for signing only. Guarantees the key never appears in
    a repr, a result or an error: every failure names a type, never material."""

    def __init__(self, account: Any) -> None:
        self._account = account
        self.address = account.address.lower()

    def __repr__(self) -> str:
        return f"Signer({self.address})"

    def sign_digest(self, digest: bytes) -> str:
        """A 65-byte ``r || s || v`` signature of a 32-byte digest, 0x-hex."""
        signed = self._account.unsafe_sign_hash(digest)
        return "0x" + bytes(signed.signature).hex()

    @classmethod
    def from_environment(cls, key_env: str) -> Signer:
        """The signer whose key the operator placed in ``key_env`` (the CLI reads it from
        ``polymarket.key``, mode 0400 or 0600). Nothing in this repository holds a key."""
        from eth_account import Account

        key = os.environ.get(key_env)
        if not key:
            raise PolymarketRefused(f"{key_env} is not set: no polymarket signer")
        try:
            return cls(Account.from_key(key))
        except Exception:  # noqa: BLE001 - the key's text never reaches an error
            raise PolymarketRefused(f"{key_env} is not a private key") from None


def order_signature(order: dict[str, Any], neg_risk: bool, signer: Signer) -> str:
    """The signature the exchange verifies for ``order``'s signature type.

    Types 0, 1 and 2 sign the order's digest. Type 3 (a Deposit Wallet) signs the
    ERC-7739 ``TypedDataSign`` wrapper whose verifying contract is the wallet and appends
    the app domain separator, the contents hash, the order type string and its length,
    as the official client does (py-clob-client-v2 ``exchange_order_builder_v2``).
    """
    if order["signatureType"] != 3:
        return signer.sign_digest(bytes.fromhex(order_hash(order, neg_risk)[2:]))
    app = domain_separator(neg_risk)
    contents = _struct_hash(order)
    wrapper = _keccak(
        _keccak(TYPED_DATA_SIGN_TYPE.encode()) + contents + _keccak(b"DepositWallet")
        + _keccak(b"1") + _word(CHAIN_ID) + _address_word(order["signer"]) + bytes(32))
    inner = signer.sign_digest(_keccak(b"\x19\x01" + app + wrapper))
    return ("0x" + inner[2:] + app.hex() + contents.hex() + ORDER_TYPE.encode().hex()
            + len(ORDER_TYPE).to_bytes(2, "big").hex())


def auth_digest(address: str, timestamp: int, nonce: int) -> bytes:
    """The L1 ``ClobAuth`` digest a wallet signs to derive its CLOB credentials."""
    domain = _keccak(_keccak(AUTH_DOMAIN_TYPE.encode()) + _keccak(b"ClobAuthDomain")
                     + _keccak(b"1") + _word(CHAIN_ID))
    struct = _keccak(_keccak(AUTH_TYPE.encode()) + _address_word(address)
                     + _keccak(str(timestamp).encode()) + _word(nonce)
                     + _keccak(AUTH_MESSAGE.encode()))
    return _keccak(b"\x19\x01" + domain + struct)


def l1_headers(signer: Signer, timestamp: int, nonce: int = 0) -> dict[str, str]:
    """The L1 headers: the signer's address and its ``ClobAuth`` signature."""
    return {"POLY_ADDRESS": signer.address,
            "POLY_SIGNATURE": signer.sign_digest(auth_digest(signer.address, timestamp, nonce)),
            "POLY_TIMESTAMP": str(timestamp), "POLY_NONCE": str(nonce)}


def hmac_signature(secret: str, timestamp: int, method: str, path: str, body: str) -> str:
    """The L2 ``POLY_SIGNATURE``: HMAC-SHA256 of ``timestamp + METHOD + path + body``."""
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    message = f"{timestamp}{method}{path}{body}".encode()
    return base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode()


@dataclass(frozen=True)
class Credentials:
    """CLOB API credentials. Guarantees none of them is in a repr."""

    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)


def l2_headers(creds: Credentials, address: str, timestamp: int, method: str, path: str,
               body: str = "") -> dict[str, str]:
    """The L2 headers for one request whose exact body bytes are ``body``."""
    return {"POLY_ADDRESS": address,
            "POLY_SIGNATURE": hmac_signature(creds.secret, timestamp, method, path, body),
            "POLY_TIMESTAMP": str(timestamp), "POLY_API_KEY": creds.key,
            "POLY_PASSPHRASE": creds.passphrase}


# --- transport ------------------------------------------------------------------------------


class ClobHttpError(RuntimeError):
    """A CLOB answer outside 2xx. ``status`` is the HTTP status; ``code`` a local reading
    of the body (never the body itself)."""

    def __init__(self, status: int, code: str | None = None) -> None:
        super().__init__(f"HTTP {status}" + (f": {code}" if code else ""))
        self.status, self.code = status, code


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """A trading request goes to the host it named or nowhere."""
        return None


#: Venue refusals read out of an answer's body, by what they say, as local reasons.
REFUSALS = (("tick size", "price breaks the market's tick"),
            ("lower than the minimum", "size below the market's minimum"),
            ("not enough balance", "not enough balance / allowance"),
            ("allowance", "not enough balance / allowance"),
            ("duplicated", "duplicated"),
            ("post_only_mode", "the matching engine accepts post-only orders only"),
            ("closed", "market is closed"),
            ("crosses book", "post-only order crosses the book"))


def refusal_code(body: Any) -> str | None:
    """A local reason for a venue refusal, read from its body; never the body's text."""
    text = json.dumps(body).lower() if body is not None else ""
    return next((reason for needle, reason in REFUSALS if needle in text), None)


def http_send(method: str, url: str, headers: dict[str, str], body: str | None,
              timeout_s: int = 10) -> Any:
    """One bounded HTTPS request that returns parsed JSON, or raises locally.

    Guarantees at most ``timeout_s`` per socket operation, at most ``MAX_BODY_BYTES``
    read, no redirect followed, numbers parsed as ``Decimal``; a non-2xx answer is a
    ``ClobHttpError`` carrying its status and a local reason, a transport failure a
    ``PolymarketUnavailable``; no body and no header is ever in an error.
    """
    data = None if body is None else body.encode()
    req = request.Request(url, data=data, method=method, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "FactoryLab/0.4", **headers})
    try:
        response = request.build_opener(_NoRedirect()).open(req, timeout=timeout_s)
    except error.HTTPError as exc:
        try:
            raw = exc.read(MAX_BODY_BYTES)
            parsed = json.loads(raw) if raw else None
        except (ValueError, OSError):
            parsed = None
        finally:
            exc.close()
        raise ClobHttpError(exc.code, refusal_code(parsed)) from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise PolymarketUnavailable(f"transport: {type(exc).__name__}") from None
    with response:
        raw = response.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise PolymarketUnavailable("response larger than the read bound")
    try:
        return json.loads(raw, parse_float=Decimal) if raw else None
    except (ValueError, UnicodeError):
        raise PolymarketUnavailable("response is not JSON") from None


class BudgetSpent(PolymarketUnavailable):
    """A pot request past ``order_requests_per_10s``: it was not sent."""


@dataclass
class RequestBudget:
    """At most ``limit`` requests in any sliding 10 s of wall time, each counted before
    it is sent. Guarantees a request past the limit is refused locally and never sent."""

    limit: int
    wall: Any = time.time_ns
    stamps: list = field(default_factory=list)

    def spend_all(self) -> None:
        """Count the whole allowance as sent now: the bound for requests a previous
        process may have sent in the window before this one took over."""
        self.stamps = [int(self.wall())] * self.limit

    def take(self) -> None:
        now = int(self.wall())
        self.stamps = [s for s in self.stamps if s > now - BUDGET_WINDOW_NS]
        if len(self.stamps) >= self.limit:
            raise BudgetSpent("polymarket order request budget spent")
        self.stamps.append(now)


# --- the live venue -------------------------------------------------------------------------


def _dec(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("not a finite decimal")
    return number


#: The CLOB's order statuses, read as the runtime's.
ORDER_STATUS = {"LIVE": "resting", "MATCHED": "filled", "CANCELED": "cancelled",
                "CANCELED_MARKET_RESOLVED": "cancelled", "INVALID": "rejected"}


def _redeemable(state: dict[str, Any], token: str, size: Decimal) -> None:
    """Keep what of a resolved token the world held when it was paid: the tokens stay
    in the wallet until redeemed, beside any the funder holds (Codex P2 on #177)."""
    held = state.setdefault("redeemable", {})
    held[token] = str(_dec(held.get(token, "0")) + size)


class LivePolymarket(PolymarketReader):
    """The polymarket pot on Polymarket's CLOB: the ``FakePolymarket`` contract, live.

    Reads the public market exactly as ``PolymarketReader`` does (seat reads and the
    kernel's settlement reads, stamped for their own budget). Everything the pot itself
    sends (orders, cancels, order lookups, fills, the account, a held token's mark and a
    write's market read) is counted by its own ``RequestBudget`` and never stamped as a
    public read.

    Guarantees, for the order path (essay II.II.b, the hard cast):

    * nothing is signed or sent for a client id whose durable intent (``intent_of``)
      does not name exactly this order and its hash;
    * an order's identity is its EIP-712 hash, fixed before submission from the
      intent's fields and a salt derived from its identity (``order_identity``);
    * an answer that did not arrive, a 5xx, a timeout or a ``Duplicated`` refusal is
      ``uncertain`` and is resolved by ``lookup`` of the hash, never by resending;
    * a fill is reported once, when its trade is CONFIRMED, and a FAILED trade never.
    """

    name: str = "polymarket-live"

    def __init__(self, *, funder: str, signature_type: int, budget: int,
                 signer: Signer | None = None, key_env: str = "POLYMARKET_PRIVATE_KEY",
                 identity: Any = None, send: Any = http_send, data_url: str = DATA_API_URL,
                 **reader: Any) -> None:
        super().__init__(**reader)
        self.name = "polymarket-live"
        if signature_type not in SIGNATURE_TYPES:
            raise PolymarketRefused("unknown signature type")
        self.funder = funder.lower()
        self.signature_type = signature_type
        self.budget = RequestBudget(budget, wall=self.wall)
        self.send = send
        self.data_url = data_url
        self.key_env = key_env
        self._signer = signer
        self._creds: Credentials | None = None
        #: () -> (namespace, launch nonce): the launch identity folded into every salt.
        self.identity = identity or (lambda: (None, None))
        #: client id -> its durable intent, or None; set by the runtime (``install``).
        self.intent_of = lambda _client_id: None

    # ---- keys and credentials

    def signer(self) -> Signer:
        """The pot's signer, loaded on first use and checked against the manifest's funder."""
        if self._signer is None:
            self._signer = Signer.from_environment(self.key_env)
        if self.signature_type == 0 and self._signer.address != self.funder:
            raise PolymarketRefused("an EOA order is signed by the funder itself")
        return self._signer

    def _credentials(self) -> Credentials:
        if self._creds is None:
            signer = self.signer()
            now = int(self.wall()) // 1_000_000_000
            answer = None
            try:
                self.budget.take()
                answer = self.send("GET", f"{self.clob_url}/auth/derive-api-key",
                                   l1_headers(signer, now), None)
            except ClobHttpError:
                self.budget.take()
                answer = self.send("POST", f"{self.clob_url}/auth/api-key",
                                   l1_headers(signer, now), None)
            if not isinstance(answer, dict) or not all(
                    isinstance(answer.get(k), str) for k in ("apiKey", "secret", "passphrase")):
                raise PolymarketUnavailable("credentials answer has no key")
            self._creds = Credentials(answer["apiKey"], answer["secret"], answer["passphrase"])
        return self._creds

    def _l2(self, method: str, path: str, *, query: dict | None = None,
            body: Any = None) -> Any:
        """One authenticated CLOB request inside the pot's budget."""
        creds = self._credentials()
        text = "" if body is None else json.dumps(body, separators=(",", ":"))
        self.budget.take()
        now = int(self.wall()) // 1_000_000_000
        url = f"{self.clob_url}{path}" + (f"?{parse.urlencode(query)}" if query else "")
        return self.send(method, url, l2_headers(creds, self.signer().address, now, method,
                                                 path, text), text if body is not None else None)

    def _public(self, url: str) -> Any:
        """One public GET the pot sends inside its own budget (never a seat's)."""
        self.budget.take()
        return self.send("GET", url, {}, None)

    # ---- the market, for the pot's own checks (budgeted, never stamped as a seat read)

    def write_market(self, market_id: str) -> dict[str, Any]:
        """One market by id, read for a write's checks or a held token's resolution."""
        from factorylab.world.polymarket import market_detail

        fresh = {self.CACHE_KEY: self.nonce()}
        detail = market_detail(self._public(
            f"{self.gamma_url}/markets/{parse.quote(market_id, safe='')}?"
            f"{parse.urlencode(fresh)}"))
        if detail is None:
            raise PolymarketUnavailable("market response has no tradable shape")
        return detail

    def write_market_of_token(self, token_id: str) -> dict[str, Any] | None:
        """The market listing ``token_id``, looked up as ``market_of_token`` does."""
        from factorylab.world.polymarket import market_detail

        for closed in ("true", None, "true"):
            query = {k: v for k, v in (("clob_token_ids", token_id), ("closed", closed),
                                       (self.CACHE_KEY, self.nonce())) if v is not None}
            raw = self._public(f"{self.gamma_url}/markets?{parse.urlencode(query)}")
            for row in raw if isinstance(raw, list) else []:
                detail = market_detail(row)
                if detail and any(o["token_id"] == token_id for o in detail["outcomes"]):
                    return detail
        return None

    def mark_book(self, token_id: str) -> dict[str, Any]:
        """A held token's book at depth 1, read for its mark inside the pot's budget."""
        return parse_book(self._public(
            f"{self.clob_url}/book?{parse.urlencode({'token_id': token_id})}"), 1)

    # ---- orders

    def order_identity(self, *, client_id: str, token_id: str, is_buy: bool, size: Decimal,
                       price: Decimal, market: dict[str, Any]) -> dict[str, Any]:
        """The order an intent names, and its hash, before anything is sent.

        Guarantees a pure function of its arguments, the launch identity and the wall
        clock's millisecond (the order's ``timestamp``, which the exchange requires and
        which the intent then records): the runtime ledgers the result with the intent,
        and ``place`` signs exactly it. Refuses an order the exchange would read
        differently from the one asked for (``order_amounts``).
        """
        tick = _dec(market["tick_size"])
        maker_amount, taker_amount = order_amounts(is_buy, size, price, tick)
        namespace, nonce = self.identity()
        signer = self.funder if self.signature_type in (0, 3) else self.signer().address
        order = {"salt": salt_of(f"{namespace}:{nonce}:{client_id}"), "maker": self.funder,
                 "signer": signer, "tokenId": str(int(token_id)),
                 "makerAmount": str(maker_amount), "takerAmount": str(taker_amount),
                 "side": 0 if is_buy else 1, "signatureType": self.signature_type,
                 "timestamp": str(int(self.wall()) // 1_000_000), "metadata": ZERO32,
                 "builder": ZERO32}
        neg_risk = bool(market.get("neg_risk"))
        fees = market.get("fees") or {}
        return {"order": order, "neg_risk": neg_risk,
                "order_hash": order_hash(order, neg_risk),
                "fee_rate": str(fees.get("rate") or "0") if fees.get("enabled") else "0",
                "fee_exponent": str(fees.get("exponent") or "1")}

    def _intended(self, client_id: str, operation: str) -> dict[str, Any]:
        intent = self.intent_of(client_id)
        if not isinstance(intent, dict) or intent.get("operation") != operation:
            raise PolymarketRefused("no durable intent names this order")
        return intent

    def place(self, *, client_id: str, token_id: str, is_buy: bool, size: Decimal,
              price: Decimal) -> dict[str, Any]:
        """Sign and send the order the client id's intent names, as a GTC limit order.

        Refuses, sending nothing, unless the intent exists, names this token, side,
        size and price, and records the identity whose hash its order rebuilds to.
        """
        intent = self._intended(client_id, "polymarket.place_limit")
        identity, args = intent.get("order_identity"), intent.get("args", {})
        if (not isinstance(identity, dict) or str(args.get("token_id")) != str(token_id)
                or (args.get("side") == "buy") != is_buy
                or _dec(args.get("size")) != size or _dec(args.get("price")) != price):
            raise PolymarketRefused("the intent does not name this order")
        order, neg_risk = dict(identity["order"]), bool(identity["neg_risk"])
        if (order_hash(order, neg_risk) != intent.get("order_hash")
                or order_price_size(order["side"], int(order["makerAmount"]),
                                    int(order["takerAmount"])) != (price, size)
                or order["tokenId"] != str(int(token_id))):
            raise PolymarketRefused("the intent's order does not rebuild to its hash")
        signature = order_signature(order, neg_risk, self.signer())
        order_id = intent["order_hash"]
        try:
            owner = self._credentials().key
        except BudgetSpent:
            return self._rejected(order_id, "polymarket order request budget spent")
        body = {"order": {"salt": order["salt"], "maker": order["maker"],
                          "signer": order["signer"], "tokenId": order["tokenId"],
                          "makerAmount": order["makerAmount"],
                          "takerAmount": order["takerAmount"],
                          "side": "BUY" if order["side"] == 0 else "SELL",
                          "expiration": "0", "signatureType": order["signatureType"],
                          "timestamp": order["timestamp"], "metadata": order["metadata"],
                          "builder": order["builder"], "signature": signature},
                "owner": owner, "orderType": "GTC", "postOnly": False, "deferExec": False}
        try:
            answer = self._l2("POST", "/order", body=body)
        except BudgetSpent:
            return self._rejected(order_id, "polymarket order request budget spent")
        except ClobHttpError as exc:
            if exc.status >= 500 or exc.code == "duplicated":
                return {"order_id": order_id, "status": "uncertain",
                        "error": f"order answer: {exc}"}
            return self._rejected(order_id, exc.code or f"rejected by the venue: HTTP {exc.status}")
        return self._placed(order_id, size, answer)

    @staticmethod
    def _rejected(order_id: str, reason: str) -> dict[str, Any]:
        return {"order_id": order_id, "status": "rejected", "filled_size": "0",
                "avg_px": None, "error": reason}

    def _placed(self, order_id: str, size: Decimal, answer: Any) -> dict[str, Any]:
        if not isinstance(answer, dict):
            return {"order_id": order_id, "status": "uncertain",
                    "error": "order answer is not an object"}
        if answer.get("success") is not True:
            code = refusal_code(answer.get("errorMsg"))
            if code == "duplicated":
                return {"order_id": order_id, "status": "uncertain", "error": "duplicated"}
            return self._rejected(order_id, code or "rejected by the venue")
        if answer.get("orderID") and str(answer["orderID"]).lower() != order_id.lower():
            return {"order_id": order_id, "status": "uncertain",
                    "error": "venue answered another order id"}
        status = answer.get("status")
        if status == "live":
            return {"order_id": order_id, "status": "resting", "filled_size": "0",
                    "avg_px": None, "error": None}
        # "matched" says the order took liquidity on arrival; how much of it, and whether
        # the rest rests, is the order's own status, read back by its hash: an ACK is
        # never a fill (fills are booked from CONFIRMED trades alone).
        return {"order_id": order_id, "status": "uncertain",
                "error": f"order status {str(status)[:20]} is read back by its hash"}

    def cancel(self, *, client_id: str, order_id: str) -> dict[str, Any]:
        """Cancel one resting order by its hash; refuses, sending nothing, without an intent."""
        intent = self._intended(client_id, "polymarket.cancel")
        if str(intent.get("args", {}).get("order_id")) != str(order_id):
            raise PolymarketRefused("the intent does not name this cancellation")
        try:
            answer = self._l2("DELETE", "/order", body={"orderID": order_id})
        except BudgetSpent:
            return {"order_id": order_id, "status": "rejected",
                    "error": "polymarket order request budget spent"}
        except ClobHttpError as exc:
            if exc.status >= 500:
                return {"order_id": order_id, "status": "uncertain", "error": str(exc)}
            return {"order_id": order_id, "status": "rejected",
                    "error": exc.code or f"HTTP {exc.status}"}
        canceled = answer.get("canceled") if isinstance(answer, dict) else None
        if isinstance(canceled, list) and order_id in canceled:
            return self.lookup(client_id, order_id=order_id, cancel=True)
        refused = answer.get("not_canceled") if isinstance(answer, dict) else None
        if isinstance(refused, dict) and order_id in refused:
            return {"order_id": order_id, "status": "rejected",
                    "error": refusal_code(refused[order_id]) or "order is not resting"}
        return {"order_id": order_id, "status": "uncertain",
                "error": "cancel answer names neither outcome"}

    def lookup(self, client_id: str, *, order_id: str | None = None,
               cancel: bool = False) -> dict[str, Any]:
        """What the CLOB holds under an order hash, as the runtime reads an answer.

        An order the CLOB does not know (404) or did not answer for is ``uncertain``,
        never a negative acknowledgement: a lost submission may still arrive. For a
        cancellation, a cancelled order is ``cancelled`` and a filled one ``rejected``.
        """
        if not order_id:
            return {"order_id": None, "status": "uncertain", "error": "no order hash"}
        try:
            answer = self._l2("GET", f"/data/order/{order_id}")
        except (ClobHttpError, PolymarketUnavailable) as exc:
            return {"order_id": order_id, "status": "uncertain",
                    "error": f"lookup: {type(exc).__name__}"}
        if not isinstance(answer, dict) or str(answer.get("id", "")).lower() != order_id.lower():
            return {"order_id": order_id, "status": "uncertain", "error": "order not observed"}
        status = ORDER_STATUS.get(str(answer.get("status", "")).upper())
        try:
            matched = _dec(answer.get("size_matched", "0"))
        except (ValueError, ArithmeticError):
            status = None
        if status is None:
            return {"order_id": order_id, "status": "uncertain", "error": "unknown order status"}
        if cancel:
            status = {"filled": "rejected", "resting": "uncertain"}.get(status, status)
        price = answer.get("price")
        return {"order_id": order_id, "status": status, "filled_size": str(matched),
                "avg_px": None if not matched else str(price), "error": None}

    # ---- the pot

    def account(self, *, markets: dict[str, str] | None = None,
                resolved: dict[str, str] | None = None) -> dict[str, Any]:
        """The pot as its custodian states it: pUSD, what resting buys hold, tokens held.

        ``markets`` names each token's market as the world found it; ``resolved`` each
        resolved token's payout, which a held token not yet redeemed is worth.
        """
        observed = int(self.wall())
        balance = self._l2("GET", "/balance-allowance",
                           query={"asset_type": "COLLATERAL",
                                  "signature_type": self.signature_type})
        usdc = _dec(balance["balance"]) / UNIT
        orders = self._open_orders()
        held = sum((_dec(o["price"]) * _dec(o["remaining"]) for o in orders
                    if o["side"] == "buy"), Decimal(0))
        rows = self._positions()
        selling: dict[str, Decimal] = {}
        for order in orders:
            if order["side"] == "sell":
                selling[order["token_id"]] = selling.get(order["token_id"], 0) + _dec(
                    order["remaining"])
        positions = []
        for row in rows if isinstance(rows, list) else []:
            token, size = str(row.get("asset", "")), _dec(row.get("size", "0"))
            if not token or size <= 0:
                continue
            position = {"token_id": token, "market_id": (markets or {}).get(token),
                        "outcome_index": int(row.get("outcomeIndex", 0)),
                        "outcome_name": row.get("outcome"), "size": str(size),
                        "avg_px": str(_dec(row.get("avgPrice", "0"))),
                        "available": str(size - selling.get(token, Decimal(0)))}
            if resolved and token in resolved:
                position["payout"] = str(resolved[token])
            positions.append(position)
        positions.sort(key=lambda p: p["token_id"])
        return {"usdc": str(usdc), "usdc_available": str(usdc - held), "positions": positions,
                "open_orders": orders, "observed_at_ns": observed}

    def _positions(self) -> list[dict[str, Any]]:
        """Every position the Data API lists for the funder, read to the listing's end
        (Astra P1 on #177: one page of 500 could truncate it); a listing longer than the
        page bound is unavailable, never a partial pot."""
        rows: list = []
        # The listing ends at an empty page: a server may cap a page below the limit
        # asked for, so a short page is no proof of the end.
        for _ in range(MAX_TRADE_PAGES + 1):
            batch = self._public(f"{self.data_url}/positions?" + parse.urlencode(
                {"user": self.funder, "sizeThreshold": "0", "limit": str(POSITIONS_PAGE),
                 "offset": str(len(rows))}))
            if not isinstance(batch, list):
                raise PolymarketUnavailable("positions answer is not a list")
            if not batch:
                return rows
            rows.extend(batch)
        raise PolymarketUnavailable("positions did not fit the page bound")

    def _open_orders(self) -> list[dict[str, str]]:
        orders, cursor = [], FIRST_CURSOR
        for _ in range(MAX_TRADE_PAGES):
            page = self._l2("GET", "/data/orders", query={"next_cursor": cursor})
            for row in page.get("data", []) if isinstance(page, dict) else []:
                size, matched = _dec(row["original_size"]), _dec(row.get("size_matched", "0"))
                orders.append({"order_id": str(row["id"]), "token_id": str(row["asset_id"]),
                               "side": "buy" if str(row["side"]).upper() == "BUY" else "sell",
                               "price": str(_dec(row["price"])), "size": str(size),
                               "remaining": str(size - matched)})
            cursor = page.get("next_cursor") if isinstance(page, dict) else END_CURSOR
            if not cursor or cursor == END_CURSOR:
                return orders
        raise PolymarketUnavailable("open orders did not fit the page bound")

    # ---- fills and resolutions

    def poll(self, *, now_ns: int, cursor: dict[str, Any],
             orders: dict[str, dict[str, str]]) -> dict[str, Any]:
        """The pot's fills and resolutions since ``cursor``: ``{events, cursor, complete}``.

        Guarantees each fill of one of ``orders`` (this world's orders, as their intents
        name them) is reported exactly once, when its trade is CONFIRMED, in the shape
        ``FakePolymarket`` reports it, with the fee a taker pays at the market's schedule
        its intent recorded and the realised P&L on the pot's own average cost; a FAILED
        trade is reported never; a trade not yet final holds the cursor so it is read
        again. A held token's market is read (one a poll, in turn) and, once it has a
        payout, one ``resolution`` is reported for what the pot holds of it, and each of
        this world's resting orders on it the venue cancelled is reported ``cancelled``.
        ``complete`` is False when anything went unread; the cursor then keeps it.
        """
        state = json.loads(json.dumps(cursor or {}))
        # Where a read starts is a durable fact, never the moment a poll happens to run
        # (Astra P0, Codex on #177): ``_fills`` holds it at or before the earliest order
        # of this world not wholly booked, less the overlap. No fill of an order can
        # precede the order's own signed timestamp, and a funded wallet's older history
        # is never paged through.
        state.setdefault("seen", {})
        state.setdefault("book", {})
        state.setdefault("resolved", {})
        state.setdefault("terminal", [])
        state.setdefault("turn", 0)
        events: list[dict[str, Any]] = []
        complete = True
        for step in (lambda trial: self._fills(trial, orders),
                     lambda trial: self._resolutions(trial, orders, now_ns)):
            # Each step works on a copy and commits only whole: a read that failed half
            # way leaves the cursor where it was, and what it would have reported is
            # reported by a later poll, once.
            trial = json.loads(json.dumps(state))
            try:
                found = step(trial)
            except (PolymarketUnavailable, ClobHttpError, KeyError, TypeError, ValueError,
                    ArithmeticError, StopIteration):
                complete = False
                continue
            state = trial
            events.extend(found)
        # A read that stopped at the page bound resumes where it stopped (``page``).
        return {"events": events, "cursor": state,
                "complete": complete and "page" not in state}

    def _fills(self, state: dict[str, Any], orders: dict[str, dict[str, str]]) -> list[dict]:
        if not orders:
            return []
        outstanding = [int(o["timestamp"]) // 1000 for oid, o in orders.items()
                       if o.get("timestamp") is not None
                       and _dec(state.get("booked", {}).get(oid, "0")) < _dec(o["size"])]
        if "page" not in state:
            floor = (min(outstanding) - TRADE_OVERLAP_S) if outstanding else None
            if "after" not in state:
                state["after"] = max(0, floor) if floor is not None else 0
            elif floor is not None and floor < state["after"]:
                state["after"] = max(0, floor)
        # At most MAX_TRADE_PAGES pages a poll. A listing longer than that is read over
        # several polls: what was read is booked (the seen set keeps each leg once), the
        # page to resume at is kept in the cursor and ``after`` does not move until the
        # listing has been read to its end, so no row is skipped (Codex P1 on #177).
        rows, page_cursor, ended = [], state.get("page", FIRST_CURSOR), False
        for _ in range(MAX_TRADE_PAGES):
            page = self._l2("GET", "/data/trades", query={
                "maker_address": self.funder, "after": str(state["after"]),
                "next_cursor": page_cursor})
            rows.extend(page.get("data", []) if isinstance(page, dict) else [])
            page_cursor = page.get("next_cursor") if isinstance(page, dict) else END_CURSOR
            if not page_cursor or page_cursor == END_CURSOR:
                ended = True
                break
        found, pending = [], []
        for trade in rows:
            status = str(trade.get("status", "")).upper().removeprefix("TRADE_STATUS_")
            at = int(_dec(trade.get("match_time", "0")))
            # The execution's instant: match_time_nano where the venue states it, else
            # only its second (Sol P0 on #177: never the trade id's lexical order).
            nano = trade.get("match_time_nano")
            try:
                instant = (int(_dec(nano)), True) if nano not in (None, "") else (
                    at * 1_000_000_000, False)
            except (ValueError, ArithmeticError):
                instant = (at * 1_000_000_000, False)
            legs = []
            if str(trade.get("taker_order_id", "")) in orders:
                legs.append((str(trade["taker_order_id"]), trade.get("size"),
                             trade.get("price"), True, trade.get("fee_rate_bps")))
            for maker in trade.get("maker_orders") or []:
                if str(maker.get("order_id", "")) in orders:
                    legs.append((str(maker["order_id"]), maker.get("matched_amount"),
                                 maker.get("price"), False, maker.get("fee_rate_bps")))
            for order_id, size, price, taker, fee_bps in legs:
                key = f"{trade.get('id')}:{order_id}:{int(taker)}"
                if key in state["seen"]:
                    continue
                if status == TRADE_FAILED:
                    # A failed leg never settles; its quantity is kept, so the order's
                    # matched size, once terminal, is released by it (Sol P2 on #177).
                    state["seen"][key] = at
                    failed = state.setdefault("failed", {})
                    failed[order_id] = str(_dec(failed.get(order_id, "0")) + _dec(size))
                    continue
                if status != TRADE_FINAL:
                    pending.append(at)
                    continue
                state["seen"][key] = at
                found.append({"instant": instant[0], "exact": instant[1], "at": at,
                              "key": key, "order_id": order_id, "size": _dec(size),
                              "price": _dec(price), "taker": taker, "fee_bps": fee_bps})
        events = self._in_execution_order(state, orders, found)
        if not ended:
            state["page"] = page_cursor
            state.setdefault("pending", [])
            state["pending"] = sorted(set(state["pending"]) | set(pending))
            return events
        pending = sorted(set(pending) | set(state.pop("pending", [])))
        state.pop("page", None)
        # The next read starts before the oldest trade not yet final (it is read again
        # until it is), else an overlap before the newest trade seen, so a trade the
        # venue lists late is still read. The seen set keeps every leg this world ever
        # booked or saw fail (one short key a fill), so however far back a read starts,
        # nothing is booked twice.
        newest = max(state["seen"].values(), default=0)
        after = max(state["after"], newest - TRADE_OVERLAP_S)
        if pending:
            after = min(after, min(pending) - 1)
        state["after"] = max(0, after)
        return events

    def _in_execution_order(self, state: dict[str, Any], orders: dict[str, dict[str, str]],
                            found: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Book confirmed legs in the order they executed, as the venue states it.

        Sol P0 on #177: cost basis follows execution order, never trade ids. Legs are
        ordered per token by their execution instant (``match_time_nano``). Legs whose
        order the venue does not establish (the same instant, or the same second where
        one of them states only its second) form one group, and no chronology is
        invented for it: of the orderings that keep the holding nonnegative (buys first,
        sales first, or buys above the running cost, sales, then the rest), the one
        booked is the one that reports the least profit.
        """
        by_token: dict[str, list[dict[str, Any]]] = {}
        for leg in found:
            by_token.setdefault(orders[leg["order_id"]]["token_id"], []).append(leg)
        booked: list[tuple[int, int, dict[str, Any]]] = []
        for token, legs in sorted(by_token.items()):
            legs.sort(key=lambda leg: (leg["instant"], leg["key"]))
            groups: list[list[dict[str, Any]]] = []
            for leg in legs:
                last = groups[-1][-1] if groups else None
                if last is not None and (
                        leg["instant"] == last["instant"]
                        or (not (leg["exact"] and last["exact"])
                            and leg["at"] == last["at"])):
                    groups[-1].append(leg)
                else:
                    groups.append([leg])
            for group in groups:
                for leg in self._least_profit(state, orders, token, group):
                    event = self._fill_event(
                        state, orders[leg["order_id"]], leg["order_id"], leg["size"],
                        leg["price"], leg["taker"], leg["at"], leg["fee_bps"])
                    event["ts_ns"] = leg["instant"]
                    booked.append((group[0]["instant"], len(booked), event))
        return [event for _instant, _n, event in sorted(booked, key=lambda b: b[:2])]

    @staticmethod
    def _least_profit(state: dict[str, Any], orders: dict[str, dict[str, str]], token: str,
                      group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(group) == 1:
            return group
        held, avg = (_dec(v) for v in state["book"].get(token, ("0", "0")))
        buys = [leg for leg in group if orders[leg["order_id"]]["side"] == "buy"]
        sells = [leg for leg in group if orders[leg["order_id"]]["side"] != "buy"]
        options = [buys + sells, sells + buys,
                   [b for b in buys if b["price"] > avg] + sells
                   + [b for b in buys if b["price"] <= avg]]

        def realized(order: list[dict[str, Any]]) -> Decimal | None:
            size, cost, total = held, avg, Decimal(0)
            for leg in order:
                if orders[leg["order_id"]]["side"] == "buy":
                    cost = (size * cost + leg["size"] * leg["price"]) / (size + leg["size"])
                    size += leg["size"]
                else:
                    if leg["size"] > size:
                        return None
                    total += (leg["price"] - cost) * leg["size"]
                    size -= leg["size"]
            return total

        scored = [(realized(o), n, o) for n, o in enumerate(options)]
        valid = [item for item in scored if item[0] is not None]
        return min(valid)[2] if valid else options[0]

    @staticmethod
    def _fill_event(state: dict[str, Any], order: dict[str, str], order_id: str,
                    size: Decimal, price: Decimal, taker: bool, at: int,
                    fee_bps: Any = None) -> dict[str, Any]:
        token, is_buy = order["token_id"], order["side"] == "buy"
        fee = execution_fee(size, price, taker, fee_bps, order.get("fee_exponent", "1"))
        held, avg = (_dec(v) for v in state["book"].get(token, ("0", "0")))
        realized = Decimal(0)
        if is_buy:
            total = held + size
            avg = (held * avg + size * price) / total
            held = total
        else:
            realized = (price - avg) * size
            held -= size
        state["book"][token] = [str(held), str(avg)]
        booked = state.setdefault("booked", {})
        booked[order_id] = str(_dec(booked.get(order_id, "0")) + size)
        return {"kind": "fill", "order_id": order_id, "token_id": token,
                "market_id": order.get("market_id"), "is_buy": is_buy, "size": str(size),
                "px": str(price), "fee_usd": "0" if fee is None else str(fee),
                "realized_usd": str(realized), "ts_ns": at * 1_000_000_000,
                **({"fee_unresolved": True} if fee is None else {})}

    def _resolutions(self, state: dict[str, Any], orders: dict[str, dict[str, str]],
                     now_ns: int) -> list[dict]:
        markets = {o["token_id"]: o.get("market_id") for o in orders.values()}
        # What the pot holds or may still come to hold now: a token with an order that
        # rests, is unanswered or has matched more than is booked, never one whose
        # orders are all over (Codex P2 on #177: the rotation grew with history).
        open_tokens = {o["token_id"] for oid, o in orders.items()
                       if o.get("open", True) and oid not in state["terminal"]}
        held = {token for token, (size, _avg) in state["book"].items() if _dec(size) > 0}
        candidates = sorted(t for t in held | open_tokens
                            if t not in state["resolved"] and markets.get(t))
        events: list[dict[str, Any]] = []
        facts = state.setdefault("resolution_facts", {})

        def selling(token: str) -> bool:
            # A sell of this world's on the token that may still have matched quantity
            # not yet booked (Codex P1 on #177): what the pot holds of the token is not
            # known until it is terminal and wholly booked, so no payout is sized yet.
            return any(o["token_id"] == token and o["side"] == "sell"
                       and oid not in state["terminal"] for oid, o in orders.items())

        # Inventory a trade confirmed after its market resolved (Astra P0 on #177): it is
        # paid its token's payout once, when it is booked, never lost. A resolution is
        # paid only once no sell of the token may still take from what it pays.
        for token, paid in sorted(state["resolved"].items()):
            size, avg = (_dec(v) for v in state["book"].get(token, ("0", "0")))
            if size > 0 and token in facts and not selling(token):
                state["book"][token] = ["0", str(avg)]
                _redeemable(state, token, size)
                events.append({**facts[token], "kind": "resolution", "token_id": token,
                               "payout": str(paid), "size": str(size),
                               "realized_usd": str((_dec(paid) - avg) * size),
                               "ts_ns": now_ns})
        if candidates:
            # One market read a poll, in turn: what the pot holds or has resting.
            token = candidates[state["turn"] % len(candidates)]
            state["turn"] += 1
            market = self.write_market(str(markets[token]))
            paid = payout(market, token)
            if paid is not None:
                state["resolved"][token] = str(paid)
                outcome = next(o for o in market["outcomes"] if o["token_id"] == token)
                facts[token] = {"market_id": markets[token],
                                "condition_id": market.get("condition_id"),
                                "outcome_index": outcome["outcome_index"],
                                "outcome_name": outcome["outcome"]}
                size, avg = (_dec(v) for v in state["book"].get(token, ("0", "0")))
                if size > 0 and not selling(token):
                    state["book"][token] = ["0", str(avg)]
                    _redeemable(state, token, size)
                    events.append({
                        **facts[token], "kind": "resolution", "token_id": token,
                        "payout": str(paid), "size": str(size),
                        "realized_usd": str((paid - avg) * size), "ts_ns": now_ns})
        # A resolution cancels what rests on the market (CANCELED_MARKET_RESOLVED): each
        # of this world's orders on a resolved token is read back, two a poll, until the
        # venue says it is terminal AND every quantity it matched is booked from a
        # CONFIRMED trade (Astra P0 on #177): an order matched but not yet confirmed
        # stays read, so its fill, and the payout of what it bought, are booked later.
        waiting = sorted(oid for oid, o in orders.items()
                         if o["token_id"] in state["resolved"] and oid not in state["terminal"])
        start = state.get("lookup_turn", 0)
        state["lookup_turn"] = start + 1
        for order_id in [waiting[(start + k) % len(waiting)]
                         for k in range(min(2, len(waiting)))]:
            answer = self.lookup("", order_id=order_id)
            if answer["status"] not in ("cancelled", "filled", "rejected"):
                continue
            complete = _dec(answer.get("filled_size") or "0") <= _dec(
                state.get("booked", {}).get(order_id, "0")) + _dec(
                state.get("failed", {}).get(order_id, "0"))
            if answer["status"] == "cancelled" and order_id not in state.setdefault(
                    "cancel_told", []):
                state["cancel_told"].append(order_id)
                events.append({"kind": "cancelled", "order_id": order_id,
                               "token_id": orders[order_id]["token_id"],
                               "market_id": markets.get(orders[order_id]["token_id"]),
                               "ts_ns": now_ns})
            if complete:
                state["terminal"].append(order_id)
        return events

    def drain_events(self) -> list[dict[str, Any]]:
        """Nothing: the live venue's events arrive through ``poll`` alone."""
        return []


def live_venue(spec: Any, *, identity: Any = None) -> LivePolymarket:
    """The live order venue a ``[polymarket] venue = "live", orders = true`` world trades.

    Loads no key and sends nothing: the signer is read from ``POLYMARKET_PRIVATE_KEY`` on
    first use and the CLOB credentials derived on first use, inside a journaled call.
    """
    return LivePolymarket(funder=spec.funder, signature_type=spec.signature_type,
                          budget=spec.order_requests_per_10s, identity=identity)
