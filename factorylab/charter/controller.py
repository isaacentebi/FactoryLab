"""Settled metric windows carry bounded prices and ledger-first revision evidence."""

from dataclasses import dataclass, replace
from math import isfinite
from typing import Literal

from factorylab.charter.amendment import proposed_price
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.timing import TimingRegistry


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


def violation(region: CardRegion, value: float) -> float:
    """Return finite, nonnegative distance outside inclusive bounds in observation units."""
    value = _number(value, "value")
    distance = 0.0
    if region.kind in ("min", "band") and value < region.lo:
        distance = region.lo - value
    elif region.kind in ("max", "band") and value > region.hi:
        distance = value - region.hi
    return _number(distance / region.scale, "violation")


@dataclass(frozen=True)
class _CardState:
    region: CardRegion | None
    price: float = 0.0
    updates: int = 0
    saturations: int = 0
    max_step: float = 0.0
    last_window_end_event: int | None = None
    previous_violation: float = 0.0
    relief_window: int | None = None


class PriceController:
    """Prices stay bounded; revisions require ledgered windows or adopted proposals.

    The caller supplies settled observations. Prices are soft penalties only;
    this controller has no settlement, reserve, exploration or spending authority.
    """

    def __init__(
        self,
        ledger: Ledger,
        *,
        eta: float,
        decay: float,
        lambda_max: float,
        min_window_events: int,
        timing: TimingRegistry | None = None,
        kappa: float = 0.5,
    ) -> None:
        """Require finite rates, nonnegative damping and positive bounds/window separation."""
        self.__kappa = _number(kappa, "kappa")
        if self.__kappa < 0:
            raise ValueError("kappa must be nonnegative")
        self.__eta = _number(eta, "eta")
        self.__decay = _number(decay, "decay")
        self.__lambda_max = _number(lambda_max, "lambda_max")
        if min(self.__eta, self.__decay, self.__lambda_max) <= 0:
            raise ValueError("eta, decay and lambda_max must be positive")
        if type(min_window_events) is not int or min_window_events < 1:
            raise ValueError("min_window_events must be a positive integer")
        self.__min_window_events = min_window_events
        self.__ledger = ledger
        self.__timing = timing
        self.__cards: dict[str, _CardState] = {}

    def register(self, region: CardRegion) -> None:
        """Give a new card zero price and exclusive ownership of its timing closures.

        Reuse an unstarted price loop's declared dependencies, or register a leaf
        if absent. Existing closure history and duplicate card ids are rejected.
        """
        if not isinstance(region, CardRegion):
            raise ValueError("region must be a CardRegion")
        if region.card_id in self.__cards:
            raise ValueError("card_id is already registered")
        if self.__timing is not None:
            loop_id = f"price:{region.card_id}"
            try:
                count = self.__timing.closure_count(loop_id)
            except KeyError:
                self.__timing.register_loop(loop_id, [])
            else:
                if count:
                    raise ValueError("price timing loop must have no prior closures")
        self.__cards[region.card_id] = _CardState(region)

    def register_pending(self, card_id: str) -> None:
        """Register zero price without a region; penalties wait until bounds are available."""
        if not isinstance(card_id, str) or not card_id.strip():
            raise ValueError("card_id must be a nonempty string")
        if card_id in self.__cards:
            raise ValueError("card_id is already registered")
        self.__ledger.append({"kind": "price.register", "card_id": card_id})
        if self.__timing is not None:
            try:
                self.__timing.closure_count(f"price:{card_id}")
            except KeyError:
                self.__timing.register_loop(f"price:{card_id}", [])
        self.__cards[card_id] = _CardState(None)

    def clear_region(self, card_id: str) -> None:
        """Suspend penalties for unavailable bounds while preserving the card's price."""
        state = self.__cards[card_id]
        self.__ledger.append({"kind": "price.region_cleared", "card_id": card_id})
        self.__cards[card_id] = replace(state, region=None)

    def set_price(self, card_id: str, value: float, *, amendment_id: str) -> None:
        """Ledger a bounded adopted price before mutation, preserving observation history."""
        state = self.__cards[card_id]
        price = proposed_price(value, self.__lambda_max)
        if not isinstance(amendment_id, str) or not amendment_id.strip():
            raise ValueError("amendment_id is required")
        self.__ledger.append({
            "kind": "price.proposed", "card_id": card_id, "amendment_id": amendment_id,
            "lambda_before": state.price, "lambda_after": price,
        })
        self.__cards[card_id] = replace(state, price=price)

    def relieve(self, card_id: str, *, window: int) -> None:
        """Halve the effective price for one window without erasing accumulated pressure."""
        state = self.__cards[card_id]
        self.__ledger.append({
            "kind": "immune.price_relief", "card_id": card_id, "window": window,
            "lambda": state.price, "effective_lambda": state.price / 2,
        })
        self.__cards[card_id] = replace(state, relief_window=window)

    def expire_relief(self, *, window: int) -> None:
        """Restore underlying prices after the relief window's settlements have completed."""
        for card_id, state in tuple(self.__cards.items()):
            if state.relief_window is not None and state.relief_window <= window:
                self.__ledger.append({
                    "kind": "immune.price_relief_expired", "card_id": card_id,
                    "window": window, "lambda": state.price,
                })
                self.__cards[card_id] = replace(state, relief_window=None)

    def remove(self, card_id: str, *, amendment_id: str) -> None:
        """Drop a known card's price and region only after recording its removal."""
        state = self.__cards[card_id]
        self.__ledger.append({
            "kind": "price.removed", "card_id": card_id, "amendment_id": amendment_id,
            "lambda_before": state.price,
        })
        del self.__cards[card_id]

    def update_region(self, region: CardRegion) -> None:
        """Replace a registered card's bounds; its price, counts and timing history survive.

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

    def observe(self, card_id: str, value: float, window_end_event: int) -> None:
        """Ledger each accepted update or skipped window before any state/clock changes.

        Nonnegative event indices double as logical nanosecond timestamps for
        this loop, not wall time. Accepted indices strictly increase per card.
        Saturation means the requested price was outside the bounds; max_step
        measures the largest absolute change after clipping.
        """
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
        violation = self.violation(card_id, value)
        damping = (
            self.__kappa * max(0.0, state.previous_violation - violation)
            if violation > 0 else 0.0
        )
        requested = (
            state.price + self.__eta * violation - damping
            if violation > 0 else state.price - self.__decay
        )
        price = min(self.__lambda_max, max(0.0, requested))
        saturated = requested < 0 or requested > self.__lambda_max
        updated = _CardState(
            region=state.region,
            price=price,
            updates=state.updates + 1,
            saturations=state.saturations + int(saturated),
            max_step=max(state.max_step, abs(price - state.price)),
            last_window_end_event=window_end_event,
            previous_violation=violation,
            relief_window=state.relief_window,
        )
        self.__ledger.append(
            {
                "kind": "price.update",
                "card_id": card_id,
                "value": value,
                "violation": violation,
                "previous_violation": state.previous_violation,
                "damping": damping,
                "lambda_before": state.price,
                "lambda_after": price,
                "saturated": saturated,
                "window_end_event": window_end_event,
            }
        )
        if self.__timing is not None:
            self.__timing.record_closure(f"price:{card_id}", window_end_event)
        self.__cards[card_id] = updated

    def price(self, card_id: str) -> float:
        """Return the current price, or zero for any unregistered identifier."""
        state = self.__cards.get(card_id) if isinstance(card_id, str) else None
        if state is None:
            return 0.0
        return state.price / 2 if state.relief_window is not None else state.price

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

    def snapshot(self) -> dict:
        """Return detached parameters and per-card prices, cadence and revision evidence."""
        return {
            "parameters": {
                "eta": self.__eta,
                "kappa": self.__kappa,
                "decay": self.__decay,
                "lambda_max": self.__lambda_max,
                "min_window_events": self.__min_window_events,
            },
            "cards": {
                card_id: {
                    "lambda": state.price,
                    "effective_lambda": self.price(card_id),
                    "relief_window": state.relief_window,
                    "updates": state.updates,
                    "saturations": state.saturations,
                    "max_step": state.max_step,
                    "last_window_end_event": state.last_window_end_event,
                }
                for card_id, state in self.__cards.items()
            },
        }
