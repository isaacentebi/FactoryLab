"""Finite full-information games distinguish external from swap regret.

The three-action investment trap has rows Left, Mid, Right and opponent
columns Top, Bottom. Its loss matrix is ((3/8, 1), (1, 0), (1/2, 1/2)).
Play Top for floor(T/2) rounds, then Bottom for the rest, identically for both
learners. This is a fixed-parameter variant of Deng, Schneider & Sivan,
"Strategizing against No-regret Learners", section 4.1, Table 1 (2019):
https://arxiv.org/html/1909.13861v2. Learner utility u is mapped to (1-u)/2,
with Top/Left utility fixed at 1/4. Hedge banks Left's initial advantage,
then chooses Right while Mid recovers its cumulative deficit. Replacing those
Right decisions with Mid gains about 3T/16 asymptotically. The same fixed
opponent sequence allows Blum--Mansour's conditional learners to escape.

The two-action dominance game has losses ((0, 1/4), (1, 3/4)), with alternating
opponent columns. Both regret notions are equal here because replacing the
dominated action by the dominant one is the optimal fixed action AND swap.
More generally with two actions swap <= 2*max(0, external): vanishing external
regret implies vanishing swap regret, not equality of every finite total.

Histories retain each full loss vector, policy, and sampled action. Default
regret is the realized-action regret, computed from every counterfactual loss,
not a bandit estimate. expected=True instead evaluates the exact policy-weighted
counterfactual sum from the same history. External regret is signed; swap
regret includes the identity mapping and is nonnegative. These benchmarks
claim only fixed-menu, synchronous full-information behavior.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from random import Random

from .base import FullInfoFeedback, Learner, _actions, _probabilities


@dataclass(frozen=True)
class MatrixGame:
    """Rows are unique learner actions and columns are unique opponent moves."""

    actions: tuple[str, ...]
    opponent_actions: tuple[str, ...]
    losses: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        _actions(self.actions)
        _actions(self.opponent_actions)
        if len(self.losses) != len(self.actions) or any(
            len(row) != len(self.opponent_actions) for row in self.losses
        ):
            raise ValueError("loss matrix shape must match action sets")
        assert all(math.isfinite(v) and 0 <= v <= 1 for row in self.losses for v in row)

    def loss_vector(self, opponent_action: str) -> dict[str, float]:
        """Return all counterfactual learner losses for the selected opponent move."""
        column = self.opponent_actions.index(opponent_action)
        return {a: self.losses[i][column] for i, a in enumerate(self.actions)}


@dataclass(frozen=True)
class Round:
    """A full loss vector and the actual policy accompany each sampled action."""

    losses: dict[str, float]
    probs: dict[str, float]
    chosen: str

    def __post_init__(self) -> None:
        _actions(tuple(self.losses))
        FullInfoFeedback(self.losses)
        _probabilities(self.probs, tuple(self.losses))
        if self.chosen not in self.probs or self.probs[self.chosen] <= 0:
            raise ValueError("chosen action must have positive probability")
        object.__setattr__(self, "losses", dict(self.losses))
        object.__setattr__(self, "probs", dict(self.probs))


def investment_trap() -> MatrixGame:
    """Return the three-action fixed-matrix external/swap separation game."""
    return MatrixGame(
        ("Left", "Mid", "Right"), ("Top", "Bottom"), ((0.375, 1.0), (1.0, 0.0), (0.5, 0.5))
    )


def trap_sequence(rounds: int) -> tuple[str, ...]:
    """Return a predetermined Top-then-Bottom sequence of the requested length."""
    if rounds < 0:
        raise ValueError("rounds must be nonnegative")
    return ("Top",) * (rounds // 2) + ("Bottom",) * (rounds - rounds // 2)


def two_action_game() -> MatrixGame:
    """Return a dominance game where finite external and swap regret coincide."""
    return MatrixGame(("better", "worse"), ("even", "odd"), ((0.0, 0.25), (1.0, 0.75)))


def simulate(
    game: MatrixGame, learner: Learner, opponent_sequence: Sequence[str], *, seed: int = 0
) -> list[Round]:
    """Return reproducible full-information history, updating once per sampled round."""
    rng = Random(seed)
    history = []
    for opponent_action in opponent_sequence:
        losses = game.loss_vector(opponent_action)
        probs = learner.distribution(game.actions)
        _probabilities(probs, game.actions)
        chosen = rng.choices(game.actions, weights=[probs[a] for a in game.actions], k=1)[0]
        history.append(Round(losses, probs, chosen))
        learner.update(FullInfoFeedback(losses))
    return history


def _history_actions(history: Sequence[Round]) -> tuple[str, ...]:
    if not history:
        return ()
    actions = tuple(history[0].losses)
    for round_ in history:
        if set(round_.losses) != set(actions):
            raise ValueError("regret requires a fixed full-information action universe")
        FullInfoFeedback(round_.losses)
        _probabilities(round_.probs, actions)
        if round_.chosen not in actions:
            raise ValueError("history contains an unknown chosen action")
    return actions


def external_regret(history: Sequence[Round], *, expected: bool = False) -> float:
    """Return total loss minus the best fixed action's full-history loss, without clipping."""
    actions = _history_actions(history)
    if not actions:
        return 0.0
    incurred = math.fsum(
        math.fsum(r.probs[a] * r.losses[a] for a in actions) if expected else r.losses[r.chosen]
        for r in history
    )
    return incurred - min(math.fsum(r.losses[a] for r in history) for a in actions)


def swap_regret(history: Sequence[Round], *, expected: bool = False) -> float:
    """Return the exact best fixed mapping's improvement, including the identity mapping."""
    actions = _history_actions(history)
    return math.fsum(
        max(
            math.fsum(
                (r.probs[a] if expected else float(r.chosen == a)) * (r.losses[a] - r.losses[b])
                for r in history
            )
            for b in actions
        )
        for a in actions
    )
