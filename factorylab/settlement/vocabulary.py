"""Seed and versioned population predicates depend only on supplied public facts."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, replace
from fractions import Fraction

from factorylab.kernel.events import EventKind
from factorylab.kernel.money import require_money
from factorylab.kernel.registry import _freeze
from factorylab.settlement.scoring import _require_id, _require_probability


class _Unobservable:
    """The documented absence of a fact the owner is not at fault for.

    A ``facts_for`` callable returns this instead of ``None`` when the world was
    asked and did not answer, and the commitment's owner could not have made it
    answer. The commitment still settles censored — nothing is scored from a
    fact nobody has — but it is an excluded sample for accountable resolution
    (``avoidably_unresolved_share``), never a silent zero and never blame.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "UNOBSERVABLE"

    def __bool__(self) -> bool:
        return False


#: The single documented-unobservability marker; compared by identity.
UNOBSERVABLE = _Unobservable()


#: Why a judging contract cannot be bought as a child. It is stated here, with
#: the rest of what a judge may be asked, so the one line in the catalogue's
#: addressing text and the runtime's refusal say the same thing.
COMMISSIONED_JUDGE_REFUSAL = (
    "a judging contract cannot be commissioned as a child: a requested judge may only "
    "address the chain that requested it, and nothing judges its own output or its "
    "ancestors', so the route has nothing it could execute. Judging work reaches a seat "
    "through the router's sampling, the adversarial share and the cascade"
)

#: A seat declining a commission. Paid work may be declined; the call is the
#: only cost (§6.B, and the deferral contract).
DECLINED_DEFINITION = "declined-v1"


def commission_block(*, subject: str | None, scope: str, horizon: int, budget_micro: int) -> dict:
    """What one evaluation commission buys: a subject, a scope, a horizon and a budget.

    Evaluation is work someone pays for, not an obligation a seat owes the
    world. The commission says what is being asked about, over what evidence, by
    when, and for how much; the answer may be a verdict or a refusal of the
    commission itself. There is no kernel list of what may be judged (evaluations
    S1): what a verdict is scored on is a schematic (world.scoring).
    """
    return {
        "subject": subject,
        "scope": scope,
        "horizon_events": int(horizon),
        "budget_micro": int(budget_micro),
        "you_may": (
            'answer the commission, or decline it with {"status": "cannot", "reason": ...}'
        ),
    }


def evaluator_answer_schema(forecasts: dict, register: dict) -> dict:
    """The evaluator answer schema.

    It lives here rather than inline in ``runtime.loop`` so the charter's own
    vocabulary owns what a judge is asked to say, and the loop names it once.

    Ruling R1: the verdict is itself the prediction the world grades, so there is
    no separate payoff field to fill; ``status`` lets the same answer decline the
    commission outright.
    """
    properties = {
        "verdict": {"type": "number", "minimum": 0, "maximum": 1},
        "status": {"enum": ["cannot"]},
        "reason": {"type": "string"},
        "rationale": {"type": "string"},
        "propensity": {"type": "object"},
        "forecasts": forecasts,
        "register": register,
        "about_handle": {"type": "string"},
    }
    return {"type": "object", "properties": properties, "required": ["rationale"]}


@dataclass(frozen=True)
class Predicate:
    """Predicate identity and its parameter schema remain deeply immutable."""

    id: str
    description: str
    param_schema: dict
    horizon_param: str
    proposable: bool = True
    code: str | None = None
    version: int = 1
    provenance: str = "seed"

    def __post_init__(self) -> None:
        for value in (self.id, self.description, self.horizon_param):
            _require_id(value)
        if not isinstance(self.param_schema, Mapping):
            raise ValueError("param_schema must be a mapping")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("predicate version must be a positive integer")
        _require_id(self.provenance)
        if self.code is not None:
            validate_predicate_definition(self.id, self.description, self.code)
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
    "The realised P&L credited to the return, as opener (net of its opening fee and "
    "funding) or as closer (net of its closing fee), exceeds its own compute and tool cost.",
    _seed("return_paid_off", "Kernel consequence.").param_schema,
    "horizon_events",
    proposable=False,
)


