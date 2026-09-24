"""Run a prepaid, testnet-only edition 4 rehearsal with an external admission cap.

The runner makes a fresh in-memory manifest from the edition 3 rehearsal manifest.  It
does not make a top-up, an x402 purchase, or a treasury transfer.  A provider wrapper
admits a call only when its quote fits the independent cap and records the provider's
actual bill separately. Failed dispatches retain their full quote as liability while
later work may continue; repeated failures and unauthoritative successful bills stop
admission. A failed completion is never retried by this wrapper.

The one exception is ``--capital-loop`` on a hybrid Venice world
(worlds/edition6-capital-loop.toml): ``treasury.transfer to_venice`` stays open and
spends REAL Base mainnet USDC for Venice credit, paid for in the testnet pots by a
shadow send. Every other treasury route and every x402 purchase stays denied. The
operator runbook is docs/architecture/capital-loop-rehearsal.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

from factorylab.charter.provenance import charter_content, charter_digest, roster_hash
from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import LiveClock
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import PromptSpec, WorldManifest, load_manifest
from factorylab.world.metering import UnbilledFailure, classify_provider_failure
from factorylab.world.models import ModelRequest, ModelResponse

DEFAULT_WORLD = Path("worlds/edition6-testnet-rehearsal.toml")
DEFAULT_SOURCE = Path("/tmp/factorylab-edition4-baseline-source")
DEFAULT_CAP_MICRO = 5_000_000
DEFAULT_DURATION_NS = 30 * 60 * 1_000_000_000
SHORT_TICK_NS = 10 * 1_000_000_000
ALLOWED_PREPAID = frozenset(("openrouter", "venice"))
FACTOR_PROMPTS = frozenset(("reference", "compact"))
FACTOR_REASONING = frozenset(("preserve", "off", "on"))


class RehearsalRefused(UnbilledFailure):
    """A local preflight refused to start a rehearsal before paid work was admitted."""

    def __init__(self, reason: str):
        self.reason = reason
        self.sent = False
        super().__init__(reason)


@dataclass
class Admission:
    """Track independent admission and provider-bill bounds for one rehearsal.

    Guarantees: no admitted call starts above the remaining quote cap or call count;
    a feasibility probe whose quote is above the whole cap is refused without
    stopping admission; any other quote above the remaining cap stops admission;
    every completion attempt is counted. With population recovery enabled, failed
    dispatches retain their quote and three consecutive exceptions stop admission.
    Otherwise a dispatched failure stops immediately, preserving probe protocols. Successful
    responses with unknown bills and reported overruns still stop immediately.
    """

    cap_micro: int
    max_calls: int
    recover_provider_failures: bool = False
    attempted: int = 0
    known_micro: int = 0
    uncertain_micro: int = 0
    unknown_bills: int = 0
    uncertain_bills: int = 0
    overruns: int = 0
    refusals: int = 0
    stop_reason: str | None = None
    consecutive_failures: int = 0

    @property
    def remaining_micro(self) -> int:
        return max(0, self.cap_micro - self.known_micro - self.uncertain_micro)

    def can_admit(self, ceiling_micro: int, *, probe: bool = False) -> tuple[bool, str]:
        if type(ceiling_micro) is not int or ceiling_micro < 0:
            return False, "invalid_quote"
        if self.stop_reason is not None:
            return False, self.stop_reason
        if self.attempted >= self.max_calls:
            self.stop_reason = self.stop_reason or "max_calls"
            return False, "max_calls"
        if ceiling_micro > self.remaining_micro:
            # The experimenter's spending bound on a rehearsal, outside the world: not
            # factory architecture, and no seat's doing. The router probes every seat's
            # feasibility at twice its quote. A probe above the whole cap names a seat
            # this rehearsal can never afford, which only makes that seat infeasible; a
            # sticky stop there ended whole runs, before any call was made, on a seat
            # whose worst case alone exceeds the cap. Anything else that does not fit
            # (an exhausted cap, or an actual call) ends the rehearsal, so the cap never
            # enters the experiment's data as a seat's failed return or a run of NOOPs.
            if probe and ceiling_micro > self.cap_micro:
                return False, "quote_above_cap"
            self.stop_reason = self.stop_reason or "quote_above_remaining_cap"
            return False, "quote_above_remaining_cap"
        return True, ""

    def admit(self, ceiling_micro: int) -> None:
        allowed, reason = self.can_admit(ceiling_micro)
        if not allowed:
            self.refusals += 1
            raise RehearsalRefused(reason)

    def attempted_call(self) -> None:
        """Count before dispatch so a transport exception is still an attempt."""
        self.attempted += 1

    def observe(self, response: ModelResponse, ceiling_micro: int) -> None:
        cost = response.cost_micro
        if response.raw.get("cost_source") == "table":
            self.uncertain_bills += 1
            self.uncertain_micro += max(ceiling_micro, cost if type(cost) is int else 0)
            self.stop_reason = "non_authoritative_table_cost"
            return
        if type(cost) is not int or cost < 0:
            self.unknown_bills += 1
            self.uncertain_bills += 1
            self.uncertain_micro += max(0, ceiling_micro)
            self.stop_reason = "unknown_bill"
            return
        self.consecutive_failures = 0
        self.known_micro += cost
        if cost > ceiling_micro:
            self.overruns += 1
            self.stop_reason = "reported_overrun"
        elif self.known_micro + self.uncertain_micro >= self.cap_micro:
            self.stop_reason = (
                "cap_exhausted"
                if self.known_micro + self.uncertain_micro == self.cap_micro
                else "cap_exceeded_by_reported_bill"
            )
        elif self.attempted >= self.max_calls:
            self.stop_reason = "max_calls"

    def observe_exception(self, exc: BaseException, ceiling_micro: int) -> None:
        """Classify an exception without retaining a provider body or credential."""
        self.consecutive_failures += 1
        classified = classify_provider_failure(exc) if isinstance(exc, Exception) else exc
        if not isinstance(classified, UnbilledFailure) and getattr(classified, "sent", True):
            self.unknown_bills += 1
            self.uncertain_bills += 1
            self.uncertain_micro += max(0, ceiling_micro)
            if not self.recover_provider_failures:
                self.stop_reason = "unknown_bill_after_dispatch"
        if not self.recover_provider_failures:
            return
        if self.known_micro + self.uncertain_micro >= self.cap_micro:
            self.stop_reason = "cap_exhausted"
        elif self.attempted >= self.max_calls:
            self.stop_reason = "max_calls"
        elif self.consecutive_failures >= 3:
            self.stop_reason = "consecutive_provider_failures"

    def report(self) -> dict[str, Any]:
        known_calls = self.attempted - self.uncertain_bills
        return {
            "cap_micro": self.cap_micro,
            "max_calls": self.max_calls,
            "attempted": self.attempted,
            "known_calls": known_calls,
            "known_micro": self.known_micro,
            "uncertain_calls": self.uncertain_bills,
            "uncertain_micro": self.uncertain_micro,
            "overruns": self.overruns,
            "refusals": self.refusals,
            "consecutive_provider_failures": self.consecutive_failures,
            "recover_provider_failures": self.recover_provider_failures,
            "max_consecutive_provider_failures": 3 if self.recover_provider_failures else None,
            "remaining_micro": self.remaining_micro,
            "known_mean_micro": (
                str(Fraction(self.known_micro, known_calls)) if known_calls else None
            ),
            "stop_reason": self.stop_reason,
        }


class PrepaidProvider:
    """Restrict a provider to the two prepaid namespaces and an admission ledger."""

    name = "edition4-prepaid-admission"

    def __init__(self, inner: Any, manifest: WorldManifest, admission: Admission):
        self.inner = inner
        self.manifest = manifest
        self.admission = admission
        self._prices = manifest.price_table()

    def _namespace_allowed(self, model_id: str) -> bool:
        if model_id.startswith("venice:"):
            return "venice" in {m.provider for m in self.manifest.models}
        return not model_id.startswith("x402:") and "openrouter" in {
            m.provider for m in self.manifest.models
        }

    def _ceiling(self, req: ModelRequest) -> int:
        from factorylab.world.models import prompt_chars

        price = self._prices.price(req.model_id)
        # The wire schema is input too (models.contract, §II.b), so it is reserved for.
        return price.cost(int(prompt_chars(req) * 1.5) + 64, req.max_tokens)

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        if not self._namespace_allowed(model_id):
            return False, "provider: rail denied"
        allowed, reason = self.admission.can_admit(ceiling_micro, probe=True)
        if reason == "quote_above_cap":
            # The rehearsal's cap is its compute budget, so a seat whose worst case
            # exceeds all of it is excluded for compute, exactly as a seat whose ceiling
            # exceeds the wallet is. The runtime then applies its own rule over its live
            # seats: a draw whose every candidate is excluded for compute joins the
            # insolvency streak, which ends or pauses the world (treasury.insolvency_events).
            return False, (f"compute: ceiling {ceiling_micro} exceeds rehearsal cap "
                           f"{self.admission.cap_micro}")
        if not allowed:
            return False, f"admission: {reason}"
        affordable = getattr(self.inner, "affordable", None)
        if affordable is None:
            return True, ""
        try:
            return affordable(model_id, ceiling_micro)
        except Exception:
            return False, "provider: balance unavailable"

    def complete(self, req: ModelRequest) -> ModelResponse:
        if not self._namespace_allowed(req.model_id):
            raise RehearsalRefused("provider_rail_denied")
        ceiling = self._ceiling(req)
        self.admission.admit(ceiling)
        self.admission.attempted_call()
        try:
            response = self.inner.complete(req)
        except Exception as exc:  # provider messages are never copied into evidence
            self.admission.observe_exception(exc, ceiling)
            raise
        self.admission.observe(response, ceiling)
        return response

    def catalogue(self):
        method = getattr(self.inner, "catalogue", None)
        return [] if method is None else method()

    def balance_micro(self):
        method = getattr(self.inner, "balance_micro", None)
        return None if method is None else method()

    def balance_of(self, model_id: str):
        method = getattr(self.inner, "balance_of", None)
        return None if method is None else method(model_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class DeniedMarket:
    """An x402-shaped object that cannot quote, register, or submit a paid call."""

    max_request_micro = 0

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        return False, "x402: rail denied by rehearsal"

    def quote(self, *_args, **_kwargs):
        raise RehearsalRefused("x402_denied")

    def register(self, *_args, **_kwargs):
        raise RehearsalRefused("x402_denied")

    def complete(self, *_args, **_kwargs):
        raise RehearsalRefused("x402_denied")


class DeniedTransferRail:
    """Preserve venue balance reads while refusing every treasury transfer direction."""

    name = "edition4-denied"

    def __init__(self, reader: Any):
        self.reader = reader

    def balances(self):
        return self.reader.balances()

    def gas_view(self):
        return {"configured": False, "remaining": {}}

    def plan(self, _direction: str):
        from factorylab.world.evm import RailError

        raise RailError("treasury rail denied by rehearsal")

    def preflight(self, direction: str, _amount: int, _gas_spent: dict):
        self.plan(direction)

    def prepare(self, direction: str, _state: dict, _gas_spent: dict):
        self.plan(direction)

    def send(self, direction: str, _reference: dict):
        self.plan(direction)

    def poll(self, direction: str, _state: dict):
        self.plan(direction)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reader, name)


class CapitalLoopRail:
    """Guarantees a capital-loop rehearsal can convert to Venice and move nothing else.

    The hybrid rail underneath can run every treasury route; this rehearsal admits only
    ``to_venice`` (its shadow leg and its real top-up) and refuses the CCTP exits and
    class moves before signing, as ``DeniedTransferRail`` refuses them all. Everything
    else, including the rail's name, reads through to the hybrid rail.
    """

    ALLOWED = ("to_venice",)
    STEPS = ("shadow_send", "venice_top_up")

    def __init__(self, rail: Any):
        self.rail = rail

    def _admit(self, direction: str) -> None:
        from factorylab.world.evm import RailError

        if direction not in self.ALLOWED and direction not in self.STEPS:
            raise RailError("treasury rail denied by rehearsal")

    def plan(self, direction: str):
        self._admit(direction)
        return self.rail.plan(direction)

    def preflight(self, direction: str, amount: int, gas_spent: dict):
        self._admit(direction)
        return self.rail.preflight(direction, amount, gas_spent)

    def prepare(self, step: str, state: dict, gas_spent: dict):
        self._admit(step)
        return self.rail.prepare(step, state, gas_spent)

    def send(self, step: str, reference: dict):
        self._admit(step)
        return self.rail.send(step, reference)

    def poll(self, step: str, state: dict):
        self._admit(step)
        return self.rail.poll(step, state)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.rail, name)


def venue_snapshot(exchange: Any) -> dict[str, Any]:
    """Read balances, positions and open orders without submitting a venue operation."""
    target = getattr(exchange, "target", exchange)
    try:
        account = target.account()
        positions = [
            {"coin": str(position.coin), "size": str(position.size),
             "entry_px": str(position.entry_px)}
            for position in getattr(account, "positions", ())
        ]
        result = {"equity_usd": str(account.equity_usd), "positions": positions}
    except Exception as exc:
        result = {"account_error": _safe_exception(exc)}
    try:
        orders = target.open_orders()
        result["open_orders"] = [
            {key: str(order.get(key, "") if isinstance(order, dict)
                      else getattr(order, key, ""))
             for key in ("coin", "client_id", "status", "order_id", "size", "side")}
            for order in orders
        ]
    except Exception as exc:
        result["open_orders_error"] = _safe_exception(exc)
    return result


class AdmissionClock:
    """Stop a supplied clock as soon as the prepaid admission guard becomes terminal."""

    def __init__(self, base: Any, admission: Admission):
        self.base = base
        self.admission = admission

    @property
    def interval_ns(self):
        return self.base.interval_ns

    @property
    def start_ns(self):
        if hasattr(self.base, "start_ns"):
            return self.base.start_ns
        return self.base.now_ns() if hasattr(self.base, "now_ns") else 0

    def set_interval(self, interval_ns: int) -> None:
        setter = getattr(self.base, "set_interval", None)
        if setter is not None:
            setter(interval_ns)

    def now_ns(self) -> int:
        now = getattr(self.base, "now_ns", None)
        return now() if callable(now) else self.start_ns

    def state(self):
        state = getattr(self.base, "state", None)
        return state() if state is not None else {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def events(self):
        stream = self.base.events() if hasattr(self.base, "events") else iter(self.base)
        for event in stream:
            yield event
            if self.admission.stop_reason is not None:
                return


def source_hash(root: Path | None = None) -> tuple[str, str]:
    """Hash the imported source tree and refuse a requested frozen-tree mismatch."""
    import factorylab

    imported = Path(factorylab.__file__).resolve().parents[1]
    requested = (root or DEFAULT_SOURCE).resolve()
    if not requested.is_dir() or requested != imported:
        raise RehearsalRefused("source_root_mismatch")
    actual = imported
    digest = hashlib.sha256()
    package = actual / "factorylab"
    package = package if package.is_dir() else actual
    files = sorted(p for p in package.rglob("*.py") if p.is_file() and ".key" not in p.name)
    for path in files:
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return str(actual), digest.hexdigest()


def runner_hash() -> str:
    """Hash this runner beside the imported runtime identity in every report."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def _factor_models(base: WorldManifest, reasoning: str):
    """Return the roster's model tiers with one explicit, provenance-visible override."""
    if reasoning == "preserve":
        return base.models
    models = []
    for model in base.models:
        configured = dict(model.reasoning)
        if reasoning == "off":
            effective = {"enabled": False}
        else:
            if not configured:
                raise RehearsalRefused("reasoning_on_requires_declared_tier_support")
            # A declared disabled toggle is explicit support for the provider adapter's
            # boolean control. Enabling it is the arm under test, not proof that the
            # upstream model generated or exposed reasoning.
            effective = {"enabled": True} if configured.get("enabled") is False else configured
        models.append(replace(model, reasoning=tuple(sorted(effective.items()))))
    return tuple(models)


