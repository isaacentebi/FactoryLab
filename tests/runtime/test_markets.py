"""The charter's markets: posted λ, and futarchy on motions (charter audit M1, M2, P1).

Essay II.IV.a: λ "reaches the committee as a speculative price posted by the
factory"; "vote on values, bet on beliefs"; "any representative error that
misprices the marginal worth of a constraint is penalized through the conventional
reward channel"; and "A futarchic λ, however, is necessarily forward-looking".
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.charter.market import (
    LAMBDA_POST_DEFINITION,
    MOTION_FORECAST_DEFINITION,
    branch_violation,
    brier,
    enactment_rate,
    margin,
    post_score,
    shadow_price,
    standing,
    violation_sign,
    weighted_median,
)
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime import pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

ELIGIBLE = {"seed-observer": "producer", "seed-decider": "producer", "eval-a": "evaluator",
            "eval-b": "evaluator", "antagonist-a": "antagonist", "meta-a": "meta"}


# --- the arithmetic -------------------------------------------------------------------

def test_the_post_score_is_strictly_proper_for_the_mean():
    """Whatever the realized price's distribution, the honest mean alone scores best."""
    lambda_max = 1.0
    for outcomes in ([(0.1, 0.5), (0.9, 0.5)], [(0.0, 0.2), (0.3, 0.3), (1.0, 0.5)],
                     [(0.4, 1.0)]):
        mean = sum(y * p for y, p in outcomes)

        def expected(post, outcomes=outcomes):
            return sum(p * post_score(post, y, lambda_max) for y, p in outcomes)

        best = expected(mean)
        for step in range(101):
            post = step / 100
            if abs(post - mean) > 1e-9:
                assert expected(post) < best
    assert post_score(0.3, 0.3, 1.0) == 1.0 and post_score(0.0, 2.0, 2.0) == 0.0
    for bad in ((1.5, 0.5, 1.0), (0.5, -0.1, 1.0), (0.5, 0.5, 0.0), (float("nan"), 0.1, 1.0)):
        with pytest.raises(ValueError):
            post_score(*bad)


def test_the_aggregate_is_a_standing_weighted_median():
    assert weighted_median([(0.1, 1), (0.5, 1), (0.9, 1)]) == 0.5
    # One seat's extreme post moves the median by at most a rank, never by its size.
    assert weighted_median([(0.1, 1), (0.5, 1), (1.0, 1)]) == 0.5
    assert weighted_median([(0.1, 0.1), (0.5, 0.1), (0.9, 5.0)]) == 0.9
    assert weighted_median([]) is None
    assert standing(None) == 0.5 and standing((3, 3.0)) == pytest.approx(3.5 / 4)
    assert standing((3, 0.0)) == pytest.approx(0.5 / 4)


def test_a_forecast_moves_the_expected_violation_symmetrically_by_at_most_a_step():
    """Cold review: relief at q = 1 used to cancel the whole violation while deepening
    at q = 1 added one step. Both now move it by q * step, in opposite directions."""
    for q in (0.0, 0.3, 1.0):
        relief = branch_violation(0.6, q, -1, 0.1)
        deepen = branch_violation(0.6, q, +1, 0.1)
        assert 0.6 - relief == pytest.approx(deepen - 0.6) == pytest.approx(q * 0.1)
    assert branch_violation(0.05, 1.0, -1, 0.1) == 0.0  # never below zero
    assert violation_sign("max", None, 1.0, 2.0, "increase") == 1
    assert violation_sign("min", 0.9, None, 0.5, "increase") == -1
    assert violation_sign("band", 0.2, 0.8, 0.9, "decrease") == -1
    assert violation_sign("band", 0.2, 0.8, 0.1, "decrease") == 1
    assert brier(0.2, 1) == pytest.approx(0.36) and brier(1, True) == 1.0
    assert enactment_rate(0, 0) == 0.5 and enactment_rate(3, 1) == pytest.approx(4 / 6)


def test_the_shadow_price_is_the_realized_marginal_consequence_or_nothing():
    points = [{"v": 0.0, "consequence": 0.2, "micro_usd": 100},
              {"v": 1.0, "consequence": 0.8, "micro_usd": 300},
              {"v": 1.0, "consequence": 0.8, "micro_usd": 500}]
    row = margin(points)
    assert row["slope"] == pytest.approx(0.6)
    assert row["micro_usd_per_violation"] == pytest.approx(300)
    assert shadow_price(points, 1.0) == pytest.approx(0.6)
    assert shadow_price(points, 0.5) == 0.5  # clipped to lambda_max
    worse = [dict(point, consequence=1 - point["consequence"]) for point in points]
    assert shadow_price(worse, 1.0) == 0.0  # violating costs consequence: no premium
    assert shadow_price(points[:2], 1.0) is None  # too few scopes
    assert shadow_price([dict(point, v=0.5) for point in points], 1.0) is None  # no variance


