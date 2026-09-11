import itertools
import math

import pytest

from factorylab.learners.blum_mansour import BlumMansour
from factorylab.learners.hedge import Hedge
from factorylab.learners.reference_games import (
    MatrixGame,
    Round,
    external_regret,
    investment_trap,
    simulate,
    swap_regret,
    trap_sequence,
    two_action_game,
)


def test_regret_comparison_at_5000_seed_zero():
    game = investment_trap()
    rounds = 5000
    eta = math.sqrt(8 * math.log(len(game.actions)) / rounds)
    sequence = trap_sequence(rounds)
    hedge = simulate(game, Hedge(game.actions, eta), sequence, seed=0)
    reduction = simulate(
        game, BlumMansour(lambda a: Hedge(a, eta), game.actions), sequence, seed=0
    )
    assert [r.losses for r in hedge] == [r.losses for r in reduction]
    hedge_swap = swap_regret(hedge) / rounds
    bm_swap = swap_regret(reduction) / rounds
    print(f"T={rounds}, seed=0: Hedge swap/T={hedge_swap:.8f}; BlumMansour swap/T={bm_swap:.8f}")
    # 0.15 < the trap's 3/16 limit; 0.03 allows finite-horizon error while separating the learners.
    assert hedge_swap > 0.15
    assert bm_swap < 0.03
    assert external_regret(hedge) / rounds < 0.02
    assert swap_regret(hedge, expected=True) / rounds > 0.15
    assert swap_regret(reduction, expected=True) / rounds < 0.03
    # A shorter, independently parameterized horizon verifies decreasing normalized BM regret.
    short_t = 1000
    short_eta = math.sqrt(8 * math.log(len(game.actions)) / short_t)
    short = simulate(
        game, BlumMansour(lambda a: Hedge(a, short_eta), game.actions),
        trap_sequence(short_t), seed=0,
    )
    assert bm_swap < swap_regret(short) / short_t


@pytest.mark.parametrize("expected", [False, True])
def test_exact_regret_matches_enumerating_every_mapping(expected):
    actions = ("a", "b", "c")
    history = [
        Round({"a": 0.8, "b": 0, "c": 1}, {"a": 0.7, "b": 0.2, "c": 0.1}, "a"),
        Round({"a": 0, "b": 1, "c": 0.3}, {"a": 0.1, "b": 0.2, "c": 0.7}, "c"),
        Round({"a": 0.5, "b": 0.4, "c": 0.6}, {"a": 0.3, "b": 0.5, "c": 0.2}, "b"),
    ]
    actual = sum(
        sum(r.probs[a] * r.losses[a] for a in actions) if expected else r.losses[r.chosen]
        for r in history
    )
    comparator = min(sum(r.losses[a] for r in history) for a in actions)
    assert external_regret(history, expected=expected) == pytest.approx(actual - comparator)
    alternatives = []
    for destinations in itertools.product(actions, repeat=3):
        mapping = dict(zip(actions, destinations, strict=True))
        alternatives.append(sum(
            sum(r.probs[a] * r.losses[mapping[a]] for a in actions)
            if expected else r.losses[mapping[r.chosen]]
            for r in history
        ))
    assert swap_regret(history, expected=expected) == pytest.approx(actual - min(alternatives))


def test_two_action_regrets_coincide_in_dominance_game_for_both_learners():
    game = two_action_game()
    for learner in (Hedge(game.actions, 0.1), BlumMansour(lambda a: Hedge(a, 0.1), game.actions)):
        history = simulate(game, learner, ("even", "odd") * 2500, seed=0)
        for expected in (False, True):
            external = external_regret(history, expected=expected)
            swap = swap_regret(history, expected=expected)
            assert swap == pytest.approx(external)
            assert swap / len(history) < 0.01


def test_two_action_general_bound_does_not_claim_equal_finite_totals():
    history = [
        Round({"a": 1, "b": 0}, {"a": 1, "b": 0}, "a"),
        Round({"a": 0, "b": 1}, {"a": 0, "b": 1}, "b"),
    ]
    assert external_regret(history) == 1
    assert swap_regret(history) == 2 * external_regret(history)


def test_empty_signed_regret_and_invalid_history():
    assert external_regret([]) == swap_regret([]) == 0
    history = [
        Round({"a": 0, "b": 1}, {"a": 1, "b": 0}, "a"),
        Round({"a": 1, "b": 0}, {"a": 0, "b": 1}, "b"),
    ]
    assert external_regret(history) == -1
    assert swap_regret(history) == 0
    history.append(Round({"c": 0}, {"c": 1}, "c"))
    with pytest.raises(ValueError):
        swap_regret(history)
    with pytest.raises(ValueError):
        MatrixGame(("a",), ("b",), ((0, 1),))
    with pytest.raises(AssertionError):
        MatrixGame(("a",), ("b",), ((1.1,),))


def test_simulation_reproducibility():
    game = investment_trap()
    first = simulate(game, Hedge(game.actions, 0.1), trap_sequence(20), seed=0)
    second = simulate(game, Hedge(game.actions, 0.1), trap_sequence(20), seed=0)
    assert first == second
