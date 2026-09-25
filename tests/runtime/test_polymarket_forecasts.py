"""World-settled forecasts on Polymarket markets, in a running world.

A judge's forecast on ``event_pays`` or ``event_price_above`` is sealed like any
seed claim and settled on the world's own read of the named token at its due tick:
the market's resolution, or its midpoint (essay II.III.b: the signal that grades an
evaluator "sits outside the factory's input entirely"; II.IV.a, "bet on beliefs").
A read the world did not answer settles censored and excluded, never as a zero.
Nothing here reads the network: the live path runs a scripted reader.
"""

from decimal import Decimal

import pytest

from factorylab.kernel.events import EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime import polymarket
from factorylab.world.polymarket import PolymarketUnavailable
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_diary, _consequence_runtime
from tests.runtime.test_polymarket_surface import advance, still_fake, world

YES = "100000000000000000000"
NO = "100000000000000000001"


def settled(rt):
    """Every forecast settlement the world emits, by predicate and token."""
    found = []
    emit = rt._emit

    def spy(kind, payload, *args, **kwargs):
        if kind is EventKind.FORECAST_SETTLED:
            found.append(dict(payload))
        return emit(kind, payload, *args, **kwargs)

    rt._emit = spy
    return found


def results_of(rt):
    """Every settlement result the settler returns, with its documented exclusion."""
    results = []
    settle_due = rt.settler.settle_due

    def capture(*args, **kwargs):
        out = settle_due(*args, **kwargs)
        results.extend(out)
        return out

    rt.settler.settle_due = capture
    return results


def judge_seat(rt, judge):
    """Register ``judge`` through the real path, once: an assembly registered by
    seed-decider, which takes its venue read slot (or waits for one) as any
    registration does, and whose reads are counted under ``<id>#<version>``."""
    if judge not in rt.assemblies:
        from tests.runtime.test_real_flows import _register

        _register(rt, judge)
        rt._assign_waiting_readers()
    assert judge in rt.venue_readers, "no venue read slot is free"
    return judge


def seal(rt, *claims, judge="judge-a", about=None):
    """Seal claims by ``judge``, a registered seat with a venue read slot (a
    Polymarket claim's token lookup is its own read)."""
    if getattr(rt, "polymarket", None) is not None:
        judge_seat(rt, judge)
    handle = collateral_decision(rt, owner=judge)
    return rt._open_forecasts(handle, judge, about or handle, [
        {"predicate": pid, "q": q, "params": params} for pid, q, params in claims])


