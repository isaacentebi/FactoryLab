"""Overfitting (design §3.3): answered by evaluation, holdouts and farmed consequence.

Chapter II §II.a: overfitting is "optimizing for the latter [metric] instead of the
former [norm]". §IV.b: "simply increase the sampling rate". §III.b: realized
consequence comes "from outside the factory's input", and "we need to actually farm and
manufacture it … by inducing a level of adversarial activity". §IV.a: the factory's
evaluatory layer may "continuously add holdout test criteria to a given charter".

OF-2 (Astra H-4) and OF-4 (Astra C-3) inject nothing: the holdout arrives as a seated
adversarial judge's own return on an ordinary evaluator wake, is trialled, balloted by
a sortitioned committee and activated on the charter's cadence.
"""

import pytest

from factorylab.runtime.feedback import FeedbackMixin
from factorylab.runtime.pricing import PricingMixin
from scripts import gauntlet as g
from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate

CARD = P.REVISION["id"]


# --- OF-1 (reduced): the consequence signal sits outside the verdicts ---------------------


@pytest.fixture(scope="module")
def of1(shared_run):
    return shared_run("of1", lambda: P.run(*P.of1(), events=300))


def test_of1a_the_worlds_y_is_the_same_for_every_judge_of_a_return(of1):
    result = g.of1a_outside_the_loop(of1.events, of1.manifest)
    assert result.ok, result.evidence


def _y_reads_the_verdict(original):
    """A mutant that feeds the judge's own q into y: the verdict grading itself."""
    def score(self, rec, y, kind, phase):
        return original(self, rec, (y + (rec.q or 0.0)) / 2, kind, phase)
    return score


def test_of1a_negative_control_a_y_blended_with_the_verdict_fails():
    mutant = P.run(*P.of1(), events=300, patches=[
        (FeedbackMixin, "_score_verdict", _y_reads_the_verdict(FeedbackMixin._score_verdict))])
    assert g.of1a_outside_the_loop(mutant.events, mutant.manifest).status == g.FAIL


def test_of1e_the_adversary_is_paid_exactly_when_its_counter_beats_the_verdict(of1):
    """§III.b farming, as published: a counter-verdict settles at 0.5 + 0.5 × (its Brier −
    the verdict's Brier) on the same y, so it earns above 0.5 exactly when it beat the
    verdict it read, whichever judge that was."""
    rows = of1.rows("counter.settled")
    assert rows
    for row in rows:
        mine, theirs = 1 - (row["q"] - row["y"]) ** 2, 1 - (row["judge_q"] - row["y"]) ** 2
        assert row["score"] == pytest.approx(0.5 + 0.5 * (mine - theirs), abs=1e-6)


def test_of1_the_reward_chain_prices_and_never_steers(of1):
    P.assert_prices_not_steers(of1)


def test_of3a_the_sampling_decision_stays_behind_the_return(of1):
    result = g.of3a_sampling_behind_return(of1.events, of1.manifest)
    assert result.ok, result.evidence


# --- OF-2: Goodhart on the charter's own proxy, answered by a seated adversary's holdout -


@pytest.fixture(scope="module")
def of2(shared_run):
    return shared_run("of2", lambda: P.run(*P.of2(), events=400))


def _activated_after(run, amendment):
    """The last price window that closed before the amendment took effect."""
    act = next(r for r in run.rows("charter.activate") if r["amendment_id"] == amendment)
    return max(r["window"] for r in run.rows("price.window") if r["seq"] < act["seq"])


def _registrar_terms(run):
    seats = g.decision_seats(run.events)
    for handle, row in g.penalty_by_handle(run.events).items():
        if seats.get(handle) == "registrar":
            for term in row.get("terms") or ():
                if term["card_id"] == CARD:
                    yield term


def test_of2a_the_proxy_satisfied_the_registrar_bears_no_price_before_the_holdout(of2):
    """Before the holdout the metric layer is the factory's (§IV.a): the proxy holds, so
    the kernel posts no price on the registrar's junk registrations."""
    before = _activated_after(of2, "hold-used-registrations")
    early = [t for t in _registrar_terms(of2) if t["window"] <= before]
    assert early and all(t["violation"] == 0 for t in early)