def _require_event_index(value: int, name: str, *, positive: bool = False) -> None:
    if type(value) is not int or value < (1 if positive else 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a {qualifier} integer")


def _validate_params(
    predicate_id: str, params: dict, *, kernel: bool = False,
    predicate: Predicate | None = None,
) -> None:
    _require_id(predicate_id)
    population = (predicate is not None and predicate.id == predicate_id
                  and predicate.code is not None and predicate.proposable)
    if not population and not any(p.id == predicate_id for p in SEED_VOCABULARY) and not (
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


MAX_PREDICATE_CODE_CHARS = 8000
MAX_PREDICATE_DESCRIPTION_CHARS = 500


def validate_predicate_definition(predicate_id: str, description: str, code: str) -> None:
    """Only bounded, synchronous resolver definitions can reach a jailed preflight."""
    if isinstance(predicate_id, str) and predicate_id in (
        {p.id for p in SEED_VOCABULARY} | {RETURN_PAID_OFF.id}
    ):
        raise ValueError("seed and kernel predicate ids cannot be redefined")
    if not isinstance(predicate_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,47}", predicate_id):
        raise ValueError("predicate id must be a slug of 2-48 chars")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("predicate description is required")
    if len(description) > MAX_PREDICATE_DESCRIPTION_CHARS:
        raise ValueError(f"predicate description exceeds {MAX_PREDICATE_DESCRIPTION_CHARS} chars")
    if not isinstance(code, str):
        raise ValueError("predicate code must be a string")
    if len(code) > MAX_PREDICATE_CODE_CHARS:
        raise ValueError(f"predicate code exceeds {MAX_PREDICATE_CODE_CHARS} chars")
    try:
        module = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        raise ValueError("predicate code must define resolve(facts)") from None
    if not any(isinstance(node, ast.FunctionDef) and node.name == "resolve"
               for node in module.body):
        raise ValueError("predicate code must define resolve(facts)")


class PredicateBook:
    """One world's predicate history preserves every admitted definition for outstanding bets."""

    def __init__(
        self, registered: dict[str, list[dict]] | None = None, *,
        run: Callable[[str, dict], tuple[bool | None, str | None]] | None = None,
    ) -> None:
        self.registered = registered if registered is not None else {}
        self._run = run

    def get(self, name: str, version: int | None = None) -> Predicate | None:
        """An explicit version resolves that definition, never a later replacement."""
        _require_id(name)
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError("predicate version must be a positive integer")
        seed = next((p for p in SEED_VOCABULARY if p.id == name), None)
        if seed is not None:
            return seed if version in (None, seed.version) else None
        history = self.registered.get(name, ())
        if not history or (version is not None and version > len(history)):
            return None
        return Predicate(**history[-1 if version is None else version - 1])

    def all(self) -> list[Predicate]:
        """The current public vocabulary contains the seeds and each latest population version."""
        return list(SEED_VOCABULARY) + [self.get(name) for name in sorted(self.registered)]

    def catalogue(self) -> list[dict]:
        """Public metadata retains versioned parameter schemas without private handles."""
        return [{
            "id": p.id, "description": p.description, "params": _plain(p.param_schema),
            "horizon_param": p.horizon_param, "version": p.version,
            "provenance": "population" if p.code is not None else "seed",
        } for p in self.all()]

    def register(
        self, name: str, description: str, code: str, *, facts: dict | None,
        persist: Callable[[Predicate], None], provenance: str = "population",
        preflight: Callable[[Predicate, bool | None, str | None], None] | None = None,
    ) -> Predicate:
        """Publish a version only after boolean preflight and successful durable admission."""
        validate_predicate_definition(name, description, code)
        if facts is None:
            raise ValueError("no closed window to preflight the predicate against")
        version = len(self.registered.get(name, ())) + 1
        candidate = replace(_seed(name, description), code=code, version=version,
                            provenance=provenance)
        value, error = self._resolve(code, facts)
        if preflight is not None:
            preflight(candidate, value, error)
        if error is not None or value is None:
            raise ValueError(f"predicate preflight failed: {error}")
        entry = {f.name: _plain(getattr(candidate, f.name)) for f in fields(candidate)}
        persist(candidate)
        self.registered.setdefault(name, []).append(entry)
        return candidate

    def _resolve(self, code: str, facts: dict) -> tuple[bool | None, str | None]:
        if self._run is None:
            return None, "no predicate runner"
        try:
            value, error = self._run(code, facts)
        except Exception:
            return None, "predicate execution failed"
        if error is not None:
            return None, error
        if type(value) is not bool:
            return None, "predicate must return a boolean"
        return value, None

    def resolve(
        self, name: str, params: dict, facts: dict | None, *, version: int,
    ) -> tuple[bool | None, str | None]:
        """Resolve only the sealed population version; missing facts never become false."""
        predicate = self.get(name, version)
        if predicate is None or predicate.code is None:
            raise ValueError("unknown population predicate version")
        _validate_params(name, params, predicate=predicate)
        if facts is None:
            return None, "predicate facts unavailable"
        return self._resolve(predicate.code, facts)


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class WindowFacts:
    """Balances stay integer micro-USD and event evidence stays detached and immutable."""

    balance_at_forecast: int
    balance_at_settlement: int
    min_balance_in_window: int
    events: tuple[dict, ...]
    public_window: dict | None = None

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
        if self.public_window is not None:
            if not isinstance(self.public_window, Mapping):
                raise ValueError("public_window must contain public observation facts")
            object.__setattr__(self, "public_window", _freeze(self.public_window))


class Observer:
    """Only seed logic or the sealed population definition and supplied facts determine y."""

    def __init__(self, predicates: PredicateBook | None = None) -> None:
        self.predicates = predicates if predicates is not None else PredicateBook()

    def observe(
        self, predicate_id: str, params: dict, facts: WindowFacts, *, version: int | None = None,
    ) -> int | None:
        """Unknown claims are refused; unavailable population facts or execution remain unscored."""
        _require_id(predicate_id)
        predicate = self.predicates.get(predicate_id, version)
        _validate_params(predicate_id, params, predicate=predicate)
        if not isinstance(facts, WindowFacts):
            raise ValueError("WindowFacts required")
        if predicate is not None and predicate.code is not None:
            if version is None:
                raise ValueError("population forecasts must bind a predicate version")
            value, _error = self.predicates.resolve(
                predicate_id, params, _plain(facts.public_window), version=version)
            return None if value is None else int(value)
        if version not in (None, 1):
            raise ValueError("unknown seed predicate version")
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
