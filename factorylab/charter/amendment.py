"""Frozen proposals can change metric cards, never the charter's norms."""

import re
from dataclasses import dataclass

from factorylab.charter.charter import MetricCard


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
