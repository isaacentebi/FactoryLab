"""Round three, group B: the action a decision is learned under is the action it took
(triage row T15), at the probability the seat declared for it. Nothing here touches a
network."""


import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.runtime.propensity import declared_record
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.conftest import make_runtime


def test_the_mass_declared_on_the_action_taken_is_recorded_as_declared():
    """Audit s06 #4: the declaration is the seat's own accounting (essay II.I.b) and is
    never floored; what bounds its weight is the estimator inside the learner."""
    tiny = 1e-6
    record, reason = declared_record(
        "hold", {"hold": tiny, "buy:BTC": 1 - tiny}, learner_id="assembly:x", state_hash="h")
    assert reason is None
    assert set(record.action_ids) == {"hold", "buy:BTC"} and record.chosen == "hold"
    mass = dict(zip(record.action_ids, record.probs, strict=True))
    assert mass["hold"] == pytest.approx(tiny, rel=1e-12)
    assert sum(record.probs) == pytest.approx(1.0)
    for declared in ({"hold": 1e-9, "a": 0.5, "b": 0.5 - 1e-9},
                     {"hold": 0.04, "a": 0.96},
                     {"hold": 0.01, "a": 0.33, "b": 0.33, "c": 0.33}):
        lopsided, _ = declared_record("hold", declared, learner_id="assembly:x", state_hash="h")
        recorded = dict(zip(lopsided.action_ids, lopsided.probs, strict=True))
        total = sum(declared.values())
        assert recorded["hold"] == pytest.approx(declared["hold"] / total, rel=1e-12)
    # A declaration without the action taken is still refused, recorded degenerate.
    refused, why = declared_record("hold", {"buy:BTC": 1.0}, learner_id="assembly:x",
                                   state_hash="h")
    assert why and refused.probs == (1.0,) and refused.action_ids == ("hold",)


def test_truthful_rare_propensity_survives_learning_and_restore():
    """Audit s06 #4's probe: a seat that declared hold at .01 is recorded, frozen and
    learned at .01 through a checkpoint; the seat's learner (off-policy) bounds the
    step inside its estimator, l / (pi + eta / 2), never by rewriting the .01."""
    rt = make_runtime()
    seat = "seed-decider"
    rt.assembly_learners[seat] = SnapshotLearner(
        EXP3(("hold", "buy:BTC"), id=f"assembly:{seat}", off_policy=True))
    handle = rt.queue.open(actor="router:Tick", event_id="t",
                           propensity=PropensityRecord((seat,), (1.0,), seat, 0,
                                                       "router:Tick", "h"),
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    record, reason = declared_record("hold", {"hold": 0.01, "buy:BTC": 0.99},
                                     learner_id=f"assembly:{seat}", state_hash="h")
    assert reason is None
    rt.queue.record_propensity(handle, record)
    rt._open_assembly_round(seat, handle, record)
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    learner = restored.assembly_learners[seat]
    (frozen,) = learner.state()["snapshots"].values()
    assert frozen["executed"]["hold"] == pytest.approx(0.01, rel=1e-12)
    assert restored.queue.declared_propensity(handle).probs[0] == pytest.approx(0.01)
    eta = frozen["eta"]
    restored.queue.settle(handle, channel="verdict", score=0.0, status=SettleStatus.SETTLED,
                          definition_version="t", sampling_ref=None)
    restored._close_assembly_round(handle, 0.0)
    losses = learner.inner.state()["losses"]
    assert losses["hold"] == pytest.approx(1.0 / (0.01 + eta / 2))
    assert losses["buy:BTC"] == 0.0
    assert eta * losses["hold"] <= 2  # one step moves a logit by at most eta / beta
    learned = [i for i in restored.ledger._recovery_items() if i["kind"] == "propensity.learned"]
    assert learned and learned[-1]["propensity"] == pytest.approx(0.01)
