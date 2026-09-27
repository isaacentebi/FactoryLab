"""World-side event records.

The world package does not import the kernel. Adapters emit ``WorldEvent``
records; the runtime converts them into kernel events. Field names match the
kernel's Event so the conversion is a plain copy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorldEventKind(StrEnum):
    """Every event kind a world adapter may emit, matching the kernel's own names."""

    TICK = "Tick"
    DRIP = "Drip"
    MARKET_MID = "MarketMid"
    FUNDING = "Funding"
    FILL = "Fill"
    ORDER_REJECTED = "OrderRejected"


@dataclass(frozen=True)
class WorldEvent:
    """One observation from the world. Immutable once emitted.

    ``ts_ns`` is UTC nanoseconds since the epoch. ``source`` names the adapter.
    """

    kind: WorldEventKind
    ts_ns: int
    source: str
    payload: dict[str, Any] = field(default_factory=dict)


def funding_instant(payload: Mapping[str, Any], ts_ns: int) -> int:
    """The instant a ``Funding`` event's fact is about: the funding time a payment is
    for, or the instant a rate was stated at.

    Guarantees ``payload["funding_ns"]`` when the venue states it and ``ts_ns`` (the
    event's own time) otherwise. A venue can report a fact after its instant: a tape's
    advance passes an hour boundary and reports that boundary's payment at the advance
    time. Every reader of a funding fact reads it at its own instant, never at the
    instant it was reported (wave 16, R10-m; Codex on #152).
    """
    stated = payload.get("funding_ns")
    return int(stated) if stated is not None else int(ts_ns)