def test_the_feed_forward_term_has_the_market_s_sign_and_never_waives_the_integral():
    def controller():
        c = PriceController(Ledger(None), eta=0.2, decay=0.1, lambda_max=1.0,
                            min_window_events=1, kp=0.5)
        c.register(CardRegion("c", "max", None, 1.0, 1.0))
        c.observe("c", 1.4, 1)  # violation 0.4: P = 0.2, I = 0.08
        return c

    backward = controller()
    backward.observe("c", 1.4, 2)
    worse, better, relief = controller(), controller(), controller()
    worse.observe("c", 1.4, 2, anticipated=+0.1)
    better.observe("c", 1.4, 2, anticipated=-0.1)
    relief.observe("c", 1.4, 2, anticipated=-5.0)
    base = backward.price("c")
    # A market expecting the violation to grow prices it before it happens ...
    assert worse.price("c") == pytest.approx(base + 0.5 * 0.1)
    # ... one expecting relief eases the price by the same amount ...
    assert better.price("c") == pytest.approx(base - 0.5 * 0.1)
    # ... and never below the accumulated integral while the card still violates.
    integral = relief.snapshot()["cards"]["c"]["integral"]
    assert relief.price("c") == pytest.approx(integral) and integral > 0


def test_no_feed_forward_prices_a_card_inside_its_region():
    """Cold review: F priced an in-region card (lambda 0.0033 at v = 0)."""
    ledger = Ledger(None)
    c = PriceController(ledger, eta=0.2, decay=0.1, lambda_max=1.0, min_window_events=1,
                        kp=0.5)
    c.register(CardRegion("c", "max", None, 1.0, 1.0))
    c.observe("c", 0.5, 1, anticipated=+0.5)
    assert c.price("c") == 0.0
    update = [i for i in ledger._recovery_items() if i["kind"] == "price.update"][-1]
    assert "f" not in update


# --- a running world ----------------------------------------------------------------

def _runtime(monkeypatch, cards=None, kp=0.0):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    manifest = load_manifest("scripted")
    manifest = replace(manifest, prices=replace(manifest.prices, kp=kp))
    if cards is not None:
        manifest = replace(manifest, charter=replace(manifest.charter, cards=cards))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                 ledger_path=None, router_gamma=.1, exchange=FakeExchange(),
                 provider=ScriptedProvider())
    rt._manage_reserve_window()
    monkeypatch.setattr(rt, "_committee_eligible", lambda: dict(ELIGIBLE))
    return rt


def _handle(rt, seat, event="work"):
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id=event, propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


def _return(rt, seat, **outputs):
    handle = _handle(rt, seat)
    rt._apply_registrations(handle, Return(handle, {"action": "hold", **outputs}, 0, "ok"))
    return handle


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _next_window(rt):
    """The price loop falls due: its period is ticks, and the clock moves with them."""
    rt.clock.now_ns = rt.reserve_window_start + rt.tick_clock.interval_ns
    rt.clockwork.force("price", rt.ticks_consumed)
    rt._manage_reserve_window()


def _grade(rt):
    """Grade the policy window once min_ratio consequence periods have passed (T2)."""
    rt.ticks_consumed += rt._policy_floor()
    rt._close_policy_window(rt.window.index)


SCOPED = (
    MetricCard("ok-rate", "truthful commitments", "Well-formed share per seat.", "fraction",
               MetricWindow("returns", 1, "assembly"), {"rule": "at least", "lo": 0.9},
               "well_formed_rate", "all"),
)
#: One well-formed seat and two malformed ones, and what the world measured of each.
WORLD = {"eval-a": (True, 0.2), "eval-b": (False, 0.8), "eval-c": (False, 0.8)}


