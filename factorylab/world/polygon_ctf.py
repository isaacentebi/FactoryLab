"""Polygon's own word on the polymarket pot: pUSD and the Conditional Tokens, read only.

The venue is the world (AGENTS.md: a venue is not architecture). Issue #180: the pot's
reconciliation read only Polymarket's APIs, so an answer they gave consistently wrong
(a payout, a balance, reported identically everywhere) would be adopted, not detected.
The chain is an independent source: what a wallet holds and what a token redeems for
are facts of the Conditional Tokens contract, whatever any API says about them. This
module reads them and nothing else. It sends ``eth_chainId``, ``eth_getBlockByNumber``
and ``eth_call`` only: it holds no key, signs nothing and can move nothing.

Facts, each checked 2026-10-03 by ``eth_getCode`` and ``eth_call`` on Polygon
(chain 137, https://polygon-bor-rpc.publicnode.com):

* **Contracts.** Conditional Tokens ``0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`` and
  pUSD ``0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`` (six decimals), both deployed
  (https://docs.polymarket.com/resources/contracts, read 2026-10-03).
* **A held token is a CTF position.** An outcome token id is its ERC-1155 position id:
  ``getPositionId(collateral, getCollectionId(0x0, conditionId, 1 << outcomeIndex))``
  (https://docs.polymarket.com/trading/positions/how-positions-work;
  https://github.com/gnosis/conditional-tokens-contracts, ``CTHelpers.sol``). The
  collateral is not pUSD, as that page says, but what the adapters themselves state:
  USDC.e ``0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`` for a standard market
  (``CtfCollateralAdapter.USDCE()``) and the Neg Risk Adapter's wrapped collateral
  ``0x3A3BD7bb9528E159577F7C2e685CC81A765002E2`` for a neg-risk one
  (``NegRiskCtfCollateralAdapter.WRAPPED_COLLATERAL()``, ``NegRiskAdapter.wcol()``).
  Both rules reproduced the Gamma token ids of resolved and open markets of each kind.
* **A resolution is the condition's payout vector.** ``payoutDenominator(conditionId)``
  is 0 until the oracle reports, then each outcome's token redeems for
  ``payoutNumerators(conditionId, index) / payoutDenominator(conditionId)``; a report is
  final (``reportPayouts`` requires a zero denominator). Redemption pays exactly that
  (https://docs.polymarket.com/concepts/resolution,
  https://docs.polymarket.com/trading/positions/manage).
* **Balances.** ERC-20 ``balanceOf(address)`` on pUSD; ERC-1155
  ``balanceOfBatch(address[], uint256[])`` on the Conditional Tokens.

Every read of one observation is pinned to one block, the chain's ``finalized`` head
(about two blocks, a few seconds, behind its newest, read 2026-10-03), so the facts of
one observation are one state of the chain and none can be reorganised away after it
is bound. ``polygon-rpc.com``, the endpoint Polygon long published, answered
"API key disabled" on 2026-10-03, so the default is PublicNode's; an operator may name
another endpoint in ``POLYGON_RPC_URL`` (a keyed URL is a secret: it never appears in a
result, an error or the ledger).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

from eth_abi import decode, encode
from eth_utils import keccak

from factorylab.world.polymarket import PolymarketUnavailable
from factorylab.world.polymarket_clob import (
    CONDITIONAL_TOKENS,
    PUSD,
    BudgetSpent,
    RequestBudget,
    http_send,
)

CHAIN_ID = 137
#: The read-only endpoint used when the operator names none (``POLYGON_RPC_URL``).
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
RPC_ENV = "POLYGON_RPC_URL"
#: The collateral a standard market's positions are made of (``CtfCollateralAdapter.USDCE``).
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
#: The collateral a neg-risk market's positions are made of (``NegRiskAdapter.wcol``).
NEG_RISK_WRAPPED_COLLATERAL = "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2"
#: Outcome slots of a Polymarket market: binary, index sets 1 and 2.
OUTCOMES = 2
#: The requests one reader sends in any sliding 10 s of wall time, each counted before it
#: is sent; one past it is not sent and the read is unread. The endpoint publishes no
#: limit; a tick's reads are two reconciliations' 4 each, a resolution check's 7, and 4
#: for each resolved token whose payout is being paid.
CHAIN_REQUESTS_PER_10S = 30

_HEX = re.compile(r"0x(?:[0-9a-fA-F]{2})*")
_QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)")
_BYTES32 = re.compile(r"0x[0-9a-fA-F]{64}")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_TOKEN = re.compile(r"0|[1-9][0-9]{0,99}")


class ChainUnread(PolymarketUnavailable):
    """A chain read that did not answer as the chain answers: unread, never a zero. The
    message is a local reason; it never carries an RPC body or the endpoint's URL."""


