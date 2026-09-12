"""Registered closure clocks and reproducibly jittered upward distribution buffers."""

import random
from dataclasses import dataclass
from statistics import fmean, pvariance

from factorylab.kernel.queue import LearningReturn, SettleStatus


class TimingRegistry:
    """Loop dependencies are immutable and closure observations are strictly ordered per loop."""

    def __init__(self) -> None:
        self.__governs: dict[str, tuple[str, ...]] = {}
        self.__closures: dict[str, list[int]] = {}

    def register_loop(self, id: str, governs: list[str]) -> None:
        """Require unique loops and already registered dependencies, preventing cycles."""
        if not isinstance(id, str) or not id or id in self.__governs:
            raise ValueError("loop id must be new and nonempty")
        if len(set(governs)) != len(governs) or any(
            child not in self.__governs for child in governs
        ):
            raise ValueError("governed loops must be unique and registered first")
        self.__governs[id] = tuple(governs)
        self.__closures[id] = []

    def governed(self, loop_id: str) -> tuple[str, ...]:
        """Return immutable immediate timing dependencies for a registered loop."""
        return self.__governs[loop_id]

    def record_closure(self, loop_id: str, ts_ns: int) -> None:
        """Accept a new closure only at a strictly later nonnegative integer timestamp."""
        closures = self.__closures[loop_id]
        if type(ts_ns) is not int or ts_ns < 0 or (closures and ts_ns <= closures[-1]):
            raise ValueError("closure timestamps must be strictly increasing integer nanoseconds")
        closures.append(ts_ns)

    def closure_count(self, loop_id: str) -> int:
        """Return the cumulative count used to enforce release separation."""
        return len(self.__closures[loop_id])

    def estimated_period(self, loop_id: str) -> int | None:
        """Return floored mean inter-closure nanoseconds, or None before two closures."""
        closures = self.__closures[loop_id]
        if len(closures) < 2:
            return None
        return (closures[-1] - closures[0]) // (len(closures) - 1)

    def state(self) -> dict:
        """Retain registered dependencies and every closure without sharing mutable lists."""
        return {"governs": dict(self.__governs),
                "closures": {k: list(v) for k, v in self.__closures.items()}}

    def _restore_state(self, state: dict) -> None:
        """Authenticated closure history preserves each loop's cumulative clock."""
        self.__governs = dict(state["governs"])
        self.__closures = {k: list(v) for k, v in state["closures"].items()}


@dataclass(frozen=True)
class DistributionSummary:
    """Upward reports contain only population moments, missingness and timestamp bounds."""

    count: int
    mean: float | None
    variance: float | None
    missing: int
    oldest_ts: int
    newest_ts: int


class UpwardBuffer:
    """No report escapes before every governed loop meets a seeded minimum closure ratio."""

    def __init__(
        self,
        registry: TimingRegistry,
        governing_loop: str,
        *,
        min_ratio: int = 3,
        seed: int = 0,
        jitter: int = 1,
    ) -> None:
        if type(min_ratio) is not int or min_ratio < 1:
            raise ValueError("minimum ratio must be a positive integer")
        if type(jitter) is not int or jitter < 0 or type(seed) is not int:
            raise ValueError("jitter must be nonnegative and seed must be an integer")
        self.__registry = registry
        self.__governed = registry.governed(governing_loop)
        if not self.__governed:
            raise ValueError("upward reports require a loop that governs lower loops")
        self.__min_ratio = min_ratio
        self.__jitter = jitter
        self.__rng = random.Random(seed)
        self.__items: list[tuple[LearningReturn, int]] = []
        self.__baseline: dict[str, int] = {}
        self.__thresholds: dict[str, int] = {}
        self._reset_window()

    def _reset_window(self) -> None:
        self.__baseline = {loop: self.__registry.closure_count(loop) for loop in self.__governed}
        self.__thresholds = {
            loop: self.__min_ratio + self.__rng.randint(0, self.__jitter)
            for loop in self.__governed
        }

    def add(self, feedback: LearningReturn, ts_ns: int) -> None:
        """Retain thin attribution internally until an eligible aggregate release."""
        if not isinstance(feedback, LearningReturn):
            raise TypeError("only thin LearningReturn values may enter an upward buffer")
        if type(ts_ns) is not int or ts_ns < 0:
            raise ValueError("ts_ns must be nonnegative integer nanoseconds")
        self.__items.append((feedback, ts_ns))

    def release(self) -> DistributionSummary | None:
        """Return one summary when all closure thresholds hold; polling never redraws jitter."""
        if not self.__items or any(
            self.__registry.closure_count(loop) - self.__baseline[loop] < self.__thresholds[loop]
            for loop in self.__governed
        ):
            return None
        scores = [item.score for item, _ in self.__items if item.status == SettleStatus.SETTLED]
        timestamps = [ts for _, ts in self.__items]
        result = DistributionSummary(
            len(self.__items),
            fmean(scores) if scores else None,
            pvariance(scores) if scores else None,
            len(self.__items) - len(scores),
            min(timestamps),
            max(timestamps),
        )
        self.__items.clear()
        self._reset_window()
        return result

    def state(self) -> dict:
        """Retain pending reports, drawn thresholds, baselines and the exact jitter RNG."""
        return {
            "governed": self.__governed, "min_ratio": self.__min_ratio, "jitter": self.__jitter,
            "rng": self.__rng.getstate(), "items": list(self.__items),
            "baseline": dict(self.__baseline), "thresholds": dict(self.__thresholds),
        }

    def _restore_state(self, state: dict) -> None:
        """Authenticated buffer data resumes existing jitter draws without drawing again."""
        for name in ("governed", "min_ratio", "jitter", "items", "baseline", "thresholds"):
            setattr(self, f"_UpwardBuffer__{name}", state[name])
        self.__rng.setstate(state["rng"])
