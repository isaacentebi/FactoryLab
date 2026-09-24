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
from factorylab.world.polymarket import DEFAULT_FAKE_MARKETS, FakePolymarket, PolymarketUnavailable
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_diary, _consequence_runtime
from tests.runtime.test_polymarket_surface import advance, buy, still_fake, world

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


def seal(rt, *claims, judge="judge-a", about=None):
    """Seal claims by ``judge``, a seat with a venue read slot (a Polymarket claim's
    token lookup is its own read)."""
    if judge not in rt.venue_readers:
        rt.venue_readers.append(judge)
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


def test_a_live_price_claim_without_a_midpoint_is_excluded_and_an_unlisted_token_is_not():
    rt = world(venue="live")
    rt.polymarket.venue.target = ScriptedLive()
    results = results_of(rt)
    seal(rt, ("event_price_above", 0.5, {"horizon_events": 2, "token_id": YES, "level": 0.5}),
         ("event_pays", 0.5, {"horizon_events": 2, "token_id": "7"}))
    advance(rt, 3)
    by = {r.predicate_id: r for r in results}
    assert by["event_price_above"].excluded == "external_unobservable"
    # The claim named a token no market lists: censored, and its owner's to answer for.
    assert by["event_pays"].status is SettleStatus.CENSORED and by["event_pays"].excluded is None
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


def test_a_mixed_pass_settles_every_claim_due_in_it_on_one_read_per_question():
    """The review's missed path: Hyperliquid-side and Polymarket claims, a price claim
    among them, due at one tick, all settle in that tick's one pass. A question two
    judges share is graded on one read, and the price claim reads the book."""
    rt = world()
    passes = passes_of(rt)
    other = rt.polymarket.venue.target.market("fake-2")["outcomes"][0]["token_id"]
    about = collateral_decision(rt, owner="author")
    seal(rt, ("event_price_above", 0.5, {"horizon_events": 3, "token_id": YES,
                                          "level": 0.35}),
         ("wallet_up", 0.5, {"horizon_events": 3}), about=about)
    seal(rt, ("event_price_above", 0.4, {"horizon_events": 3, "token_id": YES,
                                          "level": 0.35}),
         ("event_pays", 0.5, {"horizon_events": 3, "token_id": other}),
         judge="judge-b", about=about)
    advance(rt, 4)
    assert len(passes) == 1
    [(tick, settled_now)] = passes
    assert sorted(r.predicate_id for r in settled_now) == [
        "event_pays", "event_price_above", "event_price_above", "wallet_up"]
    price = [r for r in settled_now if r.predicate_id == "event_price_above"]
    assert {r.y for r in price} == {1} and len({(r.status, r.y) for r in price}) == 1
    diary = _consequence_diary(rt)
    reads = [i for i in diary if i["kind"] == "polymarket.event_read"]
    assert Decimal([r for r in reads if r["predicate"] == "event_price_above"][0][
        "midpoint"]) == Decimal("0.40")
    assert not any(i["kind"] == "polymarket.event_unavailable" for i in diary)


class Listing:
    """A live reader that lists any token on a market of its own, open at 0.5, and
    counts every lookup and market read."""

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


def test_a_new_open_read_beyond_the_world_s_limit_is_refused_at_sealing():
    """N = kernel_reserve_per_minute // 2 = 5 here. Five claims on new tokens open five
    reads; a sixth new token is refused at sealing with the limit, and so is a new due
    tick on an open token; a claim joining an open due tick is admitted. A settled
    read still counts for 60 s, and each token is looked up once, ever."""
    rt = world(venue="live", read_requests_per_minute=180, kernel_reserve_per_minute=10)
    reader = rt.polymarket.venue.target = Listing()
    assert polymarket.open_limit(rt.m.polymarket) == 5
    published = rt._world_block()["polymarket_reads"]
    assert (published["open_reads_limit"], published["kernel_reserve_per_minute"]) == (5, 10)
    assert "is refused with 'polymarket open reads are at the world's limit'" in (
        published["rule"])
    judges = [f"judge-{c}" for c in "abcdefg"]
    for i in range(5):
        assert seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": _token(i)}),
                    judge=judges[i]) != []
    refused = seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": _token(5)}),
                   judge=judges[5])
    assert refused == []
    later = seal(rt, ("event_pays", 0.5, {"horizon_events": 4, "token_id": _token(0)}),
                 judge=judges[5])
    assert later == []
    joined = seal(rt, ("event_price_above", 0.5, {"horizon_events": 3, "token_id": _token(0),
                                                   "level": 0.4}), judge=judges[6])
    assert len(joined) == 1
    results = results_of(rt)
    advance(rt, 4)
    assert len(results) == 6 and all(r.y == 0 for r in results if r.predicate_id ==
                                     "event_pays")
    # Settled, the five still count for a minute: a new token waits for it.
    assert seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": _token(6)}),
                judge=judges[0]) == []
    rt.clock.now_ns += polymarket.READ_WINDOW_NS
    assert len(seal(rt, ("event_pays", 0.5, {"horizon_events": 3, "token_id": _token(6)}),
                    judge=judges[0])) == 1
    assert reader.lookups == {_token(i): 1 for i in (0, 1, 2, 3, 4, 6)}
    reasons = [i["reason"] for i in _consequence_diary(rt) if i["kind"] == "forecast.refused"]
    assert reasons == [f"{polymarket.OPEN_LIMIT_REFUSAL} of 5"] * 3


