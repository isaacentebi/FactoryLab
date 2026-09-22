"""The priced road not taken: a world measurement of a declined trade (ruling R2).

A decision that names the trade it declined, and executes nothing at the venue,
has an outcome the world writes: which way the trade it named moved over the
consequence horizon. It is "a fact about the world ...
priced ex ante on the named trade", so it is a legitimate realized-consequence
measurement (essay II.III.b: "a judgment of whether a given verdict predicted
real downstream outcomes"). It grades the verdicts on that decision. It never
replaces a verdict as the producer's own score (R2).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

#: The definition an opportunity price is recorded under.
OPPORTUNITY_DEFINITION = "opportunity-cost-v2"


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
                     scale_bps: Decimal | int | float,
                     declined: Mapping[str, str] | None) -> dict[str, Any] | None:
    """Price the road not taken: the trade the decision itself said it declined.

    ``opportunity-cost-v2`` (the architect's ruling on the #128 review). Guarantees
    ``y = 0.5 - 0.5 * tanh(gross_bps / scale_bps)``, where ``gross_bps`` is the named
    trade's gross return over the horizon, signed by its side and excluding fees:
    0.5 at no move, toward 1 as the named side moves against the trade (declining it
    was right), toward 0 as it moves for it. The function is symmetric and monotone,
    so without directional skill a hold earns 0.5 in expectation whatever trade it
    names, and naming a dull coin buys nothing (the fee-netted v1 paid about 1 for
    any trade that did not beat its fees). The benchmark is chosen ex ante, on the
    named trade, never in hindsight. Returns None when no trade is named or its
    prices are missing: a bare hold has no world outcome.
    """
    if declined is None:
        return None
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
    move = moves.get(declined["coin"])
    if move is None:
        return None
    gross = move if declined["side"] == "buy" else -move
    scale = float(scale_bps)
    return {"moves": [{"coin": c, "move_bps": str(m)} for c, m in moves.items()],
            "declined": dict(declined), "gross_bps": str(gross), "scale_bps": scale,
            "score": round(0.5 - 0.5 * math.tanh(float(gross) / scale), 6),
            "basis": "the named declined trade's gross move, marked to the horizon"}
