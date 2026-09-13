"""Runtime shared method group."""

from __future__ import annotations

from typing import Any

from factorylab.cortex.registration import REWARD_SHAPES, reward_contracts

NOOP = "NOOP"


CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"


CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v1"


DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"


# A top meta's conformity graded by Brier against the judged verdict's consequence.
DEF_META_CONSEQUENCE = "meta-consequence-v1"


def assembly_rewards(spec: Any) -> dict[str, str]:
    """Legacy specs retain seed channels and undeclared population kinds default to judged."""
    return reward_contracts(spec.emits, getattr(spec, "reward_shapes", {}))


def return_channel(kind: str, shape: str, *, higher: bool = False) -> str:
    """A declared shape selects an existing reward channel; seed Verdict retains conformity."""
    if shape not in REWARD_SHAPES:
        raise ValueError("reward shape must be judged, forecast, conformity or exposure")
    if shape == "forecast":
        # The seed Verdict also carries a verdict judged for conformity; its
        # predictions already have their own consequence decisions.
        return CH_CONFORMITY if kind == "Verdict" else CH_CONSEQUENCE
    if shape == "conformity":
        return CH_CONFORMITY if higher else CH_FAST
    return CH_EXPOSURE if shape == "exposure" else CH_VERDICT


def work_disclosure(kinds: dict[str, str], predicates: list[dict]) -> dict:
    """Publish the four reward shapes and predicate metadata without learner identities."""
    shapes = reward_contracts(tuple(kinds), kinds)
    return {
        "reward_shapes": {
            "judged": "A judge's verdict now, with consequence recorded later.",
            "forecast": "Brier on predictions when their facts are available.",
            "conformity": "Conformity judged above, or Brier against consequence at the top.",
            "exposure": "Exposure of a judge's failed payoff prediction against consequence.",
        },
        "default_reward_shape": "judged",
        "reward_contract": (
            "A new kind declares one of exactly four reward shapes. A kind without a "
            "declaration defaults to judged. There is no fifth shape: reward stays outside "
            "the loop of the thing rewarded."
        ),
        "kind_rewards": shapes,
        "predicates": _to_plain(predicates),
        "predicate_registration": {
            "kind": "predicate", "id": "has-fill", "description": "A fill occurred.",
            "code": "def resolve(facts): return facts['fills'] > 0",
        },
        "predicate_contract": (
            "resolve(facts) returns a boolean over public observation facts. Admission "
            "preflights the last closed window. Forecasts bind the registered version; "
            "replacement definitions do not change outstanding predictions."
        ),
    }


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