def test_the_event_vocabulary_is_offered_only_where_the_world_enables_the_market():
    plain = _consequence_runtime()
    assert not {"event_pays", "event_price_above"} & {p.id for p in plain.predicates.all()}
    assert seal(plain, ("event_pays", 0.5, {"horizon_events": 3, "token_id": YES})) == []
    rt = world()
    assert {"event_pays", "event_price_above"} <= {p.id for p in rt.predicates.all()}
    assert {"event_pays", "event_price_above"} <= set(
        rt._forecast_schema()["items"]["properties"]["predicate"]["enum"])
    # A malformed claim is not sealed; a well-formed one is.
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": "BTC"})) == []
    assert len(seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": YES}))) == 1


def test_a_resolution_inside_the_horizon_settles_the_claims_on_the_worlds_answer():
    rt = world(fake=still_fake(resolutions={"fake-1": (5 * 10**9, 0)}))
    found = settled(rt)
    seal(rt, ("event_pays", 0.8, {"horizon_events": 20, "token_id": YES}),
         ("event_pays", 0.3, {"horizon_events": 20, "token_id": NO}))
    seal(rt, ("event_price_above", 0.6, {"horizon_events": 20, "token_id": YES,
                                          "level": 0.9}), judge="judge-b")
    advance(rt, 25)
    by = {(s["predicate"], s["evaluator_id"], s["y"]) for s in found}
    assert by == {("event_pays", "judge-a", 1), ("event_pays", "judge-a", 0),
                  ("event_price_above", "judge-b", 1)}
    assert all(s["status"] == str(SettleStatus.SETTLED) for s in found)
    reads = [i for i in _consequence_diary(rt) if i["kind"] == "polymarket.event_read"]
    assert {(r["token_id"], r["payout"]) for r in reads} == {(YES, "1"), (NO, "0")}


def test_an_open_market_settles_a_price_claim_on_its_midpoint_and_a_payout_claim_false():
    rt = world()  # fake-1's mid stands at 0.40 and never resolves
    found = settled(rt)
    seal(rt, ("event_price_above", 0.2, {"horizon_events": 3, "token_id": YES,
                                          "level": 0.35}),
         ("event_pays", 0.2, {"horizon_events": 3, "token_id": YES}))
    advance(rt, 4)
    assert sorted((s["predicate"], s["y"]) for s in found) == [
        ("event_pays", 0), ("event_price_above", 1)]
    [read] = [i for i in _consequence_diary(rt) if i["kind"] == "polymarket.event_read"
              and i["predicate"] == "event_price_above"]
    assert Decimal(read["midpoint"]) == Decimal("0.40") and read["payout"] is None


class Unanswering:
    """A live reader that lists the token when the claim is sealed and whose market
    read fails at settlement, as a transport failure would."""

    deterministic = False

    def market_of_token(self, token_id):
        return {"market_id": "1", "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": YES, "price": "0.4"}]}

    def market(self, market_id):
        raise PolymarketUnavailable("transport: TimeoutError")


class ScriptedLive:
    """Parsed live answers: one open market with an empty book, one unlisted token. The
    CLOB's own /midpoint answers 0.5 for an empty book; it is not a price."""

    deterministic = False

    def market_of_token(self, token_id):
        if token_id != YES:
            return None
        return self.market("1")

    def market(self, market_id):
        return {"market_id": "1", "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": YES, "price": "0.4"},
                             {"token_id": NO, "price": "0.6"}]}

    def midpoint(self, token_id):
        return "0.5"

    def order_book(self, token_id, depth):
        return {"token_id": token_id, "bids": [], "asks": [], "midpoint": None}


def test_an_unanswered_read_is_censored_and_excluded_never_a_zero():
    rt = world(venue="live")
    rt.polymarket.venue.target = Unanswering()
    found = settled(rt)
    results = results_of(rt)
    seal(rt, ("event_pays", 0.9, {"horizon_events": 2, "token_id": YES}))
    advance(rt, 3)
    [result] = results
    assert result.status is SettleStatus.CENSORED and result.y is None
    assert result.excluded == "external_unobservable"
    assert [s["status"] for s in found] == [str(SettleStatus.CENSORED)]
    assert any(i["kind"] == "polymarket.event_unavailable" for i in _consequence_diary(rt))


def test_a_live_price_claim_without_a_midpoint_is_excluded_and_an_unlisted_token_refused():
    rt = world(venue="live")
    reader = rt.polymarket.venue.target = ScriptedLive()
    results = results_of(rt)
    sealed = seal(rt, ("event_price_above", 0.5, {"horizon_events": 2, "token_id": YES,
                                                   "level": 0.5}),
                  ("event_pays", 0.5, {"horizon_events": 2, "token_id": "7"}))
    # A token no market lists is refused at sealing, charged to the seat, and not
    # cached: the next claim on it is looked up again (and may find it listed).
    assert len(sealed) == 1 and "7" not in rt.polymarket.token_markets
    assert polymarket._read_used(rt, "judge-a") == 2 * 3
    rt.clock.now_ns += polymarket.READ_WINDOW_NS  # the share's 10 s slides on
    looked = []
    lookup = reader.market_of_token
    reader.market_of_token = lambda token: looked.append(token) or lookup(token)
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 2, "token_id": "7"})) == []
    assert looked == ["7"]
    assert polymarket._read_used(rt, "judge-a") == 3
    advance(rt, 3)
    [result] = results
    assert result.predicate_id == "event_price_above"
    assert result.excluded == "external_unobservable"
    reasons = [i["reason"] for i in _consequence_diary(rt) if i["kind"] == "forecast.refused"]
    assert reasons == [polymarket.NOT_LISTED_REFUSAL] * 2
    # A live world publishes reads only.
    assert not any(t in rt.tool_specs for t in (*polymarket.WRITES, polymarket.ACCOUNT))


