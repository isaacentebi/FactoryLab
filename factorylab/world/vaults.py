"""Hyperliquid vaults as a venue surface: the venue's terms, its wire, and a fake book.

Sources verified 2026-09-22 (docs fetched, public read-only /info queried on both
networks, no signed call made):
https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-leaders-legacy
https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors-legacy
https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/hypercore-vaults-legacy
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint#deposit-or-withdraw-from-a-vault
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#retrieve-details-for-a-vault
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#retrieve-a-users-vault-deposits
https://github.com/hyperliquid-dex/hyperliquid-python-sdk
    (0.24.0 in .venv: Exchange.vault_usd_transfer and Info.user_vault_equities; it has
    no createVault, vaultDetails or leadingVaults helper)
https://github.com/nktkas/hyperliquid/blob/main/src/api/exchange/_methods/createVault.ts
https://github.com/nktkas/hyperliquid/blob/main/src/api/exchange/_methods/vaultTransfer.ts
https://github.com/nktkas/hyperliquid/blob/main/src/api/exchange/_methods/vaultDistribute.ts
https://github.com/nktkas/hyperliquid/blob/main/src/api/info/_methods/leadingVaults.ts
https://github.com/nktkas/hyperliquid/blob/main/src/api/info/_methods/userNonFundingLedgerUpdates.ts

What the documents say and the venue showed:

* Leader profit share 10%: the leaders page, and ``leaderCommission: 0.1`` in
  ``vaultDetails`` for mainnet user vaults (HLP, a protocol vault, reports 0).
* Leader keeps at least 5% of the vault; a withdrawal that would take it lower is
  refused (leaders page).
* Creation needs an initial deposit of at least 100 USDC (leaders page; the TS SDK
  schema ``initialUsd >= 100 * 1e6``) and a 10,000 USDC creation fee. The docs say
  so, and every ``vaultCreate`` ledger row read in September 2026, mainnet and
  testnet, carries ``fee: "10000.0"`` (a December 2025 row carried ``"100.0"``; the
  fee changed, so it is read from the row, never assumed, when the venue reports it).
* Depositor lockup: 1 day for user vaults (depositors page); mainnet followers show
  ``lockupUntil - vaultEntryTime = 86_400_000`` ms. Testnet followers show 20_000 ms.
* The commission is taken when a depositor withdraws, on the profit of the part
  withdrawn: the depositors page's example pays 190 of 200 against a basis of 100,
  and a mainnet ``vaultWithdraw`` row satisfies ``commission = 0.1 * (requestedUsd -
  basis)`` exactly. The leader receives it as a ``vaultLeaderCommission`` row on its
  own account.
* A leader's own withdrawal is charged the commission too, and the same
  transaction pays it straight back to the leader as a ``vaultLeaderCommission``
  row with the same hash (mainnet, leader 0xf4f7…239f, 2026-08-20). That row is the
  factory's own money returning, never income from outside.
* ``vaultTransfer`` carries ``usd`` as an integer of micro-USDC (SDK signature and TS
  schema, ``float * 1e6``) and answers ``{"status": "ok", "response": {"type":
  "default"}}``. ``createVault`` carries name (3-50), description (10-250),
  ``initialUsd`` (micro-USDC) and ``nonce`` and answers with the vault address.
  Neither action carries a client order id, so a lost acknowledgement is resolved by
  finding the transfer's own row in ``userNonFundingLedgerUpdates``.
* ``vaultDistribute`` hands vault funds to followers (``usd: 0`` closes the vault);
  there is no action that distributes the profit share, which the venue pays itself.

Unverified without a signed call: the msgpack field order ``createVault`` is hashed
with, whether a testnet creation is charged the same fee in the same class, which
account class the commission is credited to (perps is assumed), the leader's own
lockup, and why testnet reported a leader's ``maxWithdrawable`` as half its equity.
"""

from __future__ import annotations

import hashlib
from decimal import ROUND_DOWN, Decimal
from typing import Any

#: The leader's share of a depositor's profit, taken at the depositor's withdrawal.
LEADER_PROFIT_SHARE = Decimal("0.1")
#: The least fraction of its vault a leader may hold after its own withdrawal.
LEADER_MIN_FRACTION = Decimal("0.05")
#: The least initial deposit a vault is created with, in USDC.
MIN_CREATE_USD = Decimal(100)
#: The creation fee the venue charged every vault created in September 2026, in USDC.
CREATE_FEE_USD = Decimal(10_000)
#: Depositor lockup after the latest deposit, by network, in nanoseconds.
LOCKUP_NS = {"mainnet": 86_400 * 10**9, "testnet": 20 * 10**9}
NAME_LENGTH = (3, 50)
DESCRIPTION_LENGTH = (10, 250)
ADDRESS_PATTERN = r"^0x[0-9a-fA-F]{40}$"
MICRO = Decimal("0.000001")
#: The venue ledger row kinds this surface reads.
LEDGER_KINDS = ("vaultCreate", "vaultDeposit", "vaultWithdraw", "vaultDistribution",
                "vaultLeaderCommission")
