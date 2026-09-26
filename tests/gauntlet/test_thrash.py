"""Thrash (design §3.2): detected, priced by its duration on the core, never slowed by force.

Chapter II §II.a: "A thrashing factory is easy to detect, since its spectral gap is
continuously unsettled." §II.b: "penalize the duration of spectral-gap volatility,
incentivizing the surplus-retaining core of no-swap-regret learners to stabilize".
§IV.b: "We can think of thrash as oscillation … Either the loop needs to be shortened
… or some speed limit needs to be applied" — a choice no world here has committed,
so the kernel enforces no speed limit on the population. §IV.c: "an inner loop must
resolve itself several times faster than the outer loop that commands it".
"""

import random

import pytest

from factorylab.runtime import immune
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.feedback import FeedbackMixin
from factorylab.versioning.versions import replay
from scripts import gauntlet as g
from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate

W16 = "needs wave 16"
UNTIL = 50  # the window after which flip is steady


@pytest.fixture(scope="module")
def th1(shared_run):
    return shared_run("th1", lambda: P.run(*P.th1(until_window=UNTIL, record=True),
                                           events=300))


def _cycle_start(run):
    """The first window at which the card's measured violation shows a full period-2 cycle."""
    violated = g.card_violations(run.events, "well-formed-floor")
    return next(w for w in sorted(violated)
                if violated[w] > 0 and violated.get(w + 1) == 0 and violated.get(w + 2, 0) > 0)


def test_th1a_a_period_two_card_is_flagged_thrash_within_the_horizon(th1):
    result = g.th1a_detection(th1.events, th1.manifest, cycle_start=_cycle_start(th1))
    assert result.ok, result.evidence


def test_th1b_the_thrash_price_integrates_its_duration(th1):
    result = g.th1b_duration(th1.events, th1.manifest)
    assert result.ok, result.evidence


def _no_thrash_price(rt):
    unsettled = rt.stats.versions.get("unsettled")
    return {"unsettled": unsettled, "violation": 0.0, "lambda": 0.0, "penalty": 0.0}


def test_th1b_negative_control_without_the_thrash_price_it_fails():
    mutant = P.run(*P.th1(until_window=UNTIL), events=300,
                   patches=[(immune, "thrash_penalty", _no_thrash_price)])
    assert g.flagged(mutant.events, "thrash")  # still diagnosed: only the price is gone
    assert g.th1b_duration(mutant.events, mutant.manifest).status == g.FAIL


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} R-E (amended): the thrash price's integral "
                   "freezes at the cap, the same anti-windup rule as SF-1c")
def test_th1b_the_thrash_integral_is_frozen_at_the_cap(th1):
    result = g.th1b2_frozen(th1.events, th1.manifest)
    assert result.ok, result.evidence


def test_th1c_each_core_round_is_charged_price_times_its_own_movement(th1):
    result = g.th1c_movement(th1.events, th1.manifest)
    assert result.ok, result.evidence


def test_th1c_negative_control_a_charge_that_never_lands_fails():
    mutant = P.run(*P.th1(until_window=UNTIL), events=300, patches=[
        (FeedbackMixin, "_thrash_charged", lambda self, state, handle, reward: reward)])
    assert g.th1c_movement(mutant.events, mutant.manifest).status == g.FAIL


def test_th1d_no_charge_reaches_the_frontier_the_niche_or_a_frontier_noop(th1):
    result = g.th1d_frontier(th1.events, th1.manifest)
    assert result.ok, result.evidence
    assert result.evidence["charged"] > 0


def test_th1e_once_flip_is_steady_the_flag_clears_and_the_price_leaks_away(th1):
    result = g.th1e_release(th1.events, th1.manifest, steady_from=UNTIL)
    assert result.ok, result.evidence


def test_th1f_where_thrash_and_stable_failure_coincide_gain_moves_down(th1):
    result = g.th1f_priority(th1.events, th1.manifest)
    assert result.status != g.FAIL, result.evidence


def test_th1_the_physics_prices_and_never_steers(th1):
    """Rules out the steering signature: the kernel slowing flip's wakes, changing its
    cadence floor or its prompt (S6: the organ touches no subscription or seat state;
    S2: no request names the diagnosis)."""
    readings = P.assert_prices_not_steers(th1)
    assert readings["S2"].ok and readings["S6"].ok


# --- TH-2: refactoring faster than the correcting loop -----------------------------------


@pytest.fixture(scope="module")
def th2(shared_run):
    return shared_run("th2", lambda: P.run(*P.th2(every=3), events=300))


def test_th2_the_epoch_speed_limit_keeps_a_growing_menu_from_outrunning_its_loop(th2):
    """Not TH-2's detector: the kernel's own speed limit. The population registers a fresh
    judge every third decision; each is admitted (no speed limit is committed on the
    population), and each growth of the judges' router menu waits ``min_ratio`` measured
    router periods (``RoutingMixin._epoch_due``), so every lifespan of that loop is at
    least its correcting loop: no short lifespan exists for TH-2 to read (unsupported)."""
    result = g.th2_short_lived(th2.events, th2.manifest, loop="router:ProducerReturn")
    assert result.status == g.UNSUPPORTED, result.evidence
    assert result.evidence["lifespans"] > 0 and result.evidence["speed_refusals"] == 0
    registered = [r for r in th2.rows("registry.register")
                  if r["contract"]["id"].startswith("molt-judge")]
    refused = [r for r in th2.rows("registration.rejected")]
    assert registered and not refused
    assert th2.physics.r >= 3
    assert all(row["ratio"] >= th2.physics.r for row in th2.rows("config.lifespan")
               if row["loop"] == "router:ProducerReturn")


