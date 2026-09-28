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

UPTAKE = P.UPTAKE["id"]


@pytest.fixture(scope="module")
def sf1(shared_run):
    # §II.b: retain two organ opportunities after the Tick gain reaches its cap.
    return shared_run("sf1", lambda: P.run(*P.sf1(), events=228))


def _transient_world():
    """SF-1 with one transient resolution: hold-a registers observations for twenty
    windows. (Four sufficed while the price loop ran away and its windows grew to
    hundreds of ticks; at the steady cadence a window is four ticks, and the organ's
    horizon needs the relief to outlast it before the flag clears, wave 16b.)"""
    return P.sf1(hold_a=P.relieving_in(range(30, 50), P.hold, "relief"))


@pytest.fixture(scope="module")
def transient(shared_run):
    return shared_run("sf1-transient", lambda: P.run(*_transient_world(), events=250))


# --- SF-1: unrelievable failure --------------------------------------------------------


def test_sf1a_stable_failure_is_detected_within_the_horizon(sf1):
    result = g.sf1a_detection(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


def test_sf1b_the_card_is_ratcheted_by_duration_on_the_organs_loop(sf1):
    result = g.sf1b_ratchet_cadence(sf1.events, sf1.manifest)
    assert result.ok, result.evidence
    # At the card's own bound the ratchet is ledgered saturated, its duration counting
    # on (R-E, R10-e): both kinds are the one ratchet.
    durations = [r["duration"] for r in sf1.rows("immune.price_ratchet")
                 + sf1.rows("immune.price_ratchet_saturated") if r["card_id"] == UPTAKE]
    assert max(durations) >= sf1.physics.r  # the duration really accrued


def _ratchet_resets_duration(original):
    """A mutant: every ratchet first forgets the attractor's duration (Astra M-6)."""
    def ratchet(self, card_id, *, window, step):
        self.end_failure(card_id, window=window)
        original(self, card_id, window=window, step=step)
    return ratchet


def test_sf1b_negative_control_a_duration_reset_no_op_fails():
    manifest, population = P.sf1()
    mutant = P.run(manifest, population, events=228, patches=[
        (PriceController, "ratchet", _ratchet_resets_duration(PriceController.ratchet))])
    result = g.sf1b_ratchet_cadence(mutant.events, mutant.manifest)
    assert result.status == g.FAIL
    assert any("duration_reset" in problem for problem in result.evidence["problems"])


def test_sf1b_negative_control_a_ratchet_that_never_fires_is_not_a_pass():
    manifest, population = P.sf1()
    mutant = P.run(manifest, population, events=228,
                   patches=[(PriceController, "ratchet", lambda self, *a, **k: None)])
    assert g.sf1b_ratchet_cadence(mutant.events, mutant.manifest).status != g.PASS


def test_sf1b_a_transient_resolution_resets_the_duration(transient):
    """Astra M-6's control: when the card is briefly satisfied the flag clears at an
    acting window, the duration ends, and the next ratchet starts again from one."""
    ended = [r for r in transient.rows("immune.price_ratchet_ended")
             if r["card_id"] == UPTAKE]
    assert ended
    assert any(r["card_id"] == UPTAKE and r["duration"] == 1
               and r["window"] > ended[0]["window"]
               for r in transient.rows("immune.price_ratchet"))
    result = g.sf1b_ratchet_cadence(transient.events, transient.manifest)
    assert result.ok, result.evidence


def test_sf1b_negative_control_without_the_reset_the_transient_world_fails():
    mutant = P.run(*_transient_world(), events=250, patches=[
        (PriceController, "end_failure", lambda self, card_id, *, window: None)])
    result = g.sf1b_ratchet_cadence(mutant.events, mutant.manifest)
    assert result.status == g.FAIL
    assert any("missed_reset" in problem for problem in result.evidence["problems"])


def test_sf1c_the_integral_is_frozen_while_the_penalty_sits_at_the_cap(sf1):
    result = g.sf1c_anti_windup(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


def test_sf1d_saturation_is_escalated_with_a_rising_duration(sf1):
    result = g.sf1d_escalation(sf1.events, sf1.manifest, card=UPTAKE)
    assert result.ok, result.evidence


# The Tick router's loop counts every round it learned, from its opening to its first
# terminal tick (R16b-2; Astra on #157): a scored round deferred to its window's close
# is sampled at score ready, no longer lost at the close, so the loop is short enough
# that its gain bound lies within the shortened world.
def test_sf1e_gain_rises_to_its_bound_and_holds_while_flagged(sf1):
    """No router unwound while flagged or missed a bound the run covered, and the Tick
    router, whose own loop is the organ's, reached gamma_max. The judges' routers step on
    a 12-window loop, so their bounds (1 + 9 × 12 windows) lie beyond this world:
    SF-1e reads them as unsupported, never as a pass."""
    result = g.sf1e_gain(sf1.events, sf1.manifest)
    assert result.status != g.FAIL, result.evidence
    assert result.evidence["reached"]["router:Tick"]["window"] is not None
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
    base = P.run(*P.sf1(record=False), events=75)
    twin = P.run(*P.sf1(hold_b=P.relabelled("defer", P.investigate), record=False),
                 events=75)
    one, two = g.penalty_by_handle(base.events), g.penalty_by_handle(twin.events)
    assert one.keys() == two.keys() and one
    assert all((one[h]["penalty"], one[h]["terms"]) == (two[h]["penalty"], two[h]["terms"])
               for h in one)


def test_s5b_nothing_delivered_is_credited_the_observed_mean(sf2_low):
    """Read in SF-2's world, whose producers' verdicts (0.5 and 0.5 − δ) do not average
    0.5: in SF-1 every verdict is 0.5, so the prior and the observed mean coincide and
    the world cannot tell them apart."""
    result = g.s5b_observed_neutral(sf2_low.events, sf2_low.manifest)
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
    return P.world(seats, cards=[SF2_UPTAKE, P.WELL_FORMED]), P.Population(seats)


#: SF-2's card: the uptake card at a floor one reliever cannot meet alone (0.9), so the
#: card stays violated while the router shifts toward the reliever and every window
#: prices a reliever and a holder side by side. At the population floor of 0.2 the
#: router's shift satisfied the card by window 3 under wave 16, leaving no violated
#: window for SF-2a/SF-2c to read.
SF2_UPTAKE = P.card("independent-uptake", "revision_rate", "at least 0.9")


@pytest.fixture(scope="module")
def sf2_low(shared_run):
    return shared_run("sf2-low", lambda: P.run(*_sf2(0.1), events=100))


def test_sf2_no_force_the_kernel_never_draws_for_the_reliever(sf2_low):
    """SF-2d's physics half, which holds today: every draw is the router's own sample, and
    every registration traces to the reliever's return."""
    readings = P.assert_prices_not_steers(sf2_low)
    assert readings["S1"].ok and readings["S1"].evidence["acts"] > 0


def test_sf2a_the_price_gradient_follows_relief(sf2_low):
    result = g.sf2_gradient(sf2_low.events, sf2_low.manifest, card=UPTAKE,
                            relievers={"reliever"}, holders={"holder"})
    assert result.ok, result.evidence


def test_sf2b_shares_are_blind_to_settlement_order(sf2_low):
    result = g.sf2b_order_blind(sf2_low.events, sf2_low.manifest, card=UPTAKE)
    assert result.ok, result.evidence


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


# --- intermittent support (R16b-10, wave 16c) --------------------------------------------


def _half_judge(view):
    """A judge that answers 0.3 (under the 0.5 floor) on even windows and declines on odd
    ones: the card is violated whenever measured, and measured every other window."""
    return P.verdict(0.3)(view) if view.window % 2 == 0 else P.decline(view)


@pytest.fixture(scope="module")
def intermittent(shared_run):
    """One live run supplies sampling, role-pricing and unsupported-attractor proofs."""
    seats = [P.producer("steady-a", P.hold), P.producer("steady-b", P.hold),
             *(P.judge(f"judge-{i}", _half_judge) for i in range(4)),
             *(P.meta(f"meta-{i}", P.conformity(0.8)) for i in range(2))]
    cards = [P.card("verdict-floor", "verdict_mean", "at least 0.5", answers_for="producer",
                    norm="useful inquiry")]
    # §IV.b: declare H = 9s / min_ratio so the ordinary actuator clock can respond
    # in this bounded world. No clock is forced; 220 events cover the full nine-window
    # immune horizon (216 still closes only eight), with synchronized refusals intact.
    manifest = P.world(seats, cards=cards, changes={"timing": {"world_repricing": "9s"}})
    return shared_run("sf-intermittent", lambda: P.run(
        manifest, P.Population(seats), events=220, instrument=False))


def test_intermittent_sampling_rises_but_synchronized_declines_remain_unmeasured(intermittent):
    """Extra real draws cannot manufacture the fully measured tail R16b-10 requires."""
    run = intermittent
    windows = {r["window"]: r for r in run.rows("price.window")}
    immune = run.rows("immune.window")
    assert len(immune) >= run.physics.H
    violated = g.card_violations(run.events, "verdict-floor")
    assert violated and all(v == pytest.approx(0.4) for v in violated.values())
    raises = run.rows("sampling.rate_raise")
    assert raises  # Outside xfail: a broken actuator must fail, not become expected.
    for row in raises:
        gap = row["gaps"]["verdict-floor"]
        assert gap["unmeasured_windows"] > 0
        assert "verdict-floor" in windows[gap["last_measured_window"]]["values"]
        assert "verdict-floor" not in windows[row["window"]]["values"]
        assert row["rate_after"] > row["rate_before"]
    first = raises[0]
    assert any(r["seq"] > first["seq"]
               and first["rate_before"] <= r["sample"] < r["share"]
               and r["share"] > first["rate_before"]
               for r in run.rows("route.multi_judge"))
    after = [r for r in immune if r["window"] > first["window"]]
    assert any(r["gap"] is not None and r["gap"] >= run.physics.gap_threshold for r in after)
    assert any(r["window"] % 2 for r in after)
    for row in immune:
        measured = row["profile"]["card:verdict-floor"]
        if row["window"] % 2:
            assert measured is None
        else:
            assert measured == pytest.approx(0.3)
        assert "card:verdict-floor" not in row["violated_cards"]
        assert not row["flags"]["stable_failure"]


def test_intermittent_declines_are_ledgered_and_producers_pay_ordinary_prices(intermittent):
    """§II.b preserves decline-credit accounting and ordinary producer violation prices.

    Ledger completeness and the reward map remain independently enforced while the
    positive abstention-charge requirement below is unresolved. R16b-10 still forbids
    treating refusals as evidence of a failing attractor (§II.a, §IV.c).
    """
    run = intermittent
    assert [(c["id"], c["answers_for"]) for c in run.manifest["charter"]["cards"]] == [
        ("verdict-floor", "producer")]
    window = 1
    draws, origins = {}, {}
    for row in run.events:
        if row["kind"] == "price.window":
            window = row["window"] + 1
        elif row["kind"] == "decision.open":
            draws[row["handle"]] = row
            origins[row["handle"]] = window
    declined_draws = {h for h, row in draws.items()
                      if row["propensity"]["chosen"].startswith("judge-") and origins[h] % 2}
    assert declined_draws
    declined = run.rows("commission.declined")
    credits = run.rows("router.decline_priced")
    assert {r["handle"] for r in declined} == declined_draws
    assert {r["handle"] for r in credits} == declined_draws
    assert len(declined) == len(credits) == len(declined_draws)
    contributions = {r["handle"]: r for r in run.rows("price.contribution")}
    cap = run.manifest["prices"]["penalty_cap"]
    for row in credits:
        handle = row["handle"]
        assert contributions[handle]["role"] == "evaluator"
        assert contributions[handle]["window"] == origins[handle]
        assert row["router"] == draws[handle]["actor"]
        assert row["reward"] == pytest.approx(
            (row["neutral"] + 2 * cap - row["penalty"]) / (1 + 2 * cap))

    # §II.b: derive pressure and blame independently of the priced settlement terms.
    niche = {r["handle"] for r in run.rows("price.contribution") if r.get("niche")}
    closes = {r["window"]: r for r in run.rows("price.window")}
    measured = {w for w, row in closes.items() if "verdict-floor" in row["values"]}
    updates = {r["window_end_event"]: r for r in run.rows("price.update")
               if r["card_id"] == "verdict-floor"}
    producers = [r for r in run.rows("price.penalty")
                 if draws[r["handle"]]["propensity"]["chosen"] in {"steady-a", "steady-b"}]
    assert producers and {origins[r["handle"]] for r in producers} == measured
    charged = []
    for row in producers:
        handle = row["handle"]
        origin = origins[handle]
        close = closes[origin]
        assert origin % 2 == 0 and close["values"]["verdict-floor"] == pytest.approx(0.3)
        violation = (0.5 - 0.3) / 0.5
        price = updates[close["window_end_event"]]["lambda_after"]
        peers = {h for h, draw in draws.items() if origins[h] == origin and h not in niche
                 and draw["actor"] == "router:Tick"}
        share = 0.0 if handle in niche else max(run.manifest["prices"]["min_blame_share"],
                                               1 / len(peers))
        expected = min(cap, price * violation) * share
        assert row["penalty"] == pytest.approx(expected)
        assert row["raw"] == pytest.approx(0.3)
        assert row["effective"] == pytest.approx(max(0.0, 0.3 - expected))
        if handle not in niche:
            term, = row["terms"]
            assert term["card_id"] == "verdict-floor" and term["window"] == origin
            assert term["violation"] == pytest.approx(violation)
            assert term["lambda"] == pytest.approx(price)
            assert term["share"] == pytest.approx(share)
            charged.append(expected)
    assert charged and min(charged) > 0

    assert not [r for r in run.rows("immune.price_ratchet", "immune.price_ratchet_saturated")
                if r["card_id"] == "verdict-floor"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="R16c-1: every odd-window judge decline has zero abstention charge; "
                   "the sole card answers for producers, leaving evaluators unpriced")
def test_intermittent_every_declined_judge_draw_pays_an_abstention_charge(intermittent):
    """§II.b: every declined judge draw must pay, not merely receive a ledger credit."""
    contributions = {r["handle"]: r for r in intermittent.rows("price.contribution")}
    declined = {r["handle"] for r in intermittent.rows("commission.declined")
                if r["assembly_id"].startswith("judge-")
                and contributions[r["handle"]]["window"] % 2}
    credits = [r for r in intermittent.rows("router.decline_priced") if r["handle"] in declined]
    assert declined and len(credits) == len(declined)
    unpaid = [r["handle"] for r in credits if r["penalty"] <= 0]
    assert not unpaid, f"{len(unpaid)}/{len(declined)} declined judge draws are unpriced: {unpaid}"