def test_a_live_read_world_can_answer_from_the_simulated_venue_offline():
    rt = world(venue="live")
    specs = dict(rt.tool_specs)
    polymarket.simulate_reads(rt)
    assert rt.tool_specs == specs and rt.polymarket.writes is False
    assert rt.polymarket.venue.deterministic
    before = rt.polymarket.venue.target.market("fake-1")["outcomes"][0]["price"]
    advance(rt, 30)
    after = rt.polymarket.venue.target.market("fake-1")["outcomes"][0]["price"]
    assert before != after  # the simulated market moves on the world's clock
    assert rt.polymarket.account() is None
    with pytest.raises(ValueError, match="live reader only"):
        polymarket.simulate_reads(world())


class Resolving:
    """A live reader whose market resolves, or whose read fails, between two calls."""

    deterministic = False

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def market_of_token(self, token_id):
        # The lookup at sealing: which market lists the token.
        return {"market_id": "1", "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": YES, "price": "0.6"},
                             {"token_id": NO, "price": "0.4"}]}

    def market(self, market_id):
        # Each settlement pass reads the market by the id the lookup found.
        assert market_id == "1"
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        closed = answer == "resolved"
        return {"market_id": "1", "closed": closed,
                "uma_resolution_status": "resolved" if closed else None,
                "outcomes": [{"token_id": YES, "price": "1" if closed else "0.6"},
                             {"token_id": NO, "price": "0" if closed else "0.4"}]}

    def order_book(self, token_id, depth):
        return {"token_id": token_id, "bids": [{"price": "0.59", "size": "5"}],
                "asks": [{"price": "0.61", "size": "5"}], "midpoint": "0.6"}


def test_the_judges_of_one_question_are_graded_against_one_read_of_the_world():
    """PR #145 review: a resolution between two judges' reads graded one question two
    ways. The world is read once a token a settlement pass, and every claim on it in
    that pass is answered from that snapshot."""
    for answers, expected in ((("open", "resolved"), {0}),
                              ((PolymarketUnavailable("transport: TimeoutError"), "open"),
                               {None})):
        rt = world(venue="live")
        reader = rt.polymarket.venue.target = Resolving(*answers)
        results = results_of(rt)
        about = collateral_decision(rt, owner="author")
        claim = ("event_pays", 0.5, {"horizon_events": 2, "token_id": YES})
        seal(rt, claim, judge="judge-a", about=about)
        seal(rt, claim, judge="judge-b", about=about)
        advance(rt, 3)
        assert len(results) == 2 and reader.calls == 1
        assert {r.y for r in results} == expected
        assert len({(r.status, r.excluded) for r in results}) == 1
    # The next pass reads the world again: a later question sees the resolution.
    rt = world(venue="live")
    reader = rt.polymarket.venue.target = Resolving("open", "resolved")
    results = results_of(rt)
    seal(rt, ("event_pays", 0.5, {"horizon_events": 2, "token_id": YES}))
    seal(rt, ("event_pays", 0.5, {"horizon_events": 4, "token_id": YES}), judge="judge-b")
    advance(rt, 5)
    assert [r.y for r in results] == [0, 1] and reader.calls == 2



def passes_of(rt):
    """Every settlement pass that settled something: the tick, and what it settled."""
    passes = []
    settle_due = rt.settler.settle_due

    def capture(*args, **kwargs):
        out = settle_due(*args, **kwargs)
        if out:
            passes.append((rt.ticks_consumed, list(out)))
        return out

    rt.settler.settle_due = capture
    return passes


