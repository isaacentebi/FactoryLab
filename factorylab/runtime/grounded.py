"""The priced road not taken: a world measurement of a declined trade (ruling R2).

A decision that names the trade it declined, and executes nothing at the venue,
has an outcome the world writes: whether the trade it named would have made money
over the consequence horizon, net of the venue's own round-trip fee and funding. It
is "a fact about the world ... priced ex ante on the named trade", so it is a
legitimate realized-consequence measurement (essay II.III.b: "a judgment of whether
a given verdict predicted real downstream outcomes"). It grades the verdicts on that
decision. It never replaces a verdict as the producer's own score (R2).

Naming it is part of the I/O contract of every return the world's first-tier
verdicts are about (the judged and exposure kinds) whenever that return executes no
venue operation (``counterfactual_refusal``). Essay II.III.b: the signal that
grades an evaluator must sit outside the loop it judges, and a return with no world
outcome leaves its judges graded by other models' readings alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

#: The definition a declined trade's price is recorded under (wave 16, D1).
OPPORTUNITY_DEFINITION = "declined-trade-net-v1"
#: The definition a refused answer order's own named trade is priced under.
ATTEMPTED_DEFINITION = "attempted-trade-net-v1"
#: Definitions earlier worlds priced these roads under, kept as names only so their
#: diaries stay readable: gross tanh scores on a manifest scale, no fee, no funding.
RETIRED_DEFINITIONS = ("opportunity-cost-v2", "attempted-trade-v1")


def latest_mids(runtime: Any) -> tuple[tuple[str, str], ...]:
    """The last broadcast mid of every coin, from the world's own tick record.

    Guarantees no venue read: the mids are the ones already delivered as
    ``MarketMid`` events, so freezing and pricing a contract cost no I/O and
    replay identically.
    """
    rows = getattr(runtime, "recent_mids", {}) or {}
    return tuple(sorted((str(coin), str(dq[-1]["mid"])) for coin, dq in rows.items() if dq))


def declined_trade(outputs: Mapping,
                   listed: Iterable[str] | None = None) -> dict[str, str] | None:
    """The trade a decision says it declined, as ``{coin, side}``, or None.

    Accepts ``counterfactual: {"coin": "BTC", "side": "buy"}`` or the action-label
    form ``"buy:BTC"``. Anything else names no trade: nothing is inferred. With
    ``listed`` (the coins the world lists), guarantees the coin is returned in the
    world's own spelling, matched without regard to case, and None for a coin the
    world does not list.
    """
    raw = outputs.get("counterfactual") if isinstance(outputs, Mapping) else None
    if isinstance(raw, str) and raw.count(":") >= 1:
        side, coin = raw.split(":")[:2]
        raw = {"side": side, "coin": coin}
    if not isinstance(raw, Mapping):
        return None
    side = str(raw.get("side", "")).strip().lower()
    coin = str(raw.get("coin", "")).strip().upper()
    if side not in ("buy", "sell") or not coin:
        return None
    if listed is not None:
        listed = tuple(listed)
        named = str(raw.get("coin", "")).strip()
        # The world's own spelling: an exact match first, else the one coin that
        # matches without regard to case (a venue may list mixed-case names).
        spelled = [named] if named in listed else [c for c in listed if c.upper() == coin]
        if len(spelled) != 1:
            return None
        coin = spelled[0]
    return {"coin": coin, "side": side}


#: Why a return's ``counterfactual`` does not satisfy its contract (``counterfactual_refusal``).
COUNTERFACTUAL_ABSENT = "counterfactual {coin, side} is absent from a return that executed no " \
    "venue operation"
COUNTERFACTUAL_SHAPE = "counterfactual is not {coin, side} with side buy or sell"
COUNTERFACTUAL_UNLISTED = "counterfactual names a coin the world does not list"


def counterfactual_refusal(outputs: Mapping, listed: Iterable[str]) -> str | None:
    """Why a return that executed no venue operation fails its contract, or None.

    Essay II.III.b: an evaluator is graded by realized consequence, which includes
    the priced road not taken, benchmarked ex ante; ``opportunity_cost`` prices it
    from the trade the return names. Guarantees None exactly when the return names a
    side and a coin in ``listed`` (the coins the world lists when the return is made,
    ``latest_mids``), or when the field is absent and the world lists no coin at all,
    where no trade can be named. The field is the published object form alone. A
    named coin the world does not list is refused whether or not any is listed. The
    reason is a fact about the return, never advice.
    """
    listed = tuple(listed)
    raw = outputs.get("counterfactual") if isinstance(outputs, Mapping) else None
    if raw is None:
        return COUNTERFACTUAL_ABSENT if listed else None
    if not isinstance(raw, Mapping) or declined_trade({"counterfactual": raw}) is None:
        return COUNTERFACTUAL_SHAPE
    if declined_trade({"counterfactual": raw}, listed) is None:
        return COUNTERFACTUAL_UNLISTED
    return None


def _net(open_mids: Iterable[tuple[str, str]], due_mids: Iterable[tuple[str, str]],
         entry_rate: Decimal | str | None, exit_rate: Decimal | str | None,
         named: Mapping[str, str] | None,
         funding_rates: Iterable[Decimal | str]) -> dict[str, Any] | None:
    """The named trade's move over the horizon, net of the venue's round trip and funding.

    Guarantees ``net_bps = s * (due - open) / open * 10^4 - (entry_rate + exit_rate) *
    10^4 - s * sum(funding_rates) * 10^4`` in exact decimals, ``s`` = +1 for a buy and
    -1 for a sell (longs pay a positive funding rate), or None when no trade is named,
    a price is missing or unusable, or either leg's taker rate was never read (an
    unread rate is never a number). Each leg pays its own rate (wave 16, D7: the road
    not taken pays the round trip an acting lot opened and marked at the same instants
    pays).
    """
    if named is None or entry_rate is None or exit_rate is None:
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
            moves[coin] = (after - before) / before * Decimal(10_000)
    move = moves.get(named["coin"])
    try:
        legs = (Decimal(str(entry_rate)), Decimal(str(exit_rate)))
        rates = [Decimal(str(r)) for r in funding_rates]
    except (InvalidOperation, ValueError):
        return None
    if (move is None or not all(leg.is_finite() and leg >= 0 for leg in legs)
            or not all(r.is_finite() for r in rates)):
        return None
    sign = 1 if named["side"] == "buy" else -1
    gross = sign * move
    entry_fee, exit_fee = (leg * Decimal(10_000) for leg in legs)
    fee = entry_fee + exit_fee
    funding = -sign * sum(rates, Decimal(0)) * Decimal(10_000)
    net = gross - fee + funding
    return {"moves": [{"coin": c, "move_bps": str(m.quantize(Decimal("0.01")))}
                      for c, m in moves.items()],
            "gross_bps": str(gross.quantize(Decimal("0.01"))),
            "round_trip_fee_bps": str(fee.normalize()),
            "entry_fee_bps": str(entry_fee.normalize()),
            "exit_fee_bps": str(exit_fee.normalize()),
            "funding_bps": str(funding.quantize(Decimal("0.0001"))),
            "funding_payments": len(rates),
            "net_bps": str(net.quantize(Decimal("0.0001"))),
            "_net": net}


def advance_funding(state: dict, ts_ns: int, rate: str) -> None:
    """Assign the rate in force at every funding time a venue rate print has passed.

    ``state`` is ``{"interval", "cursor", "rate", "rates"}``: the venue's funding
    interval in nanoseconds, the time up to which funding times are assigned, the rate
    of the latest print at or before it (None before any), and the ``[time, rate]``
    pairs assigned so far. Guarantees every funding time ``t`` (a multiple of the
    interval) in ``(cursor, ts_ns]`` is assigned the rate of the latest print at or
    before ``t``: this print's own rate when ``t == ts_ns``, the previous one's
    otherwise (None when the world had read none: an unread rate is never a number).
    """
    interval = int(state["interval"])
    tau = (int(state["cursor"]) // interval + 1) * interval
    while tau <= ts_ns:
        state["rates"].append([tau, str(rate) if tau == ts_ns else state["rate"]])
        tau += interval
    if ts_ns >= int(state["cursor"]):
        state["cursor"] = int(ts_ns)
        state["rate"] = str(rate)


#: A named trade's funding times not yet all assigned a rate (``funding_due``).
FUNDING_PENDING = "funding-pending"


def funding_due(state: dict | None, open_ns: int, due_ns: int) -> list[str] | str | None:
    """The rates the named side would have paid at the venue's funding times in
    ``(open_ns, due_ns]``, in order.

    Guarantees ``[]`` for a coin with no funding (``state`` None: a spot pair) or no
    funding time in the window, ``FUNDING_PENDING`` while a funding time in the window
    has not yet been passed by a rate print, and None when one was passed with no
    rate read before it (an unread rate is never a number).
    """
    if state is None:
        return []
    interval = int(state["interval"])
    last = (due_ns // interval) * interval
    if last > open_ns and last > int(state["cursor"]):
        return FUNDING_PENDING
    rates = [rate for tau, rate in state["rates"] if open_ns < tau <= due_ns]
    return None if any(rate is None for rate in rates) else rates


def opportunity_cost(open_mids: Iterable[tuple[str, str]],
                     due_mids: Iterable[tuple[str, str]],
                     entry_rate: Decimal | str | None,
                     exit_rate: Decimal | str | None,
                     declined: Mapping[str, str] | None,
                     funding_rates: Iterable[Decimal | str] = ()) -> dict[str, Any] | None:
    """Price the road not taken: the trade the decision itself said it declined.

    ``declined-trade-net-v1`` (wave 16, D1; ruling R2: "what the declined trade did,
    net of fees, priced ex ante on the named trade"). Guarantees ``y = 1`` when the
    named trade would not have beaten the venue's round trip over the horizon
    (``net_bps <= 0``, declining was right in money) and ``y = 0`` otherwise, with
    ``net_bps`` from ``_net``: the gross move signed by the named side, less the
    venue's taker rate on each leg (``entry_rate`` in force at the decision, the ex-ante
    element; ``exit_rate`` in force at the horizon), less the funding the named side
    would have paid at the venue's funding times inside the horizon. Every term is a
    money fact the venue states; no scale is an architect's. Returns None when no trade
    is named, a price is missing or either leg's taker rate is unread: a bare hold has
    no world outcome.
    """
    priced = _net(open_mids, due_mids, entry_rate, exit_rate, declined, funding_rates)
    if priced is None:
        return None
    net = priced.pop("_net")
    return {**priced, "declined": dict(declined), "score": 1.0 if net <= 0 else 0.0,
            "basis": "the named declined trade's move over the horizon, net of the venue's "
                     "round-trip taker fee and funding"}


def attempted_trade(outputs: Mapping, listed: Iterable[str]) -> dict[str, str] | None:
    """The trade an answer order named, as ``{coin, side}`` in the world's spelling, or None.

    Guarantees a trade only for an answer ``{"action": "order", "coin", "side", ...}``
    whose side is buy or sell and whose coin the world lists (``listed``, the coins
    of ``latest_mids``); the caller says whether the answer's kind owns the answer
    order. Nothing is inferred: the coin and side are the seat's own.
    """
    if not isinstance(outputs, Mapping) or outputs.get("action") != "order":
        return None
    return declined_trade({"counterfactual": {"coin": outputs.get("coin"),
                                              "side": outputs.get("side")}}, listed)


def attempted_cost(open_mids: Iterable[tuple[str, str]],
                   due_mids: Iterable[tuple[str, str]],
                   entry_rate: Decimal | str | None,
                   exit_rate: Decimal | str | None,
                   attempted: Mapping[str, str] | None,
                   funding_rates: Iterable[Decimal | str] = ()) -> dict[str, Any] | None:
    """Price the road a refused order tried to take: the trade it named, for its side.

    ``attempted-trade-net-v1`` (wave 16, D1). An answer order names its trade ex ante;
    when nothing the decision wrote executed, the world measures that trade over the
    same horizon, from the same frozen mids and the same money terms as
    ``opportunity_cost``. Guarantees the complement of the declined form: ``y = 1``
    when the attempted trade would have beaten the venue's round trip
    (``net_bps > 0``) and ``y = 0`` otherwise. Returns None when no trade is named, a
    price is missing or either leg's taker rate is unread.
    """
    priced = _net(open_mids, due_mids, entry_rate, exit_rate, attempted, funding_rates)
    if priced is None:
        return None
    net = priced.pop("_net")
    return {**priced, "attempted": dict(attempted), "score": 1.0 if net > 0 else 0.0,
            "basis": "the refused order's named trade's move over the horizon, net of the "
                     "venue's round-trip taker fee and funding"}
