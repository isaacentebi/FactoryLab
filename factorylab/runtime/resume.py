"""Authenticated checkpoints and deterministic replay of the runtime's normal paths.

Snapshots contain data, never executable objects, clients or credentials. Tail
replay re-executes events against an append-checking ledger and recorded external
responses. The real venue and paid providers are never called again for a recorded
response. An unacknowledged live write is ambiguous and cannot be retried safely.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import Any

from factorylab.kernel.ledger import Ledger, LedgerLock, _canonical


class ResumeError(RuntimeError):
    """Recovery refuses invalid evidence or an ambiguous external side effect."""


class _ReplayFault(BaseException):
    """Replay faults cannot be mistaken for provider failures by ordinary runtime handlers."""


def _record_types() -> dict[str, type]:
    from factorylab.charter.amendment import Amendment
    from factorylab.charter.charter import Charter, MetricCard
    from factorylab.charter.committee import Ballot, Committee, Seat
    from factorylab.charter.controller import CardRegion, _CardState
    from factorylab.cortex.assembly import AssemblySpec
    from factorylab.cortex.tools import PopulationTool
    from factorylab.kernel.events import Event, EventKind
    from factorylab.kernel.queue import Decision, LearningReturn, PropensityRecord, SettleStatus
    from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds
    from factorylab.kernel.timing import DistributionSummary
    from factorylab.kernel.wallet import DripSchedule, Reservation
    from factorylab.runtime.cascade import CascadeGate
    from factorylab.runtime.loop import MeasureWindow, PendingJudgement, RunStats
    from factorylab.settlement.forecast import Forecast
    from factorylab.settlement.lots import Lot, LotOrder, LotTable, Payoff, ReturnAccount
    from factorylab.settlement.standing import _Standing
    from factorylab.world.events import WorldEvent, WorldEventKind
    from factorylab.world.exchange import (
        AccountState,
        Fill,
        FundingEvent,
        FundingPayment,
        Order,
        OrderResult,
        Position,
    )
    from factorylab.world.market import SellerModel
    from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse, TokenPrice
    from factorylab.world.x402 import PaymentQuote

    classes = (
        Amendment, Charter, MetricCard, Ballot, Committee, Seat, CardRegion, _CardState,
        AssemblySpec, PopulationTool, Event, EventKind, Decision, LearningReturn, PropensityRecord,
        SettleStatus, Contract, PriceSpec, ResourceBounds, DistributionSummary, DripSchedule,
        Reservation, CascadeGate, MeasureWindow, PendingJudgement, RunStats, Forecast, Lot,
        LotOrder, LotTable, Payoff, ReturnAccount, _Standing, WorldEvent, WorldEventKind,
        AccountState, Fill, FundingEvent, FundingPayment, Order, OrderResult, Position, SellerModel,
        CatalogueEntry, ModelRequest, ModelResponse, TokenPrice, PaymentQuote,
    )
    return {cls.__name__: cls for cls in classes}


def encode(value: Any) -> Any:
    """Preserve types, mapping order, integer keys and exact numeric representations in JSON."""
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite checkpoint number")
        return {"$float": repr(value)}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, Fraction):
        return {"$fraction": str(value)}
    if isinstance(value, random.Random):
        return {"$random": encode(value.getstate())}
    if is_dataclass(value) and not isinstance(value, type):
        return {"$record": type(value).__name__,
                "fields": {f.name: encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return {"$record": type(value).__name__,
                "fields": {k: encode(v) for k, v in value._asdict().items()}}
    if isinstance(value, Mapping):
        # The ledger sorts JSON object keys. Pair lists preserve semantic insertion order.
        return {"$map": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, deque):
        return {"$deque": [encode(v) for v in value], "maxlen": value.maxlen}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"$frozen" if isinstance(value, frozenset) else "$set":
                sorted((encode(v) for v in value), key=_canonical)}
    if isinstance(value, list):
        return [encode(v) for v in value]
    raise TypeError(f"unsupported checkpoint type: {type(value).__name__}")


def decode(value: Any) -> Any:
    """Decode only known data records; ledger data cannot request imports or executable code."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "$float" in value:
        result = float(value["$float"])
        if not math.isfinite(result):
            raise ResumeError("nonfinite checkpoint number")
        return result
    if "$decimal" in value:
        return Decimal(value["$decimal"])
    if "$fraction" in value:
        return Fraction(value["$fraction"])
    if "$map" in value:
        return {decode(k): decode(v) for k, v in value["$map"]}
    if "$tuple" in value:
        return tuple(decode(v) for v in value["$tuple"])
    if "$deque" in value:
        return deque((decode(v) for v in value["$deque"]), maxlen=value["maxlen"])
    if "$set" in value:
        return {decode(v) for v in value["$set"]}
    if "$frozen" in value:
        return frozenset(decode(v) for v in value["$frozen"])
    if "$random" in value:
        rng = random.Random()
        rng.setstate(decode(value["$random"]))
        return rng
    kind = value.get("$record", value.get("$enum"))
    cls = _record_types().get(kind)
    if cls is None:
        raise ResumeError("unknown checkpoint record type")
    if "$enum" in value:
        return cls(value["value"])
    return cls(**{k: decode(v) for k, v in value["fields"].items()})