def _scoped_window(rt):
    """A window in which the malformed seats' decisions paid off better: violating paid."""
    for seat, (ok, consequence) in WORLD.items():
        handle = _handle(rt, seat, f"work-{seat}")
        rt.card_samples.returned(handle=handle, assembly=seat, role="evaluator",
                                 window=rt.window.index,
                                 ret=Return(handle, {}, 1, "ok" if ok else "failed"))
        sample = rt._contribution(handle, "evaluator")
        sample.update(invocations=1, ok=int(ok), cost=100)
        # A measured outcome of the world, never a judgement's consequence score: that
        # is not priced through lambda (wave 16, section 9).
        rt.world_outcomes[handle] = {"state": "measured", "y": consequence,
                                     "kind": "return_paid_off", "tick": rt.ticks_consumed,
                                     "ns": rt.clock.now_ns}


def _to_margin(rt, posted_window):
    while posted_window in rt.margin_windows or posted_window >= rt.window.index:
        _next_window(rt)


def test_a_post_is_scored_on_the_realized_shadow_price_not_on_the_committee_s_lambda(
        monkeypatch):
    """Architect's ruling: a post is scored against the window's realized marginal
    consequence per unit of violation. Posting the current lambda, or zero, no longer
    scores near 1 when that slope differs; adopting a post by motion cannot make it
    come true."""
    rt = _runtime(monkeypatch, SCOPED)
    card = "ok-rate"
    rt.controller.set_price(card, 0.1, amendment_id="test")
    window = rt.window.index
    _return(rt, "eval-a", shadow_prices={card: 0.1})  # the committee's own lambda
    _return(rt, "antagonist-a", shadow_prices={card: 0.0})
    _return(rt, "seed-decider", shadow_prices={card: 0.6})
    posts = _items(rt, "lambda_post.posted")
    assert rt._posted_lambda(card)["lambda"] == 0.1
    row = next(r for r in rt._world_block()["card_prices"] if r["card_id"] == card)
    assert row["posted"]["lambda"] == 0.1 and "lambda" in row
    _scoped_window(rt)
    # The committee adopts a post; the price law moves, the target does not.
    rt.controller.set_price(card, 0.9, amendment_id="adopt-posted")
    _to_margin(rt, window)
    settled = {e["posted"]: e for e in _items(rt, "lambda_post.settled")}
    assert all(e["realized"] == pytest.approx(0.6) for e in settled.values())
    assert settled[0.1]["score"] == pytest.approx(1 - 0.5 ** 2)
    assert settled[0.0]["score"] == pytest.approx(1 - 0.6 ** 2)
    assert settled[0.6]["score"] == pytest.approx(1.0)
    margin_row, = [e for e in _items(rt, "price.margin") if e["window"] == window]
    assert margin_row["slope"] == pytest.approx(0.6) and len(margin_row["points"]) == 3
    # The score reaches each poster's durable identity on the policy channel.
    post = posts[0]["handle"]
    history = rt.queue.history(post)
    assert history[-1].definition_version == LAMBDA_POST_DEFINITION
    assert history[-1].score == pytest.approx(settled[0.1]["score"])
    assert rt.queue.get(post).actor == "assembly:eval-a"
    assert rt.lambda_standing["eval-a"][0] == 1
    # The same margins are the window's lambda-to-dollar statistic, in public.
    published = next(r for r in rt._world_block()["card_prices"] if r["card_id"] == card)
    assert published["last_window_margin"]["marginal_consequence"] == pytest.approx(0.6)
    assert published["last_window_margin"]["micro_usd_per_violation"] == pytest.approx(0.0)
    assert "lambda_dollars" in rt._mechanics_block()["committee"]
    assert "card_contract" in rt._mechanics_block()["committee"]


def test_a_post_on_an_unidentified_window_is_censored_never_scored_on_a_default(monkeypatch):
    rt = _runtime(monkeypatch)  # the seed cards: no scope has a measured consequence
    window = rt.window.index
    _return(rt, "eval-a", shadow_prices={"well_formed_rate": 0.3})
    _to_margin(rt, window)
    settled, = _items(rt, "lambda_post.settled")
    assert settled["status"] == "censored" and settled["realized"] is None
    assert rt.queue.history(settled["handle"])[-1].status is SettleStatus.CENSORED
    assert "eval-a" not in rt.lambda_standing


@pytest.mark.parametrize(("prices", "reason"), [
    ({"no-such-card": 0.2}, "not priced now"),
    ({"well_formed_rate": 1.5}, "lambda must be"),
    ({"well_formed_rate": True}, "lambda must be"),
    ([0.2], "must map"),
])
def test_an_invalid_post_is_refused_with_its_reason(monkeypatch, prices, reason):
    rt = _runtime(monkeypatch)
    _return(rt, "eval-a", shadow_prices=prices)
    refused, = _items(rt, "lambda_post.refused")
    assert reason in refused["reason"] and rt.lambda_posts == []


