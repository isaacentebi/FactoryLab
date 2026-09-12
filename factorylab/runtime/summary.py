"""Runtime summary method group."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds
from factorylab.runtime.shared import EVALUATION_BOUNDARY
from factorylab.world.models import TokenPrice


def _duration_str(ns: int) -> str:
    """Whole seconds/minutes/hours where exact, else seconds with a decimal."""
    if ns % 3_600_000_000_000 == 0:
        return f"{ns // 3_600_000_000_000}h"
    if ns % 60_000_000_000 == 0:
        return f"{ns // 60_000_000_000}m"
    if ns % 1_000_000_000 == 0:
        return f"{ns // 1_000_000_000}s"
    return f"{ns / 1_000_000_000:g}s"


@dataclass
class RunStats:
    resumes: int = 0
    events: int = 0
    decisions: int = 0
    invocations: int = 0
    noops: int = 0
    orders_placed: int = 0
    orders_rejected: int = 0
    fills: int = 0
    producer_returns: int = 0
    verdicts: int = 0
    meta_verdicts: dict[int, int] = field(default_factory=dict)
    conformities: int = 0
    censored: int = 0
    fast_settlements: int = 0
    forecasts_sealed: int = 0
    forecasts_settled: int = 0
    timeouts: int = 0
    upward_releases: int = 0
    reserve_windows: int = 0
    exclusions: int = 0
    registrations_accepted: int = 0
    registrations_rejected: int = 0
    epochs: int = 0
    routers_replaced: int = 0
    reconciliations: int = 0
    tool_calls: int = 0
    tool_call_failures: int = 0
    population_tools_registered: int = 0
    observations_registered: int = 0  # A11
    assembly_learners_registered: int = 0  # A10
    amendments_proposed: int = 0
    amendments_passed: int = 0
    amendments_activated: int = 0
    clock_changes: int = 0
    votes_cast: int = 0
    transfer_intents: int = 0
    exposures_settled: int = 0
    exposures_won: int = 0
    price_updates: int = 0
    price_skipped: int = 0
    penalized_settlements: int = 0
    max_settlement_latency_events: int = 0
    last_window_values: dict[str, float] = field(default_factory=dict)
    sample_propensity: dict[str, Any] | None = None
    invocation_status: dict[str, int] = field(default_factory=dict)
    invocations_by_role: dict[str, int] = field(default_factory=dict)
    stop_reasons: dict[str, int] = field(default_factory=dict)
    invocations_by_assembly: dict[str, int] = field(default_factory=dict)
    # settled consequences delivered per assembly (novelty trials, A13)
    consequences_by_assembly: dict[str, int] = field(default_factory=dict)
    registered_window: dict[str, int] = field(default_factory=dict)  # assembly -> window index
    immune_windows: list[dict] = field(default_factory=list)
    pathologies: dict[str, bool] = field(default_factory=lambda: {
        "stable_failure": False, "thrash": False, "learning_death": False,
    })


def _price_str(value: Any) -> str:
    """Micro-USD per token equals USD per million tokens; render exactly, Fraction or int."""
    num = getattr(value, "numerator", value)
    den = getattr(value, "denominator", 1)
    return str((Decimal(num) / Decimal(den)).normalize())


def _equity_or_none(exchange: Any) -> str | None:
    try:
        return str(exchange.account().equity_usd)
    except RuntimeError:
        return None


def _as_unit(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    v = float(value)
    if v != v or v < 0 or v > 1:
        return None
    return v


def _model_contract(model_id: str, price: TokenPrice, provider: str) -> Contract:
    return Contract(
        id=f"model:{model_id}",
        version=1,
        kind="model",
        description=f"{provider} model {model_id}"
        + (" on eip155:8453 (USDC)" if provider == "x402" else ""),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        price=PriceSpec(
            {
                "input_token": int(price.input_micro),
                "output_token": int(price.output_micro),
                **(
                    {"request": price.per_request_micro}
                    if provider == "x402" or price.per_request_micro
                    else {}
                ),
            }
        ),
        permissions=frozenset({"model.complete"}),
        resource_bounds=ResourceBounds(),
    )


def _assembly_contract(aid: str, role: str, accepts: tuple[str, ...], max_tokens: int) -> Contract:
    return Contract(
        id=aid,
        version=1,
        kind="assembly",
        description=f"{role} assembly",
        input_schema={"type": "object", "properties": {"kind": {"enum": list(accepts)}}},
        output_schema={"type": "object"},
        price=PriceSpec({}),
        permissions=frozenset({"model.complete", "exchange.order"}),
        resource_bounds=ResourceBounds(max_output_tokens=max_tokens),
    )


class SummaryMixin:
    """Preserve runtime state and behavior for summary operations."""

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "world": self.m.name,
            "manifest_hash": self.m.manifest_hash(),
            "seed": self.seed,
            "drip": self.use_drip,
            "terminated": self.termination.final,
            "termination_reason": self.termination.reason,
            "seal_key_released": self.ledger.seal_key_released(),
            "wallet_balance_micro": self.wallet.balance,
            "wallet_conservation": self.wallet.check_conservation(),
            "ledger_verify": self.ledger.verify(),
            "outstanding_decisions": len(self.queue.outstanding()),
            "exchange_equity_usd": _equity_or_none(self.exchange.target),
            "live": self.live,
            "evaluation_boundary": EVALUATION_BOUNDARY,
            "charter_edition": self.charter.edition,
            "tools": sorted(self.tool_specs),
            "standing": self.standing.snapshot(),
            "prices": self.controller.snapshot(),
            "routers": {
                st.learner.id: {
                    "event_kind": st.kind,
                    "universe": st.universe,
                    "epoch": st.epoch,
                    "learner": type(st.learner).__name__,
                }
                for st in self._all_router_states()
            },
            "stats": {**vars(self.stats), **self.consequences.counts()},
        }
        if not self.termination.final or self.kill_at_end:
            summary["aggregates"] = {
                "action_frequencies": self.ledger.aggregate("action_frequencies"),
                "invocations_by_assembly": self.ledger.aggregate("invocations_by_assembly"),
                "spend_by_capability": self.ledger.aggregate("spend_by_capability"),
                "settlement_latency": self.ledger.aggregate("settlement_latency"),
            }
        return summary
