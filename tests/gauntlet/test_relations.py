"""Relations between loops, read from manifests alone (check tier; no world runs).

SF-0 (design §3.1): the duration price exists only if the integrator still has
headroom when the organ can first see an attractor. LD-3 (§3.4): a loop "whose period
exceeds the duration of opportunity" is refused or ledgered nonviable, and the kernel
never accelerates a loop by itself. LD-2c (§3.4, Astra M-3): an explorer whose
discovery outlives the grading horizon must be compensated sooner than that lifetime
(Chapter II §IV.b: "anticipatory settlement … or … guaranteed patience").
"""

import json
import tomllib

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts import gauntlet as g
from tests.gauntlet import populations as P


def _worlds():
    return sorted(p.stem for p in (P.ROOT / "worlds").glob("*.toml"))


def _scripted_raw():
    return tomllib.loads((P.ROOT / "worlds/scripted.toml").read_text())


# --- SF-0 ------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="P-1 (Q-G1): no world has integrator headroom at the "
                   "detection horizon, and no load-time relation refuses or publishes it")
@pytest.mark.parametrize("name", _worlds())
def test_sf0_every_loadable_world_has_gain_headroom_at_the_detection_horizon(name):
    """W_sat(v_ref) >= H for every priced card, conditioned on its region and window kind
    (Astra C-1). Longrun1: "118 of 123 ratchets were no-ops … λ was already at
    lambda_max". Fails today on every world: eta = 0.5 and penalty_cap = 0.5 saturate a
    unit violation in one window, while the organ needs H = min_ratio × k = 9."""
    manifest = json.loads(load_manifest(name).canonical_json())
    assert g.sf0_relation(manifest).ok


def test_sf0_the_relation_discriminates_between_a_saturating_and_a_patient_integrator():
    raw = _scripted_raw()
    saturating = g.sf0_relation(raw)
    assert saturating.status == g.FAIL and saturating.evidence["gain_headroom_windows"] == 1
    patient = {**raw, "prices": {**raw["prices"], "eta": 0.05}}
    assert g.sf0_relation(patient).ok


@pytest.mark.xfail(strict=True, reason="P-1 (Q-G1): the load-time refusal (or the published "
                   "gain_headroom_windows fact) is proposed physics awaiting the architect")
def test_sf0_negative_control_a_saturating_world_is_refused_or_publishes_its_headroom():
    raw = _scripted_raw()
    raw["prices"] = {**raw["prices"], "eta": 0.5, "penalty_cap": 0.5}
    with pytest.raises(ValueError, match="headroom"):
        manifest_from_dict(raw)


# --- I-1a: the anti-windup boundary, read on the controller itself --------------------


def _release_lag(saturated_windows):
    """Windows for the card's price to return to zero after ``saturated_windows`` of unit
    violation, on the real ``PriceController`` at the scripted world's gains."""
    from factorylab.charter.controller import CardRegion, PriceController
    from factorylab.kernel.ledger import Ledger

    prices = load_manifest("scripted").prices
    controller = PriceController(Ledger(None), eta=prices.eta, decay=prices.decay,
                                 lambda_max=prices.lambda_max, min_window_events=1,
                                 kp=prices.kp, kd=prices.kd)
    controller.register(CardRegion("c", "min", 0.5, None, 0.5))
    event = 0
    for _ in range(saturated_windows):
        event += 1
        controller.observe("c", 0.0, event)  # violation 1: the penalty sits at the cap
    lag = 0
    while controller.price("c") > 0:
        event += 1
        lag += 1
        controller.observe("c", 1.0, event)
    return lag


@pytest.mark.xfail(strict=True, reason="needs wave 16 R-E (amended): anti-windup at the cap. "
                   "Today the integral keeps winding to lambda_max while the penalty is "
                   "already capped, so release lags grow with saturation's duration")
def test_i1a_release_after_saturation_is_independent_of_how_long_it_lasted():
    """I-1a (design §4): the release lag after an exit is at most T_rel(λ at first
    saturation) + 1 and within one window of itself whether saturation lasted D or 3D
    windows. The negative control is today's controller: without anti-windup at the
    cap the lag after 3D exceeds the lag after D."""
    lags = {d: _release_lag(d) for d in (1, 3)}
    assert abs(lags[3] - lags[1]) <= 1, lags


# --- LD-3 -------------------------------------------------------------------------------


def _launched(raw, *, run_ticks):
    rt = Runtime(manifest_from_dict(raw), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.events_budget = run_ticks  # the run length viability reads; nothing is run
    period = rt.cadence.consequence_period_events()
    rt._manage_reserve_window()  # launch: opens the outer loops and checks viability
    items = [i for i in rt.ledger._recovery_items() if i["kind"].startswith("governance.")]
    return rt, period, items


@pytest.mark.parametrize("repricing", ["30s", "59s"])
def test_ld3_an_overstable_world_is_refused_at_load(repricing):
    """``min_ratio`` × the consequence backstop (3 × 20 ticks at 1 s) exceeds the world's
    repricing period: the existing ``max_tick_ns`` invariant refuses the world, since its
    tick no longer fits the loops the horizon commands (time audit T2)."""
    raw = _scripted_raw()
    raw["timing"] = {**raw["timing"], "world_repricing": repricing}
    with pytest.raises(ValueError, match="max_tick"):
        manifest_from_dict(raw)


def test_ld3_a_short_run_is_ledgered_nonviable_and_no_loop_is_accelerated():
    """A world that loads but whose whole run is shorter than ``min_ratio`` × its slowest
    loop has no governance tier: the launch ledgers ``governance.nonviable``, and the
    consequence loop is left as it was (``_check_viability``: "Nothing here accelerates a
    loop")."""
    raw = _scripted_raw()
    rt, period, items = _launched(raw, run_ticks=10)
    assert [i for i in items if i["kind"] == "governance.nonviable"], items
    assert rt.cadence.consequence_period_events() == period
    assert rt.m.evaluation == manifest_from_dict(raw).evaluation


def test_ld3_counter_case_a_world_that_fits_its_repricing_is_viable():
    raw = _scripted_raw()
    raw["timing"] = {**raw["timing"], "world_repricing": "90s"}
    _rt, _period, items = _launched(raw, run_ticks=1000)
    assert not [i for i in items if i["kind"] == "governance.nonviable"]


# --- LD-2c ------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=(
    "needs wave 16 D2 and R-I Q3. The refusal needs the grading horizon H_f = "
    "world_repricing / min_ratio in venue time (wave 16 D2, not on this branch) and a "
    "patience term (R-I: 'Q3: patience later'). SUNSET: when D2 lands, a world that lists "
    "a venue with funding and no patience covering L = round_trip_fee / funding_rate is "
    "refused at load, and this marker is removed; if patience has not landed by the "
    "edition-7 world file, that world is refused and this test must pass before it ships."))
def test_ld2c_a_world_whose_discoveries_outlive_the_grading_horizon_needs_patience():
    """Longrun1's measured carry: funding 5.4 bp/h, a 9 bp round trip, so a carry breaks
    even after L = 9 / 5.4 ≈ 1.67 h, longer than H_f = 1 h / 3 = 20 min. With no
    patience, the realized-consequence signal cannot see such a discovery before its
    judge is scored, so the world must be refused at load (Astra M-3)."""
    raw = tomllib.loads((P.ROOT / "worlds/edition6-capital-loop.toml").read_text())
    with pytest.raises(ValueError, match="patience"):
        manifest_from_dict(raw)