def test_one_post_per_seat_card_and_window(monkeypatch):
    rt = _runtime(monkeypatch)
    _return(rt, "eval-a", shadow_prices={"well_formed_rate": 0.2})
    _return(rt, "eval-a", shadow_prices={"well_formed_rate": 0.4})
    assert len(rt.lambda_posts) == 1
    assert "one post per seat" in _items(rt, "lambda_post.refused")[0]["reason"]


def test_a_lambda_motion_adopts_the_posted_price(monkeypatch):
    rt = _runtime(monkeypatch)
    _return(rt, "eval-a", shadow_prices={"well_formed_rate": 0.35})
    handle = _handle(rt, "seed-decider", "motion")
    rt._propose_amendment(handle, {
        "kind": "amendment", "id": "adopt-posted", "lambda": {"well_formed_rate": "posted"},
        "predicted_effect": {"card_id": "well_formed_rate", "direction": "increase",
                             "window": 1}})
    motion, = rt.charter_book.agenda()
    assert motion.proposed_prices == (("well_formed_rate", 0.35),)
    adopted, = _items(rt, "lambda_post.adopted")
    assert adopted["lambda"] == 0.35 and adopted["posts"] == 1
    with pytest.raises(ValueError, match="no posted lambda"):
        rt._propose_amendment(handle, {
            "kind": "amendment", "id": "adopt-none", "lambda": {"forecast_skill": "posted"},
            "predicted_effect": {"card_id": "forecast_skill", "direction": "increase",
                                 "window": 1}})


# --- futarchy on motions --------------------------------------------------------------

SMALL = (
    MetricCard("ok-rate", "truthful commitments", "Well-formed share.", "fraction",
               MetricWindow("returns", 1, None), {"rule": "at least", "lo": 0.9},
               "well_formed_rate", "all"),
    MetricCard("spend", "care with scarce resources", "Cost per return.", "micro-USD",
               MetricWindow("returns", 1, None), {"rule": "at most", "hi": 1000},
               "cost_per_return", "all"),
)


def _motion(rt, direction="increase"):
    rt._propose_amendment(_handle(rt, "seed-decider", "motion"), {
        "kind": "amendment", "id": "drop-spend", "remove": ["spend"],
        "predicted_effect": {"card_id": "ok-rate", "direction": direction, "window": 1}})


def _sample(rt, ok):
    handle = _handle(rt, "seed-observer", "sample")
    rt.card_samples.returned(handle=handle, assembly="seed-observer", role="producer",
                             window=rt.window.index,
                             ret=Return(handle, {}, 1, "ok" if ok else "failed"))


def _decide(rt, monkeypatch, vote):
    """Hold the next governance boundary with every seat voting ``vote``."""
    def ballot(assembly_id, req, role, *, child=False):
        return Return(req.handle, {"vote": vote, "reason": "scripted"}, 0, "ok")

    monkeypatch.setattr(rt, "_invoke", ballot)
    rt.clock.now_ns = rt.cadence.earliest_ns(rt.tick_clock)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    monkeypatch.undo()


def test_both_branches_are_forecast_and_the_reject_branch_is_graded(monkeypatch):
    """P1: a failed motion's no votes and its reject-branch forecasts are scored."""
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt)
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 0.2},
        {"motion": "drop-spend", "branch": "enact", "q": 0.9}])
    market = rt._motion_market("drop-spend")
    assert market == {"enact": {"forecasts": 1, "mean_q": 0.9},
                      "reject": {"forecasts": 1, "mean_q": 0.2}}
    _sample(rt, ok=False)  # the unchanged charter's measurement at the decision: 0.0
    _decide(rt, monkeypatch, vote=False)
    seating, = _items(rt, "charter.seat")
    assert rt.charter.edition == 1 and _items(rt, "policy.rejected")
    # The enact branch did not happen: its forecast is void, and nothing grades it.
    void, = _items(rt, "policy.void")
    assert void["branch"] == "enact" and void["decided"] == "reject"
    assert rt.queue.history(void["handle"])[-1].status is SettleStatus.CENSORED
    assert {v["branch"] for v in rt.pending_votes} == {"reject"}
    _sample(rt, ok=True)  # the unchanged charter improved anyway: 1.0
    _grade(rt)
    outcomes = _items(rt, "policy.outcome")
    forecast = next(o for o in outcomes if o["forecast"])
    assert forecast["branch"] == "reject" and forecast["y"] is True
    assert forecast["score"] == pytest.approx(0.36)
    handle = forecast["handle"]
    assert rt.queue.history(handle)[-1].definition_version == MOTION_FORECAST_DEFINITION
    ballots = [o for o in outcomes if not o["forecast"]]
    assert ballots and all(o["branch"] == "reject" for o in ballots)
    # A no vote said the motion makes no difference; the charter moved without it.
    assert all(o["q"] == 1.0 and o["score"] == 1.0 for o in ballots)


