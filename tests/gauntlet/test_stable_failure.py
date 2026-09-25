"""Stable failure (design §3.1): detected, priced by its duration, never broken by force.

Chapter II §II.a: stable failure is "a near-invariant region (a robust version with a
wide spectral gap) whose input–output distribution is failing against its input".
§II.b: "price the duration of failure, ratcheting up penalties the longer the
factory spends in a wide-spectral-gap attractor"; §IV.b: "the duration of a failure
state needs to ratchet up the available gain … However, gain ramped high enough …
will, if unchecked, overshoot into an oscillation condition (thrash)".

SF-1 is the longrun1 shape: no seat can relieve the card, so the physics must price
and escalate, and must not break the attractor for them. Each physics response has a
negative control that disables it and must fail its criterion (design G2).
"""

import pytest

from factorylab.charter.controller import PriceController
from scripts import gauntlet as g
from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate

W16 = "needs wave 16"
UPTAKE = P.UPTAKE["id"]


@pytest.fixture(scope="module")
def sf1(shared_run):
    return shared_run("sf1", lambda: P.run(*P.sf1(), events=300))


def _transient_world():
    """SF-1 with one transient resolution: hold-a registers observations for four windows."""
    return P.sf1(hold_a=P.relieving_in(range(30, 34), P.hold, "relief"))


@pytest.fixture(scope="module")
def transient(shared_run):
    return shared_run("sf1-transient", lambda: P.run(*_transient_world(), events=300))


# --- SF-1: unrelievable failure --------------------------------------------------------


def test_sf1a_stable_failure_is_detected_within_the_horizon(sf1):
    result = g.sf1a_detection(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


def test_sf1b_the_card_is_ratcheted_by_duration_on_the_organs_loop(sf1):
    result = g.sf1b_ratchet_cadence(sf1.events, sf1.manifest)
    assert result.ok, result.evidence
    durations = [r["duration"] for r in sf1.rows("immune.price_ratchet")
                 if r["card_id"] == UPTAKE]
    assert max(durations) >= sf1.physics.r  # the duration really accrued


def _ratchet_resets_duration(original):
    """A mutant: every ratchet first forgets the attractor's duration (Astra M-6)."""
    def ratchet(self, card_id, *, window, step):
        self.end_failure(card_id, window=window)
        original(self, card_id, window=window, step=step)
    return ratchet


def test_sf1b_negative_control_a_duration_reset_no_op_fails():
    manifest, population = P.sf1()
    mutant = P.run(manifest, population, events=300, patches=[
        (PriceController, "ratchet", _ratchet_resets_duration(PriceController.ratchet))])
    result = g.sf1b_ratchet_cadence(mutant.events, mutant.manifest)
    assert result.status == g.FAIL
    assert any("duration_reset" in problem for problem in result.evidence["problems"])


def test_sf1b_negative_control_a_ratchet_that_never_fires_is_not_a_pass():
    manifest, population = P.sf1()
    mutant = P.run(manifest, population, events=300,
                   patches=[(PriceController, "ratchet", lambda self, *a, **k: None)])
    assert g.sf1b_ratchet_cadence(mutant.events, mutant.manifest).status != g.PASS


def test_sf1b_a_transient_resolution_resets_the_duration(transient):
    """Astra M-6's control: when the card is briefly satisfied the flag clears at an
    acting window, the duration ends, and the next ratchet starts again from one."""
    assert transient.rows("immune.price_ratchet_ended")
    result = g.sf1b_ratchet_cadence(transient.events, transient.manifest)
    assert result.ok, result.evidence


def test_sf1b_negative_control_without_the_reset_the_transient_world_fails():
    mutant = P.run(*_transient_world(), events=300, patches=[
        (PriceController, "end_failure", lambda self, card_id, *, window: None)])
    result = g.sf1b_ratchet_cadence(mutant.events, mutant.manifest)
    assert result.status == g.FAIL
    assert any("missed_reset" in problem for problem in result.evidence["problems"])


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} R-E (amended): the integrator freezes while "
                   "the penalty sits at penalty_cap (anti-windup)")
def test_sf1c_the_integral_is_frozen_while_the_penalty_sits_at_the_cap(sf1):
    result = g.sf1c_anti_windup(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D5/R-E: saturation is ledgered and published "
                   "to governance as the card's shadow price at bound")
