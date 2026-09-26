"""A consequence outcome is a function of world facts, never of how they were batched.

Codex on #152 (eaf23e0) found two outcomes that depended on how the venue's events
were split into batches, rather than on the world: a first quote after a named trade's
lapse opened it, and a fill after H, processed before the outcome was fixed, entered
it. The invariant, tested once for the whole class: a consequence outcome (a named
trade, an acting return) is a function only of the world facts with fact-time at or
before H (fills, funding, fee reads), the first mark with fact-time at or after H, and
the original lapse deadline. It is independent of how the facts are split into
batches, of when outcomes are resolved, and of the order of facts within one instant.

The property test feeds one fixed sequence of world facts under many partitions into
batches, many resolve schedules and shuffles of every same-instant group, with the
processing clock at each batch's end (the worst case a venue batch gives), and asserts
every variant produces byte-identical outcomes, consequence rows and late money. The
named-trade side runs the runtime's own freeze, observation, lapse and pricing methods
on a stand-in carrying only their state, so 200 variants fit in the check tier.

Assumed of the venue, as every venue in this repository delivers: facts arrive in
fact-time order across batches (a batch holds the facts through its instant), and two
fills of one instant keep the venue's own order (their order is itself a fact); a
funding payment is stated at its funding time (``funding_ns``).
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.feedback import FeedbackMixin
from factorylab.runtime.venue import VenueMixin
from factorylab.settlement.consequence import ReturnConsequences

S = 10**9
T0 = 3 * 3600 * S - 60 * S  # an hour boundary falls 60 s in
H = 90 * S
PATIENCE = H + 40 * S
VARIANTS = 200


def _t(seconds: float) -> int:
    return T0 + int(seconds * S)


class _Named(FeedbackMixin, VenueMixin):
    """The runtime's named-trade state, and nothing else: its own methods run on it."""

    def __init__(self, history: dict) -> None:
        self.reference_mids: dict = {}
        self.venue_marks: dict = {}
        self.funding_prints: dict = {}
        self.facts_seen_ns = self.tick_through_ns = None
        self.clock = SimpleNamespace(now_ns=T0)
        self.fee_schedule = {"rates": {}, "read_ns": T0, "history": history}
        self.exchange = SimpleNamespace(funding_interval_ns=3600 * S)
        self.m = SimpleNamespace(timing=SimpleNamespace(world_repricing_ns=270 * S))
        self.ticks_consumed = 0

    def _horizon_ns(self) -> int:
        return H

    def _patience_ns(self) -> int:
        return PATIENCE


class _Book(ReturnConsequences):
    """The consequence book on the venue's clock, as the runtime's is."""

    def __init__(self, history: dict) -> None:
        super().__init__(Ledger(), 1, horizon_ns=H)
        self.clock_ns = T0
        self.history = history

    def _now_ns(self) -> int:
        return self.clock_ns

    def _patience_ns(self) -> int:
        return PATIENCE

    def _exit_rates(self):
        def rate_at(instrument: str, at_ns: int) -> str | None:
            before = [rate for ns, rate in self.history.get(instrument, []) if ns <= at_ns]
            return before[-1] if before else None
        return rate_at


