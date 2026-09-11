"""Launch-declared predicates depend exclusively on the supplied observation window."""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from factorylab.kernel.events import EventKind
from factorylab.kernel.money import require_money
from factorylab.kernel.registry import _freeze
from factorylab.settlement.scoring import _require_id, _require_probability


@dataclass(frozen=True)
class Predicate:
    """Predicate identity and its parameter schema remain deeply immutable."""

    id: str
    description: str
    param_schema: dict
    horizon_param: str
    proposable: bool = True

    def __post_init__(self) -> None:
        for value in (self.id, self.description, self.horizon_param):
            _require_id(value)
        if not isinstance(self.param_schema, Mapping):
            raise ValueError("param_schema must be a mapping")
        object.__setattr__(self, "param_schema", _freeze(self.param_schema))


def _seed(predicate_id: str, description: str, *, drawdown: bool = False) -> Predicate:
    properties = {"horizon_events": {"type": "integer", "minimum": 1}}
    if drawdown:
        properties["fraction"] = {"type": "number", "minimum": 0, "maximum": 1}
    return Predicate(
        predicate_id,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "horizon_events",
    )


SEED_VOCABULARY = (
    _seed("wallet_up", "Wallet balance at settlement exceeds balance at forecast."),
    _seed("fill_within", "At least one Fill event occurred in the window."),
    _seed("rejected_within", "At least one OrderRejected event occurred in the window."),
    _seed("liquidated_within", "At least one Fill has liquidation true in its payload."),
    _seed(
        "drawdown_exceeds",
        "Minimum window balance is below (1 - fraction) times balance at forecast.",
        drawdown=True,
    ),
)

# Kernel-only commitment: deliberately absent from SEED_VOCABULARY and Observer.
RETURN_PAID_OFF = Predicate(
    "return_paid_off",
    "The return's FIFO proceeds, net of fees and funding, exceed its own compute cost.",
    _seed("return_paid_off", "Kernel consequence.").param_schema,
    "horizon_events",
    proposable=False,
)


def _require_event_index(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a {qualifier} integer")


def _validate_params(predicate_id: str, params: dict, *, kernel: bool = False) -> None:
    _require_id(predicate_id)
    if not any(predicate.id == predicate_id for predicate in SEED_VOCABULARY) and not (
        kernel and predicate_id == RETURN_PAID_OFF.id
    ):
        raise ValueError(f"unknown predicate: {predicate_id}")
    required = {"horizon_events"}
    if predicate_id == "drawdown_exceeds":
        required.add("fraction")
    if not isinstance(params, Mapping) or set(params) != required:
        raise ValueError("params must contain exactly the predicate's declared parameters")
    _require_event_index(params["horizon_events"], "horizon_events", positive=True)
    if predicate_id == "drawdown_exceeds":
        _require_probability(params["fraction"], "fraction")


@dataclass(frozen=True)
class WindowFacts:
    """Balances stay integer micro-USD and event evidence stays detached and immutable."""

    balance_at_forecast: int
    balance_at_settlement: int
    min_balance_in_window: int
    events: tuple[dict, ...]

    def __post_init__(self) -> None:
        for balance in (
            self.balance_at_forecast,
            self.balance_at_settlement,
            self.min_balance_in_window,
        ):
            require_money(balance)
        events = tuple(self.events)
        for event in events:
            if (
                not isinstance(event, Mapping)
                or not isinstance(event.get("kind"), str)
                or not event["kind"]
                or not isinstance(event.get("payload"), Mapping)
            ):
                raise ValueError("each event requires a kind and a payload mapping")
        object.__setattr__(self, "events", _freeze(events))


class Observer:
    """Only the fixed seed predicates and the supplied window can determine y."""

    def observe(self, predicate_id: str, params: dict, facts: WindowFacts) -> int:
        """Return binary truth for valid seed parameters; reject unknown or malformed claims."""
        _validate_params(predicate_id, params)
        if not isinstance(facts, WindowFacts):
            raise ValueError("WindowFacts required")
        if predicate_id == "wallet_up":
            return int(facts.balance_at_settlement > facts.balance_at_forecast)
        if predicate_id == "drawdown_exceeds":
            # Cross-multiplication preserves strict thresholds even above float precision.
            fraction = Fraction(str(params["fraction"]))
            return int(
                facts.min_balance_in_window * fraction.denominator
                < (fraction.denominator - fraction.numerator) * facts.balance_at_forecast
            )
        if predicate_id == "rejected_within":
            return int(any(event["kind"] == EventKind.ORDER_REJECTED for event in facts.events))
        return int(
            any(
                event["kind"] == EventKind.FILL
                and (predicate_id == "fill_within" or event["payload"].get("liquidation") is True)
                for event in facts.events
            )
        )