class Logged(FakePolymarket):
    """The simulated venue, logging each request it counts with the world time and
    whether the kernel (a settlement pass or a mark) sent it."""

    def _count(self, requests):
        super()._count(requests)
        log = self.__dict__.setdefault("log", [])
        log.append((self.clock(), requests, self.kernel[0]))


def test_the_kernel_s_requests_in_any_minute_fit_its_reserve():
    """The bound ``open_limit`` proves, checked against the simulated venue's counted
    requests: seats keep opening claims (price claims, which read the book) on the
    world's tokens at many due ticks while the pot holds a position the kernel marks,
    for five world minutes; in no 60 s window does the kernel send more than
    ``kernel_reserve_per_minute``, and the limit refuses what would pass it."""
    markets = tuple({**m, "resolves_after_s": None} for m in DEFAULT_FAKE_MARKETS)
    fake = Logged(start_usdc=Decimal(50), markets=markets, step_ticks=0)
    rt = world(fake=fake, read_requests_per_minute=180, kernel_reserve_per_minute=10)
    fake.clock, fake.kernel = (lambda: rt.clock.now_ns), [False]
    settle_due, tick = rt._settle_due_forecasts, polymarket.tick

    def kernel(call):
        def run(*args, **kwargs):
            fake.kernel[0] = True
            try:
                return call(*args, **kwargs)
            finally:
                fake.kernel[0] = False
        return run

    rt._settle_due_forecasts = kernel(settle_due)
    handle = collateral_decision(rt)
    assert buy(rt, handle)["status"] == "filled"  # a held token, marked once a minute
    tokens = [o["token_id"] for m in markets
              for o in fake.market(m["market_id"])["outcomes"]]
    judges = [f"judge-{i}" for i in range(6)]
    for step in range(300):
        seal(rt, ("event_price_above", 0.5, {"horizon_events": 1 + step % 3,
                                              "token_id": tokens[step % len(tokens)],
                                              "level": 0.3}),
             judge=judges[step % len(judges)])
        rt.ticks_consumed += 1
        rt.n += 1
        rt.balance_at.append(rt.wallet.balance)  # the event history a claim settles over
        rt.clock.now_ns += 10**9
        kernel(tick)(rt)
        rt._settle_due_forecasts()
    sent = [(ts, n) for ts, n, by_kernel in fake.log if by_kernel]
    assert sum(n for _ts, n in sent) > 10  # the kernel read, many times over
    minute = polymarket.READ_WINDOW_NS
    worst = max(sum(n for ts, n in sent if start - minute < ts <= start)
                for start, _n in sent)
    assert worst <= rt.m.polymarket.kernel_reserve_per_minute
    assert any(i["kind"] == "forecast.refused" for i in _consequence_diary(rt))


def test_one_question_is_settled_in_one_pass_even_at_the_limit():
    """Nothing defers: the judges of one question, among claims filling the world's
    limit, are all settled in the pass of their due tick, on one read."""
    rt = world(venue="live", read_requests_per_minute=180, kernel_reserve_per_minute=10)
    rt.polymarket.venue.target = Listing()
    passes = passes_of(rt)
    about = collateral_decision(rt, owner="author")
    claim = ("event_price_above", 0.5, {"horizon_events": 2, "token_id": _token(0),
                                        "level": 0.4})
    for judge in ("judge-a", "judge-b", "judge-c"):
        assert len(seal(rt, claim, judge=judge, about=about)) == 1
    for i in range(1, 5):
        assert seal(rt, ("event_pays", 0.5, {"horizon_events": 2, "token_id": _token(i)}),
                    judge=f"judge-{i}")
    advance(rt, 3)
    [(_tick, settled_now)] = passes
    question = [r for r in settled_now if r.predicate_id == "event_price_above"]
    assert len(question) == 3 and len({(r.status, r.y) for r in question}) == 1
    assert len(settled_now) == 7
