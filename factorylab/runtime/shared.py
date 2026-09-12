"""Runtime shared method group."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from factorylab.kernel.money import usd_to_money

NOOP = "NOOP"


CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"


CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v1"


DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"


PRODUCER_KINDS = frozenset({"Tick", "MarketMid", "Funding", "Fill", "OrderRejected"})


EVALUATION_BOUNDARY = "producer → evaluator → meta"


class SimClock:
    """Simulated time. Every kernel component reads the current event's timestamp from here."""

    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


def _usd_to_micro(value: str | Decimal) -> int:
    q = Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    return usd_to_money(str(q))


def _to_plain(payload: Any) -> Any:
    if hasattr(payload, "items"):
        return {k: _to_plain(v) for k, v in payload.items()}
    if isinstance(payload, list | tuple):
        return [_to_plain(v) for v in payload]
    return payload
