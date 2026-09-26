"""Settled metric windows carry bounded prices and ledger-first revision evidence."""

import sys
from dataclasses import dataclass, replace
from math import copysign, isfinite
from typing import Literal

from factorylab.charter.amendment import proposed_price
from factorylab.kernel.ledger import Ledger


def _number(value: float, name: str) -> float:
    """Return a finite float from a built-in int or float, never a boolean."""
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


#: The largest finite float: what an overflowing bound, violation or term is held at.
#: Codex on #152: a finite subnormal violation (5e-324) made ``cap / v`` infinity,
#: which no ledger row can state; the largest float is ledgerable and still
#: effectively unbounded (wave 16, ruling R-E: a price has no bound of its own).
FLOAT_MAX = sys.float_info.max


def held(value: float) -> float:
    """``value``, or the largest finite float of its sign where it overflowed.

    Guarantees a finite result for any non-NaN float: an infinity from an
    overflowing product or sum is held at ``FLOAT_MAX``, never ledgered as infinity.
    """
    return value if isfinite(value) else copysign(FLOAT_MAX, value)


def part(own: float, values) -> float:
    """``own`` as a share of the sum of the nonnegative ``values``, in [0, 1].

    Guarantees the share of the exact sum even where that sum overflows a float
    (violations held at ``FLOAT_MAX``; Codex on #152): every term is read relative
    to the largest first. Zero when every value is zero.
    """
    values = list(values)
    top = max(values, default=0.0)
    if top <= 0:
        return 0.0
    return (own / top) / sum(v / top for v in values)


def ratio(numerator: float, denominator: float) -> float:
    """``numerator / denominator`` for a positive ``denominator``, held finite.

    Guarantees a finite quotient: one that overflows (a measured denominator as
    small as a subnormal float) is ``FLOAT_MAX`` of its sign (``held``).
    """
    return held(held(numerator) / denominator)


@dataclass(frozen=True)
class CardRegion:
    """An immutable numeric region has its required bounds and a positive finite scale.

    Optional opposite bounds on max/min regions are validated but ignored.
    The runtime supplies numeric bounds; acceptable-region prose is never parsed.
    """

    card_id: str
    kind: Literal["max", "min", "band"]
    lo: float | None
    hi: float | None
    scale: float

    def __post_init__(self) -> None:
        if not isinstance(self.card_id, str) or not self.card_id.strip():
            raise ValueError("card_id must be a nonempty string")
        if self.kind not in ("max", "min", "band"):
            raise ValueError("kind must be max, min or band")
        for name in ("lo", "hi", "scale"):
            value = getattr(self, name)
            if name == "scale" or value is not None:
                object.__setattr__(self, name, _number(value, name))
        if self.scale <= 0:
            raise ValueError("scale must be positive")
        if self.kind in ("max", "band") and self.hi is None:
            raise ValueError("max and band regions require hi")
        if self.kind in ("min", "band") and self.lo is None:
            raise ValueError("min and band regions require lo")
        if self.kind == "band" and self.lo >= self.hi:
            raise ValueError("band requires lo < hi")


def relative_region(region: CardRegion) -> CardRegion:
    """Normalize a card by its bound magnitude, band width, or units at a zero bound.

    A twofold breach of a positive cap has distance one regardless of currency
    units. Bands use their width; zero one-sided bounds retain declared units.
    The returned scale is ledgered with the bounds for identical offline use.
    """
    if region.kind == "band":
        scale = region.hi - region.lo
    else:
        scale = abs(region.hi if region.kind == "max" else region.lo)
    return replace(region, scale=scale if scale and isfinite(scale) else region.scale)


def violation(region: CardRegion, value: float) -> float:
    """Return finite, nonnegative distance outside inclusive bounds in observation units."""
    value = _number(value, "value")
    distance = 0.0
    if region.kind in ("min", "band") and value < region.lo:
        distance = region.lo - value
    elif region.kind in ("max", "band") and value > region.hi:
        distance = value - region.hi
    # Held finite: a subnormal scale, or a distance across the whole float range,
    # overflows (Codex on #152).
    return _number(ratio(distance, region.scale), "violation")


