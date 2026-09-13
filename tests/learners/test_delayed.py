import math
from dataclasses import FrozenInstanceError
from random import Random

import pytest

from factorylab.learners.base import BanditFeedback, FullInfoFeedback, Learner
from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.delayed import SnapshotLearner
from factorylab.learners.exp3 import EXP3
from factorylab.learners.hedge import Hedge
from tests.learners.reference_games import (
    Round,
    external_regret,
    investment_trap,
    simulate,
    simulate_delayed,
    swap_regret,
    trap_sequence,
)


@pytest.mark.parametrize("kind", ["hedge", "exp3", "blum_mansour"])
def test_immediate_ordered_delivery_matches_synchronous(kind):
    actions = ("a", "b", "c")

    def make():
        if kind == "exp3":
            return EXP3(actions, 0.3)
        if kind == "hedge":
            return Hedge(actions, 0.4)
        return BlumMansour(lambda a: Hedge(a, 0.4), actions)

    synchronous = make()
    inner = make()
    delayed = SnapshotLearner(inner)
    assert isinstance(delayed, Learner)
    assert delayed.id == inner.id
    rng = Random(0)
    for index in range(60):
        support = (actions, ("c", "a"), ("b",))[index % 3]
        p = synchronous.distribution(support)
        assert delayed.distribution_for(str(index), support) == p
        chosen = rng.choices(support, weights=list(p.values()))[0]
        losses = {a: rng.random() for a in actions}
        feedback = (
            BanditFeedback(chosen, 1 - losses[chosen], p[chosen])
            if kind == "exp3" else FullInfoFeedback(losses)
        )
        synchronous.update(feedback)
        delayed.update_for(str(index), feedback)
        assert delayed.state()["inner"] == synchronous.state()
        assert not delayed.state()["snapshots"]


def test_investment_trap_at_5000_seed_zero_separates_delivery_from_adaptation():
    game = investment_trap()
    count = 5000
    eta = math.sqrt(8 * math.log(len(game.actions)) / count)
    sequence = trap_sequence(count)

    def make():
        return BlumMansour(lambda a: Hedge(a, eta), game.actions)

    synchronous = simulate(game, make(), sequence, seed=0)
    immediate = SnapshotLearner(make())
    immediate_history = []
    rng = Random(0)
    for index, opponent in enumerate(sequence):
        losses = game.loss_vector(opponent)
        p = immediate.distribution_for(str(index), game.actions)
        chosen = rng.choices(game.actions, weights=list(p.values()))[0]
        immediate_history.append(Round(losses, p, chosen))
        immediate.update_for(str(index), FullInfoFeedback(losses))

    order = list(range(count))
    ordered = SnapshotLearner(make())
    ordered_history = simulate_delayed(game, ordered, sequence, delay_permutation=order)
    Random(0).shuffle(order)
    shuffled = SnapshotLearner(make())
    shuffled_history = simulate_delayed(game, shuffled, sequence, delay_permutation=order)
    assert [r.probs for r in immediate_history] == [r.probs for r in synchronous]
    assert immediate_history == synchronous
    assert [r.probs for r in shuffled_history] == [r.probs for r in ordered_history]
    assert shuffled_history == ordered_history
    assert all(r.probs == dict.fromkeys(game.actions, 1 / 3) for r in shuffled_history)
    # A fixed opponent sequence does not supply feedback during the opening
    # phase. Comparing this batch to an adaptive synchronous run is invalid.
    assert shuffled_history[1].probs != synchronous[1].probs
    for expected in (False, True):
        for regret in (external_regret, swap_regret):
            reference = regret(synchronous, expected=expected)
            batch = regret(ordered_history, expected=expected)
            assert regret(immediate_history, expected=expected) == pytest.approx(
                reference, rel=0, abs=1e-9,
            )
            assert regret(shuffled_history, expected=expected) == pytest.approx(
                batch, rel=0, abs=1e-9,
            )
            print(
                f"T={count}, seed=0, {regret.__name__}, expected={expected}: "
                f"synchronous/immediate={reference:.12f}; ordered/shuffled batch={batch:.12f}"
            )
    # Identical histories alone cannot establish that the updates were applied.
    ordered_bases = ordered.state()["inner"]["bases"]
    shuffled_bases = shuffled.state()["inner"]["bases"]
    for left, right in zip(ordered_bases, shuffled_bases, strict=True):
        left_logs = left["log_weights"]
        right_logs = right["log_weights"]
        assert right_logs == pytest.approx(left_logs, rel=0, abs=1e-9)
        assert min(right_logs.values()) < -1


