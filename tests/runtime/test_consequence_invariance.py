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
on a stand-in carrying only their state, so 400 variants fit in the check tier.

Assumed of the venue, as every venue in this repository delivers: facts arrive in
fact-time order across batches (a batch holds the facts through its instant), and two
fills of one instant keep the venue's own order (their order is itself a fact); a
funding payment is stated at its funding time (``funding_ns``). A third of the variants
read a polled venue whose fills and funding arrive only at polls that lag the ticks, with
the venue's delivered-through watermark at each poll (ruling R10-o); the others read an
advancing venue, each batch one advance to any instant (a tick or a safety pass) whose
watermark covers only what it delivered to accounting, and a third end at the tape's
close with the terminal settlement right after the final advance.
"""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

from factorylab.runtime.feedback import FeedbackMixin
from factorylab.runtime.venue import VenueMixin
from factorylab.settlement.consequence import ReturnConsequences

S = 10**9
T0 = 3 * 3600 * S - 60 * S  # an hour boundary falls 60 s in
H = 90 * S
PATIENCE = H + 40 * S
VARIANTS = 400


def _t(seconds: float) -> int:
    return T0 + int(seconds * S)


class _Named(FeedbackMixin, VenueMixin):
    """The runtime's named-trade state, and nothing else: its own methods run on it."""

    def __init__(self, history: dict) -> None:
        self.reference_mids: dict = {}
        self.venue_marks: dict = {}
        self.funding_prints: dict = {}
        self.facts_seen_ns = self.tick_through_ns = self.advance_through_ns = None
        self.clock = SimpleNamespace(now_ns=T0)
        self.fee_schedule = {"rates": {}, "read_ns": T0, "history": history}
        self.exchange = SimpleNamespace(funding_interval_ns=3600 * S)
        self.m = SimpleNamespace(timing=SimpleNamespace(world_repricing_ns=270 * S))
        self.ticks_consumed = 0
        # The funding-rate stream's delivered-through instant: its latest successful
        # read (a failed read reads nothing).
        self.rates_ns: int | None = None

    def _stream_watermark(self, stream: str):
        if stream == "hl:rates":
            return float("-inf") if self.rates_ns is None else self.rates_ns - 1
        return super()._stream_watermark(stream)

    def _horizon_ns(self) -> int:
        return H

    def _patience_ns(self) -> int:
        return PATIENCE


