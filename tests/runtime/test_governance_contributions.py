"""Chapter II §II.b/IV.a: governance facts retain their actual decision owner."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factorylab.charter.amendment import Amendment, PredictedEffect
from factorylab.charter.book import CharterBook
from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.measurement import CardSamples
from factorylab.cortex.registration import ChallengeProposal
from factorylab.cortex.request import Return
from factorylab.runtime.governance import GovernanceMixin, Retirement
from factorylab.runtime.pricing import MeasureWindow, PricingMixin


class GovernanceHarness(GovernanceMixin, PricingMixin):
    def __init__(self):
        self.window = MeasureWindow(3, 0)
        self.price_windows = {}
        self.price_origins = {}
        self.return_kinds = {"owner": ("Action",)}
        self.handle_to_assembly = {"owner": "seat"}
        self.assemblies = {}
        self.registered_observations = {}
        self.observation_runner = SimpleNamespace(run=Mock())
        self.ledger = SimpleNamespace(append=Mock())
        self.clock = SimpleNamespace(now_ns=10)
        card = MetricCard("formed", "quality", "formed returns", "fraction",
                          {"kind": "returns", "n": 10, "per": "role"},
                          {"rule": "at least", "lo": 0.9}, "well_formed_rate", "producer")
        self.charter = Charter(1, ("quality",), (card,))
        self.charter_book = CharterBook(self.ledger, self.charter)
        self.stats = SimpleNamespace(amendments_proposed=0, amendments_activated=0)
        self.card_samples = CardSamples()
        self.challenges = {}
        self.kind_reward_shapes = {}
        self._register_proposal = Mock()
        self._derive_regions = Mock()
        self.priced = set()
        self._activate_policy_ballots = Mock()
        self.controller = SimpleNamespace(price=lambda _: 1, set_price=Mock(),
                                          register_pending=Mock())


def _proposal(motion_id="motion"):
    return {"id": motion_id, "lambda": {"formed": 2},
            "predicted_effect": {"card_id": "formed", "direction": "increase", "window": 1}}


def test_direct_proposals_accumulate_only_for_the_actual_owner():
    rt = GovernanceHarness()
    rt._propose_amendment("owner", _proposal("first"))
    rt._propose_amendment("owner", _proposal("second"))
    assert rt.window.amendments_proposed == 2
    assert rt.window.decisions.get("owner", {}).get("amendments_proposed") == 2
    assert set(rt.window.decisions) == {"owner"}


def test_accepted_registration_marks_only_one_revised_decision():
    rt = GovernanceHarness()
    rt.stats.registrations_accepted = rt.stats.registrations_rejected = 0
    rt._event_kinds = lambda: frozenset({"Tick"})
    rt.prices = SimpleNamespace(prices={})
    rt.tool_specs = {}
    rt.tool_jail_available = True
    rt.ev = SimpleNamespace(trial_amount_micro=0)
    rt.retired_assemblies = set()
    challenge = {
        "kind": "challenge", "card_id": "formed", "evidence": "a changed threshold",
        "trial_windows": 1,
        "replacement": {"observation": "well_formed_rate", "rule": "at least",
                        "value": 0.8, "window": {"kind": "returns", "n": 10, "per": "role"}},
        "predicted_effect": {"card_id": "formed", "direction": "increase", "window": 1},
    }
    rt._apply_registrations("owner", Return("owner", {"register": [challenge]}, 0, "ok"))
    assert rt.window.revision_handles == {"owner"}
    assert rt.window.decisions["owner"]["registrations"] == 1
    assert rt.window.decisions["owner"]["revised_decisions"] == 1
    rt._record_motion_revision("owner")
    assert rt.window.decisions["owner"]["revised_decisions"] == 1


def test_refused_direct_proposal_does_not_record_a_contribution():
    rt = GovernanceHarness()
    with pytest.raises(ValueError, match="unchanged"):
        rt._propose_amendment("owner", {**_proposal(), "lambda": {"formed": 1}})
    assert rt.window.amendments_proposed == 0
    assert rt.window.decisions == {}


def test_deferred_challenge_records_proposal_at_ballot_not_trial():
    rt = GovernanceHarness()
    rt._register_challenge(
        "owner", ChallengeProposal(
            card_id="formed", evidence="different measured threshold", trial_windows=1,
            replacement={"observation": "well_formed_rate", "rule": "at least",
                         "value": 0.8, "window": {"kind": "returns", "n": 10,
                                                  "per": "role"}}),
        predicted_effect=PredictedEffect("formed", "increase", 1))
    assert rt.window.decisions == {}
    rt._close_challenge_window(rt.window.index)
    rt._ballot_due_challenges()
    rt._ballot_due_challenges()
    assert rt.window.amendments_proposed == 1
    assert rt.window.decisions.get("owner", {}).get("amendments_proposed") == 1


@pytest.mark.parametrize("row_window, revised", [(None, False), (3, False), (3, True),
                                               (2, False)])
def test_activated_editions_record_counts_without_duplicate_rows(row_window, revised):
    rt = GovernanceHarness()
    if row_window is not None:
        rt.card_samples.returned(handle="owner", assembly="seat", role="producer",
                                 window=row_window, ret=Return("owner", {}, 0, "ok"))
        if revised:
            rt.card_samples.revised("owner")
    for edition in (2, 3):
        am = Amendment(id=f"motion-{edition}", proposer_handle="owner", edition_base=edition - 1,
                       add=(), replace=(), remove=(), proposed_prices=(("formed", edition),),
                       predicted_effect=PredictedEffect("formed", "increase", 1))
        rt._apply_edition(Charter(edition, rt.charter.norms, rt.charter.cards), am)
    assert rt.window.amendments_activated == rt.window.revision_returns == 2
    sample = rt.window.decisions.get("owner", {})
    assert sample.get("amendments_activated") == 2
    assert sample.get("supplemental", {}).get("revision_returns") == (
        1 if row_window == 3 and not revised else 2)
    assert sample.get("revised_decisions", 0) == 0


@pytest.mark.parametrize("stale", [False, True])
def test_retirement_revision_belongs_to_proposer_only_after_activation(stale):
    rt = GovernanceHarness()
    rt.assemblies["target"] = SimpleNamespace(spec=SimpleNamespace(version=1))
    rt.retired_assemblies = {"target"} if stale else set()
    rt._retire_assembly = Mock()
    rt._censor_ballots = Mock()
    motion = Retirement("retire", "owner", "target", 1,
                        PredictedEffect("formed", "increase", 1))
    rt.retirement_proposals = {motion.id: {"status": "passed", "proposal": motion}}
    rt._activate_retirements_if_due()
    rt._activate_retirements_if_due()
    assert rt.window.revision_returns == int(not stale)
    if stale:
        assert rt.window.decisions == {}
    else:
        sample = rt.window.decisions.get("owner", {})
        assert sample.get("supplemental", {}).get("revision_returns") == 1
        assert sample.get("revised_decisions", 0) == 0
        assert "target" not in rt.window.decisions