def _facts() -> list[tuple]:
    """One fixed sequence of world facts and decisions, in fact-time order."""
    facts: list[tuple] = []
    for coin, rate in (("BTC", "0.00045"), ("ETH", "0.0004"), ("SOL", "0.0005"),
                       ("DOGE", "0.0005")):
        facts.append((_t(0), "fee", coin, rate))
    facts.append((_t(50), "fee", "BTC", "0.00035"))  # BTC's rate falls before H
    for coin in ("BTC", "SOL"):
        facts.append((_t(-10), "rate", coin, "0.0001"))
        facts.append((_t(60), "rate", coin, "0.0003"))  # the hour boundary's print
    for step in range(0, 28):
        at = _t(10 * step)
        facts.append((at, "tick"))
        facts.append((at, "mid", "BTC", str(100 + (step % 7) * 0.25)))
        if step <= 10:
            facts.append((at, "mid", "ETH", str(2000 + step)))  # ETH stops publishing
        if step >= 2:
            facts.append((at, "mid", "SOL", str(20 + step * 0.1)))  # SOL starts at 20 s
        if step >= 15:
            # DOGE's first quote at 150 s, just after its decision's lapse (13 + 130 s).
            facts.append((at, "mid", "DOGE", str(0.1 + step * 0.001)))
    facts.append((_t(105), "mid", "BTC", "101.5"))  # A2's mark, at its very horizon
    # Named trades: BTC opens at its 10 s mark; DOGE's first quote comes after its lapse;
    # SOL opens at its first quote, after the decision (R10-h).
    facts.append((_t(12), "decide", "N-BTC", "BTC", "buy"))
    facts.append((_t(13), "decide", "N-DOGE", "DOGE", "sell"))
    facts.append((_t(14), "decide", "N-SOL", "SOL", "buy"))
    # Acting returns.
    facts.append((_t(12.5), "open", "A1", "o-A1", "filled"))
    facts.append((_t(12.5), "fill", "o-A1", "BTC", True, "0.001", "100.0"))
    facts.append((_t(15), "open", "A2", "o-A2", "resting"))
    facts.append((_t(105), "fill", "o-A2", "BTC", True, "0.001", "101.0"))  # at its H
    facts.append((_t(16), "open", "A3", "o-A3", "resting"))
    facts.append((_t(120), "fill", "o-A3", "BTC", True, "0.001", "101.2"))  # after its H
    facts.append((_t(17), "open", "A4", "o-A4", "filled"))
    facts.append((_t(17), "fill", "o-A4", "ETH", True, "0.01", "2001"))  # ETH goes quiet
    facts.append((_t(18), "open", "A5", "o-A5", "filled"))  # A1's closer, after A1's H
    facts.append((_t(250), "fill", "o-A5", "BTC", False, "0.001", "102.0"))
    facts.append((_t(60), "funding", "BTC", "0.00003"))  # before every H
    facts.append((_t(115), "funding", "BTC", "0.00005"))  # after A1's and A2's H
    facts.sort(key=lambda fact: (fact[0], fact[1] != "decide" and fact[1] != "open"))
    return facts


def _groups(facts: list[tuple], rng: random.Random) -> list[tuple]:
    """``facts`` with every same-instant group shuffled, except the venue's own order of
    two fills of one instant, and decisions kept before the facts of their instant."""
    out, i = [], 0
    while i < len(facts):
        j = i
        while j < len(facts) and facts[j][0] == facts[i][0]:
            j += 1
        group = facts[i:j]
        actions = [f for f in group if f[1] in ("decide", "open")]
        world = [f for f in group if f[1] not in ("decide", "open")]
        fills = [f for f in world if f[1] == "fill"]
        rng.shuffle(world)
        order = iter(fills)
        world = [next(order) if f[1] == "fill" else f for f in world]
        out.extend(actions + world)
        i = j
    return out