class RecoveryJournal:
    """Each replay append must match the next authenticated item before state can change."""

    def __init__(self, ledger: Ledger, clock) -> None:
        self.ledger = ledger
        self.clock = clock
        self.tail: list[dict] = []
        self.position = 0
        self.active = False
        self.bootstrap = False
        self.recovering = False
        self.failure: str | None = None

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    def peek(self) -> dict | None:
        """Return the next replay item internally, without advancing past its state change."""
        return self.tail[self.position] if self.position < len(self.tail) else None

    def append(self, entry: dict) -> int:
        """Verify historical appends in order, otherwise durably append to the existing chain."""
        if self.failure is not None:
            raise _ReplayFault(self.failure)
        if self.bootstrap:
            return 0
        expected = self.peek()
        if expected is None:
            return self.ledger.append(entry)
        actual = dict(entry)
        actual.setdefault("ts", self.clock())
        saved = {k: v for k, v in expected.items() if k not in ("seq", "prev_hash", "hash")}
        if _canonical(actual) != _canonical(saved):
            self.fail(
                f"tail diverged at seq {expected['seq']}: "
                f"expected {saved.get('kind')}, produced {actual.get('kind')}"
            )
        self.position += 1
        return expected["seq"]

    def fail(self, message: str) -> None:
        """Latch replay failure so exception cleanup cannot release holds or append new evidence."""
        self.failure = message
        raise _ReplayFault(message)

    def call(self, name: str, function, args: tuple, kwargs: dict, *, deterministic=False):
        """Recorded calls return their original result; only deterministic fakes run in replay."""
        if not self.active:
            return function(*args, **kwargs)
        # The x402 evidence callback appends ledger-only payment evidence inside complete().
        arguments = {k: v for k, v in kwargs.items() if k != "record"}
        replayed = self.peek() is not None
        ambiguous_retry = False
        fingerprint = hashlib.sha256(_canonical(encode((args, arguments)))).hexdigest()
        seq = self.append({"kind": "io.call", "name": name, "input_hash": fingerprint})
        if replayed and not deterministic:
            while (item := self.peek()) is not None and item.get("kind") != "io.result":
                if "record" not in kwargs or not item.get("kind", "").startswith("x402."):
                    self.fail(f"missing result for recorded call {name} at seq {seq}")
                self.append({k: v for k, v in item.items()
                             if k not in ("seq", "prev_hash", "hash")})
            if item is not None:
                if item.get("call") != seq:
                    self.fail(f"mismatched call result at seq {item['seq']}")
                result_entry = {k: v for k, v in item.items()
                                if k not in ("seq", "prev_hash", "hash")}
                result = decode(item["result"]) if "error" not in item else None
                self.append(result_entry)
                if "error" in item:
                    raise _recorded_error(item["error"], item.get("reason"))
                return result
            if not _read_only(name) and name != "treasury.rail.send":
                self.fail(f"unacknowledged external write {name} at seq {seq}; "
                          "refusing to submit it twice")
            ambiguous_retry = name == "treasury.rail.send"
        try:
            if self.recovering and not replayed and not deterministic and not _read_only(name):
                raise RuntimeError("interrupted event: external write was never dispatched")
            result = function(*args, **kwargs)
        except Exception as exc:
            from factorylab.world.evm import Pending, RailError

            failure = exc
            if ambiguous_retry:
                # A used-nonce rejection after a lost acknowledgement cannot prove failure.
                failure = Pending("replayed submission requires receipt reconciliation")

            # Client exceptions can contain credentials. Preserve only a safe exception class.
            error = type(failure).__name__
            # RailError messages are locally generated bounded reasons, never provider bodies.
            reason = str(failure) if isinstance(failure, RailError) else None
            self.append({"kind": "io.result", "call": seq, "error": error,
                         **({"reason": reason} if reason is not None else {})})
            raise _recorded_error(error, reason) from None
        self.append({"kind": "io.result", "call": seq, "result": encode(result)})
        return result