def effective_manifest(
    base: WorldManifest,
    *,
    prompt_mode: str | None = None,
    reasoning: str = "preserve",
    native_completions: bool = False,
    capital_loop: bool = False,
) -> WorldManifest:
    """Freeze one factorized short-tick testnet identity before any paid work.

    Guarantees the treasury is stripped to an unconfigured rail unless ``capital_loop``
    is asked for, and then only for a world that declares the hybrid Venice mode with a
    reserve: its treasury is kept whole so ``to_venice`` can spend real mainnet USDC.
    """
    if base.exchange.kind != "hyperliquid" or base.exchange.mainnet:
        raise RehearsalRefused("testnet_hyperliquid_required")
    if type(capital_loop) is not bool:
        raise ValueError("capital_loop must be boolean")
    if capital_loop and (base.treasury.venice_network != "base-mainnet"
                         or base.treasury.reserve_address is None):
        raise RehearsalRefused("capital_loop_requires_hybrid_venice_world")
    providers = {m.provider for m in base.models}
    if not providers <= ALLOWED_PREPAID:
        raise RehearsalRefused("unsupported_or_x402_model_rail")
    if prompt_mode is not None and prompt_mode not in FACTOR_PROMPTS:
        raise ValueError("prompt_mode must be reference or compact")
    if reasoning not in FACTOR_REASONING:
        raise ValueError("reasoning must be preserve, off, or on")
    if type(native_completions) is not bool:
        raise ValueError("native_completions must be boolean")
    models = _factor_models(base, reasoning)
    if native_completions:
        models = tuple(replace(m, reasoning=tuple(
            (k, v) for k, v in m.reasoning if k != "max_tokens")) for m in models)
    exchange = replace(base.exchange, client_namespace=uuid4().hex)
    # An absent reserve selects UnconfiguredRail. It refuses transfers and does not
    # construct a signer; the charter, seed roster, $300 endowment and venue cash stay.
    treasury = replace(base.treasury, reserve_address=None, cctp_forwarding="never",
                       hyperevm_gas_budget_wei=0, base_gas_budget_wei=0,
                       venice_network=None, venice_shadow_sink=None,
                       max_venice_total_micro=None, venice_reserve_floor_micro=None,
                       venice_pay_to=None)
    if capital_loop:
        # The capital loop keeps its reserve and hybrid keys; the CCTP routes stay
        # unfunded (no gas budgets) and CapitalLoopRail refuses them before signing.
        treasury = replace(base.treasury, cctp_forwarding="never",
                           hyperevm_gas_budget_wei=0, base_gas_budget_wei=0)
    manifest = replace(
        base,
        name=f"{base.name}-edition4-rehearsal",
        tick_interval_ns=SHORT_TICK_NS,
        exchange=exchange,
        treasury=treasury,
        prompt=base.prompt if prompt_mode is None else PromptSpec(mode=prompt_mode),
        models=models,
        assemblies=(tuple(replace(a, max_tokens=None) for a in base.assemblies)
                    if native_completions else base.assemblies),
    )
    manifest.validate()
    return manifest


