"""Relations between loops, read from manifests alone (check tier; no world runs).

SF-0 (design §3.1): the duration price exists only if the integrator still has
headroom when the organ can first see an attractor. LD-3 (§3.4): a loop "whose period
exceeds the duration of opportunity" is refused or ledgered nonviable, and the kernel
never accelerates a loop by itself. LD-2c (§3.4, Astra M-3; R16b-4): an explorer is
compensated sooner than the lifetime of what it found (Chapter II §IV.b: "anticipatory
settlement … or … guaranteed patience"): patience is floored at the horizon H, so it
covers ``world_repricing``.
"""

import json
import tomllib

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts import gauntlet as g
from tests.gauntlet import populations as P


def _worlds():
    """Every world ``load_manifest`` accepts, outside ``worlds/history``."""
    from tests.audit.class2_corpus import launchable_worlds

    return launchable_worlds()


def _scripted_raw():
    return tomllib.loads((P.ROOT / "worlds/scripted.toml").read_text())


# --- SF-0 ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", _worlds())
def test_sf0_every_loadable_world_has_gain_headroom_at_the_detection_horizon(name):
    """W_sat(v_ref) >= H for every priced card, conditioned on its region and window kind
    (Astra C-1). Longrun1: "118 of 123 ratchets were no-ops … λ was already at
    lambda_max". Wave 16 (Q-G1) derives eta from the relation, so every world holds it:
    a unit violation takes H = min_ratio × k = 9 windows to saturate the cap."""
    manifest = json.loads(load_manifest(name).canonical_json())
    assert g.sf0_relation(manifest).ok


def test_sf0_the_relation_discriminates_between_a_saturating_and_a_patient_integrator():
    raw = _scripted_raw()
    saturating = g.sf0_relation(raw)
    assert saturating.status == g.FAIL and saturating.evidence["gain_headroom_windows"] == 1
    patient = {**raw, "prices": {**raw["prices"], "eta": 0.05}}
    assert g.sf0_relation(patient).ok


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
                                 penalty_cap=prices.penalty_cap, min_window_events=1,
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


def test_i1a_release_after_saturation_is_independent_of_how_long_it_lasted():
    """I-1a (design §4): the release lag after an exit is at most T_rel(λ at first
    saturation) + 1 and within one window of itself whether saturation lasted D or 3D
    windows. Wave 16 (R-E, amended) freezes the integral at the cap, so it holds; before
    it, without anti-windup, the lag after 3D exceeded the lag after D."""
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


@pytest.mark.parametrize("repricing", ["5s", "8s"])
def test_ld3_an_overstable_world_is_refused_at_load(repricing):
    """A world whose tick does not fit the loops its consequence horizon commands is
    refused (time audit T2, the ``max_tick_ns`` invariant). Wave 16 (D2) derives the
    horizon from the venue, ``world_repricing / min_ratio``, so the tick must be at most
    ``world_repricing / min_ratio²``: 1 s ticks need a repricing of at least 9 s. (Before
    wave 16 the horizon was the consequence backstop in ticks, and 30 s or 59 s of
    repricing were too short for it.)"""
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


def _horizon_relations(rt):
    """Every period derived from the consequence loop, against H in delivered ticks
    (floor division; a world that lists no venue grades at its backstop)."""
    from factorylab.runtime.clockwork import tick_ns

    horizon_ns = rt.m.consequence_horizon_ns
    horizon = (rt.ev.consequence_backstop_ticks if horizon_ns is None
               else max(1, horizon_ns // tick_ns(rt.tick_clock)))
    return {"H_ticks": horizon, "period": rt._consequence_period(),
            "patience": rt._patience(), "slowest": rt.cadence.slowest_period_events(),
            "min_ratio": rt.m.timing.min_ratio}


@pytest.mark.parametrize("name", _worlds())
def test_ld2c_guaranteed_patience_covers_the_worlds_repricing_period(name):
    """R16b-4 (essay IV.b: "anticipatory settlement … or … guaranteed patience"; IV.c:
    governance relates to the slowest loop). The consequence loop is floored at the
    horizon H in delivered ticks, so after launch a seat's protected trial lasts
    ``min_ratio × H`` = ``world_repricing``, and the novelty accrual, the sampling
    actuator and the governance floor all see H. A discovery that outlives
    ``world_repricing`` is the world's declaration to correct, never a kernel refusal:
    L is a venue fact the kernel cannot know at load (the refusal this test once
    demanded is rejected: it is not a Chapter II remedy, and it is Class 2)."""
    from tests.audit.class2_corpus import static_runtime

    rt = static_runtime(name)  # its launch identity on the simulated venue, offline
    rt.events_budget = 10_000
    rt._manage_reserve_window()  # launch
    got = _horizon_relations(rt)
    assert got["period"] >= got["H_ticks"], got
    assert got["patience"] >= got["min_ratio"] * got["H_ticks"], got
    assert got["slowest"] >= got["H_ticks"], got


def test_ld2c_a_backstop_shorter_than_the_horizon_no_longer_shortens_patience():
    """The violating world: a consequence backstop of ``min_ratio`` ticks, far shorter
    than H. Before R16b-4 the period (and patience) was the backstop; now it is H."""
    raw = _scripted_raw()
    raw["evaluation"] = {**raw.get("evaluation", {}), "consequence_backstop_events": 3}
    rt, _period, _items = _launched(raw, run_ticks=10_000)
    got = _horizon_relations(rt)
    assert got["H_ticks"] > 3, got  # the backstop is shorter than the horizon
    assert got["period"] == got["H_ticks"], got
    assert got["patience"] == got["min_ratio"] * got["H_ticks"], got
    floors = [i for i in rt.ledger._recovery_items() if i["kind"] == "cadence.floor"]
    assert floors and floors[-1]["ticks"] == got["H_ticks"]