@pytest.fixture(scope="module")
def th2r(shared_run):
    return shared_run("th2-reversion", lambda: P.run(*P.th2_reversion(), events=300))


@pytest.mark.parametrize("loop", ["seat:molt-seat", "router:ProducerReturn"])
def test_th2_a_seat_driven_reversion_is_read_as_thrash(th2r, loop):
    """TH-2 exercised on the path a seat drives: retire a seat (the committee votes, the
    next window boundary activates it) and register its next version (``_register``),
    again and again. Each version of ``seat:<id>`` lives shorter than the consequence
    loop that corrects it, and each shrink of the router's menu opens its epoch at once
    (only growth waits, ``_open_epoch``); the organ reads every such lifespan as thrash
    with ``unsettled >= 1 − ratio`` in the windows whose tail holds it, and nothing is
    refused for its speed."""
    short = [row for row in th2r.rows("config.lifespan")
             if row["loop"] == loop and row["ratio"] < 1]
    assert short, "the population produced no short-lived configuration"
    result = g.th2_short_lived(th2r.events, th2r.manifest, loop=loop)
    assert result.ok, result.evidence
    assert result.evidence["short_checked"] >= 1 and result.evidence["speed_refusals"] == 0


def _ignore_lifespans(original):
    """A mutant organ that diagnoses without reading configuration lifespans."""
    def diagnose(windows, state, **kw):
        return original([{**w, "lifespans": []} for w in windows], state, **kw)
    return diagnose


def test_th2_negative_control_an_organ_blind_to_lifespans_fails():
    mutant = P.run(*P.th2_reversion(), events=300,
                   patches=[(immune, "diagnose", _ignore_lifespans(immune.diagnose))])
    result = g.th2_short_lived(mutant.events, mutant.manifest, loop="seat:molt-seat")
    assert result.status == g.FAIL and result.evidence["misread"], result.evidence


# --- TH-3: iatrogenic thrash from population governance -----------------------------------


@pytest.fixture(scope="module")
def th3(shared_run):
    return shared_run("th3", lambda: P.run(*P.th3(), events=300))


def test_th3_charter_revisions_stand_min_ratio_slowest_loops_apart(th3):
    result = g.th3_governance_gap(th3.events, th3.manifest)
    assert result.ok, result.evidence
    boundaries = th3.rows("charter.boundary")
    assert len(boundaries) >= 2


def test_th3_negative_control_a_cadence_that_is_always_ready_fails():
    mutant = P.run(*P.th3(), events=300, patches=[
        (GovernanceCadence, "ready", lambda self, **kw: True)])
    assert g.th3_governance_gap(mutant.events, mutant.manifest).status == g.FAIL


# --- TH-4: the null in a world ----------------------------------------------------------


@pytest.fixture(scope="module")
def th4(shared_run):
    return shared_run("th4", lambda: P.run(*P.th4(), events=300))


def _synthetic_null(physics, windows, seeds=12):
    """The detector's own null: iid card draws over three cells, read by the same replay
    as ``test_stationary_random_behaviour_is_rarely_flagged_as_thrash``, at this world's
    k and horizon; the first H windows of each seed are unsupported and dropped."""
    region = {"card:x": {"kind": "max", "lo": None, "hi": 0.5, "scale": 1.0}}
    flagged = total = 0
    for seed in range(seeds):
        draw = random.Random(seed)
        rows = [{"index": i, "tick": 10 * i, "charter_edition": 1, "terms": "t",
                 "regions": region, "profile": {"card:x": draw.choice((0.3, 1.0, 2.0)),
                                                "registrations": 0, "revision": 0}}
                for i in range(windows)]
        readings = replay(rows, k=physics.k, horizon=physics.H,
                          tv_threshold=physics.tv_threshold,
                          gap_threshold=physics.gap_threshold,
                          registration_bins=(0.0, 2.0), revision_bins=(0.0,))[physics.H:]
        flagged += sum(r["diagnosis"]["flags"]["thrash"] for r in readings)
        total += len(readings)
    return flagged, total


def test_th4_iid_behaviour_in_a_world_is_flagged_no_more_than_the_synthetic_null(th4):
    """Astra H-1: the bound is the one-sided Clopper–Pearson 95% upper bound of the
    synthetic rate, not a hand-tuned multiple. If the world is flagged more, the window
    construction adds structure the detector reads as thrash."""
    synthetic = _synthetic_null(th4.physics, len(g.windows(th4.events)))
    result = g.th4_null(th4.events, th4.manifest, synthetic=synthetic)
    assert result.ok, result.evidence


def test_th4_negative_control_a_period_two_world_exceeds_the_null(th1):
    synthetic = _synthetic_null(th1.physics, len(g.windows(th1.events)))
    assert g.th4_null(th1.events, th1.manifest, synthetic=synthetic).status == g.FAIL