class _Rows:
    """The ledger the book writes to, as plain rows: the ledger itself is not under test
    here, and its encryption would cost the check tier's time budget."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> None:
        self.rows.append(dict(row))

    def _recovery_items(self) -> list[dict]:
        return list(self.rows)


class _Book(ReturnConsequences):
    """The consequence book on the venue's clock, as the runtime's is."""

    def __init__(self, history: dict) -> None:
        super().__init__(_Rows(), 1, horizon_ns=H)
        self.clock_ns = T0
        self.fee_history = history
        # Each polled stream's delivered-through instant (ruling R10-o): Hyperliquid's
        # fills and funding, Polymarket's events, each polled on its own; and an
        # advancing venue's: the time it was last advanced to, every stream alike.
        self.hl_ns: int | None = None
        self.pm_ns: int | None = None
        # Hyperliquid's funding poll on its own, when a test stops it (else as fills).
        self.funding_ns: int | None = None
        self.advance_ns: int | None = None
        # Polymarket: each token's book, read at its instant (a failed read reads
        # nothing), and its events feed (fills and resolutions).
        self.books: dict[str, int] = {}

    def _now_ns(self) -> int:
        return self.clock_ns

    def _stream_watermark(self, stream: str) -> int | float | None:
        if stream.startswith("pm:book:"):
            read = self.books.get(stream.removeprefix("pm:book:"))
            return float("-inf") if read is None else read - 1
        if stream == "hl:funding" and self.funding_ns is not None:
            return self.funding_ns - 1
        polled = {"pm:events": self.pm_ns, "hl:fills": self.hl_ns,
                  "hl:funding": self.hl_ns}
        if stream not in polled:
            return None  # mids: stamped when read, covered by the facts seen
        if polled[stream] is not None:
            return polled[stream] - 1  # a poll may miss a fact of its very instant
        return self.advance_ns

    def _patience_ns(self) -> int:
        return PATIENCE

    def _exit_rates(self):
        def rate_at(instrument: str, at_ns: int) -> str | None:
            before = [rate for ns, rate in self.fee_history.get(instrument, [])
                      if ns <= at_ns]
            return before[-1] if before else None
        return rate_at


def _facts() -> list[tuple]:
    """One fixed sequence of world facts and decisions, in fact-time order."""
    facts: list[tuple] = []
    for coin, rate in (("BTC", "0.00045"), ("ETH", "0.0004"), ("SOL", "0.0005"),
                       ("DOGE", "0.0005"), ("PURR/USDC", "0.0007")):
        facts.append((_t(0), "fee", coin, rate))
    # The funding-rate reads fail from 200 s on, for good: a perp trade priced with them
    # whose horizon falls after would wait forever; the spot trades, which pay no
    # funding, never wait on them (N-PURR2's horizon is 240 s).
    for step in range(0, 28):
        facts.append((_t(10 * step), "rateread", step < 20))
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
        facts.append((at, "mid", "PURR/USDC", str(0.2 + step * 0.002)))
        if step >= 15:
            # DOGE's first quote at 150 s, just after its decision's lapse (13 + 130 s).
            facts.append((at, "mid", "DOGE", str(0.1 + step * 0.001)))
    facts.append((_t(105), "mid", "BTC", "101.5"))  # A2's mark, at its very horizon
    # Named trades: BTC opens at its 10 s mark; DOGE's first quote comes after its lapse;
    # SOL opens at its first quote, after the decision (R10-h).
    facts.append((_t(12), "decide", "N-BTC", "BTC", "buy"))
    facts.append((_t(13), "decide", "N-DOGE", "DOGE", "sell"))
    facts.append((_t(14), "decide", "N-SOL", "SOL", "buy"))
    facts.append((_t(16), "decide", "N-PURR", "PURR/USDC", "sell"))
    facts.append((_t(152), "decide", "N-PURR2", "PURR/USDC", "buy"))
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
    # Polymarket. PM:A's book fails to read from 100 s to 170 s, across P1's H (110 s)
    # and its patience (150 s): P1 waits, never no_mark, and is marked by the first
    # read after, at 180 s. PM:B resolves at 114 s, after P2's H (112 s) and before any
    # other mutation after it: P2 is graded on what it held at H, marked by the
    # resolution (the first price after H), and the redemption is late money. P3's fill
    # at 35 s is held while another order's ownership is pending (30 s to 60 s).
    for step in range(0, 28):
        at = _t(10 * step)
        failed = 10 <= step <= 17
        facts.append((at, "pmbook", "PM:A", None if failed else str(0.40 + step * 0.005)))
        if step < 20:
            facts.append((at, "pmbook", "PM:B", str(0.60 + step * 0.004)))
    facts.append((_t(20), "open", "P1", "o-P1", "filled"))
    facts.append((_t(20), "fill", "o-P1", "PM:A", True, "10", "0.40", "event"))
    facts.append((_t(22), "open", "P2", "o-P2", "filled"))
    facts.append((_t(22), "fill", "o-P2", "PM:B", True, "10", "0.60", "event"))
    facts.append((_t(25), "open", "P3", "o-P3", "resting"))
    facts.append((_t(30), "intent", "c-held", "P3", "PM:B"))
    facts.append((_t(35), "fill", "o-P3", "PM:B", True, "10", "0.61", "event"))
    facts.append((_t(60), "ack", "c-held"))
    facts.append((_t(114), "pmresolve", "PM:B", "1"))
    # PM:C's book is read on time but is empty from 30 s on: read, it states no price, so
    # P4 reaches its patience (154 s) and is no_mark, never held forever.
    for step in range(0, 28):
        facts.append((_t(10 * step), "pmbook", "PM:C", "0.5" if step < 3 else ""))
    facts.append((_t(24), "open", "P4", "o-P4", "filled"))
    facts.append((_t(24), "fill", "o-P4", "PM:C", True, "10", "0.50", "event"))
    # PM:D's book is read until 60 s, then never again (Codex on #152): P5 bought 10 of
    # PM:D and 10 of PM:A at 26 s, and P6's sell closed its PM:D lot at 40 s, before P5's
    # H (116 s). P5 holds PM:A at H, so it waits on PM:A's book through H; it needs
    # PM:D's streams only through 40 s, so the dead book never holds it.
    for step in range(0, 28):
        facts.append((_t(10 * step), "pmbook", "PM:D",
                      str(0.50 + step * 0.01) if step <= 6 else None))
    facts.append((_t(26), "openpair", "P5", "o-P5", "o-P5a"))
    facts.append((_t(26), "fill", "o-P5", "PM:D", True, "10", "0.52", "event"))
    facts.append((_t(26), "fill", "o-P5a", "PM:A", True, "10", "0.53", "event"))
    facts.append((_t(40), "open", "P6", "o-P6", "filled"))
    facts.append((_t(40), "fill", "o-P6", "PM:D", False, "10", "0.55", "event"))
    facts.sort(key=lambda fact: (fact[0], fact[1] not in ("decide", "open", "openpair")))
    return facts


def _pay(book, fact: tuple, event: int) -> None:
    book.observe("Funding", {"coin": fact[2], "paid_usd": fact[3], "rate": "0",
                             "ts_ns": fact[0]}, event)


def _fill(book, fact: tuple, event: int) -> None:
    market = fact[7] if len(fact) > 7 else "perp"
    book.observe("Fill", {"order_id": fact[2], "coin": fact[3], "is_buy": fact[4],
                          "size": fact[5], "px": fact[6], "fee_usd": "0", "ts_ns": fact[0],
                          "market": market, "inventory_size": fact[5]}, event)


def _resolve(book, fact: tuple, event: int) -> None:
    book.redeem(fact[2], fact[3], event, {"token_id": fact[2]}, at_ns=fact[0])


def _deliver(book, fact: tuple, event: int) -> None:
    {"fill": _fill, "funding": _pay, "pmresolve": _resolve}[fact[1]](book, fact, event)


def _groups(facts: list[tuple], rng: random.Random) -> list[tuple]:
    """``facts`` with every same-instant group shuffled, except the venue's own order of
    two fills of one instant, and decisions kept before the facts of their instant."""
    out, i = [], 0
    while i < len(facts):
        j = i
        while j < len(facts) and facts[j][0] == facts[i][0]:
            j += 1
        group = facts[i:j]
        actions = [f for f in group if f[1] in ("decide", "open", "openpair")]
        world = [f for f in group if f[1] not in ("decide", "open", "openpair")]
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
    # A third of the variants read a polled venue (ruling R10-o): fills and funding
    # payments are reported only at polls at random instants, which lag the ticks; each
    # poll delivers what executed by then and raises the venue's watermark to its time.
    # The others read an advancing venue (fake, tape): each batch is one advance, to an
    # instant anywhere (a tick, or a safety pass while a model thinks), which delivers
    # every fact through it to accounting before its watermark rises. In a third, the
    # tape ends with the last fact: its final advance is followed at once by the
    # terminal settlement, complete through the tape's close, and nothing after it.
    mode = rng.choice(("advance", "lag", "tape_end"))
    # Resolve schedules: frequent, or sparse (outcomes fixed long after their facts).
    per_fact, per_batch = rng.choice(((0.3, 0.5), (0.0, 0.1)))
    lag = mode == "lag"
    facts = _facts()
    if lag:
        # Hyperliquid's fills and funding, and Polymarket's events, are each polled on
        # their own schedule. In half, Hyperliquid's fills read is unavailable from 80 s
        # to 220 s, across the horizons, while Polymarket's events (a resolution after
        # H) keep arriving: its pre-H fills are delivered after the resolution.
        outage = rng.random() < 0.5
        for kind in ("poll", "pmpoll"):
            at = T0
            while at < _t(330):
                at += rng.randint(5, 70) * S
                if not (kind == "poll" and outage and _t(80) < at < _t(220)):
                    facts.append((at, kind))
        facts.sort(key=lambda fact: (fact[0], fact[1] not in ("decide", "open", "openpair")))
        book.hl_ns = book.pm_ns = T0
    polled: list[tuple] = []
    pm_polled: list[tuple] = []
    sequence = _groups(facts, rng)
    # An advance delivers every fact through its instant, so it never splits an instant.
    boundaries = [i for i in range(1, len(sequence))
                  if lag or sequence[i][0] != sequence[i - 1][0]]
    cuts = sorted(rng.sample(boundaries, rng.randint(3, min(40, len(boundaries)))))
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

    for number, batch in enumerate(batches):
        batch_end = max(fact[0] for fact in batch)
        final_advance = mode == "tape_end" and number == len(batches) - 1
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
            elif kind == "rateread":
                if fact[2]:  # a failed read reads nothing
                    named.rates_ns = at
            elif kind == "mid":
                named._observe_mid(fact[2], at, fact[3])
                book.observe("MarketMid", {"coin": fact[2], "mid": fact[3], "ts_ns": at},
                             event)
            elif kind in ("funding", "fill", "pmresolve") and lag:
                # Executed now, reported at its own venue's next poll.
                on_pm = kind == "pmresolve" or (kind == "fill" and len(fact) > 7)
                (pm_polled if on_pm else polled).append(fact)
            elif kind in ("funding", "fill", "pmresolve"):
                _deliver(book, fact, event)
            elif kind in ("poll", "pmpoll"):
                buffer = polled if kind == "poll" else pm_polled
                for reported in sorted(buffer, key=lambda f: f[0]):
                    _deliver(book, reported, event)
                buffer.clear()
                if kind == "poll":
                    book.hl_ns = at
                else:
                    book.pm_ns = at
            elif kind == "pmbook":
                if fact[3] is not None:  # a failed read delivers nothing and reads nothing
                    if fact[3]:  # an empty book is read, and states no price
                        book.observe("MarketMid", {"coin": fact[2], "mid": fact[3],
                                                   "ts_ns": at}, event)
                    book.books[fact[2]] = at
            elif kind == "intent":
                book.order_intent(fact[2], fact[3], fact[4])
            elif kind == "ack":
                book.order_acknowledged(fact[2])
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
                size = ("0.01" if fact[2] == "A4" else "10" if fact[2].startswith("P")
                        else "0.001")
                book.order_result(fact[2], {"status": fact[4], "order_id": fact[3],
                                            "filled_size": size if fact[4] == "filled"
                                            else "0"}, {"size": size}, event)
                book.finish(fact[2], 0)
            elif kind == "openpair":
                # One return, two orders filled at once, on two instruments.
                book.clock_ns = at
                book.start(fact[2], event)
                for order in fact[3:]:
                    book.order_result(fact[2], {"status": "filled", "order_id": order,
                                                "filled_size": "10"}, {"size": "10"}, event)
                book.finish(fact[2], 0)
            if rng.random() < per_fact and not final_advance:
                settle()
        if not lag:
            book.advance_ns = named.advance_through_ns = batch_end
        if rng.random() < per_batch and not final_advance:
            settle()
    if mode == "tape_end":
        # The tape closed with its last fact: complete through it, and settled at once.
        book.tick_through_ns = named.tick_through_ns = sequence[-1][0]
    else:
        # The world goes on: a tick long after every patience, and every outcome is fixed.
        book.tick_through_ns = named.tick_through_ns = _t(10_000)
    for reported in sorted(polled + pm_polled, key=lambda f: f[0]):
        _deliver(book, reported, event)
    for coin in ("PM:A", "PM:B", "PM:C"):
        book.books[coin] = _t(10_000)  # the books are read long after, one last time
    if lag:
        book.hl_ns = book.pm_ns = _t(10_000)
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


def _acting(book, handle: str, order: str, status: str, at_ns: int) -> None:
    book.clock_ns = at_ns
    book.start(handle, 0)
    book.order_result(handle, {"status": status, "order_id": order,
                               "filled_size": "0.001" if status == "filled" else "0"},
                      {"size": "0.001"}, 0)
    book.finish(handle, 0)


def _fill_at(book, order: str, is_buy: bool, px: str, at_ns: int) -> None:
    book.observe("Fill", {"order_id": order, "coin": "BTC", "is_buy": is_buy,
                          "size": "0.001", "px": px, "fee_usd": "0", "ts_ns": at_ns,
                          "market": "perp", "inventory_size": "0.001"}, 0)


def test_a_position_closed_before_h_never_waits_on_a_funding_poll_that_stopped():
    """Codex on #152: a return needs an instrument's streams only through min(H, t_flat).
    C1 bought BTC and a spot pair at 1 s; C2's sell closed its BTC lot at 5 s, and the
    funding poll stops at 10 s, for good. C1 holds only the spot pair at H, which pays
    no funding: it is fixed at H; before, its closed BTC position held it on the funding
    stream forever."""
    book = _Book({"BTC": [[T0, "0"]], "PURR/USDC": [[T0, "0"]]})
    book.clock_ns = _t(1)
    book.start("C1", 0)
    for order in ("o-C1", "o-C1s"):
        book.order_result("C1", {"status": "filled", "order_id": order,
                                 "filled_size": "0.001"}, {"size": "0.001"}, 0)
    book.finish("C1", 0)
    _fill_at(book, "o-C1", True, "100", _t(1))
    book.observe("Fill", {"order_id": "o-C1s", "coin": "PURR/USDC", "is_buy": True,
                          "size": "0.001", "px": "0.2", "fee_usd": "0", "ts_ns": _t(1),
                          "market": "spot", "inventory_size": "0.001"}, 0)
    _acting(book, "C2", "o-C2", "filled", _t(5))
    _fill_at(book, "o-C2", False, "101", _t(5))
    book.funding_ns = _t(10)  # delivered through 10 s, then never again
    book.hl_ns = _t(10_000)
    book.clock_ns = book.tick_through_ns = _t(10_000)
    book.observe("MarketMid", {"coin": "PURR/USDC", "mid": "0.21", "ts_ns": _t(100)}, 1)
    book.resolve(1)
    payoff = book.payoff("C1")
    assert payoff is not None and payoff.censored is None and payoff.marked


def test_a_resting_order_still_waits_on_its_fills_through_h():
    """A return with an order resting in an instrument needs its fills through H, flat
    or not: a fills poll that stopped before H holds it, and it is fixed once the fills
    are delivered through H."""
    book = _Book({"BTC": [[T0, "0"]]})
    _acting(book, "C3", "o-C3", "resting", _t(1))
    book.hl_ns = _t(10)  # fills (and funding) delivered through 10 s only
    book.funding_ns = _t(10_000)
    book.clock_ns = book.tick_through_ns = _t(10_000)
    book.observe("MarketMid", {"coin": "BTC", "mid": "101", "ts_ns": _t(9_000)}, 1)
    book.resolve(1)
    assert book.payoff("C3") is None
    book.hl_ns = _t(10_000)
    book.resolve(2)
    assert book.payoff("C3") is not None


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
    assert named["N-BTC"][0] == named["N-SOL"][0] == named["N-PURR"][0] == "measured"
    assert named["N-PURR"][2]["funding_payments"] == 0  # a spot pair pays no funding
    # Its horizon (240 s) is after the funding-rate reads failed for good: a spot trade
    # still resolves, since it never waits on them.
    assert named["N-PURR2"][0] == "measured"
    btc = named["N-BTC"][2]
    # Each leg at its own rate on its own notional (D7): the exit leg's 3.5 bp is paid on
    # the exit notional, the entry's scaled by the move (Codex on #152).
    (move,) = [float(m["move_bps"]) for m in btc["moves"] if m["coin"] == "BTC"]
    assert btc["entry_fee_bps"] == "4.5"
    assert abs(float(btc["exit_fee_bps"]) - 3.5 * (1 + move / 10_000)) <= 1e-4
    assert btc["funding_payments"] == 1  # the hour boundary at 60 s, inside its window
    fixed = {row["handle"]: row for row in outcome["rows"]
             if row["kind"] == "consequence.outcome"}
    assert fixed["A2"]["marked"] is True  # filled at its H, marked at MarketMid(H)
    assert fixed["A3"]["marked"] is False and fixed["A3"]["net_micro"] == 0  # late money
    assert fixed["A4"]["censored"] == "no_mark"
    assert outcome["late"].get("A1")  # its lot closed after its outcome was fixed
    # Polymarket: P1 was held through the failed reads, never no_mark, and marked by the
    # first read after them; P2 graded on its lot at H, marked by the resolution, its
    # redemption late money; P3 graded with the fill held while an owner was pending.
    assert fixed["P1"]["censored"] is None and fixed["P1"]["marked"] is True
    assert fixed["P1"]["net_micro"] == round((0.40 + 18 * 0.005 - 0.40) * 10 * 10**6)
    assert fixed["P2"]["marked"] is True and fixed["P2"]["net_micro"] == 4_000_000
    assert outcome["late"]["P2"] == 4_000_000  # (1 - 0.60) * 10, paid at the resolution
    # P3's held fill counts: its lot was redeemed at 114 s, before its own H (115 s).
    assert fixed["P3"]["marked"] is False and fixed["P3"]["net_micro"] == 3_900_000
    assert fixed["P4"]["censored"] == "no_mark"  # its empty book was read through H
    # P5 was flat in PM:D from 40 s, whose book died at 70 s, and held PM:A at H: fixed,
    # marked on PM:A, never held by PM:D's dead book.
    assert fixed["P5"]["censored"] is None and fixed["P5"]["marked"] is True
