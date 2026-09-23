"""Frozen proposals can change metric cards, never the charter's norms."""

import re
from dataclasses import dataclass
from math import isfinite

from factorylab.charter.charter import MetricCard


def proposed_answers_for(value: object, card_id: str) -> str:
    """Population cards explicitly name a supported scoring role, independent of card id."""
    if not isinstance(value, str) or value.strip().lower() not in (
        "producer", "evaluator", "meta", "antagonist", "all",
    ):
        raise ValueError(
            f"card {card_id} answers_for: expected producer, evaluator, meta, antagonist or all"
        )
    return value.strip().lower()


def proposed_price(value: object, lambda_max: float) -> float:
    """Return a finite proposed lambda within the inclusive bound, rejecting booleans."""
    reason = f"lambda must be a finite number in [0, {lambda_max}]; booleans are invalid"
    if type(value) not in (int, float):
        raise ValueError(reason)
    try:
        price = float(value)
    except OverflowError as exc:
        raise ValueError(reason) from exc
    if not isfinite(price) or not 0 <= price <= lambda_max:
        raise ValueError(reason)
    return price


def proposed_tick_interval(value: object, min_ns: int, max_ns: int | float) -> int:
    """Return exact integer nanoseconds for a duration within inclusive clock bounds."""
    reason = f"tick_interval must be a duration string within [{min_ns}ns, {max_ns}ns]"
    if not isinstance(value, str):
        raise ValueError(reason)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ns|s|m|h|d)", value.strip())
    if match is None:
        raise ValueError(reason)
    units = {"ns": 1, "s": 10**9, "m": 60 * 10**9, "h": 3600 * 10**9, "d": 86400 * 10**9}
    whole, _, fraction = match[1].partition(".")
    amount, remainder = divmod(
        int(whole + fraction) * units[match[2]], 10 ** len(fraction)
    )
    if remainder or not min_ns <= amount <= max_ns:
        raise ValueError(reason)
    return int(amount)


#: The seed observation a clock motion's prediction may name: compute spent per window.
BURN_OBSERVATION = "burn_per_window"
#: The three change classes a motion may carry, one per motion (charter audit P3).
CHANGE_CLASSES = ("cards", "lambda", "clock")


@dataclass(frozen=True)
class PredictedEffect:
    """A policy forecast binds one target, a strict direction and a post-activation window count.

    The target is a card (``card_id``) or, for a clock motion, an observation
    (``observation``): exactly one of the two.
    """

    card_id: str | None
    direction: str
    window: int
    observation: str | None = None

    def __post_init__(self) -> None:
        if (self.card_id is None) == (self.observation is None):
            raise ValueError("predicted_effect names exactly one of card_id or observation")
        target = self.card_id if self.observation is None else self.observation
        name = "card_id" if self.observation is None else "observation"
        if not isinstance(target, str) or not target.strip():
            raise ValueError(f"predicted_effect.{name} is required")
        if self.direction not in ("increase", "decrease"):
            raise ValueError("predicted_effect.direction must be increase or decrease")
        if type(self.window) is not int or self.window < 1:
            raise ValueError("predicted_effect.window must be a positive count of closed windows")

    @classmethod
    def parse(cls, value: object) -> "PredictedEffect":
        """Reject unfalsifiable prose without inventing a direction or evaluation horizon."""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            present = {k: v for k, v in value.items() if v is not None}
            if set(present) in ({"card_id", "direction", "window"},
                                {"observation", "direction", "window"}):
                return cls(**{"card_id": None, **present})
        raise ValueError("predicted_effect needs card_id (or observation), direction and window")

    def as_dict(self) -> dict:
        """The wire form: the one target it names, the direction and the window."""
        target = ({"card_id": self.card_id} if self.observation is None
                  else {"observation": self.observation})
        return {**target, "direction": self.direction, "window": self.window}


def effect_schema() -> dict:
    """The accepted prediction schema names no preferred direction or horizon."""
    return {"type": "object", "properties": {
        "card_id": {"type": "string"},
        "observation": {"type": "string"},
        "direction": {"enum": ["increase", "decrease"]},
        "window": {"type": "integer", "minimum": 1},
    }, "required": ["direction", "window"], "additionalProperties": False}


