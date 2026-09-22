"""The priced road not taken: a world measurement of a declined trade (ruling R2).

A decision that names the trade it declined, and executes nothing at the venue,
has an outcome the world writes: what that trade would have netted over the
consequence horizon after a round trip's fees. It is "a fact about the world ...
priced ex ante on the named trade", so it is a legitimate realized-consequence
measurement (essay II.III.b: "a judgment of whether a given verdict predicted
real downstream outcomes"). It grades the verdicts on that decision. It never
replaces a verdict as the producer's own score (R2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

#: The definition an opportunity price is recorded under.
OPPORTUNITY_DEFINITION = "opportunity-cost-v1"
#: A venue whose fee is not published is priced at Hyperliquid's base taker tier.
DEFAULT_TAKER_FEE_BPS = Decimal("4.5")


def latest_mids(runtime: Any) -> tuple[tuple[str, str], ...]:
    """The last broadcast mid of every coin, from the world's own tick record.

    Guarantees no venue read: the mids are the ones already delivered as
    ``MarketMid`` events, so freezing and pricing a contract cost no I/O and
    replay identically.
    """
    rows = getattr(runtime, "recent_mids", {}) or {}
    return tuple(sorted((str(coin), str(dq[-1]["mid"])) for coin, dq in rows.items() if dq))


def declined_trade(outputs: Mapping) -> dict[str, str] | None:
    """The trade a decision says it declined, as ``{coin, side}``, or None.

    Accepts ``counterfactual: {"coin": "BTC", "side": "buy"}`` or the action-label
    form ``"buy:BTC"``. Anything else names no trade: nothing is inferred.
    """
    raw = outputs.get("counterfactual") if isinstance(outputs, Mapping) else None
    if isinstance(raw, str) and raw.count(":") >= 1:
        side, coin = raw.split(":")[:2]
        raw = {"side": side, "coin": coin}
    if not isinstance(raw, Mapping):
        return None
    side = str(raw.get("side", "")).strip().lower()
    coin = str(raw.get("coin", "")).strip().upper()
    return {"coin": coin, "side": side} if side in ("buy", "sell") and coin else None


def opportunity_cost(open_mids: Iterable[tuple[str, str]],
                     due_mids: Iterable[tuple[str, str]],
                     round_trip_bps: Decimal,
                     declined: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Price the road not taken: the trade the decision itself said it declined.

    Guarantees the benchmark is chosen ex ante, never in hindsight: the best move
    after the fact is a trade nobody could have known to take, and scoring a hold
    against it would teach a population to trade noise. With a named declined
    trade (a directional forecast), ``regret`` is what that trade would have
    netted over the horizon after a round trip's fees, and the score is
    ``cost / (cost + regret)`` in (0, 1]: 1 when declining it was right (it would
    have lost or not paid its fees), falling as the profit passed up grows. With
    none named, the decision's consequence is exactly zero and it settles at the
    neutral 0.5; trades must beat that. Returns None when the prices are missing.
    """
    opened = dict(open_mids)
    due = dict(due_mids)
    moves = {}
    for coin in sorted(set(opened) & set(due)):
        try:
            before, after = Decimal(opened[coin]), Decimal(due[coin])
        except (InvalidOperation, ValueError):
            continue
        if before > 0 and after > 0:
            moves[coin] = ((after - before) / before * Decimal(10_000)).quantize(
                Decimal("0.01"))
    if not moves:
        return None
    cost = max(Decimal(round_trip_bps), Decimal(1))
    rows = [{"coin": c, "move_bps": str(m)} for c, m in moves.items()]
    if declined is None:
        return {"moves": rows, "declined": None, "round_trip_fee_bps": str(cost),
                "regret_bps": None, "score": 0.5,
                "basis": "no declined trade named: a decision with zero consequence"}
    move = moves.get(declined["coin"])
    if move is None:
        return None
    gross = move if declined["side"] == "buy" else -move
    regret = max(Decimal(0), gross - cost)
    return {"moves": rows, "declined": dict(declined), "round_trip_fee_bps": str(cost),
            "declined_net_bps": str((gross - cost).quantize(Decimal("0.01"))),
            "regret_bps": str(regret.quantize(Decimal("0.01"))),
            "score": round(float(cost / (cost + regret)), 4),
            "basis": "the named declined trade, marked to the horizon net of a round trip"}
