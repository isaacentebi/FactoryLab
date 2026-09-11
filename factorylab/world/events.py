"""World-side event records.

The world package does not import the kernel. Adapters emit ``WorldEvent``
records; the runtime converts them into kernel events. Field names match the
kernel's Event so the conversion is a plain copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WorldEventKind(StrEnum):
    """Event kinds a world adapter may emit. Mirrors spec section 4.5."""

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
