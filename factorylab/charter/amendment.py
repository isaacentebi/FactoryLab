"""Frozen proposals can change metric cards, never the charter's norms."""

import re
from dataclasses import dataclass
from math import isfinite

from factorylab.charter.charter import MetricCard


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


@dataclass(frozen=True)
class Amendment:
    """A candidate's typed card changes and predicted effect cannot change after creation.

    Norm membership and card existence are checked against the current charter
    by ``CharterBook.propose``. There is no operation for editing norms.
    """

    id: str
    proposer_handle: str
    edition_base: int
    add: tuple[MetricCard, ...]
    replace: tuple[MetricCard, ...]
    remove: tuple[str, ...]
    predicted_effect: str
    proposed_prices: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or re.fullmatch(r"[a-z][a-z0-9-]{1,47}", self.id) is None:
            raise ValueError("amendment id must be a slug of 2-48 chars")
        if not isinstance(self.proposer_handle, str) or not self.proposer_handle.strip():
            raise ValueError("proposer_handle is required")
        if type(self.edition_base) is not int or self.edition_base < 1:
            raise ValueError("edition_base must be a positive integer")
        if (
            not isinstance(self.predicted_effect, str)
            or not self.predicted_effect.strip()
            or len(self.predicted_effect) > 2000
        ):
            raise ValueError("predicted_effect must be non-empty and at most 2000 chars")
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
        eligible = {card.id for card in (*self.add, *self.replace)}
        if any(card_id not in eligible for card_id, _ in prices):
            raise ValueError("lambda may only accompany an added or replaced card")
        if len({card_id for card_id, _ in prices}) != len(prices):
            raise ValueError("a card may have only one proposed lambda")
        object.__setattr__(self, "proposed_prices", prices)