@pytest.mark.parametrize("reduction", [False, True])
def test_handle_lifecycle_plain_update_and_state(reduction):
    actions = ("a", "b")
    inner = (
        BlumMansour(lambda a: Hedge(a, 0.2), actions) if reduction else Hedge(actions, 0.2)
    )
    learner = SnapshotLearner(inner, id="delayed")
    assert learner.id == "delayed"
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]
    feedback = FullInfoFeedback({"a": 0, "b": 1})
    with pytest.raises(KeyError):
        learner.update_for("missing", feedback)
    with pytest.raises(TypeError, match="update_for"):
        learner.update(feedback)
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]

    returned = learner.distribution_for("round", actions)
    before = learner.state()
    assert before["snapshots"]
    saved = before
    assert saved["inner"] == inner.state()
    assert set(saved["snapshots"]) == {"round"}
    if reduction:
        assert saved["snapshots"]["round"]["support"] == list(actions)
        assert len(saved["snapshots"]["round"]["rows"]) == len(actions)
    else:
        assert saved["snapshots"]["round"] == returned
    returned["a"] = -1
    assert learner.state() == before
    with pytest.raises(KeyError):
        learner.distribution_for("round", ("b",))
    assert learner.state() == before
    learner.distribution_for("second", ("b",))
    learner.update_for("round", feedback)
    assert set(learner.state()["snapshots"]) == {"second"}
    with pytest.raises(KeyError):
        learner.update_for("round", feedback)
    with pytest.raises(KeyError):
        learner.distribution_for("round", actions)
    learner.update_for("second", feedback)
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]


def test_plain_distribution_delegates_without_snapshot():
    inner = BlumMansour(lambda a: Hedge(a, 0.2), ("a", "b"))
    learner = SnapshotLearner(inner)
    assert learner.distribution(inner.actions) == inner.distribution(inner.actions)
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]
    with pytest.raises(RuntimeError, match="pending"):
        learner.distribution(("a",))
    inner.update(FullInfoFeedback({"a": 0, "b": 1}))
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]


def test_invalid_feedback_keeps_snapshot_for_retry():
    inner = BlumMansour(lambda a: Hedge(a, 0.2), ("a", "b"))
    learner = SnapshotLearner(inner)
    learner.distribution_for("old", inner.actions)
    before = learner.state()
    with pytest.raises(ValueError):
        learner.update_for("old", FullInfoFeedback({"a": 1}))
    with pytest.raises(TypeError):
        learner.update_for("old", BanditFeedback("a", 1, 0.5))
    assert learner.state() == before
    learner.update_for("old", FullInfoFeedback({"a": 0, "b": 1}))
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]


def test_explicit_snapshot_is_immutable_owned_and_preserves_plain_pending_round():
    inner = BlumMansour(lambda a: Hedge(a, 0.2), ("a", "b"))
    with pytest.raises(RuntimeError):
        inner.snapshot()
    p = inner.distribution(inner.actions)
    snapshot = inner.snapshot()
    assert dict(snapshot.p) == p
    with pytest.raises(FrozenInstanceError):
        snapshot.support = ("b",)
    with pytest.raises(TypeError):
        snapshot.rows[0][0] = ("a", -1)
    with pytest.raises(RuntimeError):
        inner.update(FullInfoFeedback({"a": 0, "b": 1}))
    other = BlumMansour(lambda a: Hedge(a, 0.2), inner.actions)
    with pytest.raises(ValueError, match="belong"):
        other.update_from_snapshot(snapshot, FullInfoFeedback({"a": 0, "b": 1}))
    current = inner.distribution(("b",))
    before = inner.state()
    with pytest.raises(ValueError):
        inner.update_from_snapshot(snapshot, FullInfoFeedback({"a": 0}))
    assert inner.state() == before
    inner.update_from_snapshot(snapshot, FullInfoFeedback({"a": 0, "b": 1}))
    assert inner.distribution(("b",)) == current
    inner.update(FullInfoFeedback({"a": 1, "b": 0}))


def test_full_info_updates_use_old_p_with_interleaved_rounds_and_supports():
    bases = []

    class RecordingHedge(Hedge):
        def update(self, feedback):
            self.received = feedback
            super().update(feedback)

    def factory(actions):
        base = RecordingHedge(actions, 0.4)
        base.update(FullInfoFeedback({a: (i + len(bases)) % 3 / 2
                                      for i, a in enumerate(actions)}))
        bases.append(base)
        return base

    inner = BlumMansour(factory, ("a", "b", "c"))
    learner = SnapshotLearner(inner)
    old_p = learner.distribution_for("old", ("c", "a"))
    learner.distribution_for("fast", inner.actions)
    learner.update_for("fast", FullInfoFeedback({"a": 1, "b": 0, "c": 0.2}))
    new_p = learner.distribution_for("new", inner.actions)
    assert new_p["a"] != old_p["a"]
    losses = {"a": 0.3, "b": 1, "c": 0.8}
    learner.update_for("old", FullInfoFeedback(losses))
    for action, base in zip(inner.actions, bases, strict=True):
        assert base.received.losses == {
            a: old_p.get(action, 0) * loss for a, loss in losses.items()
        }
    learner.update_for("new", FullInfoFeedback(losses))
    for action, base in zip(inner.actions, bases, strict=True):
        assert base.received.losses == {a: new_p[action] * loss for a, loss in losses.items()}


