"""Settled metric windows carry bounded prices and ledger-first revision evidence."""

from dataclasses import dataclass, replace
from math import isfinite
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
    return _number(distance / region.scale, "violation")


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
    # The integral term: accumulated pressure, bounded to [0, lambda_max].
    integral: float = 0.0
    # The last accepted observation, for the PID's derivative on measurement.
    previous_value: float | None = None
    # Consecutive windows the immune organ diagnosed this card violated inside a
    # stable-failure attractor; zero once the attractor is left.
    failing_windows: int = 0
    # Charter audit M7: observed windows that closed with the card priced at
    # lambda_max, and the current run of consecutive observed windows in violation.
    windows_at_max: int = 0
    violation_windows: int = 0


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
    price's climb, not a cancellation of it. ``I`` is held in ``[0, lambda_max]``
    and stops integrating only while the output is already saturated high without
    it (``P + I >= lambda_max``) *and* the violation is still growing
    (anti-windup). With ``Kp = Kd = 0`` the law is the integral alone.

    Feed-forward (essay II.IV.a: "Adopting a futarchic model also changes the PID
    controller … A futarchic λ, however, is necessarily forward-looking—the market
    continually reprices based on the expectation of constraint violations"). The
    runtime may pass ``anticipated``: the change in the card's violation that the
    conditional forecasts on open motions expect, ``ê - v``
    (``charter.market.expected_violation``). The law adds
    ``F = Kp * max(anticipated, -v)``, so the proportional part prices the expected
    violation, ``P + F = Kp * max(0, ê)``, while ``I`` and ``D`` stay on realized
    measurement. The combination is argued this way:

    - the backward terms remain the only memory. ``I`` still integrates realized
      violation alone and ``D`` still answers a measured move, so a market that is
      wrong cannot wind the integral up or down: its error reaches the price only
      through ``F``, for as long as the forecasts stand, and vanishes when they
      settle;
    - ``F`` uses the gain the charter already committed for converting violation into
      price, ``Kp``, so no new constant enters and a world with ``Kp = 0`` is
      exactly as backward-looking as it was;
    - ``P + F >= 0``, so a card still out of its region is never priced below its
      accumulated integral, however strongly the market expects relief (the same
      guarantee ``D`` keeps);
    - a mispriced ``F`` is an arbitrage: the forecasts that drive it are scored
      against the branch the world takes, so the adversarial population profits by
      correcting it.

    Without ``anticipated`` the law is the backward PID above, unchanged.

    The price is clipped to ``[0, lambda_max]``.
    """

    def __init__(
        self,
        ledger: Ledger,
        *,
        eta: float,
        decay: float,
        lambda_max: float,
        min_window_events: int,
        kp: float = 0.0,
        kd: float = 0.0,
    ) -> None:
        """Require finite rates, nonnegative gains and positive bounds/window separation."""
        self.__kp = _number(kp, "kp")
        self.__kd = _number(kd, "kd")
        if self.__kp < 0 or self.__kd < 0:
            raise ValueError("kp and kd must be nonnegative")
        self.__eta = _number(eta, "eta")
        self.__decay = _number(decay, "decay")
        self.__lambda_max = _number(lambda_max, "lambda_max")
        if min(self.__eta, self.__decay, self.__lambda_max) <= 0:
            raise ValueError("eta, decay and lambda_max must be positive")
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
        price = proposed_price(value, self.__lambda_max)
        if not isinstance(amendment_id, str) or not amendment_id.strip():
            raise ValueError("amendment_id is required")
        self.__ledger.append({
            "kind": "price.proposed", "card_id": card_id, "amendment_id": amendment_id,
            "lambda_before": state.price, "lambda_after": price,
        })
        self.__cards[card_id] = replace(state, price=price, integral=price)

    def ratchet(self, card_id: str, *, window: int, step: float) -> None:
        """Raise a card's price with the duration of the failing attractor it sits in.

        Essay II.II.b: in stable failure "price the duration of failure, ratcheting
        up penalties the longer the factory spends" in the failing attractor. The
        n-th consecutive diagnosed window adds ``n * step`` to the card's
        accumulated pressure and to its price, both bounded by ``lambda_max``, so
        the raise persists through later windows until the violation ends and the
        ordinary decay unwinds it. The entry is ledgered before any state changes.
        """
        step = _number(step, "step")
        if step <= 0:
            raise ValueError("step must be positive")
        state = self.__cards[card_id]
        duration = state.failing_windows + 1
        raised = step * duration
        price = min(self.__lambda_max, state.price + raised)
        integral = min(self.__lambda_max, state.integral + raised)
        self.__ledger.append({
            "kind": "immune.price_ratchet", "card_id": card_id, "window": window,
            "duration": duration, "step": raised,
            "lambda_before": state.price, "lambda_after": price,
        })
        self.__cards[card_id] = replace(state, price=price, integral=integral,
                                        failing_windows=duration)

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
                holdout: float = 0.0, anticipated: float | None = None) -> None:
        """Ledger each accepted update or skipped window before any state/clock changes.

        Nonnegative event indices double as logical nanosecond timestamps for
        this loop, not wall time. Accepted indices strictly increase per card.
        Saturation means the requested price was outside the bounds; max_step
        measures the largest absolute change after clipping.

        ``holdout`` is the violation the card's failed holdouts add
        (``charter.holdout_violation``); the card is priced on the larger of it
        and its region violation. ``anticipated`` is the market's expected change
        in the violation, for the feed-forward term (see the class docstring).
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
        violation = max(self.violation(card_id, value), holdout)
        requested, integral, terms = self._pid(state, value, violation)
        if anticipated is not None:
            feed_forward = self.__kp * max(anticipated, -violation)
            requested += feed_forward
            terms = {**terms, "f": feed_forward, "anticipated": anticipated}
        price = min(self.__lambda_max, max(0.0, requested))
        saturated = requested < 0 or requested > self.__lambda_max
        updated = replace(
            state,
            price=price,
            updates=state.updates + 1,
            saturations=state.saturations + int(saturated),
            max_step=max(state.max_step, abs(price - state.price)),
            last_window_end_event=window_end_event,
            previous_violation=violation,
            integral=integral,
            previous_value=value,
            windows_at_max=state.windows_at_max + int(price >= self.__lambda_max),
            violation_windows=state.violation_windows + 1 if violation > 0 else 0,
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
            **terms,
            **({"holdout": holdout} if holdout else {}),
        }
        self.__ledger.append(entry)
        self.__cards[card_id] = updated

    def _pid(self, state: _CardState, value: float,
             violation: float) -> tuple[float, float, dict[str, float]]:
        """Guarantees a violating card is never priced below its accumulated integral.

        Returns the unclipped PID output, the next bounded integral and the three
        terms. The derivative is the change in the measurement itself, signed so
        that a move deeper into violation is positive, taken only while the card
        violates, and only its positive part: a card moving back toward its region
        but still outside it keeps ``P + I``, so a shrinking violation can lower the
        price only through ``P``, never to zero while it lasts. The integral never
        leaves ``[0, lambda_max]`` and holds only while ``P`` plus the integral
        already reaches ``lambda_max`` and the violation is still growing.
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
            derivative = max(0.0, self.__kd * sign * (value - state.previous_value)
                             / region.scale)
        proportional = self.__kp * violation
        if violation <= 0:
            integral = max(0.0, state.integral - self.__decay)
        elif (proportional + state.integral >= self.__lambda_max
              and violation > state.previous_violation):
            # Saturated high without any new integration and still climbing: hold,
            # never wind up. A violation that is flat or easing keeps integrating
            # even when P alone saturates, so I is there when P falls away.
            integral = state.integral
        else:
            integral = min(self.__lambda_max, state.integral + self.__eta * violation)
        return (proportional + integral + derivative, integral,
                {"p": proportional, "i": integral, "d": derivative})

    def saturation(self, card_id: str) -> dict[str, int]:
        """Public per-card statistics: windows priced at lambda_max, and violation duration.

        Charter audit M7 (essay II.IV: "if it cannot be satisfied beyond what is
        priced as acceptable, then the factory needs to be scrapped"): the
        evidence on which that threat could be invoked. Counted in observed
        windows; an unregistered card has zeros. Nothing here kills anything.
        """
        state = self.__cards.get(card_id) if isinstance(card_id, str) else None
        if state is None:
            return {"windows_at_lambda_max": 0, "violation_windows": 0}
        return {"windows_at_lambda_max": state.windows_at_max,
                "violation_windows": state.violation_windows}

    def price(self, card_id: str) -> float:
        """Return the current price, or zero for any unregistered identifier."""
        state = self.__cards.get(card_id) if isinstance(card_id, str) else None
        if state is None:
            return 0.0
        return state.price

    def penalty(self, values: dict[str, float]) -> float:
        """Return the unclipped sum for known cards only; callers own score clipping."""
        return sum(
            (
                self.price(card_id) * self.violation(card_id, value)
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
            raise ValueError("eta, decay and lambda_max must be positive")

    def snapshot(self) -> dict:
        """Return detached parameters and per-card prices, cadence and revision evidence."""
        return {
            "parameters": {
                "eta": self.__eta,
                "decay": self.__decay,
                "lambda_max": self.__lambda_max,
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
                    "failing_windows": state.failing_windows,
                    "windows_at_lambda_max": state.windows_at_max,
                    "violation_windows": state.violation_windows,
                }
                for card_id, state in self.__cards.items()
            },
        }