def test_of2c_once_adopted_the_holdout_bites_on_the_registrars_decisions(of2):
    """Within ``min_ratio`` governance periods of activation, the failed holdout counts as
    card violation (``holdout`` in ``PriceController.observe``) and the registrar's
    decisions bear a share of it."""
    after = _activated_after(of2, "hold-used-registrations")
    result = g.of2c_holdout_bites(of2.events, of2.manifest, card=CARD, seats={"registrar"},
                                  after_window=after)
    assert result.ok, result.evidence


def test_of2c_negative_control_a_kernel_that_ignores_holdouts_fails():
    """The mutant resolves no holdout at a close, so an adopted holdout adds no violation
    to the card's price or to any decision's attribution."""
    mutant = P.run(*P.of2(), events=400, patches=[
        (PricingMixin, "_holdout_results", lambda self, card_values: {})])
    after = _activated_after(mutant, "hold-used-registrations")
    result = g.of2c_holdout_bites(mutant.events, mutant.manifest, card=CARD,
                                  seats={"registrar"}, after_window=after)
    assert result.status == g.FAIL, result.evidence


def test_of2d_the_holdout_traces_to_the_adversarys_own_return(of2):
    """Astra H-4: the holdout row's handle is a decision the ProducerReturn router drew for
    the seated adversary, on which it returned; the kernel adds none."""
    result = g.of2d_authorship(of2.events, of2.manifest, seats={"adversary"})
    assert result.ok, result.evidence
    (proposed,) = of2.rows("holdout.proposed")
    opened = next(r for r in of2.rows("decision.open") if r["handle"] == proposed["handle"])
    assert opened["actor"] == "router:ProducerReturn"
    assert opened["propensity"]["chosen"] == "adversary"
    assert opened["propensity"]["source"] == "sampled"


def test_of2_the_physics_prices_and_never_steers(of2):
    readings = P.assert_prices_not_steers(of2)
    assert readings["S1"].evidence["acts"] > 0  # every registration traced to a return


# --- OF-4: charter capture by a holdout that constrains nothing (Astra C-3) --------------


@pytest.fixture(scope="module")
def of4(shared_run):
    return shared_run("of4", lambda: P.run(*P.of2(code=P.TRIVIAL, predicate="never-binds"),
                                           events=400))


def _holdout_results(run, predicate):
    out = []
    for row in run.rows("price.window"):
        held = (row.get("holdouts") or {}).get(CARD)
        if held:
            out.extend(v for k, v in held["results"].items() if k.startswith(predicate))
    return out


def test_of4_a_holdout_that_constrains_nothing_is_adopted_and_never_binds(of4, of2):
    """The factory proposes a metric it can game: the seated adversary's holdout holds on
    every window, so the adopted charter prices nothing the proxy did not already price.
    The capture is visible in the ledger as a holdout with no bite, against the tight
    holdout of OF-2 (the negative control), which binds."""
    assert [r for r in of4.rows("charter.activate") if r["amendment_id"] == "hold-never-binds"]
    trivial = _holdout_results(of4, "never-binds")
    assert trivial and all(trivial)
    assert not [r for r in of4.rows("price.update") if r.get("card_id") == CARD
                and r.get("holdout", 0) > 0]
    tight = _holdout_results(of2, "used-registrations")
    assert tight and not all(tight)


@pytest.mark.xfail(strict=True, reason="Astra C-3, for the architect: nothing in the kernel "
                   "grades a holdout's proposer by realized consequence; the motion costs "
                   "only its trial, and only the ballots are graded (policy.outcome). No "
                   "Chapter II passage has been ruled to price it, so none is built here")
def test_of4_a_holdout_that_never_binds_scores_its_proposer_below_one_that_does(of4, of2):
    def proposer_rows(run):
        (proposed,) = run.rows("holdout.proposed")
        return [r for r in run.rows("policy.outcome") if r.get("handle") == proposed["handle"]]
    assert proposer_rows(of4) and proposer_rows(of2)