def _ratio(numerator: int, denominator: int) -> str | None:
    """Return an exact report ratio, or no value when its denominator is absent."""
    return str(Fraction(numerator, denominator)) if denominator else None


def _behavioral_screen(
    manifest: WorldManifest,
    runtime: Any,
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    admission: Admission,
    *,
    planned_ticks: int,
    minimum_ticks: int,
) -> dict[str, Any]:
    """Report whether the preregistered tick screen was delivered."""
    delivered = int(getattr(runtime, "ticks_consumed", 0))
    criteria = {
        "delivered_ticks": delivered >= minimum_ticks,
        "authoritative_bills": admission.uncertain_bills == 0 and admission.overruns == 0,
    }
    decisions = int(summary.get("stats", {}).get("decisions") or 0)
    io = summary.get("process_io_metrics") or {}
    complete = (io.get("provider") or {}).get("complete") or {}
    account = (io.get("exchange") or {}).get("account") or {}
    mids = (io.get("exchange") or {}).get("mids") or {}
    selected_calls = sum(int(metric.get("calls") or 0)
                         for metric in (complete, account, mids))
    selected_elapsed_ns = sum(int(metric.get("elapsed_ns") or 0)
                              for metric in (complete, account, mids))
    sufficient = all(criteria.values())
    backstop = manifest.evaluation.consequence_backstop_ticks
    return {
        "status": "sufficient" if sufficient else "inconclusive",
        "criteria_met": criteria,
        "declared_before_run": {
            "planned_tick_ceiling": planned_ticks,
            "minimum_delivered_ticks": minimum_ticks,
        },
        "horizons_ticks": {
            "consequence_backstop": backstop,
            "governance_activation_floor": backstop * manifest.timing.min_ratio,
            "observe_backstop_then_governance_floor": backstop * (manifest.timing.min_ratio + 1),
        },
        "delivered": {
            "ticks": delivered,
            "outstanding_decisions": int(summary.get("outstanding_decisions") or 0),
            "calls": admission.attempted,
            "decisions": decisions,
            "known_micro_per_tick": _ratio(admission.known_micro, delivered),
            "known_micro_per_decision": _ratio(admission.known_micro, decisions),
            "calls_per_tick": _ratio(admission.attempted, delivered),
            "decisions_per_tick": _ratio(decisions, delivered),
        },
        "critical_path_io": {
            "provider_complete_calls": int(complete.get("calls") or 0),
            "provider_complete_elapsed_ns": int(complete.get("elapsed_ns") or 0),
            "exchange_account_calls": int(account.get("calls") or 0),
            "exchange_account_elapsed_ns": int(account.get("elapsed_ns") or 0),
            "exchange_mids_calls": int(mids.get("calls") or 0),
            "exchange_mids_elapsed_ns": int(mids.get("elapsed_ns") or 0),
            "selected_total_calls": selected_calls,
            "selected_total_elapsed_ns": selected_elapsed_ns,
            "selected_mean_elapsed_ns": _ratio(selected_elapsed_ns, selected_calls),
            "scope": io.get("scope", "unavailable for injected/non-live runtime"),
        },
        "interpretation": (
            "coverage contract met; behavioral interpretation still requires comparison"
            if sufficient else
            "coverage contract not met; do not interpret absence or rates as behavior"
        ),
    }


