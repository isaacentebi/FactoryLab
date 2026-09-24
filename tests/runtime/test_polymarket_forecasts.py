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


def seal(rt, *claims, judge="judge-a", about=None):
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
    """A live reader whose market reads fail, as a transport failure would."""

    deterministic = False

    def market_of_token(self, token_id):
        raise PolymarketUnavailable("transport: TimeoutError")


class ScriptedLive:
    """Parsed live answers: one open market with an empty book, one unlisted token. The
    CLOB's own /midpoint answers 0.5 for an empty book; it is not a price."""

    deterministic = False

    def market_of_token(self, token_id):
        if token_id != YES:
            return None
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
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        closed = answer == "resolved"
        return {"market_id": "1", "closed": closed,
                "uma_resolution_status": "resolved" if closed else None,
                "outcomes": [{"token_id": YES, "price": "1" if closed else "0.6"},
                             {"token_id": NO, "price": "0" if closed else "0.4"}]}

    def market(self, market_id):
        # Once a token's market is known, the world reads that market by its id.
        assert market_id == "1"
        return self.market_of_token(YES)

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


class Counting:
    """A live reader that sends what Gamma and the CLOB would, and keeps each request's
    world time: every token is on an open market, found on the second listing."""

    deterministic = False

    def __init__(self, clock):
        self.clock, self.sent, self.at, self.lookups = clock, 0, [], {}

    def _send(self, requests):
        self.sent += requests
        self.at.extend([self.clock()] * requests)

    def requests_sent(self):
        return self.sent

    def market_of_token(self, token_id):
        self._send(2)  # the closed listing, then the open one
        self.lookups[token_id] = self.lookups.get(token_id, 0) + 1
        return self._market(token_id)

    def market(self, market_id):
        self._send(1)
        return self._market(market_id.removeprefix("m-"))

    @staticmethod
    def _market(token_id):
        return {"market_id": f"m-{token_id}", "closed": False, "uma_resolution_status": None,
                "outcomes": [{"token_id": token_id, "price": "0.5"}]}


def test_a_pass_the_request_budget_cannot_cover_defers_and_every_token_is_looked_up_once():
    """Codex P1: the kernel's settlement reads are metered with the seats' against the
    world's one budget. Thirty tokens due in one pass need more than a minute's budget:
    the pass stops before the budget is passed, records the deferral, and settles the
    rest on later passes; nothing is censored for waiting, no 60 s window ever holds
    more than the budget, and each token's market is looked up once, ever."""
    budget = 40
    rt = world(venue="live", read_requests_per_minute=budget, kernel_reserve_per_minute=24)
    reader = rt.polymarket.venue.target = Counting(lambda: rt.clock.now_ns)
    results = results_of(rt)
    tokens = [str(10**20 + 100 + i) for i in range(30)]
    for batch in range(0, 30, 2):
        about = collateral_decision(rt, owner="author")
        assert len(seal(rt, *[("event_pays", 0.5, {"horizon_events": 2, "token_id": t})
                              for t in tokens[batch:batch + 2]], about=about)) == 2
    advance(rt, 3)
    first = len(results)
    assert 0 < first < 30
    for _ in range(4):
        advance(rt, 61)
    assert len(results) == 30
    assert {r.y for r in results} == {0}  # every claim observed; none censored
    assert not any(r.status is SettleStatus.CENSORED for r in results)
    assert reader.lookups == {t: 1 for t in tokens}
    minute = 60 * 10**9
    assert max(sum(1 for t in reader.at if start <= t < start + minute)
               for start in reader.at) <= budget
    diary = _consequence_diary(rt)
    deferred = [i for i in diary if i["kind"] == "polymarket.settlement_deferred"]
    assert deferred and deferred[0]["count"] == 30 - first
    assert not any(i["kind"] == "polymarket.event_unavailable" for i in diary)
