"""Card windows are executable sample selections, never free-text promises."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MetricWindow:
    """A positive sample count selects returns, settled forecasts or closed windows."""

    kind: str
    n: int
    per: str | None

    def __post_init__(self) -> None:
        if self.kind not in ("returns", "forecasts", "windows"):
            raise ValueError("window.kind must be returns, forecasts or windows")
        if type(self.n) is not int or self.n < 1:
            raise ValueError("window.n must be a positive integer")
        if self.per not in (None, "role", "assembly"):
            raise ValueError("window.per must be role, assembly or null")

    @classmethod
    def parse(cls, value: object) -> MetricWindow:
        """Reject prose, omitted fields and unsupported selectors without coercion."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != {"kind", "n", "per"}:
            raise ValueError("window must be an object with kind, n and per")
        return cls(**value)

    def __str__(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


def window_schema() -> dict:
    """Return a detached public schema for the exact accepted window shape."""
    return {
        "type": "object",
        "properties": {
            "kind": {"enum": ["returns", "forecasts", "windows"]},
            "n": {"type": "integer", "minimum": 1},
            "per": {"enum": ["role", "assembly", None]},
        },
        "required": ["kind", "n", "per"],
        "additionalProperties": False,
    }