def _venice_reserve_transport():
    """Capture only Venice prepaid/read routes while reserve auth is still available."""
    from factorylab.world.venice import VeniceError
    from factorylab.world.x402 import VENICE_URL, X402Client, http_request

    client = X402Client(base_url=VENICE_URL)

    def transport(method: str, path: str, payload: dict | None) -> dict:
        if (method, path) not in (("GET", "/models"), ("POST", "/chat/completions")):
            raise VeniceError(None, "route denied", sent=False)
        headers = {} if method == "GET" and path == "/models" else client.auth_headers(path)
        response = http_request(method, VENICE_URL.rstrip("/") + path, payload, headers)
        if not 200 <= response.status < 300:
            raise VeniceError(response.status, "HTTP request failed")
        return response.body

    return transport, client


def build_prepaid_provider(manifest: WorldManifest, *, keep_reserve_env: bool = False) -> Any:
    """Build prepaid providers, isolating Venice reserve authentication from runtime rails.

    ``keep_reserve_env`` leaves the reserve key in the environment for the caller to
    clear: a capital-loop rehearsal's hybrid rail captures its signer while the world is
    constructed, and the runner clears the variable immediately afterwards.
    """
    _load_dotenv()
    from factorylab.world.market import MultiProvider
    from factorylab.world.openrouter import OpenRouterProvider
    from factorylab.world.venice import VeniceProvider

    providers = {m.provider for m in manifest.models}
    if not providers <= ALLOWED_PREPAID:
        raise RehearsalRefused("unsupported_provider_rail")
    config = {m.id: dict(m.reasoning) for m in manifest.models if m.reasoning}
    extra = manifest.extra_body_config()
    schema_models = manifest.schema_contract_models()
    openrouter = None
    if "openrouter" in providers:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RehearsalRefused("openrouter_credential_missing")
        openrouter = OpenRouterProvider(reasoning_config=config,
                                        web_config=manifest.web_config(), extra_body=extra,
                                        schema_models=schema_models)
    venice = None
    if "venice" in providers:
        if os.environ.get("VENICE_API_KEY"):
            venice = VeniceProvider(reasoning_config=config, web_config=manifest.web_config(),
                                    schema_models=schema_models)
        elif os.environ.get("RESERVE_PRIVATE_KEY"):
            transport, reserve_client = _venice_reserve_transport()

            class CapturedReserveVenice(VeniceProvider):
                """Keep only read/completion SIWE auth after the reserve env is cleared."""

                def balance_micro(self):
                    return reserve_client.venice_balance()

            venice = CapturedReserveVenice(
                transport=transport, reasoning_config=config,
                web_config=manifest.web_config(), schema_models=schema_models)
        else:
            raise RehearsalRefused("venice_prepaid_credential_missing")
    # Runtime must never inherit the reserve private key. The captured transport can
    # authenticate Venice's prepaid completion/read routes but has no top-up route.
    if not keep_reserve_env:
        os.environ.pop("RESERVE_PRIVATE_KEY", None)
    if providers == {"openrouter"}:
        return openrouter
    if providers == {"venice"}:
        return venice
    return MultiProvider(openrouter, venice, DeniedMarket())


def _wall_ns() -> int:
    """The wall clock: the only clock a capital-loop run stamps a real validBefore with."""
    return time.time_ns()


def _http_request():
    from factorylab.world.x402 import http_request

    return http_request


def _sibling_runs(output_dir: Path | None) -> tuple[Path, ...]:
    """Earlier runs beside this one (``work/capital-loop/<run>``) that kept a diary.

    Guarantees the kill witness a run writes beside its own directory
    (``factorylab.runtime.witness.WITNESS_DIR``) is never taken for a run: it holds
    witness lines and no ledger key, so reading it as a run refused every capital-loop
    launch after the first. A directory of that name holding a ledger key is a run and
    is read like any other (a run may not be named so; see ``_refuse_reserved_out``).
    """
    from factorylab.runtime.witness import WITNESS_DIR

    if output_dir is None:
        return ()
    return tuple(sorted(
        p for p in output_dir.parent.iterdir()
        if p != output_dir and (p / "ledger.jsonl").exists()
        and not (p.name == WITNESS_DIR and not (p / "ledger.jsonl.key").exists())))


def _refuse_reserved_out(output_dir: Path | None) -> None:
    """Guarantees no run is written where the kill witness lives beside runs."""
    from factorylab.runtime.witness import WITNESS_DIR

    if output_dir is not None and output_dir.name == WITNESS_DIR:
        raise RehearsalRefused("output_dir_reserved_for_witness")


def _denied_rails(capital_loop: bool) -> list[str]:
    """The rails this rehearsal refuses before signing; a capital loop admits to_venice."""
    denied = ["x402", "treasury.to_reserve", "treasury.to_venue", "treasury.to_venice"]
    if capital_loop:
        denied = [*denied[:-1], "treasury.spot_to_perps", "treasury.perps_to_spot"]
    return denied


def _safe_exception(exc: BaseException) -> dict[str, str]:
    """Report only repository-owned exception identity, never a provider message."""
    module = type(exc).__module__
    return {
        "type": type(exc).__name__,
        "module": module if module.startswith("factorylab") else "external",
    }


class RunStopped(BaseException):
    """An operator's SIGINT, SIGTERM or SIGHUP, turned into an orderly stop of a run.

    A BaseException, like ``KeyboardInterrupt``, so the world's own ``except Exception``
    handlers (a treasury send, a provider call) cannot swallow the stop.
    """