def _read_only(name: str) -> bool:
    if name == "treasury.provider_pots" or (
        name.startswith("treasury.rail.")
        and name.rsplit(".", 1)[-1] in ("balances", "preflight", "plan", "prepare", "poll")
    ):
        return True
    return name.rsplit(".", 1)[-1] in (
        "mids", "account", "funding", "fills", "candles", "order_book", "funding_history",
        "open_orders", "balance_micro", "affordable", "catalogue", "discover", "quote",
        "registration_price", "seller_models", "funding_payments",
    )


def _recorded_error(name: str, reason: str | None = None) -> Exception:
    from factorylab.world.evm import Pending, RailError
    from factorylab.world.market import PaymentOutcomeUnknown
    from factorylab.world.x402 import InsufficientReserve, X402Error

    classes = (ValueError, TypeError, KeyError, RuntimeError, PermissionError,
               InsufficientReserve, X402Error, PaymentOutcomeUnknown)
    cls = next((c for c in classes if c.__name__ == name), RuntimeError)
    if name in ("RailError", "Pending"):
        return (Pending if name == "Pending" else RailError)(reason or "treasury rail unavailable")
    return cls(f"external call failed ({name})")


class JournalProxy:
    """External services retain their public interface while calls acquire durable responses."""

    def __init__(self, target, journal: RecoveryJournal, name: str, *, deterministic=False):
        self.target, self.journal, self._journal_name = target, journal, name
        self.deterministic = deterministic

    def __getattr__(self, name):
        attr = getattr(self.target, name)
        if not callable(attr) or name.startswith("_"):
            return attr
        # Registration changes only a local price cache, reconstructed from saved prices.
        if self._journal_name == "market" and name == "register":
            return attr

        def call(*args, **kwargs):
            return self.journal.call(f"{self._journal_name}.{name}", attr, args, kwargs,
                                     deterministic=self.deterministic)

        return call

    def __setattr__(self, name, value):
        if name in ("target", "journal", "_journal_name", "deterministic"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.target, name, value)

    def __delattr__(self, name):
        if name in ("target", "journal", "_journal_name", "deterministic"):
            object.__delattr__(self, name)
        else:
            delattr(self.target, name)


# Explicit schemas keep SDK clients, keys, bound callbacks and dependencies out of snapshots.
_RUNTIME_FIELDS = (
    "rng", "cascade", "cascade_windows", "stats", "charter", "pending_exposure",
    "delivered_seen", "snapshot_keys", "recent_mids", "realized_to_date", "fees_to_date",
    "funding_to_date", "memory", "handle_to_assembly", "tool_specs", "population_tools",
    "tool_owner", "pending_votes", "regions", "priced", "rolling", "unparsed_logged", "window",
    "pending", "balance_at", "events_log", "last_closure_ns", "reserve_window_start", "internal",
    "n", "emitted", "insolvency_count", "_compute_routed", "_compute_unaffordable",
    "world_consumed", "ticks_consumed", "drips_consumed", "started", "catalogue", "sellers",
    "registration_feedback", "tool_jail_available",
)
_KERNEL_FIELDS = ("wallet", "queue", "registry", "reserve", "timing", "buffer")
_COMPONENT_FIELDS = (
    ("book", "_ForecastBook__", ("forecasts", "settled", "requested")),
    ("baseline", "_PrevalenceBaseline__", ("counts",)),
    ("cadence", "_", ("latencies", "last_activation_ns", "waiting", "deferred")),
    ("standing", "_ConsequenceStanding__", ("min_coverage", "evaluators")),
    ("charter_book", "_CharterBook__", (
        "editions", "proposals", "committees", "ballots", "activated", "activations",
    )),
    ("controller", "_PriceController__", (
        "eta", "kappa", "decay", "lambda_max", "min_window_events", "cards",
    )),
    ("consequences", "", ("backstop", "table", "mids")),
    ("consequence_fills", "", ("since_ns", "seen")),
    ("reconciler", "", ("every", "_ticks")),
)


