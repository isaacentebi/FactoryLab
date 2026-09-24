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


def seal(rt, *claims, judge="judge-a"):
    handle = collateral_decision(rt, owner=judge)
    return rt._open_forecasts(handle, judge, handle, [
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