class _StopOnSignal:
    """Guarantees SIGINT, SIGTERM and SIGHUP end a run through its own ``finally``.

    While armed, the first of them raises ``RunStopped`` in the main thread, wherever
    the run is, and blocks all three there before it raises, so none can interrupt the
    stop. ``hold`` (the first thing the run's ``finally`` does) disarms and blocks them
    too; ``release`` unblocks them once the report is written and announced, when a
    signal that arrived meanwhile is only recorded; ``restore`` puts back the handlers
    ``arm`` replaced. A signal whose disposition is ``SIG_IGN`` when armed (SIGHUP under
    ``nohup``) is left alone. No thread is started. Python runs signal handlers, and
    raises ``KeyboardInterrupt``, only in the main thread: off it nothing is installed
    and nothing here stops a run, which then ends only as its process does.
    """

    NAMES = ("SIGINT", "SIGTERM", "SIGHUP")

    def __init__(self) -> None:
        self.previous: dict[int, Any] = {}
        self.armed = False
        self.received: list[str] = []
        self._mask: set | None = None

    def arm(self) -> None:
        import signal
        import threading

        if threading.current_thread() is not threading.main_thread():
            return
        for name in self.NAMES:
            number = getattr(signal, name, None)
            if number is None or signal.getsignal(number) is signal.SIG_IGN:
                continue  # an operator's nohup (or any ignore) is theirs to keep
            self.previous[number] = signal.signal(number, self._stop)
        self.armed = True

    def _stop(self, number: int, _frame: Any) -> None:
        import signal

        name = signal.Signals(number).name
        self.received.append(name)
        if self.armed:
            self.armed = False
            self._block()
            raise RunStopped(name)

    def _block(self) -> None:
        import signal
        import threading

        if (self._mask is None and self.previous and hasattr(signal, "pthread_sigmask")
                and threading.current_thread() is threading.main_thread()):
            self._mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(self.previous))

    def hold(self) -> None:
        """No handled signal is raised or delivered from here until ``release``."""
        self.armed = False
        self._block()

    def release(self) -> None:
        """Unblock; a signal that arrived while held reaches ``_stop`` and is recorded."""
        import signal

        self.armed = False
        if self._mask is not None:
            mask, self._mask = self._mask, None
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)

    def restore(self) -> None:
        """Put back every handler ``arm`` replaced; never raises.

        A previous handler of ``None`` was installed from C and cannot be reinstalled
        from Python: its signal gets ``SIG_DFL``.
        """
        import signal

        self.release()
        for number, handler in self.previous.items():
            try:
                signal.signal(number, signal.SIG_DFL if handler is None else handler)
            except (ValueError, OSError, TypeError):
                pass
        self.previous = {}


def run_rehearsal(world: str | Path = DEFAULT_WORLD, **kwargs: Any) -> dict[str, Any]:
    """Run a fresh bounded testnet rehearsal and persist a sanitized evidence report.

    Keywords are ``_rehearse``'s. Guarantees a capital-loop run's reserve lock is
    released when this returns or raises, whatever the path, so a library caller can
    never keep a reserve locked in a living process by an exception it caught; and the
    signal handlers a capital-loop run installs are restored before it returns.
    """
    held: list = []
    stops = _StopOnSignal()
    try:
        return _rehearse(world, held=held, stops=stops, **kwargs)
    finally:
        stops.restore()
        for lock in held:
            lock.close()