def _selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def collateral_for(neg_risk: bool) -> str:
    """The collateral a market's positions are made of, by its kind."""
    return NEG_RISK_WRAPPED_COLLATERAL if neg_risk else USDC_E


def condition(value: Any) -> bytes:
    """A condition id: ``0x`` and 64 hex digits, or ``ChainUnread``."""
    if not isinstance(value, str) or not _BYTES32.fullmatch(value):
        raise ChainUnread("a condition id is not 32 bytes")
    return bytes.fromhex(value[2:])


def rpc_url(url: str) -> str:
    """An endpoint the reader may send to: https, a host, no credentials, no fragment.

    Guarantees a refusal never repeats the URL (a keyed URL is a secret)."""
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
        # A host or port that does not parse raises with its own text, a secret's
        # perhaps (Sol P1, round 1 of #180): only the fixed refusal below travels.
        _ = parsed and (parsed.hostname, parsed.port)
    except ValueError:
        parsed = None
    if (parsed is None or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError(f"{RPC_ENV} must be an https URL with no credentials or "
                         "fragment") from None
    return url


class PolygonCtf:
    """The pot's wallet as Polygon states it, at the chain's finalized head.

    Guarantees: it sends only ``eth_chainId``, ``eth_getBlockByNumber`` and ``eth_call``
    to the pinned contracts; every observation first proves the endpoint is chain 137
    and pins every read to one finalized block; at most ``limit`` requests leave it in
    any sliding 10 s of wall time, counted before each is sent; and every answer is
    parsed strictly (JSON-RPC 2.0, this request's id, a result and no error, a word
    exactly where a word is due), so anything else is ``ChainUnread``, never a value.
    """

    def __init__(self, *, rpc: str = POLYGON_RPC, send: Any = http_send,
                 wall: Any = time.time_ns, limit: int = CHAIN_REQUESTS_PER_10S) -> None:
        self._rpc = rpc_url(rpc)
        self.send = send
        self.budget = RequestBudget(limit, wall=wall)
        self._id = 0

    def __repr__(self) -> str:
        return "PolygonCtf()"

    @classmethod
    def from_environment(cls) -> PolygonCtf:
        """The reader on the operator's endpoint (``POLYGON_RPC_URL``), else the default."""
        return cls(rpc=os.environ.get(RPC_ENV) or POLYGON_RPC)

    # ---- transport

    def _request(self, method: str, params: list) -> Any:
        try:
            self.budget.take()
        except BudgetSpent:
            raise ChainUnread("polygon request budget spent") from None
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                           "params": params}, separators=(",", ":"))
        try:
            answer = self.send("POST", self._rpc, {}, body)
        except Exception as exc:  # noqa: BLE001 - the endpoint's words never travel on
            raise ChainUnread(f"polygon {method}: {type(exc).__name__}") from None
        if (not isinstance(answer, dict) or answer.get("jsonrpc") != "2.0"
                or answer.get("id") != self._id or "error" in answer or "result" not in answer):
            raise ChainUnread(f"polygon {method}: not a result")
        return answer["result"]

    def _quantity(self, value: Any, what: str) -> int:
        if not isinstance(value, str) or not _QUANTITY.fullmatch(value):
            raise ChainUnread(f"polygon {what} is not a quantity")
        return int(value, 16)

    def head(self) -> int:
        """The finalized block of chain 137, or ``ChainUnread``."""
        if self._quantity(self._request("eth_chainId", []), "chain id") != CHAIN_ID:
            raise ChainUnread("the endpoint is not Polygon")
        block = self._request("eth_getBlockByNumber", ["finalized", False])
        if not isinstance(block, dict):
            raise ChainUnread("polygon finalized block is not a block")
        return self._quantity(block.get("number"), "block number")

    def _call(self, to: str, signature: str, types: list[str], values: list,
              block: int) -> bytes:
        data = "0x" + (_selector(signature) + encode(types, values)).hex()
        raw = self._request("eth_call", [{"to": to, "data": data}, hex(block)])
        if not isinstance(raw, str) or not _HEX.fullmatch(raw):
            raise ChainUnread("polygon call answer is not hex")
        return bytes.fromhex(raw[2:])

    def _word(self, to: str, signature: str, types: list[str], values: list,
              block: int) -> int:
        raw = self._call(to, signature, types, values, block)
        if len(raw) != 32:
            raise ChainUnread("polygon call answer is not one word")
        return int.from_bytes(raw, "big")

    # ---- observations

    def account(self, owner: str, tokens: list[str]) -> dict[str, Any]:
        """``owner``'s pUSD and its balance of each outcome token, in six-decimal units,
        at one finalized block: ``{"block", "usdc", "tokens": {token: units}}``, amounts
        as decimal strings."""
        if not isinstance(owner, str) or not _ADDRESS.fullmatch(owner):
            raise ChainUnread("the owner is not an address")
        ids = []
        for token in tokens:
            if not isinstance(token, str) or not _TOKEN.fullmatch(token):
                raise ChainUnread("a token id is not canonical")
            ids.append(int(token))
        block = self.head()
        usdc = self._word(PUSD, "balanceOf(address)", ["address"], [owner], block)
        held: dict[str, str] = {}
        if ids:
            raw = self._call(CONDITIONAL_TOKENS, "balanceOfBatch(address[],uint256[])",
                             ["address[]", "uint256[]"], [[owner] * len(ids), ids], block)
            try:
                (balances,) = decode(["uint256[]"], raw)
            except Exception:  # noqa: BLE001 - an undecodable answer is unread
                raise ChainUnread("polygon balances are not a uint256[]") from None
            # The canonical encoding of exactly one balance a token: nothing more, less
            # or trailing.
            if len(balances) != len(ids) or encode(["uint256[]"], [list(balances)]) != raw:
                raise ChainUnread("polygon balances do not answer the tokens asked")
            held = {token: str(units) for token, units in zip(tokens, balances, strict=True)}
        return {"block": block, "usdc": str(usdc), "tokens": held}

    def resolution(self, condition_id: str, *, token: str | None = None,
                   index: int | None = None, neg_risk: bool = False) -> dict[str, Any]:
        """A condition's payout at one finalized block: ``{"block", "denominator",
        "numerators"}`` (``denominator`` 0: not reported). With ``token`` it also
        states ``issues``: whether that token is the condition's position at outcome
        ``index`` for the market's collateral (``collateral_for``)."""
        cond = condition(condition_id)
        if token is not None and (not isinstance(token, str) or not _TOKEN.fullmatch(token)
                                  or type(index) is not int or not 0 <= index < OUTCOMES):
            raise ChainUnread("a position is not a token at an outcome index")
        block = self.head()
        found: dict[str, Any] = {"block": block}
        if token is not None:
            collection = self._call(
                CONDITIONAL_TOKENS, "getCollectionId(bytes32,bytes32,uint256)",
                ["bytes32", "bytes32", "uint256"], [bytes(32), cond, 1 << index], block)
            if len(collection) != 32:
                raise ChainUnread("polygon collection id is not one word")
            position = self._word(CONDITIONAL_TOKENS, "getPositionId(address,bytes32)",
                                  ["address", "bytes32"],
                                  [collateral_for(neg_risk), collection], block)
            found["issues"] = position == int(token)
        found["denominator"] = str(self._word(
            CONDITIONAL_TOKENS, "payoutDenominator(bytes32)", ["bytes32"], [cond], block))
        found["numerators"] = [str(self._word(
            CONDITIONAL_TOKENS, "payoutNumerators(bytes32,uint256)", ["bytes32", "uint256"],
            [cond, i], block)) for i in range(OUTCOMES)]
        return found
