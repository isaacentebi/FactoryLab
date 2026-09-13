"""Runtime shared method group."""

from __future__ import annotations

import json
from typing import Any

from factorylab.cortex.registration import REWARD_SHAPES, reward_contracts

NOOP = "NOOP"


CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"


CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v1"


DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"


# A top meta's conformity graded by Brier against the judged verdict's consequence.
DEF_META_CONSEQUENCE = "meta-consequence-v1"


_PREDICATE_HARNESS = '''
import json as _predicate_json, sys as _predicate_sys
_predicate_result = resolve(_predicate_json.load(_predicate_sys.stdin))
if type(_predicate_result) is not bool:
    raise TypeError("resolve must return a boolean")
print(_predicate_json.dumps({"value": _predicate_result}, allow_nan=False))
'''


class PredicateRunner:
    """Population resolvers run only in the tool jail, with the observation resource bounds."""

    def __init__(self) -> None:
        from factorylab.cortex.sandbox import jail_available

        self.available = jail_available()

    def run(self, code: str, facts: dict) -> tuple[bool | None, str | None]:
        """Only a JSON boolean is truth evidence; execution failures remain unsupported."""
        from factorylab.cortex.sandbox import NoJail, run_python
        from factorylab.runtime.observations import OBSERVATION_CPU_S, OBSERVATION_TIMEOUT_S

        if not self.available:
            return None, "no jail on this host"
        try:
            stdin = json.dumps(facts, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            return None, "predicate facts are not JSON serializable"
        try:
            result = run_python(
                code + _PREDICATE_HARNESS, stdin=stdin,
                timeout_s=OBSERVATION_TIMEOUT_S, cpu_s=OBSERVATION_CPU_S,
                max_output_bytes=2000,
            )
            if result.timed_out:
                return None, "timeout"
            if result.returncode != 0:
                return None, f"exit {result.returncode}: {result.stderr.strip()[-200:]}"
            output = json.loads(result.stdout)
            if not isinstance(output, dict) or set(output) != {"value"}:
                return None, "predicate must return a boolean"
            if type(output["value"]) is not bool:
                return None, "predicate must return a boolean"
            return output["value"], None
        except NoJail:
            return None, "no jail on this host"
        except Exception:
            return None, "predicate execution failed"


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
            "forecast": "Return forecasts: [{predicate, params, q}]. Reward is mean Brier "
            "when every prediction resolves; missing outcomes are unscored.",
            "conformity": "Return conformity: a probability that the judged work beats its "
            "baseline. Judged above, or graded by Brier against consequence at the top.",
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