def _rehearse(
    world: str | Path = DEFAULT_WORLD,
    *,
    held: list,
    stops: _StopOnSignal | None = None,
    out: str | Path | None = None,
    duration_ns: int = DEFAULT_DURATION_NS,
    target_ticks: int | None = None,
    cap_micro: int = DEFAULT_CAP_MICRO,
    max_calls: int = 2_000,
    provider: Any | None = None,
    exchange: Any | None = None,
    clock_source: Any | None = None,
    source_root: str | Path | None = None,
    now_ns: Callable[[], int] | None = None,
    observe: bool = False,
    prompt_mode: str | None = None,
    reasoning: str = "preserve",
    minimum_ticks: int | None = None,
    capital_loop: bool = False,
    previous_runs: tuple = (),
    capital_loop_transport: Callable | None = None,
    capital_loop_lock_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run a fresh bounded testnet rehearsal and persist a sanitized evidence report.

    ``capital_loop`` runs a hybrid Venice world (docs/architecture/
    capital-loop-rehearsal.md): ``to_venice`` stays open and spends real Base mainnet
    USDC, every other treasury route and x402 purchase stays denied. It then also
    guarantees: the reserve is held by this run alone on this host from before its
    launch check until the run returns (``ReserveLock``, in ``capital_loop_lock_dir``,
    by default the operator's ``~/.factorylab/capital-loop``; ``held`` receives it so
    ``run_rehearsal`` releases it); the run is planned at least ``SETTLEMENT_RATIO``
    conversion settlement horizons long; and a run that ends with a top-up still
    submitted says so in ``report["capital_loop_outstanding"]``, written to
    ``report.json`` first and then printed on stdout and stderr, naming
    ``scripts/capital_loop_outstanding.py``.
    """
    if type(duration_ns) is not int or duration_ns <= 0:
        raise ValueError("duration_ns must be positive integer")
    if capital_loop and out is None:
        # Real money needs a diary on disk: the next launch reads it to refuse while
        # anything this run authorized could still settle.
        raise ValueError("a capital-loop rehearsal requires an output directory")
    if capital_loop and now_ns is not None:
        # A capital-loop run signs real Base mainnet authorizations: their validBefore,
        # and the settlement bound measured against it, both read the wall clock. An
        # injected clock stays for testnet-only runs, where nothing real is stamped.
        raise RehearsalRefused("capital_loop_requires_the_wall_clock")
    if now_ns is None:
        now_ns = _wall_ns
    if target_ticks is not None and (type(target_ticks) is not int or target_ticks <= 0):
        raise ValueError("target_ticks must be a positive integer")
    if type(cap_micro) is not int or cap_micro <= 0:
        raise ValueError("cap_micro must be positive integer")
    if type(max_calls) is not int or max_calls <= 0:
        raise ValueError("max_calls must be positive integer")
    if type(observe) is not bool or (observe and out is None):
        raise ValueError("observe must be boolean and requires an output directory")
    if minimum_ticks is not None and (type(minimum_ticks) is not int or minimum_ticks <= 0):
        raise ValueError("minimum_ticks must be a positive integer")
    output_dir = None
    report_path = None
    if out is not None:
        output_dir = Path(out)
        # Before anything is created: a refused launch must not leave a run directory
        # where the private kill witness lives (the #142 review).
        _refuse_reserved_out(output_dir)
        output_dir.mkdir(parents=True, exist_ok=False)
        report_path = output_dir / "report.json"
    admission = Admission(cap_micro, max_calls, recover_provider_failures=True)
    lock = None
    try:
        base = load_manifest(str(world))
        manifest = effective_manifest(
            base,
            prompt_mode=prompt_mode,
            reasoning=reasoning,
            native_completions=True,
            capital_loop=capital_loop,
        )
        launch = None
        if capital_loop:
            # The typed floor bounds nothing across runs unless the chain agrees: the
            # reserve, read keylessly now, may lose at most max_venice_total_usd before
            # reaching it, and no earlier run may have left an authorization that can
            # still settle (a crashed world's last one stays valid for its timeout).
            from factorylab.runtime.capital_loop import (
                MAX_AUTHORIZATION_S,
                ReserveLock,
                check_authorization_record,
                cooling_off_check,
                launch_check,
                settlement_bound,
            )

            # Taken before the check and held until the run returns: the floor check
            # reads the chain, which cannot see another run's signed-but-unsettled
            # authorization, so two runs on one reserve must never overlap.
            lock = ReserveLock(manifest.treasury.reserve_address,
                               lock_dir=capital_loop_lock_dir)
            held.append(lock)
            transport = capital_loop_transport or _http_request()
            # The clock delivers floor(duration / tick) ticks, or --ticks when fewer,
            # and its first tick comes at once: N ticks span N - 1 intervals.
            ticks = duration_ns // manifest.tick_interval_ns
            if target_ticks is not None:
                ticks = min(ticks, target_ticks)
            run_ns = max(0, ticks - 1) * manifest.tick_interval_ns
            # The host clock that will stamp validBefore is the runtime's own clock.
            settlement = settlement_bound(run_ns, manifest.tick_interval_ns,
                                          transport=transport,
                                          now_s=lambda: now_ns() // 1_000_000_000)
            # The reserve's last holder is read wherever it ran, not only beside --out:
            # it is the one earlier run whose authorization can still be live.
            last = lock.last_run()
            runs = tuple(previous_runs) + _sibling_runs(output_dir)
            launch = launch_check(manifest, previous_runs=runs, recorded_run=last,
                                  transport=transport)
            # Every authorization ever written ahead of signing, whatever any diary now
            # holds, is resolved against finalized Base before this run may sign.
            recorded = check_authorization_record(lock, transport=transport)
            # And the chain itself, for a record rolled back with its directory: no
            # authorization the reserve made within one settlement window of finalized
            # blocks may be missing from the record.
            cooling = cooling_off_check(
                lock, transport=transport,
                window_s=MAX_AUTHORIZATION_S + 2 * settlement["finalized_behind_s"])
            launch = {**launch, "settlement": settlement, "reserve_lock": str(lock.path),
                      "last_run": None if last is None else str(last),
                      "authorization_record": recorded, "cooling_off": cooling}
            print(json.dumps({"capital_loop_launch_check": {
                k: v for k, v in launch.items() if k != "previous_runs"}}), flush=True)
        source_path, frozen_hash = source_hash(Path(source_root) if source_root else None)
    except Exception as exc:
        if lock is not None:
            lock.close()
        report = {"status": "failed", "error": _safe_exception(exc),
                  "cost": admission.report(), "denied_rails": _denied_rails(capital_loop)}
        refusal = getattr(exc, "reason", None)
        if isinstance(refusal, str):
            # Locally generated reason codes and numbers only; never a response body.
            report["refusal"] = {"reason": refusal, **getattr(exc, "detail", {})}
            if capital_loop:
                print(json.dumps({"capital_loop_refused": report["refusal"]}, default=str),
                      flush=True)
                _announce_recovery(report)
        if report_path is not None:
            report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        return report
    planned_ticks = target_ticks or max(1, duration_ns // manifest.tick_interval_ns)
    minimum_ticks = minimum_ticks or manifest.evaluation.consequence_backstop_ticks
    if minimum_ticks > planned_ticks:
        if lock is not None:
            lock.close()
        raise ValueError("minimum_ticks exceeds the rehearsal's planned tick ceiling")
    before_reasoning = {model.id: dict(model.reasoning) for model in base.models}
    after_reasoning = {model.id: dict(model.reasoning) for model in manifest.models}
    roster_changed = roster_hash(base) != roster_hash(manifest)
    report: dict[str, Any] = {
        "status": "prepared",
        "source": {"requested_root": str(Path(source_root) if source_root else DEFAULT_SOURCE),
                    "imported_root": source_path, "sha256": frozen_hash,
                    "runner_sha256": runner_hash()},
        "world": {"path": str(Path(world)), "original_hash": base.manifest_hash(),
                  "effective_hash": manifest.manifest_hash(),
                  "original_manifest": json.loads(base.canonical_json()),
                  "manifest": json.loads(manifest.canonical_json())},
        "preserved": {
            "roster_sha256": {"from": roster_hash(base), "to": roster_hash(manifest)},
            "charter_sha256": {
                "from": charter_digest(charter_content(
                    json.loads(base.canonical_json())["charter"])),
                "to": charter_digest(charter_content(
                    json.loads(manifest.canonical_json())["charter"])),
            },
            "initial_balance_micro": {"from": base.initial_balance_micro,
                                       "to": manifest.initial_balance_micro},
            "venue_start_cash_usd": {"from": base.exchange.start_cash_usd,
                                      "to": manifest.exchange.start_cash_usd},
            "endowment": {"from": {"locked_micro": base.endowment.locked_micro,
                                      "releases": list(base.endowment.releases)},
                          "to": {"locked_micro": manifest.endowment.locked_micro,
                                  "releases": list(manifest.endowment.releases)}},
        },
        "factors": {
            "fixed_at_launch": True,
            "completion_allowance": "provider",
            "prompt_mode": manifest.prompt.mode,
            "requested": {
                "prompt_mode": prompt_mode or "preserve",
                "reasoning": reasoning,
            },
            "reasoning": {
                "requested_reasoning": reasoning,
                "from": before_reasoning,
                "provider_effective_request": after_reasoning,
                "roster_digest_changes": roster_changed,
                "actual_reasoning_provenance": {
                    "status": "unknown",
                    "reason": (
                        "provider request configuration does not establish that the upstream "
                        "model generated or exposed hidden reasoning"
                    ),
                },
            },
            "norms_preserved": base.charter.norms == manifest.charter.norms,
            "roster_preserved": not roster_changed,
        },
        "protocol": {
            "duration_ns": duration_ns,
            **({"target_ticks": target_ticks} if target_ticks is not None else {}),
            "cap_micro": cap_micro,
            "max_calls": max_calls,
            "planned_tick_ceiling": planned_ticks,
            "minimum_delivered_ticks": minimum_ticks,
            "no_live_parameter_changes": True,
            "no_horizon_extension": True,
        },
        "differences": {
            "tick_interval_ns": {"from": base.tick_interval_ns, "to": manifest.tick_interval_ns},
            "exchange.client_namespace": {"from": base.exchange.client_namespace,
                                           "to": manifest.exchange.client_namespace},
            "treasury.reserve_address": {"from": bool(base.treasury.reserve_address),
                                         "to": bool(manifest.treasury.reserve_address)},
            "treasury.cctp_forwarding": {"from": base.treasury.cctp_forwarding, "to": "never"},
            "treasury.gas_budgets": {"from": {"hyperevm": base.treasury.hyperevm_gas_budget_wei,
                                                 "base": base.treasury.base_gas_budget_wei},
                                      "to": {"hyperevm": 0, "base": 0}},
        },
        "denied_rails": _denied_rails(capital_loop),
        "cost": admission.report(),
    }
    if capital_loop:
        report["capital_loop"] = {
            "venice_network": manifest.treasury.venice_network,
            "venice_shadow_sink": manifest.treasury.venice_shadow_sink,
            "max_venice_per_window_micro": manifest.treasury.max_venice_per_window,
            "max_venice_total_micro": manifest.treasury.max_venice_total_micro,
            "venice_reserve_floor_micro": manifest.treasury.venice_reserve_floor_micro,
            "venice_pay_to": manifest.treasury.venice_pay_to,
            "reserve_address": manifest.treasury.reserve_address,
            "launch_check": launch,
        }
    runtime = None
    observer = None
    if stops is None:
        stops = _StopOnSignal()  # never armed: only run_rehearsal arms, and restores
    elif capital_loop:
        # From the world's construction on, SIGINT, SIGTERM or SIGHUP stop it through
        # the finally below: the report, the outstanding warning and the exit code are
        # written whatever stopped it (a default SIGTERM would write none of them).
        stops.arm()
    try:
        try:
            try:
                if provider is None:
                    provider = build_prepaid_provider(manifest, keep_reserve_env=capital_loop)
                guarded = provider if isinstance(provider, PrepaidProvider) else PrepaidProvider(
                    provider, manifest, admission)
                events = planned_ticks
                if clock_source is None and manifest.exchange.kind != "fake":
                    clock_source = LiveClock(manifest.tick_interval_ns, events,
                                             now_ns=now_ns, deadline_ns=now_ns() + duration_ns)
                clock_source = (AdmissionClock(clock_source, admission)
                                if clock_source is not None else None)
                runtime = Runtime(
                    manifest, events=events, seed=manifest.seed, initial_balance_micro=None,
                    ledger_path=None if output_dir is None else str(output_dir / "ledger.jsonl"),
                    router_gamma=0.1, provider=guarded, market=DeniedMarket(),
                    exchange=exchange, clock_source=clock_source,
                    kill_at_end=True, capital_loop=capital_loop,
                )
            finally:
                # The hybrid rail captured its signer during construction; the running
                # world never inherits the reserve key, capital loop or not.
                if capital_loop:
                    os.environ.pop("RESERVE_PRIVATE_KEY", None)
            if capital_loop:
                from factorylab.world.treasury_rails import HybridRail

                hybrid = runtime.treasury.rail.target
                if not isinstance(hybrid, HybridRail):
                    raise RehearsalRefused("capital_loop_requires_the_hybrid_rail")
                # This run's diary exists now and nothing has signed yet: from here on the
                # next launch on this reserve reads it, wherever its --out is.
                lock.record_run(output_dir)
                # Every authorization is written ahead, outside the diary, before it is
                # signed; and its validBefore is stamped by the one clock the settlement
                # bound was measured against, so the two cannot disagree.
                hybrid.authorization_log = lock.authorization_log(output_dir)
                hybrid.now_s = lambda: now_ns() // 1_000_000_000
                # Only the conversion is admitted; the CCTP exits and class moves are
                # refused before signing, exactly as the denied rail refuses them.
                runtime.treasury.rail.target = CapitalLoopRail(hybrid)
            else:
                # Bootstrap gives an unconfigured rail for a manifest without a reserve, but
                # that rail still supports the venue's spot/perps class move. Replace its
                # target before launch so every treasury direction is refused pre-signing.
                runtime.treasury.rail.target = DeniedTransferRail(runtime.treasury.rail.target)
            if observe:
                from scripts import edition4_observer

                observer = edition4_observer.attach_rehearsal_observer(
                    runtime, output_dir / "observer", admission_report=admission.report)
                report["observer"] = {
                    "enabled": True,
                    "scope": "rehearsal_only_recent_evidence_window",
                    "path": str(output_dir / "observer"),
                    "module_sha256": hashlib.sha256(
                        Path(edition4_observer.__file__).read_bytes()).hexdigest(),
                }
            report["venue_before"] = venue_snapshot(runtime.exchange)
            report["venue_confounds"] = {
                "declared_start_cash_usd": manifest.exchange.start_cash_usd,
                "observed_equity_usd": report["venue_before"].get("equity_usd"),
            }
            summary = runtime.run()
            report["status"] = "completed"
            report["summary"] = summary
            report["venue_after"] = venue_snapshot(runtime.exchange)
            report["timing"] = {
                "declared_interval_ns": manifest.tick_interval_ns,
                "ticks": runtime.ticks_consumed,
                "events": summary.get("stats", {}).get("events"),
                "measured_interval_ns": (
                    clock_source.base.measured_interval_ns()
                    if clock_source is not None
                    and hasattr(clock_source.base, "measured_interval_ns") else None
                ),
            }
            items = runtime.ledger._recovery_items()
            report["behavioral_screen"] = _behavioral_screen(
                manifest,
                runtime,
                summary,
                items,
                admission,
                planned_ticks=planned_ticks,
                minimum_ticks=minimum_ticks,
            )
            if output_dir is not None:
                selected = [item for item in items if item.get("kind") != "snapshot"]
                (output_dir / "events.json").write_text(
                    json.dumps(selected, indent=2, default=str) + "\n"
                )
        except (RunStopped, KeyboardInterrupt) as stop:
            # An operator's stop is an orderly end: the world is not resumed, and what it
            # left outstanding is reported below like any other end.
            report["status"] = "stopped"
            report["stopped_by"] = str(stop) if isinstance(stop, RunStopped) else "SIGINT"
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = _safe_exception(exc)
    except (RunStopped, KeyboardInterrupt) as late:
        # A stop that landed inside one of the handlers above, before it could finish.
        report["status"] = "stopped"
        report["stopped_by"] = str(late) if isinstance(late, RunStopped) else "SIGINT"
    finally:
        try:
            stops.hold()  # from here no handled signal interrupts the report
        except RunStopped as late:  # one that landed as the finally began
            stops.hold()
            report["status"], report["stopped_by"] = "stopped", str(late)
        if stops.received:
            report["signals_received"] = list(stops.received)
        if observer is not None:
            observer.detach()
        if capital_loop and runtime is not None:
            _report_outstanding(report, runtime, output_dir)
        report["cost"] = admission.report()
        try:
            if report_path is not None:
                report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 - the warning and exit code must survive it
            # Not raised: the caller still gets the report, and ``exit_code`` still
            # answers 3 for an outstanding top-up (1 otherwise).
            report["report_write_failed"] = type(exc).__name__
        # Written first, then printed; and printed even when the write failed.
        _announce_outstanding(report)
        if lock is not None:
            lock.close()
        stops.release()  # a signal that arrived meanwhile is only recorded now
    return report


def _report_outstanding(report: dict, runtime: Any, output_dir: Path | None) -> None:
    """Guarantees a run that ends with an unbooked conversion says so, and never raises.

    A top-up still submitted at the end (its authorization may settle after the world
    is dead, or settled with its credit unbooked), a shadow send still pending, or a
    diary that cannot be read is written to ``report["capital_loop_outstanding"]`` with
    the next step, ``scripts/capital_loop_outstanding.py`` on this run's directory, for
    ``_announce_outstanding`` to print once the report is on disk. Nothing is signed or
    retried.
    """
    from factorylab.runtime.capital_loop import (
        OUTSTANDING_SCRIPT,
        journaled_references,
        submitted_top_ups,
    )

    try:
        items = runtime.ledger._recovery_items()
        top_ups, shadows, unreadable = submitted_top_ups(items), journaled_references(
            items)[1], None
    except Exception as exc:  # noqa: BLE001 - an unread diary is reported, never guessed
        top_ups, shadows, unreadable = None, None, type(exc).__name__
    section = report.setdefault("capital_loop", {})
    if not top_ups and not shadows and unreadable is None:
        section["outstanding_at_end"] = {"top_ups_submitted": [], "shadow_sends_pending": []}
        return
    import shlex

    # Quoted for a shell, and given as an argument list too: a run directory with a
    # space or a ";" must neither split nor run as syntax while a top-up may be live.
    argv = ["uv", "run", "python", OUTSTANDING_SCRIPT, str(output_dir)]
    command = shlex.join(argv)
    outstanding = {
        "warning": ("the run ended with a Venice conversion unbooked: a top-up "
                    "authorization may still settle on Base mainnet after the world died"
                    if top_ups or unreadable else
                    "the run ended with a shadow send unconfirmed (testnet money)"),
        "top_ups_submitted": top_ups, "shadow_sends_pending": shadows,
        "diary_unreadable": unreadable, "next_step": command, "next_step_argv": argv,
        "runbook": "docs/architecture/capital-loop-rehearsal.md, After the run",
    }
    section["outstanding_at_end"] = report["capital_loop_outstanding"] = outstanding


def _announce_outstanding(report: dict) -> None:
    """Print a run's outstanding conversion on stdout and stderr, if it left one."""
    import sys

    outstanding = report.get("capital_loop_outstanding")
    if outstanding is None:
        return
    # stderr first, and each print alone: a closed stdout must not silence stderr.
    for line, stream in (
            (f"CAPITAL LOOP OUTSTANDING: {outstanding['warning']}. Before touching the "
             f"reserve or relaunching, run: {outstanding['next_step']}", sys.stderr),
            (json.dumps({"capital_loop_outstanding": outstanding}, default=str), sys.stdout)):
        try:
            print(line, file=stream, flush=True)
        except (OSError, ValueError):
            pass


def _announce_recovery(report: dict) -> None:
    """Print, loudly, a launch refused because real money may have moved unbooked."""
    import shlex
    import sys

    from factorylab.runtime.capital_loop import OUTSTANDING_SCRIPT, RECOVERY_REASONS

    refusal = report.get("refusal") or {}
    if refusal.get("reason") not in RECOVERY_REASONS:
        return
    if refusal["reason"] == "unrecorded_reserve_authorization":
        message = (f"CAPITAL LOOP RECOVERY: finalized Base shows the reserve's "
                   f"authorization(s) {refusal.get('nonces')} used within the last "
                   "settlement window, and the write-ahead record does not know them (a "
                   "restored or copied lock directory, or a signer outside this code). "
                   "Settle the books by hand (docs/architecture/capital-loop-rehearsal.md, "
                   "After a crash); launches refuse until they fall outside the window.")
    else:
        nonces = [row.get("nonce") for row in refusal.get("authorizations") or ()]
        steps = [shlex.join(["uv", "run", "python", OUTSTANDING_SCRIPT, "--acknowledge", n])
                 for n in nonces]
        message = (f"CAPITAL LOOP RECOVERY: finalized Base shows {len(nonces)} recorded "
                   f"authorization(s) used that no diary booked as financing: {nonces}. "
                   "Settle the books by hand (docs/architecture/capital-loop-rehearsal.md, "
                   f"After a crash), then acknowledge each: {'; '.join(steps)}")
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def exit_code(report: dict) -> int:
    """0 for a completed run with nothing outstanding and its report on disk; 3 when a
    conversion is left unbooked (a top-up with no financing booked, or a diary unread at
    the end) or a launch was refused because a recorded authorization settled unbooked,
    whatever else happened, report write included, since that is the operator's next
    step; otherwise 1."""
    from factorylab.runtime.capital_loop import RECOVERY_REASONS

    outstanding = report.get("capital_loop_outstanding") or {}
    if outstanding.get("top_ups_submitted") or outstanding.get("diary_unreadable"):
        return 3
    if (report.get("refusal") or {}).get("reason") in RECOVERY_REASONS:
        return 3  # a recorded authorization settled with no booking: a recovery
    if report.get("report_write_failed"):
        return 1
    return 0 if report.get("status") == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration", default="30m",
                        help="wall-clock safety deadline")
    parser.add_argument("--ticks", type=int,
                        help="stop after this many delivered ticks, subject to safety limits")
    parser.add_argument("--cap-usd", default="5")
    parser.add_argument("--max-calls", type=int, default=2_000)
    parser.add_argument("--prompt", choices=sorted(FACTOR_PROMPTS))
    parser.add_argument("--reasoning", choices=sorted(FACTOR_REASONING), default="preserve")
    parser.add_argument("--minimum-ticks", type=int)
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--observe", action="store_true",
                        help="write an opt-in rehearsal dashboard at completed ticks")
    parser.add_argument("--capital-loop", action="store_true",
                        help="hybrid Venice world only: admit to_venice, which spends REAL "
                        "Base mainnet USDC (docs/architecture/capital-loop-rehearsal.md)")
    parser.add_argument("--previous-run", type=Path, action="append", default=[],
                        help="with --capital-loop: an earlier run directory whose top-up "
                        "authorizations must all be settled or expired before launch "
                        "(sibling run directories of --out are always checked)")
    args = parser.parse_args(argv)
    from factorylab.runtime.worlds import duration_ns

    report = run_rehearsal(args.world, out=args.out, duration_ns=duration_ns(args.duration),
                           target_ticks=args.ticks,
                           cap_micro=usd_to_micro(args.cap_usd, rounding="floor"),
                           max_calls=args.max_calls, source_root=args.source_root,
                           observe=args.observe, prompt_mode=args.prompt,
                           reasoning=args.reasoning,
                           minimum_ticks=args.minimum_ticks,
                           capital_loop=args.capital_loop,
                           previous_runs=tuple(args.previous_run))
    summary = {"status": report["status"], "out": str(args.out), "cost": report["cost"],
               "behavioral_screen": report.get("behavioral_screen")}
    if "capital_loop_outstanding" in report:
        summary["capital_loop_outstanding"] = report["capital_loop_outstanding"]
    try:
        print(json.dumps(summary, indent=2, default=str))
    except (OSError, ValueError):
        pass  # the terminal is gone; the report is on disk and the exit code stands
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