class Listing:
    """A live reader that lists any token on a market of its own, open at 0.5, and
    counts every lookup and market read. It keeps no request count of its own."""

    deterministic = False

    def __init__(self):
        self.lookups, self.reads = {}, 0

    def market_of_token(self, token_id):
        self.lookups[token_id] = self.lookups.get(token_id, 0) + 1
        return self.market(f"m-{token_id}", count=False)

    def market(self, market_id, count=True):
        self.reads += count
        token_id = market_id.removeprefix("m-")
        return {"market_id": market_id, "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": token_id, "price": "0.5"}]}

    def order_book(self, token_id, depth):
        return {"token_id": token_id, "bids": [{"price": "0.49", "size": "5"}],
                "asks": [{"price": "0.51", "size": "5"}], "midpoint": "0.5"}


def _token(i):
    return str(10**20 + 500 + i)


def _advance(rt, ticks, *, tick_ns=10**9):
    """The per-tick path, with the event history a claim settles over."""
    for _ in range(ticks):
        rt.ticks_consumed += 1
        rt.n += 1
        rt.balance_at.append(rt.wallet.balance)
        rt.clock.now_ns += tick_ns
        polymarket.tick(rt)
        rt._settle_due_forecasts()


#: A world where the kernel's one settlement pass over every open read it keeps sends
#: more requests than the whole per-minute budget would have admitted under a global
#: meter (995268a deferred there): 48 open reads, 3 a seat over 16 slots, 3 requests
#: a minute a seat (one claim's lookup), and a pass of 2 × 48 = 96 kernel requests.
SATURATED = {"read_requests_per_10s": 144, "kernel_reserve_per_10s": 96}


def _saturate(rt, *, question=False, hyperliquid=False):
    """Every one of 16 slot seats opens its 3 open reads with price claims due at tick
    21, one claim a (world) minute, so all 48 settle in one pass. With ``question``
    the last three claims sealed are three judges of one question on one token; with
    ``hyperliquid`` the first seat also seals a ``wallet_up`` claim due then."""
    seats = [s for s in rt.venue_readers if s is not None]
    seats += [f"judge-{i}" for i in range(16 - len(seats))]
    about = collateral_decision(rt, owner="author")
    shared = {"token_id": _token(999), "level": 0.4}
    for round_, horizon in enumerate((21, 14, 7)):
        for index, seat in enumerate(seats):
            last = round_ == 2 and index >= 13
            params = dict(shared) if question and last else {
                "token_id": _token(round_ * 16 + index), "level": 0.4}
            claims = [("event_price_above", 0.5, {"horizon_events": horizon, **params})]
            if hyperliquid and index == 0 and round_ == 2:
                claims.append(("wallet_up", 0.5, {"horizon_events": horizon}))
            assert len(seal(rt, *claims, judge=seat,
                            about=about if question and last else None)) == len(claims)
        if round_ < 2:
            _advance(rt, 7, tick_ns=10 * 10**9)  # 70 s: every seat's share is back
    return about


def test_a_mixed_pass_settles_every_claim_due_in_it_on_one_read_per_question():
    """The review's missed path, at the world's limit: 48 Polymarket price claims (each
    reading the book) and a Hyperliquid-side claim fall due at one tick. The kernel
    settles every one of them in that tick's pass, never deferring: under 995268a's
    global meter the pass needed 192 requests of a 144 budget and deferred."""
    rt = world(venue="live", **SATURATED)
    reader = rt.polymarket.venue.target = Listing()
    passes = passes_of(rt)
    _saturate(rt, hyperliquid=True)
    _advance(rt, 8, tick_ns=10 * 10**9)
    assert len(passes) == 1
    [(tick, settled_now)] = passes
    assert tick == 21 and len(settled_now) == 49
    assert {r.predicate_id for r in settled_now} == {"event_price_above", "wallet_up"}
    assert all(r.y == 1 for r in settled_now if r.predicate_id == "event_price_above")
    assert reader.reads == 48  # one market read by id a token; lookups were at sealing
    diary = _consequence_diary(rt)
    assert not any(i["kind"] in ("polymarket.event_unavailable", "polymarket.settlement_deferred")
                   for i in diary)
    assert len([i for i in diary if i["kind"] == "polymarket.event_read"
                and i["midpoint"] == "0.5"]) == 48


