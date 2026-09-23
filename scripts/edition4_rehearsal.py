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
        # Models whose feasibility probe fit the cap at least once, and those whose
        # probe exceeded the whole cap: when every seated model is in the second set
        # and none in the first, no call can ever be admitted (Codex review of #136).
        self._fit: set[str] = set()
        self._over_cap: set[str] = set()

    def _namespace_allowed(self, model_id: str) -> bool:
        if model_id.startswith("venice:"):
            return "venice" in {m.provider for m in self.manifest.models}
        return not model_id.startswith("x402:") and "openrouter" in {
            m.provider for m in self.manifest.models
        }

    def _ceiling(self, req: ModelRequest) -> int:
        price = self._prices.price(req.model_id)
        chars = len(req.system) + sum(len(str(m.get("content", ""))) for m in req.messages)
        return price.cost(int(chars * 1.5) + 64, req.max_tokens)

    def affordable(self, model_id: str, ceiling_micro: int) -> tuple[bool, str]:
        if not self._namespace_allowed(model_id):
            return False, "provider: rail denied"
        allowed, reason = self.admission.can_admit(ceiling_micro, probe=True)
        if reason == "quote_above_cap":
            self._over_cap.add(model_id)
            seated = {a.model_id for a in self.manifest.assemblies}
            if not self._fit and seated <= self._over_cap:
                # A cap below every seat's worst case admits nothing; the run ends
                # rather than recording NOOP decisions for its remaining events.
                self.admission.stop_reason = (self.admission.stop_reason
                                              or "cap_below_every_seat")
        elif allowed:
            self._fit.add(model_id)
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
    openrouter = None
    if "openrouter" in providers:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RehearsalRefused("openrouter_credential_missing")
        openrouter = OpenRouterProvider(reasoning_config=config,
                                        web_config=manifest.web_config(), extra_body=extra)
    venice = None
    if "venice" in providers:
        if os.environ.get("VENICE_API_KEY"):
            venice = VeniceProvider(reasoning_config=config, web_config=manifest.web_config())
        elif os.environ.get("RESERVE_PRIVATE_KEY"):
            transport, reserve_client = _venice_reserve_transport()

            class CapturedReserveVenice(VeniceProvider):
                """Keep only read/completion SIWE auth after the reserve env is cleared."""

                def balance_micro(self):
                    return reserve_client.venice_balance()

            venice = CapturedReserveVenice(
                transport=transport, reasoning_config=config,
                web_config=manifest.web_config())
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


def _http_request():
    from factorylab.world.x402 import http_request

    return http_request


def _sibling_runs(output_dir: Path | None) -> tuple[Path, ...]:
    """Earlier runs beside this one (``work/capital-loop/<run>``) that kept a diary."""
    if output_dir is None:
        return ()
    return tuple(sorted(p for p in output_dir.parent.iterdir()
                        if p != output_dir and (p / "ledger.jsonl").exists()))


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


def run_rehearsal(
    world: str | Path = DEFAULT_WORLD,
    *,
    out: str | Path | None = None,
    duration_ns: int = DEFAULT_DURATION_NS,
    target_ticks: int | None = None,
    cap_micro: int = DEFAULT_CAP_MICRO,
    max_calls: int = 2_000,
    provider: Any | None = None,
    exchange: Any | None = None,
    clock_source: Any | None = None,
    source_root: str | Path | None = None,
    now_ns: Callable[[], int] = time.time_ns,
    observe: bool = False,
    prompt_mode: str | None = None,
    reasoning: str = "preserve",
    minimum_ticks: int | None = None,
    capital_loop: bool = False,
    previous_runs: tuple = (),
    capital_loop_transport: Callable | None = None,
) -> dict[str, Any]:
    """Run a fresh bounded testnet rehearsal and persist a sanitized evidence report.

    ``capital_loop`` runs a hybrid Venice world (docs/architecture/
    capital-loop-rehearsal.md): ``to_venice`` stays open and spends real Base mainnet
    USDC, every other treasury route and x402 purchase stays denied.
    """
    if type(duration_ns) is not int or duration_ns <= 0:
        raise ValueError("duration_ns must be positive integer")
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
        output_dir.mkdir(parents=True, exist_ok=False)
        report_path = output_dir / "report.json"
    admission = Admission(cap_micro, max_calls, recover_provider_failures=True)
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
            from factorylab.runtime.capital_loop import launch_check

            runs = tuple(previous_runs) + _sibling_runs(output_dir)
            launch = launch_check(manifest, previous_runs=runs,
                                  transport=capital_loop_transport or _http_request())
            print(json.dumps({"capital_loop_launch_check": {
                k: v for k, v in launch.items() if k != "previous_runs"}}), flush=True)
        source_path, frozen_hash = source_hash(Path(source_root) if source_root else None)
    except Exception as exc:
        report = {"status": "failed", "error": _safe_exception(exc),
                  "cost": admission.report(), "denied_rails": _denied_rails(capital_loop)}
        refusal = getattr(exc, "reason", None)
        if isinstance(refusal, str):
            # Locally generated reason codes and numbers only; never a response body.
            report["refusal"] = {"reason": refusal, **getattr(exc, "detail", {})}
            if capital_loop:
                print(json.dumps({"capital_loop_refused": report["refusal"]}, default=str),
                      flush=True)
        if report_path is not None:
            report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        return report
    planned_ticks = target_ticks or max(1, duration_ns // manifest.tick_interval_ns)
    minimum_ticks = minimum_ticks or manifest.evaluation.consequence_backstop_ticks
    if minimum_ticks > planned_ticks:
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
            # Only the conversion is admitted; the CCTP exits and class moves are
            # refused before signing, exactly as the denied rail refuses them.
            runtime.treasury.rail.target = CapitalLoopRail(runtime.treasury.rail.target)
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
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = _safe_exception(exc)
    finally:
        if observer is not None:
            observer.detach()
        report["cost"] = admission.report()
        if report_path is not None:
            report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


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
    print(json.dumps({"status": report["status"], "out": str(args.out),
                      "cost": report["cost"],
                      "behavioral_screen": report.get("behavioral_screen")}, indent=2))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
