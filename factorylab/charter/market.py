"""The charter's markets: λ posted by the factory, and conditional forecasts on motions.

Essay II.IV.a. λ "is the marginal worth of that constraint at the factory's current
operating point; it is a kind of a *shadow price* … It reaches the committee as a
speculative price posted by the factory." And: "a futarchic model opens up
conditional prediction markets … any representative error that misprices the
marginal worth of a constraint is penalized through the conventional reward
channel." This module holds the arithmetic of both markets, with no runtime state:
the scoring rules, the aggregate the committee reads, and the expected violation a
motion's conditional forecasts imply for the price law's feed-forward term.
"""

from __future__ import annotations

from math import isfinite

#: How a λ post is scored, and how a conditional forecast on a motion is scored.
LAMBDA_POST_DEFINITION = "lambda-post-quadratic-v1"
MOTION_FORECAST_DEFINITION = "motion-forecast-brier-v1"
BRANCHES = ("enact", "reject")


def post_score(posted: float, realized: float, lambda_max: float) -> float:
    """The quadratic score of a posted price against the realized one, in [0, 1].

    ``1 - ((posted - realized) / lambda_max) ** 2``. For a realized price ``Y`` in
    ``[0, lambda_max]`` the expected score ``1 - E[(p - Y)^2] / lambda_max^2`` is
    ``1 - (Var Y + (p - E[Y])^2) / lambda_max^2``, which is maximized only at
    ``p = E[Y]``: the rule is strictly proper for the mean, so a seat's best post
    is its honest expectation of the price the law will realize.
    """
    for name, value in (("posted", posted), ("realized", realized), ("lambda_max", lambda_max)):
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if lambda_max <= 0:
        raise ValueError("lambda_max must be positive")
    if not (0 <= posted <= lambda_max and 0 <= realized <= lambda_max):
        raise ValueError("posted and realized prices lie in [0, lambda_max]")
    return 1.0 - ((posted - realized) / lambda_max) ** 2


def brier(q: float, y: int | bool) -> float:
    """The Brier score ``1 - (q - y)^2`` of a probability against a binary outcome."""
    if type(q) not in (int, float) or not isfinite(q) or not 0 <= q <= 1:
        raise ValueError("q must be a probability")
    return 1.0 - (float(q) - float(bool(y))) ** 2


def standing(scores: tuple[int, float] | None) -> float:
    """A seat's weight in the posted aggregate: its mean settled post score, shrunk to 1/2.

    ``(1/2 + sum of scores) / (1 + n)``: a seat with no settled post weighs one
    half, and a seat's track record moves its weight toward its mean score as
    posts settle. ``scores`` is ``(n, sum)``.
    """
    n, total = scores if scores is not None else (0, 0.0)
    return (0.5 + total) / (1 + n)


def weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    """The lower weighted median of ``(value, weight)`` pairs with positive total weight.

    No post moves the aggregate further than its own weight's share allows: a
    median, not a mean, so one seat posting an extreme price shifts it by at most
    one rank.
    """
    rows = sorted((value, weight) for value, weight in pairs if weight > 0)
    if not rows:
        return None
    half = sum(weight for _, weight in rows) / 2
    running = 0.0
    for value, weight in rows:
        running += weight
        if running >= half:
            return value
    return rows[-1][0]


def violation_sign(kind: str, lo: float | None, hi: float | None, value: float,
                   direction: str) -> int:
    """+1 when moving the measurement in ``direction`` deepens a card's violation, else -1.

    For a ceiling an increase deepens it; for a floor a decrease does; for a band,
    the move toward the nearer edge of the side the measurement stands on.
    """
    if direction not in ("increase", "decrease"):
        raise ValueError("direction must be increase or decrease")
    up = 1 if direction == "increase" else -1
    if kind == "max":
        return up
    if kind == "min":
        return -up
    upper_side = value >= (lo + hi) / 2
    return up if upper_side else -up


def expected_violation(violation: float, forecasts: list[tuple[float, int]],
                       step: float) -> float:
    """The violation a motion's conditional forecasts expect at their horizon.

    Each forecast ``(q, sign)`` is the market's probability ``q`` that the card
    moves the way the motion predicts, ``sign`` saying whether that way deepens
    the violation (+1) or relieves it (-1). A promise kept toward the region is
    read as the violation relieved, a broken one as the violation staying where it
    is: ``(1 - q) * v``. A promise kept away from the region is read as the
    violation deepened by at least one promise resolution (``step``, in the same
    region-relative units), a broken one as unchanged: ``v + q * step``. The market's
    expectation is the mean over its forecasts, never below zero.
    """
    if not forecasts:
        return violation
    values = [((1 - q) * violation) if sign < 0 else (violation + q * step)
              for q, sign in forecasts]
    return max(0.0, sum(values) / len(values))