def runtime_state(rt) -> dict:
    """Retain learning, FIFO lots, private memory and exact source cursors in one checkpoint."""
    runtime = {name: getattr(rt, name) for name in _RUNTIME_FIELDS}
    runtime["amendment_feedback"] = getattr(rt, "amendment_feedback", None)
    components = {
        name: {field: getattr(getattr(rt, name), prefix + field) for field in names}
        for name, prefix, names in _COMPONENT_FIELDS
    }
    return {
        "format": 1, "manifest_hash": rt.m.manifest_hash(),
        "config": {
            "events": rt.events_budget, "seed": rt.seed, "initial_balance_micro": rt.initial,
            "drip": rt.use_drip, "router_gamma": rt.router_gamma, "kill_at_end": rt.kill_at_end,
        },
        "adapters": {name: {"name": getattr(getattr(rt, name).target, "name", name),
                            "deterministic": getattr(rt, name).deterministic}
                     for name in ("exchange", "provider")},
        "runtime": encode(runtime), "clock_ns": rt.clock.now_ns,
        "tick_clock": rt.tick_clock.state(),
        "kernel": {name: encode(getattr(rt, name).state()) for name in _KERNEL_FIELDS},
        "components": encode(components),
        "treasury": encode(rt.treasury.snapshot()),
        "assemblies": encode([{"spec": a.spec, "memory": a.memory}
                              for a in rt.assemblies.values()]),
        "prices": encode(rt.prices.prices),
        "routers": [st.state() for st in rt._all_router_states()],
        "venue": encode({"last_fill_ns": rt.venue.last_fill_ns,
                         "seen_fills": rt.venue.seen_fills,
                         "last_funding_ns": rt.venue.last_funding_ns,
                         "seen_funding": rt.venue.seen_funding}) if rt.venue else None,
        "venue_tool_log": encode(rt.venue_tools.log) if rt.venue_tools else None,
        "fake_exchange": encode(vars(rt.exchange.target)) if rt.exchange.deterministic else None,
        "fake_provider": encode(vars(rt.provider.target)) if rt.provider.deterministic else None,
    }


