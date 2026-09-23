"""A card's acceptable region is typed data, and its prose is derived from it.

Charter audit P2: a region used to be a sentence the runtime re-parsed, and a
challenge's promise was inferred from the sentence's first words. The region is
now ``{rule, lo, hi}``; the sentence a charter renders is a function of it. A
sentence is still accepted as input (every charter written before this module
states its regions that way), and is read into the same typed rule.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: The one deferred rule: its bound is the card's own median over the previous
#: window, read from the rolling record, never from the card.
PREVIOUS_MEDIAN = "below the median of the previous window"
#: Rules bounded below, above, on both sides, and the deferred one.
LOWER_RULES = ("at least", "above")
UPPER_RULES = ("at most", "below")
RULES = (*LOWER_RULES, *UPPER_RULES, "between", PREVIOUS_MEDIAN)

_NUMBER = r"([-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?|zero|one)"
_WORDS = {"zero": 0.0, "one": 1.0}


def _bound(value: object, name: str) -> float:
    """A finite built-in number, never a boolean."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"region.{name} must be a finite number")
    return float(value)


def number_text(value: float) -> str:
    """The shortest exact text of a bound: an integral value without its point."""
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


@dataclass(frozen=True)
class CardRule:
    """One rule and exactly the bounds it needs.

    ``at least`` and ``above`` carry ``lo``; ``at most`` and ``below`` carry
    ``hi``; ``between`` carries both with ``lo < hi``; the previous-median rule
    carries neither. Exclusive words are priced as inclusive bounds, as they
    always were.
    """

    rule: str
    lo: float | None = None
    hi: float | None = None

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"region.rule must be one of {', '.join(RULES)}")
        needs = {"lo": self.rule in (*LOWER_RULES, "between"),
                 "hi": self.rule in (*UPPER_RULES, "between")}
        for name, needed in needs.items():
            value = getattr(self, name)
            if needed:
                object.__setattr__(self, name, _bound(value, name))
            elif value is not None:
                raise ValueError(f"region.{name} does not belong to rule {self.rule}")
        if self.rule == "between" and not self.lo < self.hi:
            raise ValueError("region between needs lo < hi")

    @classmethod
    def parse(cls, value: object) -> CardRule:
        """Read a typed ``{rule, lo, hi}`` or one of the historical sentences.

        Raises ValueError for anything else: a region that cannot be read is
        never guessed at.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            unknown = set(value) - {"rule", "lo", "hi"}
            if unknown or "rule" not in value:
                raise ValueError("region must be {rule, lo, hi}")
            return cls(value["rule"], value.get("lo"), value.get("hi"))
        if not isinstance(value, str):
            raise ValueError("region must be {rule, lo, hi} or a sentence")
        text = " ".join(value.lower().strip().rstrip(".").split())
        if text == PREVIOUS_MEDIAN:
            return cls(PREVIOUS_MEDIAN)
        for rule in (*LOWER_RULES, *UPPER_RULES):
            match = re.fullmatch(rf"{rule} {_NUMBER}", text)
            if match is not None:
                bound = _WORDS.get(match[1], None)
                bound = float(match[1]) if bound is None else bound
                if not math.isfinite(bound):
                    break
                return cls(rule, lo=bound) if rule in LOWER_RULES else cls(rule, hi=bound)
        match = re.fullmatch(rf"between {_NUMBER} and {_NUMBER}", text)
        if match is not None:
            lo, hi = (_WORDS[g] if g in _WORDS else float(g) for g in (match[1], match[2]))
            if math.isfinite(lo) and math.isfinite(hi) and lo < hi:
                return cls("between", lo=lo, hi=hi)
        raise ValueError(f"region: unreadable sentence {value!r}")

    @property
    def kind(self) -> str:
        """The controller's region kind: min, max or band."""
        if self.rule in LOWER_RULES:
            return "min"
        return "band" if self.rule == "between" else "max"

    @property
    def deferred(self) -> bool:
        """True when the bound comes from the rolling record rather than the card."""
        return self.rule == PREVIOUS_MEDIAN

    def prose(self) -> str:
        """The sentence a charter renders for this region, derived and never stored apart."""
        if self.rule == PREVIOUS_MEDIAN:
            return PREVIOUS_MEDIAN
        if self.rule == "between":
            return f"between {number_text(self.lo)} and {number_text(self.hi)}"
        bound = self.lo if self.rule in LOWER_RULES else self.hi
        return f"{self.rule} {number_text(bound)}"

    def as_dict(self) -> dict:
        """The wire form: the rule and only the bounds it carries."""
        return {"rule": self.rule, **({"lo": self.lo} if self.lo is not None else {}),
                **({"hi": self.hi} if self.hi is not None else {})}


def region_schema() -> dict:
    """The accepted typed region, as a public schema."""
    return {"type": "object", "properties": {
        "rule": {"enum": list(RULES)}, "lo": {"type": "number"}, "hi": {"type": "number"}},
        "required": ["rule"], "additionalProperties": False}