#: The fake venue's own account address: a leader identity for its vault book.
FAKE_ACCOUNT = "0x" + "fa4e".rjust(40, "0")


def _usd(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("amount must be finite")
    return number


def commission_on(equity: Decimal, basis: Decimal, usd: Decimal) -> tuple[Decimal, Decimal]:
    """Guarantees the venue's arithmetic: (commission, basis of the part withdrawn).

    The withdrawn part carries its pro rata share of the depositor's basis; the
    commission is the leader's share of whatever the part is worth above that basis,
    and never negative: a loss pays no commission.
    """
    if equity <= 0 or usd <= 0 or usd > equity:
        raise ValueError("withdrawal must be positive and within the equity held")
    basis_out = (basis * usd / equity).quantize(MICRO, rounding=ROUND_DOWN)
    profit = max(Decimal(0), usd - basis_out)
    return (profit * LEADER_PROFIT_SHARE).quantize(MICRO, rounding=ROUND_DOWN), basis_out


def check_create(name: Any, description: Any, usd: Any) -> str | None:
    """Guarantees a reason for any creation the venue's own schema refuses, or None."""
    if not isinstance(name, str) or not NAME_LENGTH[0] <= len(name) <= NAME_LENGTH[1]:
        return f"vault name must be {NAME_LENGTH[0]}-{NAME_LENGTH[1]} characters"
    if (not isinstance(description, str)
            or not DESCRIPTION_LENGTH[0] <= len(description) <= DESCRIPTION_LENGTH[1]):
        low, high = DESCRIPTION_LENGTH
        return f"vault description must be {low}-{high} characters"
    try:
        amount = _usd(usd)
    except (ArithmeticError, ValueError):
        return "usd is not a number"
    if amount < MIN_CREATE_USD:
        return f"below minimum: a vault is created with at least {MIN_CREATE_USD} USDC"
    return None


def leader_share_after(own: Decimal, total: Decimal, usd: Decimal) -> Decimal | None:
    """The leader's fraction after withdrawing ``usd``, or None when nothing would remain."""
    remaining = total - usd
    if remaining <= 0:
        return None
    return (own - usd) / remaining


def row_hash(*parts: Any) -> str:
    """A deterministic transaction hash for the fake venue's ledger rows."""
    return "0x" + hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


# --------------------------------------------------------------------------- wire


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def details_from_wire(raw: Any, account: str | None, observed_ns: int) -> dict:
    """Guarantees a ``vaultDetails`` answer in this surface's shape, or an error.

    Total equity is the latest point of the vault's own day account value; the
    followers list the venue returns is capped, so a count of it is a lower bound and
    says so. A vault the venue does not know is an error, never an empty vault.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("leader"), str):
        return {"error": "vault not found"}
    followers = raw.get("followers") or []
    state = raw.get("followerState") or None
    leader = raw["leader"].lower()
    total = None
    for period, data in raw.get("portfolio") or []:
        history = (data or {}).get("accountValueHistory") or []
        if period == "day" and history:
            total = _decimal_or_none(history[-1][1])
    if total is None:
        total = sum((_decimal_or_none(f.get("vaultEquity")) or Decimal(0) for f in followers),
                    Decimal(0))
    depositors = [f for f in followers
                  if str(f.get("user", "")).lower() not in (leader, "leader")]
    own = _decimal_or_none((state or {}).get("vaultEquity")) or Decimal(0)
    lockup = (state or {}).get("lockupUntil")
    return {
        "vault": str(raw.get("vaultAddress", "")).lower(),
        "name": raw.get("name"),
        "description": raw.get("description"),
        "leader": leader,
        "is_leader": bool(account) and leader == str(account).lower(),
        "equity_usd": total,
        "depositors": len(depositors),
        "depositors_capped": len(followers) >= 100,
        "leader_fraction": _decimal_or_none(raw.get("leaderFraction")),
        "leader_commission": _decimal_or_none(raw.get("leaderCommission")),
        "own_equity_usd": own,
        "own_lockup_until_ns": int(lockup) * 1_000_000 if isinstance(lockup, int) else None,
        "max_withdrawable_usd": _decimal_or_none(raw.get("maxWithdrawable")),
        "allow_deposits": bool(raw.get("allowDeposits", True)),
        "is_closed": bool(raw.get("isClosed", False)),
        "observed_at_ns": observed_ns,
    }


#: The row type a vault row the venue sent but this surface could not read becomes.
UNPARSED = "unparsed"


def ledger_rows(page: Any) -> list[dict]:
    """Guarantees every vault row of a non-funding ledger page, normalised, in page order.

    A row of a vault kind this surface cannot read is kept as an ``unparsed`` row with
    whatever identity it has, never dropped: a dropped own ``vaultWithdraw`` would make
    the commission repaid in its transaction look like income from outside.
    """
    rows = []
    for row in page if isinstance(page, list) else []:
        try:
            delta = row["delta"]
            kind = delta.get("type")
        except (KeyError, TypeError, AttributeError):
            continue
        if kind not in LEDGER_KINDS:
            continue
        try:
            item = {"ts_ns": int(row["time"]) * 1_000_000, "hash": str(row.get("hash") or ""),
                    "type": kind, "vault": str(delta.get("vault") or "").lower() or None,
                    "user": str(delta.get("user") or "").lower() or None}
            for key, wire in (("usd", "usdc"), ("fee", "fee"), ("requested", "requestedUsd"),
                              ("commission", "commission"), ("closing_cost", "closingCost"),
                              ("basis", "basis"), ("net", "netWithdrawnUsd")):
                if wire in delta:
                    value = _decimal_or_none(delta[wire])
                    if value is None:
                        raise ValueError(wire)
                    item[key] = value
        except (KeyError, TypeError, ValueError, AttributeError):
            try:
                ts_ns = int(row["time"]) * 1_000_000
            except (KeyError, TypeError, ValueError):
                ts_ns = 0
            item = {"ts_ns": ts_ns, "hash": str(row.get("hash") or ""), "type": UNPARSED,
                    "kind": str(kind), "vault": None, "user": None}
        rows.append(item)
    return rows


def own_withdraw_hashes(rows: list[dict], account: str | None) -> set[str]:
    """The transactions of this account's own vault withdrawals.

    A leader's withdrawal from its own vault is charged the commission and repaid it
    in the same transaction, as a ``vaultLeaderCommission`` row with the same hash.
    Guarantees every such hash is named, whatever amount the repayment carries, so a
    commission in one of them is never booked as income from outside.
    """
    me = (account or "").lower()
    return {r["hash"] for r in rows
            if r["type"] == "vaultWithdraw" and r.get("user") in (None, me)}


def rebates(rows: list[dict], account: str | None) -> set[tuple[str, Decimal]]:
    """The (hash, amount) of each commission row repaying this account's own withdrawal."""
    own = own_withdraw_hashes(rows, account)
    return {(r["hash"], r.get("usd")) for r in rows
            if r["type"] == "vaultLeaderCommission" and r["hash"] in own}


def _matches(rows: list[dict], operation: str, args: dict, me: str) -> list[dict]:
    usd = _usd(args["usd"])
    if operation == "venue.vault_create":
        return [r for r in rows if r["type"] == "vaultCreate" and r.get("usd") == usd]
    if operation == "venue.vault_deposit":
        return [r for r in rows if r["type"] == "vaultDeposit" and r.get("usd") == usd
                and r["vault"] == str(args["vault"]).lower()]
    return [r for r in rows if r["type"] == "vaultWithdraw"
            and r.get("requested") == usd and r["vault"] == str(args["vault"]).lower()
            and r.get("user") in (None, me)]


def match_intent(rows: list[dict], operation: str, args: dict, account: str | None, *,
                 claimed: frozenset[str] | set[str] = frozenset(), position: int = 0,
                 peers: int = 1) -> dict:
    """Resolve one vault write from the venue's own ledger rows.

    ``claimed`` are the transaction hashes other writes are already bound to; a row
    one of them names is never this write's. ``peers`` is how many unbound writes
    share this one's operation, vault and amount (this one included), and
    ``position`` is this write's place among them in submission order.

    Guarantees a write is confirmed by a row nobody else holds: when the unclaimed
    matching rows are exactly as many as the unbound writes that could own them,
    they are paired in order and this write takes its own; when this write is the
    only candidate and one row matches, it takes that row. Any other count confirms
    nothing -- no row is an unread write, not a failed one, and a surplus row could
    be anyone's.
    """
    usd = _usd(args["usd"])
    me = (account or "").lower()
    found = sorted((r for r in _matches(rows, operation, args, me)
                    if r["hash"] not in claimed), key=lambda r: (r["ts_ns"], r["hash"]))
    if not found:
        return {"status": "uncertain", "error": "vault write not observed in the venue ledger"}
    if len(found) == peers and 0 <= position < peers:
        row = found[position]
    elif peers == 1 and len(found) == 1:
        row = found[0]
    else:
        return {"status": "uncertain",
                "error": f"{len(found)} unclaimed matching venue rows for {peers} "
                         "unbound writes confirm nothing"}
    result = {"status": "ok", "vault": row["vault"], "usd": str(usd), "hash": row["hash"]}
    if operation == "venue.vault_create" and "fee" in row:
        result["fee_usd"] = str(row["fee"])
    if operation == "venue.vault_withdraw":
        rebate = sum((r.get("usd") or Decimal(0) for r in rows
                      if r["type"] == "vaultLeaderCommission" and r["hash"] == row["hash"]
                      and r.get("user") in (None, me)), Decimal(0))
        result.update({key: str(row[key]) for key in ("net", "basis", "commission")
                       if key in row})
        result["commission_rebate"] = str(rebate)
    return result