def promise_kept(direction: str, baseline: float, value: float, region: CardRegion, *,
                 resolution: float) -> bool:
    """Grade a promise against its recorded baseline, never against compliance alone.

    A move counts once it clears ``resolution`` of the region's scale. A card that
    was outside its region kept the promise only by moving the promised way that
    far. A card already inside kept it by staying inside without moving against
    the promise; a move the wrong way is a broken promise even if the region holds.
    """
    if direction not in ("increase", "decrease"):
        raise ValueError("direction must be increase or decrease")
    if not isfinite(resolution) or resolution <= 0:
        raise ValueError("resolution must be finite and positive")
    step = resolution * region.scale
    moved = (value - baseline) * (1 if direction == "increase" else -1)
    if violation(region, baseline) == 0:
        return violation(region, value) == 0 and moved > -step
    return moved >= step


@dataclass(frozen=True)
class _CardState:
    region: CardRegion | None
    price: float = 0.0
    updates: int = 0
    saturations: int = 0
    max_step: float = 0.0
    last_window_end_event: int | None = None
    previous_violation: float = 0.0
    # The integral term: accumulated pressure. It integrates only up to the bound of
    # the window it integrates in (penalty_cap / v) and is held, never cut, while the
    # penalty sits at the cap, so a violation that spikes and subsides keeps its memory;
    # it is never used above ``episode_bound`` (ruling R10-n).
    integral: float = 0.0
    # Ruling R10-n: the largest own bound penalty_cap / v the card has seen in its
    # current failure episode (the bound of its mildest violation since it began
    # violating), 0 outside one. The episode begins at the first violating observation
    # and ends at compliance (not ``end_failure``). An integral above it (an adopted price
    # such as 1e308) prices nothing more and could never decay, so it is clamped to it;
    # a spike, whose bound is smaller, never erases the memory below it.
    episode_bound: float = 0.0
    # The last accepted observation, for the PID's derivative on measurement.
    previous_value: float | None = None
    # Consecutive windows the immune organ diagnosed this card violated inside a
    # stable-failure attractor; zero once the attractor is left.
    failing_windows: int = 0
    # Charter audit M7; wave 16, R-E: observed windows that closed with the card at its
    # bound (the penalty it presses on the reward at ``penalty_cap``), the current run
    # of consecutive such windows, and the current run of windows in violation.
    windows_at_bound: int = 0
    saturated_windows: int = 0
    violation_windows: int = 0
    # The total pressure on the reward of the roles the card answers for, at its last
    # observation: sum(lambda * v) over their cards (the runtime's reading, or the
    # card's own lambda * v). At ``penalty_cap`` no gain on the price's level exists.
    last_pressure: float = 0.0


def pressure(price: float, violation: float, cap: float) -> float:
    """One card's pressure on the reward, ``lambda * v``, saturated at ``cap``.

    A price has no bound of its own (wave 16, ruling R-E), so an adopted price as
    large as a float allows times a violation above one would overflow to infinity.
    Guarantees ``cap`` when the price is at or above the card's bound ``cap / v``
    (the penalty it presses cannot exceed the cap any reward bears), the product
    otherwise, and 0 while the card does not violate: always finite.
    """
    if violation <= 0:
        return 0.0
    if price >= ratio(cap, violation):
        return cap
    return held(price * violation)


_pressure = pressure


def _repriced(state: _CardState, price: float, cap: float) -> float:
    """The roles' pressure after this card alone moves to ``price``: the last reading
    moved by the change in the card's own saturated pressure, never below zero."""
    v = state.previous_violation
    return max(0.0, state.last_pressure - _pressure(state.price, v, cap)
               + _pressure(price, v, cap))