def test_one_question_is_settled_in_its_due_pass_even_at_the_limit():
    """Three judges of one question are sealed last, behind 45 other claims filling the
    world's open reads, all due at one tick. Nothing defers, so the question settles
    in its due pass on one read (995268a deferred it past the budget)."""
    rt = world(venue="live", **SATURATED)
    rt.polymarket.venue.target = Listing()
    passes = passes_of(rt)
    about = _saturate(rt, question=True)
    _advance(rt, 8, tick_ns=10 * 10**9)
    [(tick, settled_now)] = passes
    question = [r for r in settled_now if r.about_handle == about]
    assert tick == 21 and len(settled_now) == 48
    assert len(question) == 3 and len({(r.status, r.y) for r in question}) == 1
    reads = [i for i in _consequence_diary(rt) if i["kind"] == "polymarket.event_read"
             and i["token_id"] == _token(999)]
    assert len(reads) == 3 and len({(r["midpoint"], r["listed"]) for r in reads}) == 1


def test_a_seat_s_open_reads_are_its_own_share():
    """The review's HIGH and MEDIUM items: each seat's claims count against its own
    share of open reads (N // max_readers), per (token, due tick) it holds itself. One
    seat at its share is refused while another's claims are unaffected; a claim on a
    key another seat holds is charged to the claimant and refused at its share; and
    the answer never depends on what another seat holds."""
    rt = world(venue="live", **SATURATED)  # 3 open reads a seat
    rt.polymarket.venue.target = Listing()
    assert polymarket.seat_open_share(rt.m.polymarket, rt.m.exchange.max_readers) == 3
    for i in range(3):
        assert seal(rt, ("event_pays", 0.5, {"horizon_events": 50, "token_id": _token(i)}),
                    judge="judge-a")
        _advance(rt, 7, tick_ns=10 * 10**9)
    # judge-a is at its share: a fourth key, even one judge-b will hold, is refused.
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 20, "token_id": _token(9)}),
                judge="judge-a") == []
    # judge-b is untouched by judge-a's keys: it opens its own, one on judge-a's token
    # and tick, charged to itself.
    due = 14 + 50  # judge-a's third claim, sealed at tick 14
    assert f"{rt._reader_id('judge-a')}|due:{_token(2)}:{due}" in rt.polymarket.open_reads
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": due - rt.ticks_consumed,
                                          "token_id": _token(2)}), judge="judge-b")
    assert f"{rt._reader_id('judge-b')}|due:{_token(2)}:{due}" in rt.polymarket.open_reads
    assert polymarket.seat_open_reads(rt, rt._reader_id("judge-b")) == 1
    assert polymarket.seat_open_reads(rt, rt._reader_id("judge-a")) == 3
    assert f"{rt._reader_id('judge-a')}|due:{_token(2)}:{due}" in rt.polymarket.open_reads
    # judge-c at its share gets the same refusal whether or not someone else holds the
    # key it asks for.
    answers = []
    for held_by_another in (True, False):
        fresh = world(venue="live", **SATURATED)
        fresh.polymarket.venue.target = Listing()
        for i in range(3):
            seal(fresh, ("event_pays", 0.5, {"horizon_events": 50, "token_id": _token(i)}),
                 judge="judge-c")
            _advance(fresh, 7, tick_ns=10 * 10**9)
        if held_by_another:
            seal(fresh, ("event_pays", 0.5, {"horizon_events": 10, "token_id": _token(7)}),
                 judge="judge-d")
        seal(fresh, ("event_pays", 0.5, {"horizon_events": 10, "token_id": _token(7)}),
             judge="judge-c")
        answers.append([(i["reason"], i["token_id"]) for i in _consequence_diary(fresh)
                        if i["kind"] == "forecast.refused" and i["handle"] in {
                            h for h in fresh.handle_to_assembly
                            if fresh.handle_to_assembly[h] == "judge-c"}])
    assert answers[0] == answers[1] == [(f"{polymarket.OPEN_LIMIT_REFUSAL}: 3 open reads",
                                         _token(7))]
    published = rt._world_block()["polymarket_reads"]
    assert (published["open_reads_limit"], published["seat_open_reads"]) == (48, 3)