def test_a_reflexive_no_loses_when_the_unchanged_charter_does_not_move(monkeypatch):
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt)
    _sample(rt, ok=False)
    _decide(rt, monkeypatch, vote=False)
    _sample(rt, ok=False)  # without the motion, nothing improved
    _grade(rt)
    ballots = _items(rt, "policy.outcome")
    assert ballots and all(o["y"] is False and o["score"] == 0.0 for o in ballots)


def test_an_enacted_motion_grades_its_enact_forecasts_and_voids_the_reject_ones(monkeypatch):
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt)
    _return(rt, "antagonist-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "enact", "q": 0.75},
        {"motion": "drop-spend", "branch": "reject", "q": 0.1}])
    _sample(rt, ok=False)
    _decide(rt, monkeypatch, vote=True)
    assert rt.charter.edition == 2
    void, = _items(rt, "policy.void")
    assert void["branch"] == "reject" and void["decided"] == "enact"
    _sample(rt, ok=True)
    _grade(rt)
    forecast = next(o for o in _items(rt, "policy.outcome") if o["forecast"])
    assert forecast["branch"] == "enact" and forecast["score"] == pytest.approx(1 - 0.25 ** 2)
    ballots = [o for o in _items(rt, "policy.outcome") if not o["forecast"]]
    assert ballots and all(o["q"] == 1.0 and o["score"] == 1.0 for o in ballots)


@pytest.mark.parametrize(("forecast", "reason"), [
    ({"motion": "nope", "branch": "enact", "q": 0.5}, "undecided on the governance agenda"),
    ({"motion": "drop-spend", "branch": "maybe", "q": 0.5}, "enact or reject"),
    ({"motion": "drop-spend", "branch": "enact", "q": 1.2}, "probability"),
    ({"motion": "drop-spend", "branch": "enact"}, "exactly"),
])
def test_an_invalid_forecast_is_refused(monkeypatch, forecast, reason):
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt)
    _return(rt, "eval-a", motion_forecasts=[forecast])
    refused, = _items(rt, "motion_forecast.refused")
    assert reason in refused["reason"]


def test_a_forecast_on_one_branch_moves_nothing_risk_free(monkeypatch):
    """Cold review: a reject-branch relief forecast lowered lambda and was voided free
    when the motion passed. An undecided motion's forecasts count only as a pair."""
    rt = _runtime(monkeypatch, SMALL, kp=0.5)
    _motion(rt, direction="increase")  # toward "at least 0.9": relief
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 1.0}])
    assert rt._anticipated_violation("ok-rate", 0.45) is None
    _sample(rt, ok=False)
    rt.controller.set_price("ok-rate", 0.5, amendment_id="test")
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert "f" not in update


def test_a_pair_of_forecasts_feeds_forward_weighted_by_the_enactment_rate(monkeypatch):
    rt = _runtime(monkeypatch, SMALL, kp=0.5)
    _motion(rt, direction="increase")
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 0.2},
        {"motion": "drop-spend", "branch": "enact", "q": 1.0}])
    region = rt.regions["ok-rate"]
    step = rt._resolution_step("ok-rate")
    now = (region.lo - 0.45) / region.scale
    p = enactment_rate(0, 0)
    expected = p * (now - 1.0 * step) + (1 - p) * (now - 0.2 * step)
    assert rt._anticipated_violation("ok-rate", 0.45) == pytest.approx(expected - now)
    # Inside the region nothing is fed forward.
    assert rt._anticipated_violation("ok-rate", 0.95) is None
    _sample(rt, ok=False)
    rt.controller.set_price("ok-rate", 0.5, amendment_id="test")
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert update["f"] == pytest.approx(0.5 * (expected - now)) and update["f"] < 0
    assert abs(update["f"]) <= 0.5 * step
    assert update["lambda_after"] == pytest.approx(
        update["p"] + update["i"] + update["d"] + update["f"])