def test_shuffled_bandit_feedback_uses_frozen_master_policy_and_all_base_rows():
    bases = []

    class RecordingEXP3(EXP3):
        def update_observed_gain(self, action, gain, proposal_probability):
            self.received = action, gain, proposal_probability
            super().update_observed_gain(action, gain, proposal_probability)

    def factory(actions):
        base = RecordingEXP3(actions, 0.3)
        base.update(BanditFeedback(actions[len(bases)], 1, 0.05 * (len(bases) + 1)))
        bases.append(base)
        return base

    inner = BlumMansour(factory, ("a", "b", "c"))
    learner = SnapshotLearner(inner)
    rng = Random(0)
    rounds = []
    expected_logs = [base.state()["log_weights"] for base in bases]
    for index in range(30):
        support = (inner.actions, ("c", "a"), ("b", "c"))[index % 3]
        rows = [base.distribution(support) for base in bases]
        p = learner.distribution_for(str(index), support)
        chosen = rng.choices(support, weights=list(p.values()))[0]
        rounds.append((p, rows, BanditFeedback(chosen, rng.random(), p[chosen])))
    order = list(range(len(rounds)))
    rng.shuffle(order)
    first_p, _, first_feedback = rounds[order[0]]
    before = learner.state()
    with pytest.raises(ValueError, match="propensity"):
        learner.update_for(
            str(order[0]), BanditFeedback(first_feedback.action, 0.5,
                                         first_p[first_feedback.action] / 2),
        )
    assert learner.state() == before
    for index in order:
        p, rows, feedback = rounds[index]
        learner.update_for(str(index), feedback)
        for action, base, row, logs in zip(inner.actions, bases, rows, expected_logs, strict=True):
            k, gain, denominator = base.received
            assert k == feedback.action
            assert denominator == row[k]
            assert gain == p.get(action, 0) * feedback.reward * row[k] / feedback.propensity
            logs[k] += base.gamma / len(inner.actions) * gain / denominator
    for base, logs in zip(bases, expected_logs, strict=True):
        offset = max(logs.values())
        assert base.state()["log_weights"] == pytest.approx(
            {a: value - offset for a, value in logs.items()}, rel=0, abs=1e-12,
        )
    assert learner.state()["inner"] == inner.state()
    assert not learner.state()["snapshots"]


def test_simulator_opens_every_round_before_any_feedback():
    game = investment_trap()
    events = []

    class Traced(SnapshotLearner):
        def distribution_for(self, handle, feasible):
            events.append(("open", handle))
            return super().distribution_for(handle, feasible)

        def update_for(self, handle, feedback):
            events.append(("update", handle))
            return super().update_for(handle, feedback)

    learner = Traced(Hedge(game.actions, 0.2))
    simulate_delayed(game, learner, trap_sequence(3), delay_permutation=[2, 0, 1])
    assert events == [
        ("open", "0"), ("open", "1"), ("open", "2"),
        ("update", "2"), ("update", "0"), ("update", "1"),
    ]


@pytest.mark.parametrize("order", [[], [0, 1], [0, 0, 2], [-1, 0, 1], [0, 1, 3],
                                  [0, 1, 2, 3], [0, 1, 2.0], [False, 1, 2]])
def test_invalid_permutation_fails_without_mutation(order):
    game = investment_trap()
    learner = SnapshotLearner(Hedge(game.actions, 0.2))
    before = learner.state()
    with pytest.raises(ValueError, match="permutation"):
        simulate_delayed(game, learner, trap_sequence(3), delay_permutation=order)
    assert learner.state() == before


def test_empty_simulation_and_invalid_opponent_leave_state_unchanged():
    game = investment_trap()
    learner = SnapshotLearner(Hedge(game.actions, 0.2))
    before = learner.state()
    assert simulate_delayed(game, learner, [], delay_permutation=[]) == []
    with pytest.raises(ValueError):
        simulate_delayed(game, learner, ["Top", "invalid"], delay_permutation=[0, 1])
    assert learner.state() == before