@dataclass(frozen=True)
class Amendment:
    """A candidate's one change and its own predicted effect cannot change after creation.

    A motion carries exactly one change class (charter audit P3): cards (add,
    replace, remove), lambda (prices for current cards) or clock
    (``tick_interval``), and a clock motion predicts its effect on an
    observation, since speed is cash burn (essay II.IV). Norm membership and
    card existence are checked against the current charter by
    ``CharterBook.propose``. There is no operation for editing norms.
    """

    id: str
    proposer_handle: str
    edition_base: int
    add: tuple[MetricCard, ...]
    replace: tuple[MetricCard, ...]
    remove: tuple[str, ...]
    predicted_effect: PredictedEffect
    proposed_prices: tuple[tuple[str, float], ...] = ()
    tick_interval: str | None = None
    #: ``(card_id, "predicate@version")``: append one holdout to the card as it stands
    #: when the motion activates (charter audit M3), never to a copy frozen earlier.
    holdout: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or re.fullmatch(r"[a-z][a-z0-9-]{1,47}", self.id) is None:
            raise ValueError("amendment id must be a slug of 2-48 chars")
        if not isinstance(self.proposer_handle, str) or not self.proposer_handle.strip():
            raise ValueError("proposer_handle is required")
        if type(self.edition_base) is not int or self.edition_base < 1:
            raise ValueError("edition_base must be a positive integer")
        object.__setattr__(self, "predicted_effect", PredictedEffect.parse(self.predicted_effect))
        if self.tick_interval is not None:
            proposed_tick_interval(self.tick_interval, 1, float("inf"))
        ids = []
        for name in ("add", "replace", "remove"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)):
                raise ValueError(f"{name} must be a sequence of cards or card ids")
            values = tuple(values)
            object.__setattr__(self, name, values)
            for value in values:
                if name == "remove":
                    card_id = value
                else:
                    if not isinstance(value, MetricCard):
                        raise ValueError(
                            f"{name} must contain MetricCard instances; norms are read-only"
                        )
                    card_id = value.id
                if not isinstance(card_id, str) or not card_id.strip():
                    raise ValueError("card ids must be non-empty strings")
                ids.append(card_id)
        if len(set(ids)) != len(ids):
            raise ValueError("a card id may occur only once across amendment operations")
        prices = tuple(
            (card_id, proposed_price(value, float("inf")))
            for card_id, value in self.proposed_prices
        )
        if any(not isinstance(card_id, str) or not card_id.strip() for card_id, _ in prices):
            raise ValueError("lambda names a card id")
        if len({card_id for card_id, _ in prices}) != len(prices):
            raise ValueError("a card may have only one proposed lambda")
        object.__setattr__(self, "proposed_prices", prices)
        if self.holdout is not None:
            from factorylab.charter.charter import HOLDOUT_RE

            card_id, entry = tuple(self.holdout)
            if (not isinstance(card_id, str) or not card_id.strip()
                    or not isinstance(entry, str) or HOLDOUT_RE.fullmatch(entry) is None):
                raise ValueError("holdout is (card_id, predicate@version)")
            if self.add or self.replace or self.remove:
                raise ValueError("a holdout motion carries its holdout alone")
            object.__setattr__(self, "holdout", (card_id, entry))
        classes = self.change_classes()
        if len(classes) > 1:
            raise ValueError("a motion carries one change class (cards, lambda or clock); "
                             f"this one carries {' and '.join(classes)}")
        if "clock" in classes and self.predicted_effect.observation is None:
            raise ValueError("a clock motion predicts its effect on an observation")
        if "clock" not in classes and self.predicted_effect.observation is not None:
            raise ValueError("a cards or lambda motion predicts its effect on a card")

    def change_classes(self) -> tuple[str, ...]:
        """The change classes this motion carries, in ``CHANGE_CLASSES`` order."""
        present = {"cards": bool(self.add or self.replace or self.remove or self.holdout),
                   "lambda": bool(self.proposed_prices),
                   "clock": self.tick_interval is not None}
        return tuple(name for name in CHANGE_CLASSES if present[name])
