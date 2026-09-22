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
    brier,
    expected_violation,
    post_score,
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


def test_the_expected_violation_reads_each_forecast_at_its_two_points():
    # Relief: a kept promise relieves the violation, a broken one leaves it.
    assert expected_violation(0.6, [(0.75, -1)], 0.1) == pytest.approx(0.15)
    # Deepening: a kept promise adds at least one resolution step.
    assert expected_violation(0.0, [(0.5, +1)], 0.1) == pytest.approx(0.05)
    assert expected_violation(0.4, [], 0.1) == 0.4
    assert violation_sign("max", None, 1.0, 2.0, "increase") == 1
    assert violation_sign("min", 0.9, None, 0.5, "increase") == -1
    assert violation_sign("band", 0.2, 0.8, 0.9, "decrease") == -1
    assert violation_sign("band", 0.2, 0.8, 0.1, "decrease") == 1
    assert brier(0.2, 1) == pytest.approx(0.36) and brier(1, True) == 1.0


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
    worse.observe("c", 1.4, 2, anticipated=+0.2)
    better.observe("c", 1.4, 2, anticipated=-0.2)
    relief.observe("c", 1.4, 2, anticipated=-5.0)
    base = backward.price("c")
    # A market expecting the violation to grow prices it before it happens ...
    assert worse.price("c") == pytest.approx(base + 0.5 * 0.2)
    # ... one expecting relief eases the price ...
    assert better.price("c") == pytest.approx(base - 0.5 * 0.2)
    # ... and never below the accumulated integral while the card still violates.
    integral = relief.snapshot()["cards"]["c"]["integral"]
    assert relief.price("c") == pytest.approx(integral) and integral > 0
    update = [i for i in relief._PriceController__ledger._recovery_items()
              if i["kind"] == "price.update"][-1]
    assert update["f"] == pytest.approx(-0.5 * 0.4) and update["anticipated"] == -5.0


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
    rt.clock.now_ns = rt.reserve_window_start + rt.m.novelty.window_ns
    rt._manage_reserve_window()


def test_a_seat_posts_lambda_through_its_return_and_is_scored_on_the_realized_price(
        monkeypatch):
    rt = _runtime(monkeypatch)
    card = "well_formed_rate"
    _return(rt, "eval-a", shadow_prices={card: 0.3})
    _return(rt, "antagonist-a", shadow_prices={card: 0.9})
    _return(rt, "seed-decider", shadow_prices={card: 0.5})
    posts = _items(rt, "lambda_post.posted")
    assert [p["lambda"] for p in posts] == [0.3, 0.9, 0.5]
    assert rt._posted_lambda(card) == {"lambda": 0.5, "posts": 3,
                                       "windows": [rt.window.index] * 2}
    # The committee's agenda and world.card_prices carry it beside the controller's.
    row = next(r for r in rt._world_block()["card_prices"] if r["card_id"] == card)
    assert row["posted"]["lambda"] == 0.5 and "lambda" in row
    stats = next(r for r in rt._card_statistics() if r["card_id"] == card)
    assert stats["posted"]["posts"] == 3
    horizon = rt.m.timing.min_ratio
    for _ in range(horizon):
        _next_window(rt)
    settled = _items(rt, "lambda_post.settled")
    assert len(settled) == 3 and rt.lambda_posts == []
    realized = settled[0]["realized"]
    assert realized == rt.controller.price(card)
    for row in settled:
        assert row["score"] == pytest.approx(1 - (row["posted"] - realized) ** 2)
    # The score reaches each poster's durable identity on the policy channel.
    post = posts[0]["handle"]
    history = rt.queue.history(post)
    assert history[-1].definition_version == LAMBDA_POST_DEFINITION
    assert history[-1].score == pytest.approx(settled[0]["score"])
    assert rt.queue.get(post).actor == "assembly:eval-a"
    assert rt.lambda_standing["eval-a"][0] == 1


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
    rt._close_policy_window(rt.window.index)
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
    rt._close_policy_window(rt.window.index)
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
    rt._close_policy_window(rt.window.index)
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


def test_open_forecasts_feed_forward_into_the_card_they_name(monkeypatch):
    """The market expects relief: the card's price eases before the measurement does."""
    rt = _runtime(monkeypatch, SMALL, kp=0.5)
    _motion(rt, direction="increase")  # toward "at least 0.9": relief
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 0.8}])
    region = rt.regions["ok-rate"]
    now = (region.lo - 0.45) / region.scale
    assert rt._anticipated_violation("ok-rate", 0.45) == pytest.approx(-0.8 * now)
    # An enact-branch forecast is not the branch in force while the motion is undecided.
    assert rt._anticipated_violation("spend", 5000.0) is None
    _sample(rt, ok=False)
    rt.controller.set_price("ok-rate", 0.5, amendment_id="test")
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert update["f"] < 0 and update["anticipated"] < 0
    assert update["lambda_after"] == pytest.approx(
        update["p"] + update["i"] + update["d"] + update["f"])


def test_a_forecast_deepening_a_compliant_card_raises_its_price_early(monkeypatch):
    rt = _runtime(monkeypatch, SMALL, kp=0.5)
    _motion(rt, direction="decrease")  # away from "at least 0.9"
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 1.0}])
    _sample(rt, ok=True)  # inside the region: no violation yet
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert update["violation"] == 0 and update["f"] > 0 and update["lambda_after"] > 0


def test_without_a_proportional_gain_the_law_stays_backward(monkeypatch):
    """F uses the committed kp: a world that set kp = 0 prices only what happened."""
    rt = _runtime(monkeypatch, SMALL)
    _motion(rt, direction="decrease")
    _return(rt, "eval-a", motion_forecasts=[
        {"motion": "drop-spend", "branch": "reject", "q": 1.0}])
    _sample(rt, ok=True)
    rt._close_price_window()
    update = [i for i in _items(rt, "price.update") if i["card_id"] == "ok-rate"][-1]
    assert update["f"] == 0 and update["lambda_after"] == 0


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