class Wire:
    """A live reader that sends what Gamma and the CLOB would, and logs every request
    with the wall-clock instant it was sent and whether the kernel sent it, stamping
    it as ``PolymarketReader`` does (``drain_sends``): a token's lookup is two
    requests when listed (the closed listing, then the open one) and three when not;
    every other read one. Tokens ``7`` and ``8`` are listed nowhere, and every seventh
    book read fails, as a transport failure would. ``wall`` is its wall clock, which
    need not be the world's; each request takes ``flight_ns`` of it."""

    deterministic = False

    def __init__(self, wall, flight_ns=0):
        self.wall, self.flight_ns = wall, flight_ns
        self.kernel, self.log, self.books, self.sends = [False], [], 0, []

    def _send(self, requests):
        for _ in range(requests):
            stamp = self.wall()
            self.log.append((stamp, 1, self.kernel[0]))
            self.sends.append(stamp)
            self.advance(self.flight_ns)

    def advance(self, ns):
        pass  # a clock of the test's own moves itself

    def requests_sent(self):
        return sum(n for _t, n, _k in self.log)

    def drain_sends(self):
        sends, self.sends = self.sends, []
        return sends

    def wall_ns(self):
        return self.wall()

    def market_of_token(self, token_id):
        listed = token_id not in ("7", "8")
        self._send(2 if listed else 3)
        return self._market(token_id) if listed else None

    def market(self, market_id):
        self._send(1)
        return self._market(market_id.removeprefix("m-"))

    def search_markets(self, query, limit):
        self._send(1)
        return []

    def order_book(self, token_id, depth):
        self._send(1)
        self.books += 1
        if self.books % 7 == 0:
            raise PolymarketUnavailable("transport: TimeoutError")
        return {"token_id": token_id, "bids": [{"price": "0.49", "size": "5"}],
                "asks": [{"price": "0.51", "size": "5"}], "midpoint": "0.5"}

    @staticmethod
    def _market(token_id):
        return {"market_id": f"m-{token_id}", "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": token_id, "price": "0.5"}]}


def _worst_10s(entries):
    """The most requests any sliding 10 s holds, over ``(ns, requests)``."""
    window = polymarket.READ_WINDOW_NS
    return max((sum(n for ts, n in entries if start - window < ts <= start)
                for start, _n in entries), default=0)