def _run(rng: random.Random) -> dict:
    history: dict = {}
    named, book = _Named(history), _Book(history)
    sequence = _groups(_facts(), rng)
    cuts = sorted(rng.sample(range(1, len(sequence)), rng.randint(3, 40)))
    batches = [sequence[a:b] for a, b in zip([0, *cuts], [*cuts, len(sequence)],
                                             strict=True)]
    outcomes: dict = {}
    event = 0
    last_tick = None

    def settle() -> None:
        nonlocal event
        event += 1
        book.resolve(event)
        book.settle_late(event)
        for handle in list(named.reference_mids):
            if handle in outcomes:
                continue
            frozen = named.reference_mids[handle]
            state, rates = named._reference_outcome(frozen)
            if state == "open":
                continue
            if state == "none":
                outcomes[handle] = ["none"]
            elif None in named._fee_legs(frozen):
                outcomes[handle] = ["uninformative", "fee_unknown"]
            else:
                priced, definition = named._price_declined(
                    handle, frozen, ((frozen["coin"], frozen["res"][1]),), rates)
                outcomes[handle] = ["measured", definition, priced]

    for batch in batches:
        batch_end = max(fact[0] for fact in batch)
        for fact in batch:
            at, kind = fact[0], fact[1]
            named.clock.now_ns = book.clock_ns = batch_end
            if kind == "tick":
                book.tick_through_ns = named.tick_through_ns = last_tick
                last_tick = at
            elif kind == "fee":
                history.setdefault(fact[2], []).append([at, fact[3]])
                named.fee_schedule["rates"][fact[2]] = fact[3]
            elif kind == "rate":
                named._observe_funding(fact[2], at, fact[3])
            elif kind == "mid":
                named._observe_mid(fact[2], at, fact[3])
                book.observe("MarketMid", {"coin": fact[2], "mid": fact[3], "ts_ns": at},
                             event)
            elif kind == "funding":
                book.observe("Funding", {"coin": fact[2], "paid_usd": fact[3],
                                         "rate": "0", "ts_ns": at}, event)
            elif kind == "fill":
                book.observe("Fill", {"order_id": fact[2], "coin": fact[3], "is_buy": fact[4],
                                      "size": fact[5], "px": fact[6], "fee_usd": "0",
                                      "ts_ns": at}, event)
            elif kind == "decide":
                # A decision is the runtime's own act at its instant.
                named.clock.now_ns = at
                mids = tuple((coin, mark[1]) for coin, mark in named.venue_marks.items())
                if fact[3] not in named.venue_marks:
                    mids += ((fact[3], "0"),)
                named._freeze_named(fact[2], {"coin": fact[3], "side": fact[4]}, mids,
                                    declined={"coin": fact[3], "side": fact[4]},
                                    attempted=None)
            elif kind == "open":
                book.clock_ns = at
                book.start(fact[2], event)
                size = "0.01" if fact[2] == "A4" else "0.001"
                book.order_result(fact[2], {"status": fact[4], "order_id": fact[3],
                                            "filled_size": size if fact[4] == "filled"
                                            else "0"}, {"size": size}, event)
                book.finish(fact[2], 0)
            if rng.random() < 0.3:
                settle()
        if rng.random() < 0.5:
            settle()
    # The world goes on: a tick long after every patience, and every outcome is fixed.
    book.tick_through_ns = named.tick_through_ns = _t(10_000)
    settle()
    rows = [dict(row) for row in book.ledger._recovery_items()
            if row.get("kind") in ("consequence.outcome", "consequence.uninformative")]
    for row in rows:
        # The ledger's own chain and the event a row was written at are when this
        # ran, not what the world did.
        for key in ("at_event", "hash", "prev_hash", "seq", "ts"):
            row.pop(key, None)
    late: dict[str, int] = {}
    for row in book.ledger._recovery_items():
        if row.get("kind") == "consequence.late":
            late[row["handle"]] = late.get(row["handle"], 0) + row["micro"]
    return {"named": outcomes,
            "rows": sorted(rows, key=lambda r: (r.get("handle"), r["kind"])),
            "late": late}


def test_every_batching_of_the_same_world_facts_gives_the_same_outcomes():
    rng = random.Random(152)
    reference = None
    for variant in range(VARIANTS):
        result = json.dumps(_run(random.Random(rng.random())), sort_keys=True, default=str)
        if reference is None:
            reference = result
        assert result == reference, f"variant {variant} differs"
    outcome = json.loads(reference)
    named = outcome["named"]
    assert named["N-DOGE"] == ["none"]  # its first quote came after its lapse
    assert named["N-BTC"][0] == named["N-SOL"][0] == "measured"
    btc = named["N-BTC"][2]
    assert (btc["entry_fee_bps"], btc["exit_fee_bps"]) == ("4.5", "3.5")
    assert btc["funding_payments"] == 1  # the hour boundary at 60 s, inside its window
    fixed = {row["handle"]: row for row in outcome["rows"]
             if row["kind"] == "consequence.outcome"}
    assert fixed["A2"]["marked"] is True  # filled at its H, marked at MarketMid(H)
    assert fixed["A3"]["marked"] is False and fixed["A3"]["net_micro"] == 0  # late money
    assert fixed["A4"]["censored"] == "no_mark"
    assert outcome["late"].get("A1")  # its lot closed after its outcome was fixed
