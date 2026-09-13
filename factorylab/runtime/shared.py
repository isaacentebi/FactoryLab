"""Runtime shared method group."""

from __future__ import annotations

from typing import Any

NOOP = "NOOP"


CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"


CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v1"


DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"


# A top meta's conformity graded by Brier against the judged verdict's consequence.
DEF_META_CONSEQUENCE = "meta-consequence-v1"


class SimClock:
    """Simulated time. Every kernel component reads the current event's timestamp from here."""

    def __init__(self, now_ns: int = 0) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


def _to_plain(payload: Any) -> Any:
    if hasattr(payload, "items"):
        return {k: _to_plain(v) for k, v in payload.items()}
    if isinstance(payload, list | tuple):
        return [_to_plain(v) for v in payload]
    return payload