def test_every_polymarket_request_fits_the_published_10s_limit():
    """The bound ``open_limit`` proves, per Polymarket's own window (10 s), checked on
    every request a live world sends: seats keep claiming, at horizons up to
    ``MAX_FORECAST_HORIZON``, price claims that read the book (some on tokens no
    market lists, some books failing) while reading the market themselves, for five
    world minutes. In no sliding 10 s does the kernel send more than its reserve, the
    seats more than the rest, or the world more than ``read_requests_per_10s`` (and
    so more than Polymarket's 300)."""
    from factorylab.runtime.shared import MAX_FORECAST_HORIZON

    rt = world(venue="live")  # the defaults: 200 per 10 s, 100 of them the kernel's
    wire = rt.polymarket.venue.target = Wire(lambda: rt.clock.now_ns)
    spec = rt.m.polymarket
    settle_due = rt._settle_due_forecasts

    def kernel_pass():
        wire.kernel[0] = True
        try:
            settle_due()
        finally:
            wire.kernel[0] = False

    judges = [judge_seat(rt, s) for s in rt.venue_readers if s is not None]
    judges += [judge_seat(rt, f"judge-{i}") for i in range(16 - len(judges))]
    tokens = [_token(i) for i in range(40)] + ["7", "8"]
    for step in range(300):
        seat = judges[step % len(judges)]
        seal(rt, ("event_price_above", 0.5, {
            "horizon_events": 1 + (step * 37) % MAX_FORECAST_HORIZON,
            "token_id": tokens[step % len(tokens)], "level": 0.3}),
            ("event_pays", 0.5, {"horizon_events": 1 + (step * 11) % 30,
                                 "token_id": tokens[(step * 7) % len(tokens)]}),
            judge=seat)
        rt._run_tool(seat, collateral_decision(rt, seat),
                     {"tool": "polymarket.search", "args": {"query": f"q{step}"}})
        rt.ticks_consumed += 1
        rt.n += 1
        rt.balance_at.append(rt.wallet.balance)
        rt.clock.now_ns += 10**9
        polymarket.tick(rt)
        kernel_pass()
    kernel = [(ts, n) for ts, n, by_kernel in wire.log if by_kernel]
    seats = [(ts, n) for ts, n, by_kernel in wire.log if not by_kernel]
    assert sum(n for _ts, n in kernel) > spec.kernel_reserve_per_10s  # it read plenty
    assert _worst_10s(kernel) <= spec.kernel_reserve_per_10s
    assert _worst_10s(seats) <= spec.read_requests_per_10s - spec.kernel_reserve_per_10s
    assert _worst_10s(kernel + seats) <= spec.read_requests_per_10s <= 300
    diary = _consequence_diary(rt)
    reasons = {i["reason"] for i in diary if i["kind"] == "forecast.refused"}
    assert polymarket.NOT_LISTED_REFUSAL in reasons
    assert any(i["kind"] == "polymarket.event_unavailable" for i in diary)


class WallClock:
    """A wall clock apart from the world's: the test moves it, and each request the
    wire sends takes a little of it."""

    def __init__(self):
        self.now = 1_900_000_000 * 10**9

    def __call__(self):
        return self.now


@pytest.mark.gate
def test_every_polymarket_request_fits_the_published_limit_in_wall_time():
    """The fifth review's HIGH item: Polymarket counts wall time, and a world tick can
    be any length of it. Here the wire has its own wall clock, apart from the world's:
    ticks alternate a 30 s world tick and a 1 s one, while each takes a quarter second
    of wall time (120× and 4× compression), and every request takes 1 ms in flight.
    All 16 seats claim and read as hard as they can every tick. In no sliding 10 s of
    wall time does the kernel send more than 2 N, the seats more than 16 × share, or
    the world more than the stated bound, 2 N + 16 × share = 196 of the published
    300 (``open_limit``)."""
    rt = world(venue="live")  # the defaults: 200 per 10 s, 100 of them the kernel's
    clock = WallClock()
    wire = Wire(clock, flight_ns=1_000_000)
    wire.advance = lambda ns: setattr(clock, "now", clock.now + ns)
    rt.polymarket.venue.target = wire
    spec = rt.m.polymarket
    share = polymarket.read_share(spec, rt.m.exchange.max_readers)
    settle_due = rt._settle_due_forecasts

    def kernel_pass():
        wire.kernel[0] = True
        try:
            settle_due()
        finally:
            wire.kernel[0] = False

    judges = [judge_seat(rt, s) for s in rt.venue_readers if s is not None]
    judges += [judge_seat(rt, f"judge-{i}") for i in range(16 - len(judges))]
    tokens = [_token(i) for i in range(40)] + ["7", "8"]
    world_start, wall_start = rt.clock.now_ns, clock.now
    for step in range(160):
        for index, seat in enumerate(judges):
            k = step * 16 + index
            seal(rt, ("event_price_above", 0.5, {
                "horizon_events": 1 + (k * 37) % 30, "token_id": tokens[k % len(tokens)],
                "level": 0.3}),
                ("event_pays", 0.5, {"horizon_events": 1 + (k * 11) % 30,
                                     "token_id": tokens[(k * 7) % len(tokens)]}),
                judge=seat)
            rt._run_tool(seat, collateral_decision(rt, seat),
                         {"tool": "polymarket.search", "args": {"query": f"q{k}"}})
        rt.ticks_consumed += 1
        rt.n += 1
        rt.balance_at.append(rt.wallet.balance)
        rt.clock.now_ns += (30 if step % 2 == 0 else 1) * 10**9  # a long tick, a fast one
        clock.now += 250_000_000  # each a quarter second of wall time
        polymarket.tick(rt)
        kernel_pass()
    assert (rt.clock.now_ns - world_start) > 10 * (clock.now - wall_start)  # compressed
    kernel = [(ts, n) for ts, n, by_kernel in wire.log if by_kernel]
    seats = [(ts, n) for ts, n, by_kernel in wire.log if not by_kernel]
    bound = (polymarket.KERNEL_READS_PER_OPEN * polymarket.open_limit(spec)
             + rt.m.exchange.max_readers * share)
    assert bound == 196
    assert sum(n for _ts, n in kernel) > spec.kernel_reserve_per_10s  # it read plenty
    assert _worst_10s(kernel) <= 2 * polymarket.open_limit(spec) <= spec.kernel_reserve_per_10s
    assert _worst_10s(seats) <= rt.m.exchange.max_readers * share
    assert _worst_10s(kernel + seats) <= bound <= 300
    diary = _consequence_diary(rt)
    assert polymarket.NOT_LISTED_REFUSAL in {i["reason"] for i in diary
                                             if i["kind"] == "forecast.refused"}