def test_sf1d_saturation_is_escalated_with_a_rising_duration(sf1):
    result = g.sf1d_escalation(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


def test_sf1e_gain_rises_to_its_bound_and_holds_while_flagged(sf1):
    result = g.sf1e_gain(sf1.events, sf1.manifest)
    assert result.ok, result.evidence
    assert sf1.rows("immune.gain")


def test_sf1f_the_registration_route_stays_open_and_the_reserve_accrues(sf1):
    result = g.sf1f_route_open(sf1.events, sf1.manifest)
    assert result.ok, result.evidence


def test_sf1_the_physics_prices_and_never_steers(sf1):
    """S1–S8. No order, registration or amendment without a seat's return (none here: no
    arm ever registers); no diagnosis in any request; no price above the cap; abstention
    and decline credited alike; the organ writes only its own kinds; gain is uniform."""
    readings = P.assert_prices_not_steers(sf1)
    assert readings["S6"].ok and readings["S8-instrumented"].ok and readings["S2"].ok
    assert not sf1.rows("registry.register", "order.intent")  # nothing acted for a seat


def test_sf1_s3_an_action_label_moves_no_penalty():
    """S3, metamorphic: the same population with hold-b's final answer relabelled
    ("investigate" → "defer") settles every decision with the same penalty terms."""
    base = P.run(*P.sf1(record=False), events=150)
    twin = P.run(*P.sf1(hold_b=P.relabelled("defer", P.investigate), record=False),
                 events=150)
    one, two = g.penalty_by_handle(base.events), g.penalty_by_handle(twin.events)
    assert one.keys() == two.keys() and one
    assert all((one[h]["penalty"], one[h]["terms"]) == (two[h]["penalty"], two[h]["terms"])
               for h in one)


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D4: after a router's first settled round an "
                   "abstention is credited its observed mean, never the 0.5 prior (I-2b)")
def test_sf1_s5b_nothing_delivered_is_credited_the_observed_mean(sf1):
    result = g.s5b_observed_neutral(sf1.events, sf1.manifest)
    assert result.ok, result.evidence


# --- SF-2 and SF-3: the lever and its counter-case (wave 16 D5) ------------------------


def _sf2(delta):
    """SF-2: a reliever registers an accepted observation on every return; its judges
    give 0.5 − δ (a world-measured cost, stood in by the judges); a holder holds at 0.5."""
    reliever = P.producer("reliever", P.relieving_in(range(0, 10**6), P.hold, "relief"))
    holder = P.producer("holder", P.hold)

    def judged(view):
        seat = ((view.inputs.get("producer") or {}).get("outputs") or {})
        relieved = "register" in seat
        return {"verdict": 0.5 - delta if relieved else 0.5, "rationale": "scripted"}
    seats = [reliever, holder, *(P.judge(f"judge-{i}", judged) for i in range(4)),
             *(P.meta(f"meta-{i}", P.conformity(0.8)) for i in range(2))]
    return P.world(seats, cards=[P.UPTAKE, P.WELL_FORMED]), P.Population(seats)


@pytest.fixture(scope="module")
def sf2_low(shared_run):
    return shared_run("sf2-low", lambda: P.run(*_sf2(0.1), events=400))


def test_sf2_no_force_the_kernel_never_draws_for_the_reliever(sf2_low):
    """SF-2d's physics half, which holds today: every draw is the router's own sample, and
    every registration traces to the reliever's return."""
    readings = P.assert_prices_not_steers(sf2_low)
    assert readings["S1"].ok and readings["S1"].evidence["acts"] > 0


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D5: a reliever bears 0 and every non-reliever "
                   "an equal share frozen at close; today every decision bears 1/n by "
                   "settlement order, so Δ(t) ≡ 0 (the negative control is today's code)")
def test_sf2a_the_price_gradient_follows_relief(sf2_low):
    result = g.sf2_gradient(sf2_low.events, sf2_low.manifest, card=UPTAKE,
                            relievers={"reliever"}, holders={"holder"})
    assert result.ok, result.evidence


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D5: n_nonrelieving is frozen at the window's "
                   "close, so shares do not depend on settlement order")
def test_sf2b_shares_are_blind_to_settlement_order(sf2_low):
    result = g.sf2b_order_blind(sf2_low.events, sf2_low.manifest, card=UPTAKE)
    assert result.ok, result.evidence


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D5: without relief attribution the holder and "
                   "the reliever bear the same penalty, so no price gradient can reach the "
                   "Tick router's estimate (SF-2c lever; SF-3 in one seat's learner)")
def test_sf2c_the_lever_the_routers_estimate_follows_the_price(sf2_low):
    """Within T_learn(Δ − δ) rounds of Δ(t) > δ the Tick router's estimated reward for
    the reliever exceeds the holder's. Read here as the attributed penalty gap: the
    reliever must bear strictly less than the holder in a violated window."""
    seats = g.decision_seats(sf2_low.events)
    gaps = []
    for handle, row in g.penalty_by_handle(sf2_low.events).items():
        gaps.append((seats.get(handle), row["penalty"]))
    reliever = [p for s, p in gaps if s == "reliever"]
    holder = [p for s, p in gaps if s == "holder"]
    assert reliever and holder and max(reliever) < min(p for p in holder if p > 0)
