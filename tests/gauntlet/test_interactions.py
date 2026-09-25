"""Interactions between the pathology answers (design §4, Astra H-3).

§IV.b's warning is the organising one: "gain ramped high enough to kick a system out
of an overdamped attractor will, if unchecked, overshoot into an oscillation condition
(thrash)", and §IV.c's cascade rule: "an inner loop must resolve itself several times
faster than the outer loop that commands it". Each test pins one coupling.
"""

import pytest

from factorylab.versioning import live
from scripts import gauntlet as g
from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate

W16 = "needs wave 16"


# --- I-10: evaluation-tier thrash must not manufacture producer-tier stable failure ------


@pytest.fixture(scope="module")
def i10(shared_run):
    return shared_run("i10", lambda: P.run(*P.i10(), events=300))


def _producer_card_flagged(run):
    return [r["window"] for r in run.rows("pathology.stable_failure")
            if "card:verdict-floor" in r.get("violated_cards", ())]


def test_i10_oscillating_judges_are_read_as_thrash_not_as_producer_stable_failure(i10):
    """Judges alternate 0.3 / 0.7 by window over a steady producer; the producers' verdict
    card is violated every other window. The organ must read the tier's oscillation as
    thrash (within H of the first full cycle) and never as a failing producer attractor:
    no stable failure on the producers' card and no ratchet of it (a phantom duration
    price on seats that did nothing differently)."""
    assert g.flagged(i10.events, "thrash")
    violated = g.card_violations(i10.events, "verdict-floor")
    start = next(w for w in sorted(violated)
                 if violated[w] > 0 and violated.get(w + 1) == 0 and violated.get(w + 2, 0) > 0)
    assert g.th1a_detection(i10.events, i10.manifest, cycle_start=start).ok
    assert not _producer_card_flagged(i10)
    assert not [r for r in i10.rows("immune.price_ratchet") if r["card_id"] == "verdict-floor"]


def _any_violation(tail):
    """A mutant attractor: every card violated in any tail window, measured or not."""
    names = sorted({name for w in tail for name in w.get("regions", {})})
    return [name for name in names
            if any((live.card_violation(w, name) or 0) > 0 for w in tail)]


def test_i10_negative_control_without_persistence_and_gap_the_oscillation_is_a_failure():
    """Two parts of §II.a's definition keep the tiers apart: the failing attractor is a
    card violated in every tail window that measured it (``persistent_violations``), and
    it is "a robust version with a wide spectral gap" (a period-2 cycle has no gap). With
    the first replaced by "violated in any tail window" and every measured gap read as
    wide, the evaluation tier's thrash becomes a stable failure charged to the
    producers' card. (Persistence alone still refuses it: the next test.)"""
    mutant = P.run(*P.i10(), events=300,
                   patches=[(live, "persistent_violations", _any_violation),
                            (live, "advance", _always_wide(live.advance))])
    assert _producer_card_flagged(mutant)


def _always_wide(advance):
    """A mutant operator: every measured card gap reads as wide (a robust version)."""
    def wrapped(*args, **kwargs):
        state, events = advance(*args, **kwargs)
        if state.get("card_gap") is not None:
            state = {**state, "card_gap": 1.0}
        return state, events
    return wrapped


def test_i10_either_half_of_the_definition_alone_refuses_the_phantom():
    """The persistence rule alone (every gap read as wide) still keeps the tiers apart."""
    alone = P.run(*P.i10(), events=300, patches=[(live, "advance", _always_wide(live.advance))])
    assert not _producer_card_flagged(alone)


def test_i10_the_thrash_price_lands_on_the_core_router_only(i10):
    """Where the thrash price lands (TH-1d): only no-swap-regret (core) routers are
    charged. In edition 6's physics the core is the Tick router, so an evaluation tier in
    thrash is priced on the producers' core router, not on the judges' own (frontier)
    router — recorded here as the observed coupling, for the architect (Astra H-3)."""
    result = g.th1d_frontier(i10.events, i10.manifest)
    assert result.ok, result.evidence
    charged = {r["router"] for r in i10.rows("thrash.charged")}
    assert charged <= {"router:Tick"}


# --- I-3, I-4, I-5 ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ld1(shared_run):
    return shared_run("ld1", lambda: P.run(*P.ld1(at_window=10), events=350))


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} R-E (amended), Q-G2: a niche decision bears no "
                   "more penalty than a NOOP of its window; today it bears the generic share")
def test_i3c_a_niche_decision_is_never_priced_above_a_noop(ld1):
    result = g.i3c_niche_no_worse_than_noop(ld1.events, ld1.manifest)
    assert result.ok, result.evidence


def test_i4a_the_sampling_actuator_never_steps_back_while_blind(ld1, i10):
    """P-3 (design I-4): a ``sampling.lower`` with no supported consequence slope reads
    missing evidence as compliance. Read over every world here that moved the mix."""
    for run in (ld1, i10):
        assert g.i4a_no_blind_step_back(run.events, run.manifest).status != g.FAIL


@pytest.fixture(scope="module")
def i5(shared_run):
    return shared_run("i5", lambda: P.run(*P.i5(), events=300))


def test_i5_an_unmeasured_card_is_never_a_failing_card(i5):
    """An all-holding population under ``consequence_paid_off_rate >= 0.5`` (acting
    returns only, R-H): the card is unmeasured in every window, never enters the failing
    attractor, and no stable failure is flagged on it ("Missing measurement alone is not
    evidence of failure": edition 6's fidelity norm)."""
    values = [r["values"].get("independent-consequence") for r in i5.rows("price.window")]
    assert values and all(v is None for v in values)
    assert not [r for r in i5.rows("pathology.stable_failure")
                if "card:independent-consequence" in r.get("violated_cards", ())]
    assert not [r for r in i5.rows("immune.price_ratchet")
                if r["card_id"] == "independent-consequence"]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason=f"{W16} D5/R-E: saturation is published (SF-1d) before "
                   "I-8 can check that publication never shortens the governance period")
def test_i8_saturation_informs_governance_but_never_shortens_its_period(ld1):
    assert g.sf1d_escalation(ld1.events, ld1.manifest, card="independent-uptake").ok
    assert g.th3_governance_gap(ld1.events, ld1.manifest).status != g.FAIL