def test_sixteen_seats_bursting_in_one_tick_stay_within_the_seat_budget():
    """Every seat claims and reads as hard as it can within one tick: each is refused
    past its own share, so the seats together send at most
    ``read_requests_per_10s - kernel_reserve_per_10s`` in that 10 s."""
    rt = world(venue="live")
    wire = rt.polymarket.venue.target = Wire(lambda: rt.clock.now_ns)
    spec = rt.m.polymarket
    seats = [judge_seat(rt, s) for s in rt.venue_readers if s is not None]
    seats += [judge_seat(rt, f"judge-{i}") for i in range(16 - len(seats))]
    assert len(seats) == 16
    for round_ in range(4):
        for index, seat in enumerate(seats):
            seal(rt, ("event_pays", 0.5, {"horizon_events": 50,
                                          "token_id": _token(round_ * 32 + index)}),
                 ("event_pays", 0.5, {"horizon_events": 50,
                                      "token_id": _token(round_ * 32 + 16 + index)}),
                 judge=seat)
            rt._run_tool(seat, collateral_decision(rt, seat),
                         {"tool": "polymarket.search", "args": {"query": "q"}})
    sent = sum(n for _ts, n, _k in wire.log)
    budget = spec.read_requests_per_10s - spec.kernel_reserve_per_10s
    assert 0 < sent <= budget
    assert polymarket.read_share(spec, rt.m.exchange.max_readers) * 16 <= budget


def test_an_outage_refuses_every_claim_alike_whether_or_not_the_token_is_known():
    """Sealing always sends the claim's lookup, whether or not the world already knows
    the token (the cache serves only the kernel's settlement reads), so during an
    outage a claim on a known token and one on a new token get the same answer."""
    rt = world(venue="live")
    reader = rt.polymarket.venue.target = Listing()
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 50, "token_id": _token(1)}))
    assert _token(1) in rt.polymarket.token_markets

    def down(token_id):
        raise PolymarketUnavailable("transport: TimeoutError")

    reader.market_of_token = down
    rt.clock.now_ns += polymarket.READ_WINDOW_NS
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 50, "token_id": _token(1)}),
                judge="judge-b") == []
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 50, "token_id": _token(2)}),
                judge="judge-c") == []
    reasons = [i["reason"] for i in _consequence_diary(rt) if i["kind"] == "forecast.refused"]
    assert reasons == ["polymarket read unavailable"] * 2