def restore_runtime(rt, state: dict) -> None:
    """Restore only authenticated matching-format state, rebinding dependencies to this process."""
    from factorylab.runtime.live import LiveClock
    from factorylab.runtime.loop import RouterState
    from factorylab.world.clock import ClockSource

    if state.get("format") != 1 or state["manifest_hash"] != rt.m.manifest_hash():
        raise ResumeError("snapshot format or manifest hash differs")
    for name, saved in state["adapters"].items():
        current = getattr(rt, name)
        if (saved["name"] != getattr(current.target, "name", name)
                or saved["deterministic"] != current.deterministic):
            raise ResumeError(f"{name} adapter differs from the saved world")
    for name, value in decode(state["runtime"]).items():
        setattr(rt, name, value)
    rt.clock.now_ns = state["clock_ns"]
    saved_clock = state["tick_clock"]
    if "start_ns" in saved_clock:
        rt.tick_clock = ClockSource.restore(saved_clock)
    else:
        callbacks = ({"now_ns": rt.tick_clock.now_ns, "sleep": rt.tick_clock.sleep}
                     if isinstance(rt.tick_clock, LiveClock) else {})
        rt.tick_clock = LiveClock.restore(saved_clock, **callbacks)
    if rt.clock_source is not None:
        rt.clock_source = rt.tick_clock
    for name in _KERNEL_FIELDS:
        getattr(rt, name)._restore_state(decode(state["kernel"][name]))
    rt.treasury.restore(decode(state["treasury"]))
    components = decode(state["components"])
    for name, prefix, names in _COMPONENT_FIELDS:
        for field in names:
            if name == "controller" and field == "kappa" and field not in components[name]:
                # Older checkpoints inherited this immutable parameter from the same manifest.
                continue
            setattr(getattr(rt, name), prefix + field, components[name][field])
    rt.prices.prices = decode(state["prices"])
    rt.assemblies.clear()
    for assembly in decode(state["assemblies"]):
        restored = rt._instantiate(assembly["spec"])
        restored.memory = assembly["memory"]
    rt.routers.clear()
    for saved in state["routers"]:
        router = RouterState.restore(saved)
        rt.routers.setdefault(router.kind, []).append(router)
    if rt.venue and state["venue"] is not None:
        for name, value in decode(state["venue"]).items():
            setattr(rt.venue, name, value)
    if rt.venue_tools and state["venue_tool_log"] is not None:
        rt.venue_tools.log = decode(state["venue_tool_log"])
    for name, component in (("fake_exchange", rt.exchange), ("fake_provider", rt.provider)):
        if state[name] is not None:
            if not component.deterministic:
                raise ResumeError(f"{name} requires the original deterministic adapter")
            component.target.__dict__.clear()
            component.target.__dict__.update(decode(state[name]))
    for model_id in rt.sellers:
        rt.market.register(model_id, rt.prices.price(model_id).per_request_micro)


def resume_runtime(manifest, ledger_path: str, *, provider=None, market=None, exchange=None,
                   clock_source=None, now_ns=None, _lock=None):
    """Hold exclusive ownership before reading recovery evidence or contacting a provider."""
    lock = _lock or LedgerLock(ledger_path)
    try:
        return _resume_runtime(manifest, ledger_path, provider=provider, market=market,
                               exchange=exchange, clock_source=clock_source, now_ns=now_ns,
                               lock=lock)
    except BaseException:
        lock.close()
        raise


def _resume_runtime(manifest, ledger_path, *, provider, market, exchange, clock_source,
                    now_ns, lock):
    """Authenticate, restore, replay and reconcile before admitting another world event."""
    from factorylab.runtime.loop import Runtime, SimClock

    clock = SimClock()
    ledger = Ledger.reopen(
        ledger_path, manifest=json.loads(manifest.canonical_json()), clock_ns=clock,
    )
    items = ledger._recovery_items()
    snapshot = next((i for i in reversed(items) if i.get("kind") == "snapshot"), None)
    if snapshot is None:
        raise ResumeError("ledger has no recoverable snapshot")
    state = snapshot["state"]
    if state.get("manifest_hash") != manifest.manifest_hash():
        raise ResumeError("snapshot manifest hash differs")
    journal = RecoveryJournal(ledger, clock)
    journal.bootstrap = True
    rt = Runtime(manifest, **state["config"], ledger_path=None, provider=provider, market=market,
                 exchange=exchange, clock_source=clock_source, _journal=journal, _lock=lock)
    restore_runtime(rt, state)
    journal.bootstrap = False
    journal.active = journal.recovering = True
    journal.tail = [item for item in items[snapshot["seq"] + 1:]
                    if item.get("kind") != "ledger.repaired"]
    try:
        while (item := journal.peek()) is not None:
            if item["kind"] == "runtime.input":
                if not rt._process_event(rt._next_event(iter(()))):
                    return rt
            elif item["kind"] == "resume.begin":
                rt._resume_at(item["now_ns"])
            else:
                raise _ReplayFault(f"unexpected tail item {item['kind']} at seq {item['seq']}")
        journal.tail = []
        journal.position = 0
        journal.recovering = False
        resume_time = (time.time_ns() if now_ns is None else now_ns) if rt.live else rt.clock.now_ns
        rt._resume_at(resume_time)
    except _ReplayFault as exc:
        raise ResumeError(str(exc)) from None
    return rt


def resume_world(manifest, ledger_path: str, **kwargs) -> dict:
    """Continue the saved event budget and manifest, returning the ordinary final summary."""
    return resume_runtime(manifest, ledger_path, **kwargs).run()