class PriceController:
    """Prices stay bounded; revisions require ledgered windows or adopted proposals.

    The caller supplies settled observations. Prices are soft penalties only;
    this controller has no settlement, reserve, exploration or spending authority.

    One law sets a card's price from its window violation ``e`` (distance outside
    the region, in region-relative units): the PID of essay II.II.b ("the PID
    controller through which lambda is progressively determined"; Stooke et al.
    2020). ``lambda = Kp*e + I + D``, where ``I`` accumulates ``eta * e`` while
    the card violates and leaks ``decay`` per window once it stops, and
    ``D = Kd * max(0, d(measurement))`` is taken on the measurement, not the
    error, so a moved region cannot kick the price. ``D`` acts only while the card
    violates, and only its positive part (Stooke et al.'s own choice): a violation
    growing fast is priced before the integral has had to wind up to meet it,
    which is how Kd damps the escalation before it overshoots. A violation that is
    shrinking but still outside the region is priced by ``P + I`` alone, never
    below its accumulated integral: the damping the essay asks of Kd is on the
    price's climb, not a cancellation of it. ``I`` integrates only up to the bound
    ``penalty_cap / v`` of the window it integrates in, and stops integrating (is
    held, not cut) while the penalty sits at the cap, or while the output is
    already saturated high without it (``P + I`` at that bound) *and* the violation
    is still growing (anti-windup). With ``Kp = Kd = 0`` the law is the integral
    alone.

    Feed-forward (essay II.IV.a: "Adopting a futarchic model also changes the PID
    controller … A futarchic λ, however, is necessarily forward-looking—the market
    continually reprices based on the expectation of constraint violations"). The
    runtime may pass ``anticipated``: the change in the card's violation that the
    conditional forecasts on open motions expect, ``ê - v``, where each forecast
    reads ``max(0, v + sign * q * step)`` (``charter.market.branch_violation``) and
    only forecasts that stay liable enter (see ``runtime.markets``). While the card
    violates (``v > 0``) the law adds ``F = Kp * max(anticipated, -v)``, so the
    proportional part prices the expected violation, ``P + F = Kp * max(0, ê)``,
    while ``I`` and ``D`` stay on realized measurement. A card inside its region
    takes no feed-forward: the market may move a price the world has already made
    nonzero, never create one. The combination is argued this way:

    - the backward terms remain the only memory. ``I`` still integrates realized
      violation alone and ``D`` still answers a measured move, so a market that is
      wrong cannot wind the integral up or down: its error reaches the price only
      through ``F``, for as long as the forecasts stand, and vanishes when they
      settle;
    - ``F`` uses the gain the charter already committed for converting violation into
      price, ``Kp``, so no new constant enters and a world with ``Kp = 0`` is
      exactly as backward-looking as it was;
    - ``|F| <= Kp * step`` for any one forecast, in either direction: relief and
      deepening are read symmetrically, one promise resolution at most, so no single
      forecast can cancel ``P``;
    - ``P + F >= 0``, so a card still out of its region is never priced below its
      accumulated integral (the same guarantee ``D`` keeps);
    - every forecast that moves ``F`` is scored whichever branch the committee takes
      (a seat's undecided-motion forecasts count only as a pair on both branches), so
      moving the price is never free and a mispriced ``F`` is an arbitrage the
      adversarial population profits by correcting.

    Without ``anticipated`` the law is the backward PID above, unchanged.

    One bound (wave 16, ruling R-E): ``penalty_cap``, the reward scale. A card's price
    is clipped to ``[0, penalty_cap / v]`` while it violates (``v > 0``): above it,
    ``lambda * v`` would take more than the capped penalty any reward can bear, and no
    decision's reward would change. There is no ``lambda_max``. While the card's own
    price sits at its bound (``lambda >= penalty_cap / v``) the integrator is held, never
    cut, and never above the largest own bound of the card's current failure episode
    (ruling R10-n: a spike keeps its memory, and an adopted price above every bound
    unwinds): essay II.IV.b, "gain ramped high enough to kick a
    system out of an overdamped attractor will, if unchecked, overshoot into an
    oscillation condition (thrash)", and a wound-up integral would keep the price high
    long after the attractor was left. The gate is the card's own bound only (wave 16,
    ruling R10-e): the total pressure of its roles clips each decision's penalty and is
    published, but another card's saturation never freezes this card's integrator or
    ratchet, which would be a safe harbour for its failure (§II.b: "price the duration
    of failure"). Saturation, the card's or its roles', is counted
    (``saturated_windows``, ``windows_at_bound``) and published, never answered with
    more gain.
    """

    def __init__(
        self,
        ledger: Ledger,
        *,
        eta: float,
        decay: float,
        penalty_cap: float,
        min_window_events: int,
        kp: float = 0.0,
        kd: float = 0.0,
    ) -> None:
        """Require finite rates, nonnegative gains, a cap in (0, 1) and window separation."""
        self.__kp = _number(kp, "kp")
        self.__kd = _number(kd, "kd")
        if self.__kp < 0 or self.__kd < 0:
            raise ValueError("kp and kd must be nonnegative")
        self.__eta = _number(eta, "eta")
        self.__decay = _number(decay, "decay")
        self.__cap = _number(penalty_cap, "penalty_cap")
        if min(self.__eta, self.__decay) <= 0:
            raise ValueError("eta and decay must be positive")
        if not 0 < self.__cap < 1:
            raise ValueError("penalty_cap must be in (0, 1)")
        if type(min_window_events) is not int or min_window_events < 1:
            raise ValueError("min_window_events must be a positive integer")
        self.__min_window_events = min_window_events
        self.__ledger = ledger
        self.__cards: dict[str, _CardState] = {}

    def register(self, region: CardRegion) -> None:
        """Give a new card zero price; duplicate card ids are rejected."""
        if not isinstance(region, CardRegion):
            raise ValueError("region must be a CardRegion")
        if region.card_id in self.__cards:
            raise ValueError("card_id is already registered")
        self.__cards[region.card_id] = _CardState(region)

    def register_pending(self, card_id: str) -> None:
        """Register zero price without a region; penalties wait until bounds are available."""
        if not isinstance(card_id, str) or not card_id.strip():
            raise ValueError("card_id must be a nonempty string")
        if card_id in self.__cards:
            raise ValueError("card_id is already registered")
        self.__ledger.append({"kind": "price.register", "card_id": card_id})
        self.__cards[card_id] = _CardState(None)

    def clear_region(self, card_id: str) -> None:
        """Suspend penalties for unavailable bounds while preserving the card's price."""
        state = self.__cards[card_id]
        self.__ledger.append({"kind": "price.region_cleared", "card_id": card_id})
        self.__cards[card_id] = replace(state, region=None)

    def set_price(self, card_id: str, value: float, *, amendment_id: str) -> None:
        """Ledger a bounded adopted price before mutation, preserving observation history.

        The adopted price becomes the card's accumulated (integral) pressure, so a
        PID card resumes from it without a jump (bumpless transfer).
        """
        state = self.__cards[card_id]
        price = proposed_price(value)
        if not isinstance(amendment_id, str) or not amendment_id.strip():
            raise ValueError("amendment_id is required")
        self.__ledger.append({
            "kind": "price.proposed", "card_id": card_id, "amendment_id": amendment_id,
            "lambda_before": state.price, "lambda_after": price,
        })
        self.__cards[card_id] = replace(state, price=price, integral=price,
                                        last_pressure=_repriced(state, price, self.__cap))

    def ratchet(self, card_id: str, *, window: int, step: float) -> None:
        """Raise a card's price with the duration of the failing attractor it sits in.

        Essay II.II.b: in stable failure "price the duration of failure, ratcheting
        up penalties the longer the factory spends" in the failing attractor. The
        n-th consecutive diagnosed window adds ``n * step`` to the card's
        accumulated pressure and to its price, both bounded by the card's bound
        (``penalty_cap / v``), so the raise persists through later windows until the
        violation ends and the ordinary decay unwinds it.

        At saturation the ratchet stops (wave 16, ruling R-E): when the card's own
        price sits at its bound (its own penalty at ``penalty_cap``; ruling R10-e: never
        on another card's pressure), no gain on the price's level exists, so the
        integral and the price are left unchanged, the duration keeps
        counting, and ``immune.price_ratchet_saturated`` is ledgered (card, window,
        duration, the price at its bound). Either entry is ledgered before any state
        changes.
        """
        step = _number(step, "step")
        if step <= 0:
            raise ValueError("step must be positive")
        state = self.__cards[card_id]
        duration = state.failing_windows + 1
        bound = self._bound(state.previous_violation)
        if self._at_cap(state):
            self.__ledger.append({
                "kind": "immune.price_ratchet_saturated", "card_id": card_id,
                "window": window, "duration": duration, "lambda": state.price,
                "bound": bound, "pressure": state.last_pressure,
                "penalty_cap": self.__cap, "saturated_windows": state.saturated_windows,
            })
            self.__cards[card_id] = replace(state, failing_windows=duration)
            return
        raised = step * duration
        price = state.price + raised
        integral = state.integral + raised
        if bound is not None:
            price, integral = min(bound, price), min(bound, integral)
        self.__ledger.append({
            "kind": "immune.price_ratchet", "card_id": card_id, "window": window,
            "duration": duration, "step": raised,
            "lambda_before": state.price, "lambda_after": price,
        })
        self.__cards[card_id] = replace(state, price=price, integral=integral,
                                        failing_windows=duration,
                                        last_pressure=_repriced(state, price, self.__cap))

    def _bound(self, violation: float) -> float | None:
        """The price at which a card's own penalty takes the whole cap, or None unviolated."""
        return ratio(self.__cap, violation) if violation > 0 else None

    def _at_cap(self, state: _CardState) -> bool:
        """Whether a card's own price sits at its bound at its last violation: the gate
        on its ratchet (ruling R10-e), never its roles' pressure."""
        return self._own_bound(state.price, state.previous_violation)

    def _own_bound(self, price: float, violation: float) -> bool:
        """Whether a violating card at ``price`` is at its own bound ``cap / v`` (compared
        as a price, so a price clipped to the bound is at it whatever the float product
        rounds to)."""
        return violation > 0 and price >= ratio(self.__cap, violation)

    def _presses(self, price: float, violation: float, pressure: float) -> bool:
        """Whether a violating card at ``price`` presses the cap: its price at or above
        its bound (compared as a price, so a price clipped to the bound is at it
        whatever the float product rounds to), or the roles' pressure at the cap."""
        return violation > 0 and (price >= ratio(self.__cap, violation)
                                  or pressure >= self.__cap)

    def redefine(self, card_id: str, *, edition: int | str | None = None) -> None:
        """A card redefined under the same id with a different observation is a new
        metric (Codex on #152): its failing-attractor duration, its failure episode
        (``episode_bound``, R10-n), its runs of violating and saturated windows and its
        last reading reset; its price and cumulative counts stay, the charter's to set.
        Ledgered first."""
        state = self.__cards[card_id]
        self.__ledger.append({"kind": "price.redefined", "card_id": card_id,
                              "edition": edition, "failing_windows": state.failing_windows,
                              "episode_bound": state.episode_bound})
        self.__cards[card_id] = replace(state, failing_windows=0, episode_bound=0.0,
                                        violation_windows=0, saturated_windows=0,
                                        previous_value=None, previous_violation=0.0)

    def end_failure(self, card_id: str, *, window: int) -> None:
        """Reset a card's failing-attractor duration; its price is left to the controller.

        A card that leaves the attractor starts its next ratchet from one window.
        Nothing is ledgered for a card that was not ratcheting.
        """
        state = self.__cards[card_id]
        if not state.failing_windows:
            return
        self.__ledger.append({"kind": "immune.price_ratchet_ended", "card_id": card_id,
                              "window": window, "duration": state.failing_windows})
        # The failure episode (ruling R10-n) is not ended here: the organ calls this
        # whenever it diagnoses no stable failure, and a spike can be what moves the
        # diagnosis, so ending the episode here would let the spike erase the memory
        # the episode keeps. It ends at compliance.
        self.__cards[card_id] = replace(state, failing_windows=0)

    def card_ids(self) -> tuple[str, ...]:
        """Return every registered card id, in registration order."""
        return tuple(self.__cards)

    def remove(self, card_id: str, *, amendment_id: str) -> None:
        """Drop a known card's price and region only after recording its removal."""
        state = self.__cards[card_id]
        self.__ledger.append({
            "kind": "price.removed", "card_id": card_id, "amendment_id": amendment_id,
            "lambda_before": state.price,
        })
        del self.__cards[card_id]

    def update_region(self, region: CardRegion) -> None:
        """Replace a registered card's bounds; its price and counts survive.

        The runtime calls this when a card's region is re-derived (a rolling bound
        moved, or a new edition restated the card). Unknown cards raise KeyError.
        """
        if not isinstance(region, CardRegion):
            raise ValueError("region must be a CardRegion")
        state = self.__cards[region.card_id]
        self.__cards[region.card_id] = replace(state, region=region)

    def violation(self, card_id: str, value: float) -> float:
        """Return normalized distance outside inclusive bounds; unknown cards raise KeyError."""
        region = self.__cards[card_id].region
        value = _number(value, "value")
        if region is None:
            return 0.0
        return violation(region, value)

    def observe(self, card_id: str, value: float, window_end_event: int, *,
                holdout: float = 0.0, anticipated: float | None = None,
                pressure: float | None = None) -> None:
        """Ledger each accepted update or skipped window before any state/clock changes.

        Nonnegative event indices double as logical nanosecond timestamps for
        this loop, not wall time. Accepted indices strictly increase per card.
        Saturation means the requested price was outside the bounds; max_step
        measures the largest absolute change after clipping.

        ``holdout`` is the violation the card's failed holdouts add
        (``charter.holdout_violation``), added to its region violation.
        ``anticipated`` is the market's expected change
        in the violation, for the feed-forward term (see the class docstring).
        ``pressure`` is the total ``sum(lambda * v)`` over the cards of the roles
        this card answers for, at the prices in force before this update: with it at
        ``penalty_cap`` the window is counted and published as at the bound. The
        integrator freezes on the card's own bound only (anti-windup, rulings R-E,
        R10-e). Without it the card's own ``lambda * v`` is the pressure.
        """
        holdout = _number(holdout, "holdout")
        if anticipated is not None:
            anticipated = _number(anticipated, "anticipated")
        if holdout < 0:
            raise ValueError("holdout violation must be nonnegative")
        state = self.__cards[card_id]
        value = _number(value, "value")
        if type(window_end_event) is not int or window_end_event < 0:
            raise ValueError("window_end_event must be a nonnegative integer")
        last = state.last_window_end_event
        if last is not None and window_end_event < last + self.__min_window_events:
            self.__ledger.append(
                {
                    "kind": "price.skipped",
                    "card_id": card_id,
                    "value": value,
                    "window_end_event": window_end_event,
                    "last_window_end_event": last,
                    "min_window_events": self.__min_window_events,
                    "reason": "min_window_events",
                }
            )
            return
        violation = self.violation(card_id, value) + holdout
        if pressure is not None:
            pressure = _number(pressure, "pressure")
        # At its own bound no gain on the card's price exists, and the integrator
        # holds; another card's pressure never holds it (ruling R10-e).
        frozen = self._own_bound(state.price, violation)
        # The failure episode's largest own bound, this window's included (R10-n).
        episode = (max(state.episode_bound, ratio(self.__cap, violation)) if violation > 0
                   else 0.0)
        requested, integral, terms = self._pid(state, value, violation, frozen=frozen,
                                               episode=episode)
        if anticipated is not None and violation > 0:
            feed_forward = held(self.__kp * max(anticipated, -violation))
            requested = held(requested + feed_forward)
            terms = {**terms, "f": feed_forward, "anticipated": anticipated}
        bound = self._bound(violation)
        price = max(0.0, requested) if bound is None else min(bound, max(0.0, requested))
        saturated = requested < 0 or (bound is not None and requested > bound)
        at_bound = self._presses(price, violation, pressure or 0.0)
        # The card's own pressure before and after, saturated at the cap: finite for
        # any adopted price (``pressure``).
        own_before = _pressure(state.price, violation, self.__cap)
        own_after = _pressure(price, violation, self.__cap)
        updated = replace(
            state,
            price=price,
            updates=state.updates + 1,
            saturations=state.saturations + int(saturated),
            max_step=max(state.max_step, abs(price - state.price)),
            last_window_end_event=window_end_event,
            previous_violation=violation,
            integral=integral,
            episode_bound=episode,
            previous_value=value,
            windows_at_bound=state.windows_at_bound + int(at_bound),
            saturated_windows=state.saturated_windows + 1 if at_bound else 0,
            violation_windows=state.violation_windows + 1 if violation > 0 else 0,
            # The pressure at the prices now in force: the reading (at the prices
            # before this update) moved by this card's own change.
            last_pressure=max(0.0, (own_before if pressure is None else pressure)
                              - own_before + own_after),
        )
        entry = {
            "kind": "price.update",
            "card_id": card_id,
            "value": value,
            "violation": violation,
            "previous_violation": state.previous_violation,
            "lambda_before": state.price,
            "lambda_after": price,
            "saturated": saturated,
            "window_end_event": window_end_event,
            "bound": bound,
            "at_cap": at_bound,
            "integrator_frozen": frozen,
            **({"pressure": pressure} if pressure is not None else {}),
            **terms,
            **({"holdout": holdout} if holdout else {}),
        }
        self.__ledger.append(entry)
        self.__cards[card_id] = updated

    def _pid(self, state: _CardState, value: float, violation: float, *,
             frozen: bool = False, episode: float = 0.0
             ) -> tuple[float, float, dict[str, float]]:
        """Guarantees a violating card is never priced below its accumulated integral,
        or its bound where the integral exceeds it.

        Returns the unclipped PID output, the next integral and the three
        terms. The derivative is the change in the measurement itself, signed so
        that a move deeper into violation is positive, taken only while the card
        violates, and only its positive part: a card moving back toward its region
        but still outside it keeps ``P + I``, so a shrinking violation can lower the
        price only through ``P``, never to zero while it lasts. The integral never
        integrates past ``penalty_cap / v``; it holds, unchanged, while the penalty
        sits at the cap (``frozen``; ruling R-E), and while ``P`` plus the integral
        already reaches the bound and the violation is still growing; it leaks
        ``decay`` once the card stops violating. Ruling R10-n: the integral is used
        clamped to ``episode``, the largest own bound of the card's current failure
        episode, so a spike (a smaller bound) never cuts it, and a stored value above
        every bound of the episode (an adopted 1e308, which ``decay`` could never
        unwind) is clamped at the first violating observation and at compliance.
        """
        region = state.region
        derivative = 0.0
        if violation > 0 and state.previous_value is not None and region is not None:
            if region.kind == "band":
                sign = 1.0 if value > region.hi else -1.0
            else:
                sign = 1.0 if region.kind == "max" else -1.0
            # Positive part only (Stooke et al. 2020): Kd answers a violation that is
            # getting worse. A signed term would let a card still out of its region
            # but improving fast cancel P and I and be priced at zero (essay II.II.b:
            # Kd "dampen[s] price escalation", it does not waive the price).
            derivative = max(0.0, held(self.__kd * sign * ratio(
                value - state.previous_value, region.scale)))
        proportional = held(self.__kp * violation)
        if violation <= 0:
            # The episode that ends here leaks from at most its largest bound.
            kept = state.integral
            if state.episode_bound > 0:
                kept = min(kept, state.episode_bound)
            integral = max(0.0, kept - self.__decay)
        else:
            bound = ratio(self.__cap, violation)
            # Used at most at the episode's largest own bound (ruling R10-n): above it
            # an integral prices nothing and could never decay; a spike's smaller
            # bound never cuts it.
            kept = min(episode, state.integral)
            if frozen or (proportional + kept >= bound
                          and violation > state.previous_violation):
                # At the cap, or saturated high without any new integration and still
                # climbing: hold, never wind up, and never cut: a spike's lower bound
                # clips the price, not the pressure the card has accumulated.
                integral = kept
            else:
                integral = min(bound, kept + self.__eta * violation)
        return (held(proportional + integral + derivative), integral,
                {"p": proportional, "i": integral, "d": derivative})

    def saturation(self, card_id: str) -> dict[str, float | int | None]:
        """Public per-card statistics: the card's bound and its saturation, and its
        violation's duration.

        Charter audit M7 (essay II.IV: "if it cannot be satisfied beyond what is
        priced as acceptable, then the factory needs to be scrapped"), and wave 16,
        ruling R-E (saturation is "published to governance"): ``bound`` is the price
        at which the card's own penalty takes the whole ``penalty_cap`` at its last
        violation, ``windows_at_bound`` the observed windows it closed pressing the
        cap, ``saturated_windows`` the current run of them (the card's price held at
        its bound: its shadow price exceeds what the reward channel can express), and
        ``violation_windows`` the current run of windows in violation. An
        unregistered card has zeros. Nothing here kills anything.
        """
        state = self.__cards.get(card_id) if isinstance(card_id, str) else None
        if state is None:
            return {"bound": None, "windows_at_bound": 0, "saturated_windows": 0,
                    "violation_windows": 0}
        return {"bound": self._bound(state.previous_violation),
                "windows_at_bound": state.windows_at_bound,
                "saturated_windows": state.saturated_windows,
                "violation_windows": state.violation_windows}

    def price(self, card_id: str) -> float:
        """Return the current price, or zero for any unregistered identifier."""
        state = self.__cards.get(card_id) if isinstance(card_id, str) else None
        if state is None:
            return 0.0
        return state.price

    def penalty(self, values: dict[str, float]) -> float:
        """Return the sum over known cards of each one's pressure, saturated per card at
        the cap (``pressure``) and not clipped in total; callers own score clipping."""
        return sum(
            (
                _pressure(self.price(card_id), self.violation(card_id, value), self.__cap)
                for card_id, value in values.items()
                if card_id in self.__cards
            ),
            0.0,
        )

    @property
    def decay(self) -> float:
        """Return the per-window decay applied to a card whose observation is inside its region."""
        return self.__decay

    @decay.setter
    def decay(self, value: float) -> None:
        """Replace the decay with another finite positive rate; nothing else is touched.

        The controller owns exactly one decay slot, checkpointed with the rest of
        its parameters. Whoever revises it is responsible for recording why.
        """
        self.__decay = _number(value, "decay")
        if self.__decay <= 0:
            raise ValueError("eta and decay must be positive")

    def snapshot(self) -> dict:
        """Return detached parameters and per-card prices, cadence and revision evidence."""
        return {
            "parameters": {
                "eta": self.__eta,
                "decay": self.__decay,
                "penalty_cap": self.__cap,
                "min_window_events": self.__min_window_events,
                "kp": self.__kp,
                "kd": self.__kd,
            },
            "cards": {
                card_id: {
                    "lambda": state.price,
                    "updates": state.updates,
                    "saturations": state.saturations,
                    "max_step": state.max_step,
                    "last_window_end_event": state.last_window_end_event,
                    "integral": state.integral,
                    "episode_bound": state.episode_bound,
                    "failing_windows": state.failing_windows,
                    "bound": self._bound(state.previous_violation),
                    "windows_at_bound": state.windows_at_bound,
                    "saturated_windows": state.saturated_windows,
                    "violation_windows": state.violation_windows,
                }
                for card_id, state in self.__cards.items()
            },
        }
