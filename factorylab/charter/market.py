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


def post_score(posted: float, realized: float, scale: float) -> float:
    """The quadratic score of a posted price against the realized one, in [0, 1].

    ``1 - ((posted - realized) / scale) ** 2``, ``scale`` the posts' range, the price
    at which a unit violation's penalty takes the whole ``penalty_cap`` (wave 16,
    R-E). For a realized price ``Y`` in ``[0, scale]`` the expected score ``1 - E[(p -
    Y)^2] / scale^2`` is ``1 - (Var Y + (p - E[Y])^2) / scale^2``, which is maximized
    only at ``p = E[Y]``: the rule is strictly proper for the mean, so a seat's best
    post is its honest expectation of the price the law will realize.
    """
    for name, value in (("posted", posted), ("realized", realized), ("scale", scale)):
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if scale <= 0:
        raise ValueError("scale must be positive")
    if not (0 <= posted <= scale and 0 <= realized <= scale):
        raise ValueError("posted and realized prices lie in [0, scale]")
    return 1.0 - ((posted - realized) / scale) ** 2


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


def branch_violation(violation: float, q: float, sign: int, step: float) -> float:
    """The violation one conditional forecast expects on its branch, symmetric in direction.

    A forecast is the probability ``q`` that the card moves the way its motion
    predicts by at least one promise resolution (``step``, region-relative units).
    ``sign`` is +1 when that way deepens the violation and -1 when it relieves it.
    The expectation is ``max(0, v + sign * q * step)``: a forecast of relief lowers
    it exactly as far as an equally confident forecast of deepening raises it, and
    neither moves it by more than one resolution.
    """
    if sign not in (-1, 1):
        raise ValueError("sign is +1 or -1")
    if type(q) not in (int, float) or not isfinite(q) or not 0 <= q <= 1:
        raise ValueError("q must be a probability")
    return max(0.0, violation + sign * q * step)


def enactment_rate(passed: int, failed: int) -> float:
    """The factory's realized rate of enactment, ``(passed + 1) / (passed + failed + 2)``.

    The weight a seat's enact-branch forecast carries in the feed-forward while a
    motion is undecided; the reject branch carries the rest.
    """
    if min(passed, failed) < 0:
        raise ValueError("counts are nonnegative")
    return (passed + 1) / (passed + failed + 2)


#: The fewest scopes a window's marginal consequence is read from.
MIN_MARGIN_SCOPES = 3


def margin(points: list[dict]) -> dict:
    """A window's realized marginal consequence and marginal spend per unit of violation.

    Essay II.IV.a: λ is "the marginal worth of that constraint at the factory's
    current operating point". Each point is one scope of a card in one closed
    window: ``v``, its violation in region-relative units; ``consequence``, the mean
    world-measured consequence of the scope's decisions in that window (in [0, 1]:
    ``return_paid_off``, a declined trade's priced outcome, a judgement's consequence
    score); ``micro_usd``, their mean compute cost. The margins are the least-squares
    slopes across scopes: ``slope`` is consequence gained per unit of violation, and
    ``micro_usd_per_violation`` compute spent per unit of violation. Relaxing the
    constraint by one unit is worth ``slope`` of reward: a λ below it means violating
    pays. With fewer than ``MIN_MARGIN_SCOPES`` points, or no variance in ``v``, the
    margins are not identifiable and are None, never a default.
    """
    rows = [p for p in points if p.get("consequence") is not None]
    out: dict = {"scopes": len(rows), "slope": None, "micro_usd_per_violation": None}
    if len(rows) < MIN_MARGIN_SCOPES:
        return out
    vs = [float(p["v"]) for p in rows]
    mean_v = sum(vs) / len(vs)
    var = sum((v - mean_v) ** 2 for v in vs)
    if var <= 0:
        return out

    def slope(key: str) -> float:
        ys = [float(p[key]) for p in rows]
        mean_y = sum(ys) / len(ys)
        return sum((v - mean_v) * (y - mean_y) for v, y in zip(vs, ys, strict=True)) / var

    out["slope"] = slope("consequence")
    out["micro_usd_per_violation"] = slope("micro_usd")
    return out


def shadow_price(points: list[dict], scale: float) -> float | None:
    """The realized shadow price a λ post is scored against, or None when unidentified.

    ``clip(margin(points).slope, 0, scale)``: the reward one more unit of violation
    bought in the world, which the committee's own λ does not enter, on the posts'
    scale (``post_score``).
    """
    slope = margin(points)["slope"]
    return None if slope is None else min(scale, max(0.0, slope))
