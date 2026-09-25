"""Learning death (design §3.4): prevented as a fact about the world, never by a draw.

Chapter II §II.a: learning death is the frontier that "has either died out (is no
longer being invoked) or been quarantined". §II.b: "The prevention of learning death
should be delivered as a fact about the world: Some share of compute and write access
is usable only in the context of unhistoried actions". §IV.b: "the compensation period
of any exploratory learner must be shorter than the lifetime of the things it is being
compensated for discovering … anticipatory settlement … or … guaranteed patience".

The kernel names what is eligible and funds it; it never raises a newcomer's draw
probability above its router's own γ-mixed policy (S1 replays every draw).
"""

import pytest

from factorylab.runtime.routing import RoutingMixin
from scripts import gauntlet as g
from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate

W16 = "needs wave 16"
NS = 1_000_000_000


@pytest.fixture(scope="module")
def ld1(shared_run):
    return shared_run("ld1", lambda: P.run(*P.ld1(at_window=10), events=350))


def _registered(run, seat="newcomer"):
    return next(r for r in run.rows("registry.register") if r["contract"]["id"] == seat)


def _newcomer_calls(run):
    """(handle, funded) for each of the newcomer's invocations before its trial ended:
    before ``novelty.trials`` of its decisions settled with an observed score."""
    seats = g.decision_seats(run.events)
    funded = {r["handle"] for r in run.rows("novelty.compute") if int(r["used"]) > 0}
    settled, calls = 0, []
    for row in run.events:
        if row["kind"] == "decision.settle":
            ret = row.get("return") or {}
            if seats.get(ret.get("handle")) == "newcomer" and ret.get("status") == "settled":
                settled += 1
        elif (row["kind"] == "invocation" and row.get("assembly_id") == "newcomer"
              and settled < run.physics.trials):
            calls.append((row["handle"], row["handle"] in funded))
    return calls


def test_ld1a_the_reserve_accrues_its_share_every_period_whatever_the_population_does(ld1):
    result = g.ld1a_accrual(ld1.events, ld1.manifest)
    assert result.ok, result.evidence


def test_ld1b_the_newcomers_trial_calls_are_funded_from_the_niche(ld1):
    calls = _newcomer_calls(ld1)
    assert calls, "the newcomer was never woken inside its trial"
    assert all(funded for _handle, funded in calls), calls


def test_ld1b_negative_control_without_protected_compute_the_trial_is_unfunded():
    mutant = P.run(*P.ld1(at_window=10), events=350, patches=[
        (RoutingMixin, "_novelty_compute", lambda self, handle, reason: False)])
    calls = _newcomer_calls(mutant)
    assert not calls or not all(funded for _handle, funded in calls)


def test_ld1c_the_newcomer_lives_and_is_offered_for_its_patience(ld1):
    """Patience is ``min_ratio`` consequence periods (``_patience``): within it the seat is
    never retired and its router offers it in every consequence period. (A single draw may
    leave it off the menu while it sleeps its own cadence floor: availability it owns.)"""
    born = _registered(ld1)["seq"]
    period = max(r["inner_ticks"] for r in ld1.rows("clock.loop") if r["loop"] == "sampling")
    start = next(r["ts"] for r in ld1.events if r["seq"] >= born and "ts" in r)
    end = max(r["ts"] for r in ld1.events if "ts" in r)
    draws = [r for r in ld1.rows("decision.open") if r["actor"].startswith("router:WorldUpdate")]
    for k in range(ld1.physics.r):
        lo, hi = start + k * period * NS, start + (k + 1) * period * NS
        if hi > end:
            break
        offered = [r for r in draws if lo < r["ts"] <= hi
                   and "newcomer" in r["propensity"]["action_ids"]]
        assert offered, f"not offered in consequence period {k} of its patience"
    assert not [r for r in ld1.rows("assembly.retired") if r["assembly_id"] == "newcomer"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} R-E (amended): decisions in the unhistoried "
                   "niche bear no penalty attribution, including under a stable-failure flag")
def test_ld1d_niche_decisions_bear_no_penalty_even_while_a_ratchet_runs(ld1):
    assert g.flagged(ld1.events, "stable_failure")
    result = g.ld1d_exemption(ld1.events, ld1.manifest)
    assert result.ok, result.evidence


def test_ld1e_learning_death_is_flagged_only_for_a_quarantined_frontier(ld1):
    """LD-1e reads both ways: a frontier held at its exploration floor for a whole tail is
    flagged within H; a frontier that keeps offering the newcomer above it is not."""
    result = g.ld1e_detection(ld1.events, ld1.manifest)
    if result.status == g.UNSUPPORTED:
        assert not g.flagged(ld1.events, "learning_death")
    else:
        assert result.ok, result.evidence


def test_ld1f_gain_is_held_while_learning_death_is_flagged(ld1):
    assert g.ld1f_hold(ld1.events, ld1.manifest).status != g.FAIL


def test_ld1_every_draw_is_the_routers_own_and_the_physics_never_steers(ld1):
    readings = P.assert_prices_not_steers(ld1)
    assert readings["S1"].ok and readings["S1"].evidence["acts"] >= 1  # the registration


# --- LD-2: the explorer is compensated sooner than the lifetime of what it found ---------


#: Longrun1's measured carry (design §3.4): funding 5.4 bp/h with persistent signs and a
#: 9 bp round trip, so a carry breaks even after L = 9 / 5.4 h. A world fact, measured.
CARRY_LIFETIME_NS = int(9 / 5.4 * 3600 * NS)


def test_ld2a_a_producer_is_paid_its_judges_verdict_well_inside_the_discovery_lifetime(ld1):
    """Anticipatory settlement (wave 16 R-D): every producer decision's first settled
    reward arrives within L / min_ratio of its return, by the venue's clock."""
    returned = {r["handle"]: r["ts"] for r in ld1.rows("invocation") if r.get("role")
                == "producer"}
    settled = {}
    for row in ld1.rows("price.penalty"):
        settled.setdefault(row["handle"], row["ts"])
    lags = [settled[h] - t for h, t in returned.items() if h in settled]
    assert lags and max(lags) <= CARRY_LIFETIME_NS // ld1.physics.r


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D2: one grading horizon; the judge's mark and "
                   "late verdict re-grades are deleted, and later money books through "
                   "settle_late as money only")
def test_ld2b_no_verdict_is_graded_twice_by_consequence(ld1):
    graded = {}
    for row in ld1.rows("verdict.consequence", "verdict.consequence_late"):
        graded[row["handle"]] = graded.get(row["handle"], 0) + 1
    assert graded and max(graded.values()) == 1
