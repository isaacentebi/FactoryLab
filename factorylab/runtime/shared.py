"""Runtime shared method group."""

from __future__ import annotations

import json
from typing import Any

from factorylab.cortex.registration import REWARD_SHAPES, reward_contracts

NOOP = "NOOP"

#: The longest horizon, in world ticks, a population forecast may seal: the
#: predicate vocabulary's admitted range is 1..MAX_FORECAST_HORIZON.
MAX_FORECAST_HORIZON = 200


CH_FAST, CH_VERDICT, CH_CONFORMITY, CH_CONSEQUENCE = "fast", "verdict", "conformity", "consequence"


# Exposure centred on the judges' own consequence scores on ordinary returns (the
# Wave 2 review, item 6): v2, where v1 paid a merely miscalibrated judge's miss.
CH_EXPOSURE, DEF_EXPOSURE = "exposure", "exposure-v2"


# An adversarial judge's counter-verdict, settled on how far its own prediction beat
# the verdict it read when the world measured the return (evaluations M1).
CH_COUNTER, DEF_COUNTER = "counter", "counter-v1"


DEF_FAST, DEF_VERDICT, DEF_CONFORMITY = "fast-v1", "verdict-v1", "conformity-v1"


# An evaluator decision (a judge's or a meta's) settled on its two signals, the tier
# above's grade and the world's score of its prediction (ruling R1).
DEF_EVALUATION = "evaluation-v1"


# A composed return (a requested child) settled on its judges' verdict and the
# settled score of the decision that requested and consumed it (rulings §2, W4).
DEF_COMPOSED = "composed-v1"


#: The router key of requests for one kind of work: ``REQUEST_ROUTER + kind``. The
#: space cannot occur in an event kind (``registration.event_name``), so a request
#: router never shares a key with an event router (Chapter II rulings §2, Composition).
REQUEST_ROUTER = "request "


def request_router_key(kind: str) -> str:
    """The key under which the router for requests of ``kind`` is kept."""
    return REQUEST_ROUTER + kind


def is_request_router(learner_id: str) -> bool:
    """Whether a learner id names a request router (``router:request <kind>``)."""
    return isinstance(learner_id, str) and learner_id.startswith("router:" + REQUEST_ROUTER)


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
        raise ValueError("reward shape must be judged, forecast, conformity, exposure or "
                         "counter")
    if shape == "counter":
        return CH_COUNTER
    if shape == "forecast":
        # The seed Verdict also carries a verdict judged for conformity; its
        # predictions already have their own consequence decisions.
        return CH_CONFORMITY if kind == "Verdict" else CH_CONSEQUENCE
    if shape == "conformity":
        return CH_CONFORMITY if higher else CH_FAST
    return CH_EXPOSURE if shape == "exposure" else CH_VERDICT


def work_disclosure(kinds: dict[str, str], predicates: list[dict]) -> dict:
    """Publish the declarable reward shapes and predicate metadata without learner identities.

    The block is a registration form, not an account of the kernel: it names the
    shapes a declaration may carry and the shape it gets without one. Why the
    catalogue is closed, and that reward may not re-enter what it rewards, is
    enforced by ``reward_contracts`` and never announced (AGENTS.md).
    """
    shapes = reward_contracts(tuple(kinds), kinds)
    return {
        "reward_shapes": {
            "judged": "The mean of the verdicts the judges that read the return gave.",
            "forecast": "Return forecasts: [{predicate, params, q}]. Reward is the mean "
            "Brier score 1 - (q - y)^2 when every prediction resolves; missing outcomes are "
            "unscored.",
            "conformity": "Return conformity (0 to 1) on the judged work. Graded by the tier "
            "above, where one exists, and scored against the judged work's consequence "
            "score.",
            "exposure": "Settles on how much worse the judges' verdicts on the return "
            "scored against its measured outcome than those judges score on other returns.",
            "counter": "Return verdict (0 to 1) on the return a verdict judged. Settles when "
            "the world measures that return, on 0.5 + 0.5 * (your Brier - that verdict's "
            "Brier), Brier = 1 - (verdict - outcome)^2; unscored when the world never "
            "measures it.",
        },
        "default_reward_shape": "judged",
        "kind_rewards": shapes,
        "predicates": _to_plain(predicates),
        "predicate_registration": {
            "kind": "predicate", "id": "has-fill", "description": "A fill occurred.",
            "code": "def resolve(facts): return facts['fills'] > 0",
        },
        "predicate_readiness": {
            "requires_closed_window": True,
            "live_signal": "WORLD UPDATE public_observations.last_closed_window_values",
            "not_ready": "an empty last_closed_window_values means registration will be "
                         "rejected because there is no closed window to preflight",
        },
        "predicate_facts_example": {
            "mids": {"BTC": [[1_710_000_000_000_000_000, 81_234_500_000],
                              [1_710_000_060_000_000_000, 81_310_000_000]]},
            "funding": {"BTC": [[1_710_000_000_000_000_000, 0.0001]]},
            "wallet_balance_micro": [[1_710_000_000_000_000_000, 300_000_000],
                                     [1_710_000_060_000_000_000, 299_999_000]],
            "tick_timestamps_ns": [1_710_000_000_000_000_000,
                                   1_710_000_060_000_000_000],
        },
        "predicate_contract": (
            "resolve(facts) returns a boolean over public observation facts. Admission "
            "preflights the last closed window. Forecasts bind the registered version; "
            "replacement definitions do not change outstanding predictions. mids and funding "
            "map each coin to ordered [timestamp_ns, value] pairs, not objects; mid values and "
            "wallet balances are integer micro-USD, and wallet_balance_micro is also a series "
            "of [timestamp_ns, value] pairs."
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