def test_once_decided_only_the_branch_taken_feeds_forward(monkeypatch):
    rt = _runtime(monkeypatch, SMALL, kp=0.5)
    _motion(rt, direction="decrease")  # deepens the violation of "at least 0.9"
    _return(rt, "antagonist-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "enact", "q": 1.0}])
    _sample(rt, ok=False)
    _decide(rt, monkeypatch, vote=True)
    step = rt._resolution_step("ok-rate")
    assert rt._anticipated_violation("ok-rate", 0.45) == pytest.approx(step)


def test_without_a_proportional_gain_the_law_stays_backward(monkeypatch):
    """F uses the committed kp: a world that set kp = 0 prices only what happened."""
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt, direction="decrease")
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 1.0},
        {"motion": "drop-spend", "branch": "enact", "q": 1.0}])
    _sample(rt, ok=False)
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert update["f"] == 0


def test_posts_and_forecasts_survive_a_checkpoint_and_an_older_one_has_none(monkeypatch):
    """Resume: the markets' state is checkpointed, and absent from an older checkpoint it
    starts empty; a ballot frozen before branches existed is graded as enacted."""
    from factorylab.runtime import resume

    rt = _runtime(monkeypatch, SMALL)
    _motion(rt)
    _return(rt, "eval-a", shadow_prices={"ok-rate": 0.4},
            motion_forecasts=[{"motion": "drop-spend", "branch": "reject", "q": 0.3}])
    state = resume.runtime_state(rt)
    twin = Runtime(rt.m, ledger_path=None, **state["config"])
    resume.restore_runtime(twin, state)
    assert twin.lambda_posts == rt.lambda_posts and twin._posted_lambda("ok-rate")
    assert twin.pending_votes == rt.pending_votes
    older = resume.runtime_state(rt)
    older["runtime"]["$map"] = [pair for pair in older["runtime"]["$map"]
                                if pair[0] not in ("lambda_posts", "lambda_standing")]
    twin = Runtime(rt.m, ledger_path=None, **older["config"])
    resume.restore_runtime(twin, older)
    assert twin.lambda_posts == [] and twin.lambda_standing == {}
    ballot = {"vote": True}
    assert twin._branch_probability(ballot, ballot.get("branch", "enact")) == 1.0


def test_a_late_settlement_on_a_removed_card_is_priced_on_its_own_window(monkeypatch):
    """Codex review of #131: a settlement arriving after an amendment removed its card
    crashed the dollar statistic. The margins read each window's own frozen cards and
    prices, and a removed card's controller is never asked."""
    rt = _runtime(monkeypatch, SMALL)
    rt.controller.set_price("spend", 0.3, amendment_id="test")
    window = rt.window.index
    _return(rt, "eval-a", shadow_prices={"spend": 0.2})
    handle = _handle(rt, "seed-observer", "late")
    rt._contribution(handle, "producer").update(invocations=1, ok=1, cost=5000)
    rt.card_samples.returned(handle=handle, assembly="seed-observer", role="producer",
                             window=window, ret=Return(handle, {}, 5000, "ok"))
    _next_window(rt)  # the window closes with "spend" priced
    frozen = rt.price_windows[window].closed_prices["spend"]
    # An amendment removes the card: a new edition without it, and the card unpriced.
    from factorylab.charter.charter import Charter

    rt.charter = Charter(rt.charter.edition + 1, rt.charter.norms, rt.charter.cards[:1])
    rt._drop_cards({"ok-rate"}, "remove-spend")
    assert "spend" not in rt.priced
    asked = []
    price = rt.controller.price
    monkeypatch.setattr(rt.controller, "price",
                        lambda card_id: asked.append(card_id) or price(card_id))
    # The late settlement is priced on the window it worked in, by that window's card.
    rt._settle_priced(handle, channel="verdict", score=0.9, definition_version="t",
                      sampling_ref=None, cards="producer")
    term = next(t for t in _items(rt, "price.penalty")[-1]["terms"] if t["card_id"] == "spend")
    assert term["window"] == window and term["lambda"] == frozen
    _to_margin(rt, window)
    margin_row = next(e for e in _items(rt, "price.margin")
                      if e["window"] == window and e["card_id"] == "spend")
    assert margin_row["lambda"] == frozen
    settled, = _items(rt, "lambda_post.settled")
    assert settled["card_id"] == "spend" and settled["status"] == "censored"
    assert "spend" not in asked
