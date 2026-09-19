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
from pathlib import Path
from typing import Any

from factorylab.kernel.ledger import Ledger, LedgerLock, canonical
from factorylab.runtime.reasons import CredentialMissing, Reason
from factorylab.world.exchange import bind_launch_nonce


class ResumeError(RuntimeError):
    """Recovery refuses invalid evidence or an ambiguous external side effect."""

    def __init__(self, message: str, *, code: str = "invalid_snapshot", **details: Any) -> None:
        super().__init__(message)
        self.code = code
        # Bounded facts the refusal ledgers beside its reason (a sha, an owner): never
        # free text, never anything read from outside the world's own records.
        self.details = details


def resume_reason(exc: Exception) -> Reason:
    """Translate internal failures to bounded, non-secret operational diagnostics."""
    from factorylab.kernel.ledger import GenesisMismatchError, LedgerIntegrityError

    if isinstance(exc, ResumeError):
        try:
            return Reason(exc.code)
        except ValueError:
            return Reason.INVALID_SNAPSHOT
    if isinstance(exc, CredentialMissing):
        return Reason.CREDENTIAL_MISSING
    if isinstance(exc, GenesisMismatchError):
        return Reason.MANIFEST_MISMATCH
    if isinstance(exc, LedgerIntegrityError):
        return Reason.LEDGER_INTEGRITY
    if isinstance(exc, (FileNotFoundError, ValueError)):
        return Reason.MANIFEST_UNAVAILABLE
    return Reason.ADAPTER_UNAVAILABLE


class _ReplayFault(BaseException):
    """Replay faults cannot be mistaken for provider failures by ordinary runtime handlers."""


def _record_types() -> dict[str, type]:
    from factorylab.charter.amendment import Amendment, PredictedEffect
    from factorylab.charter.charter import Charter, MetricCard
    from factorylab.charter.committee import Ballot, Committee, Seat
    from factorylab.charter.controller import CardRegion, _CardState
    from factorylab.charter.measurement import CardSamples
    from factorylab.charter.windows import MetricWindow
    from factorylab.cortex.assembly import AssemblySpec, ProgramAssemblySpec
    from factorylab.cortex.tools import PopulationTool
    from factorylab.kernel.events import Event, EventKind
    from factorylab.kernel.queue import Decision, LearningReturn, PropensityRecord, SettleStatus
    from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds
    from factorylab.kernel.timing import DistributionSummary
    from factorylab.kernel.wallet import DripSchedule, ReleaseSchedule, Reservation
    from factorylab.runtime.cascade import CascadeGate
    from factorylab.runtime.feedback import PendingJudgement
    from factorylab.runtime.governance import Retirement, WorkAssemblySpec
    from factorylab.runtime.grounded import GroundedContract
    from factorylab.runtime.pricing import MeasureWindow
    from factorylab.runtime.routing import PopulationEvent
    from factorylab.runtime.summary import RunStats
    from factorylab.settlement.fidelity import FidelityObjection
    from factorylab.settlement.forecast import Forecast
    from factorylab.settlement.lots import Lot, LotOrder, LotTable, Payoff, ReturnAccount
    from factorylab.settlement.receipts import (
        Adjudication,
        Commitment,
        ExecutionReceipt,
        LearningReceipt,
    )
    from factorylab.settlement.settle import PredicateForecast
    from factorylab.settlement.standing import _Standing
    from factorylab.settlement.vocabulary import Predicate
    from factorylab.world.events import WorldEvent, WorldEventKind
    from factorylab.world.exchange import (
        AccountState,
        Fill,
        FundingEvent,
        FundingPayment,
        Order,
        OrderResult,
        Position,
        SpotBalance,
    )
    from factorylab.world.market import SellerModel
    from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse, TokenPrice
    from factorylab.world.x402 import PaymentQuote

    classes = (
        Amendment, PredictedEffect, Charter, MetricCard, MetricWindow, CardSamples,
        Ballot, Committee, Seat, CardRegion, _CardState,
        AssemblySpec, WorkAssemblySpec, ProgramAssemblySpec, Predicate, PredicateForecast,
        PopulationTool, Event, PopulationEvent, EventKind, Decision,
        LearningReturn, PropensityRecord,
        SettleStatus, Contract, PriceSpec, ResourceBounds, DistributionSummary, DripSchedule,
        ReleaseSchedule,
        Reservation, Retirement, CascadeGate, MeasureWindow, PendingJudgement, RunStats, Forecast,
        GroundedContract,
        Lot,
        LotOrder, LotTable, Payoff, ReturnAccount, _Standing, WorldEvent, WorldEventKind,
        AccountState, Fill, FundingEvent, FundingPayment, Order, OrderResult, Position,
        SpotBalance, SellerModel, FidelityObjection,
        Adjudication, Commitment, ExecutionReceipt, LearningReceipt,
        CatalogueEntry, ModelRequest, ModelResponse, TokenPrice, PaymentQuote,
    )
    return {cls.__name__: cls for cls in classes}


def encode(value: Any) -> Any:
    """Preserve types, mapping order, integer keys and exact numeric representations in JSON."""
    if isinstance(value, Enum):
        return {"$enum": type(value).__name__, "value": value.value}
    if type(value) is int and value.bit_length() > 12000:
        return {"$int": hex(value)}
    if isinstance(value, str):
        # A charter norm is its name and carries the definition its edition ratified
        # (C3). Without a definition it checkpoints as the plain text it always was,
        # so every checkpoint written before definitions existed is byte-identical.
        definition = getattr(value, "definition", "")
        return {"$norm": [str(value), definition]} if definition else str(value)
    if value is None or type(value) in (int, bool):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite checkpoint number")
        return {"$float": repr(value)}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite checkpoint number")
        return {"$decimal": str(value)}
    if isinstance(value, Fraction):
        if max(value.numerator.bit_length(), value.denominator.bit_length()) > 12000:
            return {"$rational": [encode(value.numerator), encode(value.denominator)]}
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
                sorted((encode(v) for v in value), key=canonical)}
    if isinstance(value, list):
        return [encode(v) for v in value]
    raise TypeError(f"unsupported checkpoint type: {type(value).__name__}")


def decode(value: Any) -> Any:
    """Decode only known data records; ledger data cannot request imports or executable code."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        if type(value) is float and not math.isfinite(value):
            raise ResumeError("nonfinite checkpoint number")
        return value
    if "$float" in value:
        result = float(value["$float"])
        if not math.isfinite(result):
            raise ResumeError("nonfinite checkpoint number")
        return result
    if "$decimal" in value:
        result = Decimal(value["$decimal"])
        if not result.is_finite():
            raise ResumeError("nonfinite checkpoint number")
        return result
    if "$fraction" in value:
        return Fraction(value["$fraction"])
    if "$int" in value:
        return int(value["$int"], 16)
    if "$rational" in value:
        numerator, denominator = map(decode, value["$rational"])
        return Fraction(numerator, denominator)
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
    if "$norm" in value:
        from factorylab.charter.charter import Norm

        name, definition = value["$norm"]
        return Norm(name, definition)
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
        self.tail = iter(())
        self.position = 0
        self.active = False
        self.bootstrap = False
        self.recovering = False
        self.failure: str | None = None
        self.connector_bodies: list[str] = []  # transient, never checkpointed
        # How many calls that may change an external answer have been made through
        # this journal, per adapter (the name before the first dot: ``exchange``,
        # ``treasury``, ``provider``...), counting every call ``_read_only`` does not
        # name. A view held above the recorded-I/O layer keys on the adapters its
        # answer depends on, so any write to them in between makes the next read go
        # out again. Transient, never checkpointed: memos keyed on it are dropped at
        # every checkpoint, so only differences within one continuation count.
        self.writes: dict[str, int] = {}

    def __getattr__(self, name):
        return getattr(self.ledger, name)

    @property
    def tail(self):
        return self._tail

    @tail.setter
    def tail(self, items):
        self._tail = iter(items)
        self._next = None

    def peek(self) -> dict | None:
        """Return the next replay item internally, without advancing past its state change."""
        if self._next is None:
            self._next = next(self._tail, None)
        return self._next

    def protect_connector_body(self, body: str) -> None:
        """Body copies and JSON-escaped copies stay out of subsequent durable surfaces."""
        if body:
            self.connector_bodies.append(body)

    def without_connector_bodies(self, value):
        """Return a detached redacted value; fetched bytes are never recovery material."""
        if not self.connector_bodies:
            return value
        if isinstance(value, str):
            for body in self.connector_bodies:
                variants = [body]
                for _ in range(3):
                    variants.append(json.dumps(variants[-1], ensure_ascii=True)[1:-1])
                for variant in sorted(set(variants), key=len, reverse=True):
                    value = value.replace(variant, "[connector body omitted]")
            return value
        if isinstance(value, dict):
            return {k: self.without_connector_bodies(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.without_connector_bodies(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.without_connector_bodies(v) for v in value)
        return value

    def append(self, entry: dict) -> int:
        """Verify historical appends in order, otherwise durably append to the existing chain."""
        if self.failure is not None:
            raise _ReplayFault(self.failure)
        if self.bootstrap:
            return 0
        # Redact content surfaces, never routing ids, paths or financial metadata:
        # a hostile body such as "/" cannot rewrite the meaning of a ledger item.
        # The recovery plane is exempt: io.call/io.result is how a read is replayed.
        if self.connector_bodies and entry.get("kind") not in ("io.call", "io.result"):
            entry = {key: self.without_connector_bodies(value)
                     if key in {"result", "args", "inputs", "outputs"} else value
                     for key, value in entry.items()}
        expected = self.peek()
        if expected is None:
            return self.ledger.append(entry)
        actual = dict(entry)
        actual.setdefault("ts", self.clock())
        saved = {k: v for k, v in expected.items() if k not in ("seq", "prev_hash", "hash")}
        if canonical(actual) != canonical(saved):
            self.fail(
                f"tail diverged at seq {expected['seq']}: "
                f"expected {saved.get('kind')}, produced {actual.get('kind')}"
            )
        self.position += 1
        self._next = None
        return expected["seq"]

    def fail(self, message: str) -> None:
        """Latch replay failure so exception cleanup cannot release holds or append new evidence."""
        self.failure = message
        raise _ReplayFault(message)

    def call(self, name: str, function, args: tuple, kwargs: dict, *, deterministic=False):
        """Recorded calls return their original result; only deterministic fakes run in replay."""
        if not _read_only(name):
            adapter = name.split(".", 1)[0]
            self.writes[adapter] = self.writes.get(adapter, 0) + 1
        if not self.active:
            return function(*args, **kwargs)
        # The x402 evidence callback appends ledger-only payment evidence inside complete().
        arguments = {k: v for k, v in kwargs.items() if k != "record"}
        replayed = self.peek() is not None
        ambiguous_retry = False
        fingerprint = hashlib.sha256(canonical(encode((args, arguments)))).hexdigest()
        seq = self.append({"kind": "io.call", "name": name, "input_hash": fingerprint})
        if replayed and not deterministic:
            payment_submitted = False
            while (item := self.peek()) is not None and item.get("kind") != "io.result":
                if "record" not in kwargs or not item.get("kind", "").startswith("x402."):
                    self.fail(f"missing result for recorded call {name} at seq {seq}")
                payment_submitted |= item.get("kind") == "x402.submitted"
                kwargs["record"]({k: v for k, v in item.items()
                                  if k not in ("seq", "prev_hash", "hash")})
            if item is not None:
                if item.get("call") != seq:
                    self.fail(f"mismatched call result at seq {item['seq']}")
                result_entry = {k: v for k, v in item.items()
                                if k not in ("seq", "prev_hash", "hash")}
                result = decode(item["result"]) if "error" not in item else None
                self.append(result_entry)
                if "error" in item:
                    raise _recorded_error(item["error"], item.get("reason"),
                                          status=item.get("status"),
                                          unbilled=item.get("unbilled", False),
                                          carry=item.get("carry"))
                return result
            if name in ("exchange.place", "exchange.close", "exchange.cancel"):
                from factorylab.world.exchange import OrderResult

                # Complete the interrupted journal call with uncertainty, then let
                # the normal intent owner query the venue using its persisted identity.
                result = ({"status": "uncertain"} if name == "exchange.cancel" else
                          OrderResult(None, "uncertain", Decimal(0), None))
                self.append({"kind": "io.result", "call": seq, "result": encode(result)})
                return result
            if name in ("market.complete", "connector.paid_fetch"):
                error = "PaymentOutcomeUnknown" if payment_submitted else "UnbilledFailure"
                self.append({"kind": "io.result", "call": seq, "error": error})
                raise _recorded_error(error)
            if name == "provider.complete":
                self.append({"kind": "io.result", "call": seq, "error": "RuntimeError"})
                raise _recorded_error("RuntimeError")
            if not _read_only(name) and name != "treasury.rail.send":
                self.fail(f"unacknowledged external write {name} at seq {seq}; "
                          "refusing to submit it twice")
            ambiguous_retry = name == "treasury.rail.send"
        try:
            if self.recovering and not replayed and not deterministic and not _read_only(name):
                from factorylab.world.metering import UnbilledFailure

                raise UnbilledFailure("interrupted event: external write was never dispatched")
            result = function(*args, **kwargs)
            encoded_result = encode(result)
        except Exception as exc:
            from factorylab.world.evm import Pending, RailError
            from factorylab.world.metering import UnbilledFailure, classify_provider_failure
            from factorylab.world.openrouter import OpenRouterError
            from factorylab.world.venice import VeniceError

            failure = exc
            if ambiguous_retry:
                # A used-nonce rejection after a lost acknowledgement cannot prove failure.
                failure = Pending("replayed submission requires receipt reconciliation")

            # Client exceptions can contain credentials. Preserve only a safe exception class.
            error = type(failure).__name__
            # RailError messages are locally generated bounded reasons, never provider bodies.
            reason = str(failure) if isinstance(failure, RailError) else None
            # A rail's pending carry (plain data such as a scan cursor) is part of the
            # recorded outcome, so the treasury persists the same cursor on replay.
            carry = getattr(failure, "carry", None) if isinstance(failure, Pending) else None
            billing = {}
            if isinstance(failure, (OpenRouterError, VeniceError)):
                status = failure.status if type(failure.status) is int else None
                billing = {"status": status,
                           "unbilled": isinstance(classify_provider_failure(failure),
                                                  UnbilledFailure)}
            self.append({"kind": "io.result", "call": seq, "error": error,
                         **({"reason": reason} if reason is not None else {}),
                         **({"carry": carry} if carry is not None else {}), **billing})
            raise _recorded_error(error, reason, carry=carry, **billing) from None
        self.append({"kind": "io.result", "call": seq, "result": encoded_result})
        return result


def _read_only(name: str) -> bool:
    if name in ("sandbox.run", "observation.run", "predicate.run", "note.read"):
        return True
    if name == "treasury.provider_pots" or (
        name.startswith("treasury.rail.")
        and name.rsplit(".", 1)[-1] in ("balances", "preflight", "plan", "prepare", "poll",
                                         "gas_view")
    ):
        return True
    return name.rsplit(".", 1)[-1] in (
        "mids", "account", "funding", "fills", "candles", "order_book", "funding_history",
        "open_orders", "balance_micro", "balance_of", "affordable", "catalogue", "discover",
        "quote", "fetch",
        "registration_price", "seller_models", "funding_payments", "lookup",
        "reserve_balance", "discover_index", "instruments",
    )


def _recorded_error(name: str, reason: str | None = None, *,
                    status: int | None = None, unbilled: bool = False,
                    carry: dict | None = None) -> Exception:
    from factorylab.world.evm import Pending, RailError
    from factorylab.world.exchange import VenueUnavailable
    from factorylab.world.market import PaymentOutcomeUnknown
    from factorylab.world.metering import UnbilledFailure
    from factorylab.world.openrouter import OpenRouterError
    from factorylab.world.venice import VeniceError
    from factorylab.world.x402 import InsufficientReserve, X402Error

    classes = (VenueUnavailable, UnbilledFailure, ConnectionError, TimeoutError, OSError,
               ValueError, TypeError, KeyError, RuntimeError, PermissionError,
               InsufficientReserve, X402Error, PaymentOutcomeUnknown, OpenRouterError, VeniceError)
    cls = next((c for c in classes if c.__name__ == name), RuntimeError)
    if cls in (OpenRouterError, VeniceError):
        if unbilled:
            from factorylab.world import metering

            cls = metering.OpenRouterError if cls is OpenRouterError else metering.VeniceError
        return cls(status, "Provider request failed")
    if name == "Pending":
        return Pending(reason or "treasury rail unavailable", carry=carry)
    if name == "RailError":
        return RailError(reason or "treasury rail unavailable")
    return cls(f"external call failed ({name})")


class JournalProxy:
    """External services retain their public interface while calls acquire durable responses."""

    def __init__(self, target, journal: RecoveryJournal, name: str, *, deterministic=False):
        self.target, self.journal, self._journal_name = target, journal, name
        self.deterministic = deterministic
        self.call_metrics = {}

    def __getattr__(self, name):
        attr = getattr(self.target, name)
        if not callable(attr) or name.startswith("_"):
            return attr
        # Registration changes only a local price cache, reconstructed from saved prices.
        if self._journal_name == "market" and name == "register":
            return attr

        def call(*args, **kwargs):
            started = time.monotonic_ns()
            try:
                return self.journal.call(f"{self._journal_name}.{name}", attr, args, kwargs,
                                         deterministic=self.deterministic)
            finally:
                if not self.deterministic and not self.journal.recovering:
                    metric = self.call_metrics.setdefault(name, {"calls": 0, "elapsed_ns": 0})
                    metric["calls"] += 1
                    metric["elapsed_ns"] += time.monotonic_ns() - started

        return call

    def __setattr__(self, name, value):
        if name in ("target", "journal", "_journal_name", "deterministic", "call_metrics"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.target, name, value)

    def __delattr__(self, name):
        if name in ("target", "journal", "_journal_name", "deterministic", "call_metrics"):
            object.__delattr__(self, name)
        else:
            delattr(self.target, name)


# Explicit schemas keep SDK clients, keys, bound callbacks and dependencies out of snapshots.
_RUNTIME_FIELDS = (
    "rng", "cascade", "cascade_windows", "stats", "charter", "pending_exposure",
    "delivered_seen", "snapshot_keys", "recent_mids", "realized_to_date", "fees_to_date",
    "funding_to_date", "spot_inventory", "handle_to_assembly", "tool_specs",
    "population_tools",
    "tool_owner", "pending_votes", "regions", "priced", "rolling", "unparsed_logged", "window",
    "pending", "balance_at", "events_log", "last_closure_ns", "reserve_window_start", "internal",
    "n", "emitted", "insolvency_count", "_compute_routed", "_compute_unaffordable",
    "world_consumed", "ticks_consumed", "drips_consumed", "started", "catalogue", "sellers",
    "registration_feedback", "tool_jail_available", "vote_handles", "voted_amendments",
    "order_intents", "market_index", "unresolved_x402",
    "exposure_evidence", "pending_meta", "verdict_outcomes", "consequence_mix",
    # Verdict commitments already closed out and already graded, by judge handle: a
    # restored runtime never re-opens, re-closes or re-grades one it finished.
    "verdicts_closed_out", "verdicts_graded",
    "sampling_history", "novelty_grant",
    "card_samples", "price_windows", "price_origins",
    "retired_assemblies", "retirement_proposals", "return_kinds", "decision_subjects",
    "event_schemas",
    # Metric challenges: frozen incumbent and replacement cards, their trial series and status.
    "challenges",
    "return_bindings",
    "return_events",
    # The population's registered measurements and its open assembly-learner rounds.
    "registered_observations", "assembly_rounds",
    # The x402 facilitator the world launched under (second reading, facilitator pin):
    # ledgered in Launch, compared on restore, read by the seller from the ledger.
    "facilitator_url",
    "registered_predicates", "kind_reward_shapes", "forecast_returns",
    "connector_calls", "connector_calls_day",
    "notes",
    # The pause between releases: None while awake, else the entry record (C2).
    "dormancy",
    # C10: each seat's last rendered call ceiling and the world size it was priced at.
    "seat_ceilings",
    # The per-launch venue identity: a resumed world keeps the client order IDs
    # it already submitted, and a fresh ledger can never reproduce them.
    "launch_nonce",
    # The release that launched the world; restore refuses a different one (C4).
    "release_digest",
    # edition 3, R3-C
    # The death witness this world launched under: whether a receiver was configured
    # and which one (the hash of its URL). Restore refuses an environment with no
    # receiver (``witness_required``) or a different one (``witness_mismatch``), so
    # the veto belongs to the launched identity and not to a mutable variable.
    "witness_required", "witness_receiver",
    # edition 3, C2
    # Thinking control: every seat's subscription, its sleep, the world it has not
    # read yet and each watcher's last observation, as one block of plain data
    # (``ThinkingMixin.subscriptions``; assignment restores the book in place).
    "subscriptions",
    # edition 3, R3-F
    # Attention and continuity: how far each seat's outcome inbox was actually
    # delivered (an ack can never reach past it) and the ``said`` records evicted
    # under MAX_SAID into the archive. The fold's offered/delivered/acknowledged
    # states ride inside ``subscriptions`` above, where the fold itself lives.
    "inbox_delivery",
    # Venue effects by custody, per decision, until its outcome settles: the
    # consequence line reports them beside provider cost (edition 3, C5).
    "venue_deltas",
    # When each judge's metas began waiting on a fact about it, and the
    # adjudication queued for each seat while it is unanswered. Both are
    # properties over a private dict (``FeedbackMixin``); ``_RUNTIME_BACKING``
    # names the attribute a restore assigns.
    "meta_waiting_since", "open_adjudications",
)
# Runtime fields read through a property with no setter, and the attribute behind it.
_RUNTIME_BACKING = {
    "meta_waiting_since": "_meta_waiting_since",
    "open_adjudications": "_open_adjudications",
    "grounded_pending": "_grounded_pending",
    "grounded_closed": "_grounded_closed",
}
# The settlement receipt books, by the path from the runtime to each. A receipt's
# id is its content address, so a book is saved as its receipts in record order
# and rebuilt as ``{receipt.id: receipt}``: the same ids, the same order, and an
# open adjudication resolved later stays under the id of the claim.
_RECEIPT_BOOKS = ("book.receipts", "consequences.receipts")

# State a runtime carries across events that the checkpoint deliberately does not
# save, by ``Class.attr`` (or a whole ``Class``), with the reason.
# ``tests/runtime/test_checkpoint_coverage.py`` restores a checkpoint at many
# points of a run and fails on any attribute that differs from the running world
# and is not named here, so a new field is either checkpointed or declared.
#
# Derived: rebuilt on demand from checkpointed state, or a read held for the tick
# that made it; a restored runtime rebuilds it or reads afresh.
_DERIVED_STATE = {
    "Runtime._instruments_memo": "the venue's instrument listing, held for the tick that read it",
    "Runtime._mids_memo": "the venue's mid prices, held for the tick that read them",
    "Runtime._account_memo": "the venue account read, held for the tick that read it",
    "Runtime._prefix_memo": "the rendered cacheable prompt prefix, keyed on what it renders",
    "Runtime._world_chars_cache": "the world block's size, keyed on the event that measured it",
    "Runtime._artifact_listing_view": "the artifact listing, rebuilt from the archive index",
    "ArtifactStore._changed": "hashes changed since the listing last drained; a restore "
                              "replaces the index and every view is rebuilt from scratch",
    "ArtifactStore.generation": "a change counter for views over the index, bumped on restore",
    "ArtifactStore.epoch": "a rebuild counter for views over the index, bumped on restore",
    "ForecastBook._ForecastBook__open_cache": "the unsettled handles, rebuilt from the "
                                              "forecast map and settled set it names",
    "ReceiptBook._ReceiptBook__execution_ids": "derived global execution-receipt cursor",
    "ReceiptBook._ReceiptBook__execution_by_handle": "derived per-handle execution index",
    "FakeTreasury._balances_memo": "the scripted rail's balances, keyed on what they read",
}
# Transient: belongs to this process or this file, not to the world.
_TRANSIENT_STATE = {
    "RecoveryJournal": "the diary itself and this process's replay cursor over it: the "
                       "checkpoint is an item in the diary, not a copy of it",
    "LedgerLock": "this process's exclusive hold on the diary file",
    "Runtime.diary_id": "bound by the restore to the diary the checkpoint came from",
    "ArtifactStore.root": "where this process finds the archive's bytes beside the ledger",
    "JournalProxy.call_metrics": "this process's wall-clock timing of its own adapter calls",
}
# Unordered: mappings a checkpoint saves in sorted order because nothing reads
# their order (lookups and order-free reductions only).
_UNORDERED_STATE = {
    "SubscriptionBook.folds": "per-seat folds, read by seat and reduced with min()",
    "SubscriptionBook.last_wake": "per-seat last wake tick, read by seat",
    "OutcomeInbox.delivered_through": "per-seat delivery cursor, read by seat",
}
_KERNEL_FIELDS = ("wallet", "queue", "registry", "reserve", "timing", "buffer")
_COMPONENT_FIELDS = (
    ("book", "_ForecastBook__", ("forecasts", "settled", "requested")),
    ("baseline", "_PrevalenceBaseline__", ("counts",)),
    ("cadence", "_", ("latencies", "last_activation_ns", "waiting", "deferred",
                       "current_event", "last_activation_event", "outstanding", "min_support")),
    ("standing", "_ConsequenceStanding__", ("min_coverage", "evaluators")),
    # ``objections``: the accepted fidelity objection each judge's return carried,
    # until its verdict settles and pairs with it.
    ("settler", "_Settler__", ("snapshots", "recorded", "objections")),
    ("charter_book", "_CharterBook__", (
        "editions", "proposals", "committees", "ballots", "activated", "activations",
        "bindings",
    )),
    ("controller", "_PriceController__", (
        "eta", "kappa", "decay", "lambda_max", "min_window_events", "cards",
    )),
    ("consequences", "", ("backstop", "table", "mids", "pending_orders", "deferred_events",
                          # R4-C: a released hold's exposure, and the censored
                          # outcomes not yet handed to the runtime.
                          "unresolved_orders", "censored_payoffs")),
    ("consequence_fills", "", ("since_ns", "seen")),
    ("reconciler", "", ("every", "_ticks")),
    # The artifact archive's index (C9): hash -> owner, kind, size, time, published.
    # The bytes stay beside the ledger and are found again by hash.
    ("artifacts", "", ("index",)),
    # Continuity (C1): the head pointer each seat holds and the inbox indexes and
    # read cursors addressed to it. Both name artifacts; the bodies are in the
    # archive and ``_verify_artifacts`` proves they are still there before the
    # world continues, so a seat never resumes into a state or an outcome it
    # cannot be shown.
    ("working_state", "", ("heads",)),
    ("outcomes", "", ("items", "cursors", "said", "seq")),
    # The bill settlement's reference: the last provider balance read per namespace and
    # what was booked through it since, so a resumed world settles against the same read.
    ("bill_settlement", "", ("reference",)),
)


def _resolve(rt, path: str):
    """The object a dotted path from the runtime names."""
    target = rt
    for part in path.split("."):
        target = getattr(target, part)
    return target


def _venue_address(exchange) -> str | None:
    address = getattr(exchange, "address", getattr(exchange, "_address", None))
    return address.lower() if isinstance(address, str) else None


class Checkpoint(dict):
    """A checkpoint's mapping is what the diary keeps; the diary it came from rides beside it.

    Two runs of one manifest and seed write byte-identical items, so the mapping
    cannot carry anything sealed under one run's own key. The diary fingerprint
    (``Ledger.diary_id``) is therefore an attribute, not a key: an in-memory
    checkpoint restored in this process still names the diary it came from, and
    a ledgered one, read back as a plain mapping, is bound by the file it is in.
    ``origin`` rides beside it the same way: where that diary lives on disk (or
    None for a memory-only ledger), so a restore into a runtime that has no path
    of its own still reads the witness file beside the diary the checkpoint came
    from, rather than depending on this process remembering the kill.
    """

    diary: str | None = None
    origin: Path | None = None


def runtime_state(rt) -> Checkpoint:
    """Retain learning, FIFO lots, private memory and exact source cursors in one checkpoint."""
    rt._ensure_connector_tool()
    runtime = {name: getattr(rt, name) for name in _RUNTIME_FIELDS}
    # The experimental delayed line retains its frozen contracts and finality.
    # Reference worlds keep their previous checkpoint shape; older checkpoints
    # restore with the mixin's empty defaults.
    if getattr(rt.ev, "producer_feedback", "verdict") == "realized":
        runtime["grounded_pending"] = rt.grounded_pending
        runtime["grounded_closed"] = rt.grounded_closed
    runtime["amendment_feedback"] = getattr(rt, "amendment_feedback", None)
    receipts = {path: list(_resolve(rt, path)) for path in _RECEIPT_BOOKS}
    components = {
        name: {field: getattr(getattr(rt, name), prefix + field) for field in names}
        for name, prefix, names in _COMPONENT_FIELDS
    }
    state = Checkpoint({
        "format": 1, "manifest_hash": rt.m.manifest_hash(),
        "config": {
            "events": rt.events_budget, "seed": rt.seed, "initial_balance_micro": rt.initial,
            "drip": rt.use_drip, "router_gamma": rt.router_gamma, "kill_at_end": rt.kill_at_end,
        },
        "adapters": {name: {"name": getattr(getattr(rt, name).target, "name", name),
                            "deterministic": getattr(rt, name).deterministic,
                            **({"address": _venue_address(rt.exchange.target)}
                               if name == "exchange" else {})}
                     for name in ("exchange", "provider")},
        "runtime": encode(runtime), "clock_ns": rt.clock.now_ns,
        "tick_clock": rt.tick_clock.state(),
        "kernel": {name: encode(getattr(rt, name).state()) for name in _KERNEL_FIELDS},
        "budget": encode(rt.budget.state()),
        "components": encode(components),
        "treasury": encode(rt.treasury.snapshot()),
        "receipts": encode(receipts),
        # A program seat's private state is restored by artifact hash (C8); the key is
        # present only for program seats, so a world without one checkpoints as before.
        "assemblies": encode([{"spec": a.spec, "memory": a.memory,
                               **({"state_sha": a.state_sha} if hasattr(a, "state_sha")
                                  else {})}
                              for a in rt.assemblies.values()]),
        "prices": encode(rt.prices.prices),
        "routers": [st.state() for st in rt._all_router_states()],
        # An assembly's own learner over its declared action set, frozen rounds included.
        "assembly_learners": {aid: learner.state()
                              for aid, learner in rt.assembly_learners.items()},
        "retired_routers": [st.state() for st in rt.retired_routers.values()],
        "venue": encode({"last_fill_ns": rt.venue.last_fill_ns,
                         "seen_fills": rt.venue.seen_fills,
                         "last_funding_ns": rt.venue.last_funding_ns,
                         "seen_funding": rt.venue.seen_funding}) if rt.venue else None,
        "venue_tool_log": encode(rt.venue_tools.log) if rt.venue_tools else None,
        "fake_exchange": encode(vars(rt.exchange.target)) if rt.exchange.deterministic else None,
        "fake_provider": encode(vars(rt.provider.target)) if rt.provider.deterministic else None,
    })
    # The diary this state descends from, beside the mapping and never in it.
    state.diary = rt.diary_id or rt.ledger.diary_id
    state.origin = rt.ledger.path
    return state


def restore_runtime(rt, state: dict) -> None:
    """Restore only authenticated matching-format state, rebinding dependencies to this process.

    Transactional (edition 3, R3-C). Every identity constraint — snapshot format
    and manifest hash, both adapters, the venue account, the release digest, the
    facilitator, the witness requirement and receiver, the killed identity, and
    the presence of every artifact the saved state names — is checked against the
    *saved* state before one field is assigned to ``rt``. A refused restore
    therefore leaves the runtime exactly as it was, rather than half a dead
    world's memory inside a live one.
    """
    from factorylab.runtime.live import LiveClock
    from factorylab.runtime.routing import RouterState
    from factorylab.world.clock import ClockSource

    if state.get("format") != 1 or state["manifest_hash"] != rt.m.manifest_hash():
        raise ResumeError("snapshot format or manifest hash differs")
    for name, saved in state["adapters"].items():
        current = getattr(rt, name)
        if (saved["name"] != getattr(current.target, "name", name)
                or saved["deterministic"] != current.deterministic):
            raise ResumeError(f"{name} adapter differs from the saved world",
                              code="adapter_mismatch")
    saved_venue = state["adapters"]["exchange"]
    if (saved_venue.get("address") != _venue_address(rt.exchange.target)):
        raise ResumeError("venue account differs from the saved world",
                          code="venue_account_mismatch")
    saved_runtime = decode(state["runtime"])
    running_digest = getattr(rt, "release_digest", None)  # read before the saved fields land
    running_facilitator = getattr(rt, "facilitator_url", None)
    # Identity validation precedes every mutation of the destination runtime.
    # The saved world names the release that launched it. A different release does
    # not continue that identity: it is a new kernel and must be a new world. It
    # also names the x402 facilitator it launched under: the seller settles every
    # paid call through it, so a different one is a steering lever outside the diary.
    saved_digest = saved_runtime.get("release_digest")
    if saved_digest is not None and saved_digest != running_digest:
        raise ResumeError("release digest differs from the saved world", code="release_mismatch")
    saved_facilitator = saved_runtime.get("facilitator_url")
    if saved_facilitator is not None and saved_facilitator != running_facilitator:
        raise ResumeError("x402 facilitator differs from the saved world",
                          code="facilitator_mismatch")
    # A checkpoint cannot revive a killed runtime. The runtime restored into may
    # already be final (its own Termination, or a Terminated event in its ledger),
    # or the identity the checkpoint names may be recorded as killed in this
    # process or in the local witness beside the diary. Either way nothing is
    # restored; the world stays dead (runtime/witness.py).
    if rt.termination.final or rt.ledger.identity()["terminated"]:
        raise ResumeError("the runtime is final; a checkpoint cannot revive it",
                          code="identity_killed")
    from factorylab.runtime.witness import killed

    # An in-memory checkpoint names its diary (Checkpoint.diary) and where that
    # diary lives (Checkpoint.origin); a ledgered one, read back as a plain
    # mapping, is bound by the file this runtime resumes. The kill record is read
    # from the witness file beside that diary, whichever of the two named it, and
    # from this runtime's own diary; process memory is the third source, not the
    # one relied on (a twin restored in memory has no diary path of its own).
    diary = getattr(state, "diary", None) or (
        rt.ledger.diary_id if rt.ledger.path is not None else None)
    witnessed = rt.ledger.path if rt.ledger.path is not None else getattr(state, "origin", None)
    if killed(world=rt.m.name, launch_nonce=saved_runtime.get("launch_nonce"),
              diary=diary, ledger_path=witnessed, remote=False) is not None:
        raise ResumeError("the checkpoint names a killed identity", code="identity_killed")
    # The witness requirement is part of the launch identity, so it is checked
    # here and not against the environment alone: a world that launched under a
    # receiver does not continue without one, or under another one (R3-C).
    check_witness_identity(saved_runtime)
    # The archive is validated against the saved state, before any of it is
    # assigned: a world does not continue with a seat's memory or a seat's
    # outcomes missing, and a refusal must leave this runtime untouched.
    components = decode(state["components"])
    _check_artifacts(rt.artifacts,
                     index=(components.get("artifacts") or {}).get("index") or {},
                     assemblies=decode(state["assemblies"]),
                     heads=(components.get("working_state") or {}).get("heads") or {},
                     outcomes=(components.get("outcomes") or {}).get("items") or {})
    for name, value in saved_runtime.items():
        setattr(rt, _RUNTIME_BACKING.get(name, name), value)
    rt.diary_id = diary
    # A checkpoint written before launch-bound venue identities keeps its historical
    # client order IDs rather than adopting this process's fresh nonce. The adapter
    # is rebound below, after a deterministic venue's own state has been restored.
    rt.launch_nonce = saved_runtime.get("launch_nonce")
    # Both identities were checked above, before any assignment. A checkpoint
    # written before release identity (or before the facilitator pin) carries
    # none; it keeps its historical Launch (nothing to replay) and, once
    # launched, adopts the running value so every later resume is bound.
    rt.release_digest = saved_digest if saved_digest is not None or not rt.started else (
        running_digest)
    rt.facilitator_url = (saved_facilitator if saved_facilitator is not None or not rt.started
                          else running_facilitator)
    rt.observer.predicates = rt.predicates
    if rt.window.index in rt.price_windows:
        rt.price_windows[rt.window.index] = rt.window
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
    if "budget" in state:  # entitlements restore exactly; older checkpoints predate them
        rt.budget._restore_state(decode(state["budget"]))
    rt.treasury.restore(decode(state["treasury"]))
    # Older checkpoints predate the receipt books; theirs start empty, as they did.
    for path, saved in decode(state.get("receipts") or {}).items():
        _resolve(rt, path).restore(saved)
    for name, prefix, names in _COMPONENT_FIELDS:
        for field in names:
            if name == "controller" and field == "kappa" and field not in components[name]:
                # Older checkpoints inherited this immutable parameter from the same manifest.
                continue
            if name == "charter_book" and field == "bindings" and field not in components[name]:
                # Older checkpoints predate the frozen observation version per proposal.
                continue
            if name == "artifacts" and name not in components:
                # Older checkpoints predate the artifact archive; it starts empty.
                continue
            if name in ("working_state", "outcomes") and name not in components:
                # Older checkpoints predate continuity; heads and inboxes start empty.
                continue
            if (name == "consequences" and field in ("unresolved_orders", "censored_payoffs")
                    and field not in components[name]):
                # Older checkpoints predate the released hold; nothing is released.
                continue
            if name == "settler" and field == "objections" and field not in components[name]:
                # Older checkpoints predate saved objections; none is pending.
                continue
            if name == "bill_settlement" and name not in components:
                # Older checkpoints predate bill settlement; the next read takes a reference.
                continue
            setattr(getattr(rt, name), prefix + field, components[name][field])
    rt.prices.prices = decode(state["prices"])
    rt.assemblies.clear()
    for assembly in decode(state["assemblies"]):
        restored = rt._instantiate(assembly["spec"])
        restored.memory = assembly["memory"]
        if "state_sha" in assembly:
            restored.state_sha = assembly["state_sha"]
    rt.routers.clear()
    for saved in state["routers"]:
        router = RouterState.restore(saved)
        rt.routers.setdefault(router.kind, []).append(router)
    from factorylab.learners.base import restore_learner

    rt.assembly_learners = {aid: restore_learner(saved)
                            for aid, saved in state.get("assembly_learners", {}).items()}
    rt.retired_routers = {}
    for saved in state.get("retired_routers", []):
        router = RouterState.restore(saved)
        rt.retired_routers[router.learner.id] = router
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
    bind_launch_nonce(rt.exchange, rt.launch_nonce)
    for model_id in rt.sellers:
        rt.market.register(model_id, rt.prices.price(model_id).per_request_micro)
    if rt.venue_tools:
        from factorylab.world.venue_tools import VenueTools

        # Rebuild from the launch seed, exactly as bootstrap did, so the restored
        # schemas match byte for byte before registered markets are replayed below.
        tool_log = rt.venue_tools.log
        rt.venue_tools = VenueTools(rt.exchange, coins=rt.m.exchange.coins,
                                   spot_pairs=rt.m.exchange.spot_pairs,
                                   max_leverage=rt.m.tools.max_leverage)
        rt.venue_tools.log = tool_log
        rt._refresh_venue_schemas()
    for contract in rt.registry.available("exchange"):
        if contract.id.startswith("market:"):
            rt._admit_market(contract.input_schema["coin"], contract.input_schema["market"])


def check_witness_identity(saved_runtime: dict) -> None:
    """The death witness this world launched under is still the one configured (R3-C).

    A world launched with a receiver has ``witness_required`` in its ``Launch``
    event and in every checkpoint. Unsetting ``FACTORYLAB_WITNESS_URL`` afterwards
    therefore removes nothing: the requirement belongs to the launched identity,
    and a resume without a receiver refuses (``witness_required``). Naming a
    different receiver refuses too (``witness_mismatch``): the record of this
    world's death is kept by the receiver it launched under, and another receiver
    has never heard of it. The URL itself is never compared, printed or stored —
    only the hash of it.

    A world launched without a receiver is unchanged: the local file decides, and
    that is the weaker guarantee ``deploy/README.md`` names.
    """
    from factorylab.runtime.witness import receiver_identity

    if not saved_runtime.get("witness_required"):
        return
    running = receiver_identity()
    if running is None:
        raise ResumeError("this world launched under a death witness receiver and the "
                          "environment names none", code="witness_required")
    saved = saved_runtime.get("witness_receiver")
    if saved is not None and saved != running:
        raise ResumeError("the configured death witness receiver is not the one this "
                          "world launched under", code="witness_mismatch")


def _check_artifacts(store, *, index: dict, assemblies, heads: dict, outcomes: dict) -> None:
    """Every sha the *saved* state names must have its bytes beside the ledger (P1-02).

    The checkpoint carries the index and each program seat's ``state_sha``; the
    bytes live under ``runs/<world>.artifacts/``. A backup that archived the diary
    without that directory, or a directory lost with the host, restores an index
    that names memory the world no longer has. Continuing would let a program run
    with no state and report ok, so the resume refuses, naming the sha and its
    owner. A memory-only twin (no ledger path) keeps no bytes to check.

    Read from the saved state and not from the runtime, so the refusal happens
    before anything is assigned and a refused restore changes nothing (R3-C).
    """
    if store.root is None:
        return
    from factorylab.kernel.artifacts import ArtifactError

    for assembly in assemblies:
        sha = assembly.get("state_sha")
        if sha is not None and sha not in index:
            raise ResumeError("a program seat names state the archive index does not hold",
                              code="artifact_missing", sha=sha,
                              owner=getattr(assembly.get("spec"), "id", None))
    # Continuity (C1) names artifacts the same way: a head, and every inbox item's
    # body. A world does not continue with a seat's state or its outcomes missing.
    for seat, head in heads.items():
        if head["sha"] not in index:
            raise ResumeError("a seat names a working state the archive index does not hold",
                              code="artifact_missing", sha=head["sha"], owner=seat)
    for seat, items in outcomes.items():
        for item in items:
            if item["sha"] not in index:
                raise ResumeError("an outcome item's body is not in the archive index",
                                  code="artifact_missing", sha=item["sha"], owner=seat)
    for sha, record in index.items():
        try:
            store.get(sha)
        except ArtifactError:
            raise ResumeError("the archive index names bytes that are missing or corrupt",
                              code="artifact_missing", sha=sha,
                              owner=record.get("owner")) from None


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
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.shared import SimClock

    clock = SimClock()
    ledger = Ledger.reopen(
        ledger_path, manifest=json.loads(manifest.canonical_json()), clock_ns=clock,
    )
    snapshot, tail = ledger._recovery_tail()
    if snapshot is None:
        if not ledger.event_times()["launch"]:
            raise ResumeError("ledger has not launched", code="no_launch")
        raise ResumeError("ledger has no recoverable snapshot")
    state = snapshot["state"]
    if state.get("manifest_hash") != manifest.manifest_hash():
        raise ResumeError("snapshot manifest hash differs")
    # The diary says it is alive; the witness may know it was killed. An earlier
    # copy of a killed diary (a backup restored beside the original) has a valid
    # chain, the right key and the right release, and no record of its own death:
    # that record lives outside the diary's directory and, when a receiver is
    # configured, outside the host. Asked before any state is restored or any
    # adapter is contacted; the refusal is ledgered like a release mismatch.
    from factorylab.runtime.witness import WitnessUnavailable, killed

    launch_nonce = decode(state["runtime"]).get("launch_nonce")
    try:
        seen = killed(world=manifest.name, launch_nonce=launch_nonce, diary=ledger.diary_id,
                      ledger_path=ledger_path)
    except WitnessUnavailable:
        # A receiver is configured and gave no verdict. The local file said nothing,
        # but the receiver is the record that survives a lost host or a deleted
        # file, and it was asked for exactly this case: no verdict is a refusal,
        # ledgered like the others, and the supervisor tries again later.
        ledger.append({
            "kind": "failed_resume", "reason": "witness_unavailable",
            "launch_nonce": launch_nonce, "snapshot_seq": snapshot["seq"],
            "ts": state["clock_ns"],
        })
        raise ResumeError("the witness receiver gave no verdict",
                          code="witness_unavailable") from None
    if seen is not None:
        ledger.append({
            "kind": "failed_resume", "reason": "identity_killed", "witness": seen,
            "launch_nonce": launch_nonce, "snapshot_seq": snapshot["seq"],
            "ts": state["clock_ns"],
        })
        raise ResumeError("the witness records this identity's kill", code="identity_killed")
    # The witness requirement the world launched under, before any state is
    # restored or any adapter contacted: unsetting the variable removes no veto.
    try:
        check_witness_identity(decode(state["runtime"]))
    except ResumeError as exc:
        ledger.append({
            "kind": "failed_resume", "reason": exc.code, "launch_nonce": launch_nonce,
            "snapshot_seq": snapshot["seq"], "ts": state["clock_ns"],
        })
        raise
    journal = RecoveryJournal(ledger, clock)
    journal.bootstrap = True
    rt = Runtime(manifest, **state["config"], ledger_path=None, provider=provider, market=market,
                 exchange=exchange, clock_source=clock_source, _journal=journal, _lock=lock)
    # The journal carries no path; the archive's bytes live beside the ledger (C9).
    from factorylab.kernel.artifacts import artifact_root

    rt.artifacts.root = artifact_root(ledger_path)
    running_digest = getattr(rt, "release_digest", None)
    running_facilitator = getattr(rt, "facilitator_url", None)
    try:
        restore_runtime(rt, state)
    except ResumeError as exc:
        if exc.code == "release_mismatch":
            # The refusal is the world's own evidence: which release launched it and
            # which one was refused. Replay skips this item like a repair note.
            ledger.append({
                "kind": "failed_resume", "reason": "release_mismatch",
                "ledgered_release_digest": decode(state["runtime"]).get("release_digest"),
                "running_release_digest": running_digest,
                "snapshot_seq": snapshot["seq"], "ts": state["clock_ns"],
            })
        elif exc.code == "identity_killed":
            ledger.append({
                "kind": "failed_resume", "reason": "identity_killed", "witness": "restore",
                "launch_nonce": launch_nonce, "snapshot_seq": snapshot["seq"],
                "ts": state["clock_ns"],
            })
        elif exc.code == "facilitator_mismatch":
            ledger.append({
                "kind": "failed_resume", "reason": "facilitator_mismatch",
                "ledgered_facilitator_url": decode(state["runtime"]).get("facilitator_url"),
                "running_facilitator_url": running_facilitator,
                "snapshot_seq": snapshot["seq"], "ts": state["clock_ns"],
            })
        elif exc.code == "artifact_missing":
            # The sha and its owner: what memory is gone and whose. The world is not
            # continued with different memory; the operator restores the bytes.
            ledger.append({
                "kind": "failed_resume", "reason": "artifact_missing",
                "sha": exc.details.get("sha"), "owner": exc.details.get("owner"),
                "snapshot_seq": snapshot["seq"], "ts": state["clock_ns"],
            })
        raise
    journal.bootstrap = False
    journal.active = journal.recovering = True
    journal.tail = (item for item in tail
                    if item.get("kind") not in ("ledger.repaired", "failed_resume"))
    try:
        if not rt.started:
            rt._launch()
        while (item := journal.peek()) is not None:
            if item["kind"] == "runtime.input":
                if not rt._process_event(rt._next_event(iter(()))):
                    return rt
            elif item["kind"] == "runtime.finish_budget":
                rt._finish_budget()
                return rt
            elif item["kind"] == "resume.begin":
                rt._resume_at(item["now_ns"])
            elif item["kind"] in ("kill.production", "kill.wind_down", "winddown.op",
                                  "winddown.op_result", "winddown.reconciliation"):
                # The diary was killed and its process died inside the wind-down
                # window: production is dead, the terminal event is simply not
                # written yet (edition 3, R3-C). A resume does not restart a dead
                # population. ``factorylab kill`` reconciles the wind-down by
                # operation id, repeats nothing, and seals the diary.
                ledger.append({
                    "kind": "failed_resume", "reason": "identity_killed",
                    "witness": "production_mark", "launch_nonce": launch_nonce,
                    "snapshot_seq": snapshot["seq"], "ts": state["clock_ns"],
                })
                raise ResumeError("production was killed before this diary was sealed",
                                  code="identity_killed")
            else:
                raise _ReplayFault(f"unexpected tail item {item['kind']} at seq {item['seq']}")
        journal.tail = []
        journal.position = 0
        journal.recovering = False
        resume_time = (time.time_ns() if now_ns is None else now_ns) if rt.live else rt.clock.now_ns
        rt._resume_at(resume_time)
    except _ReplayFault as exc:
        raise ResumeError(str(exc), code="replay_diverged") from None
    return rt


def resume_world(manifest, ledger_path: str, **kwargs) -> dict:
    """Continue the saved event budget and manifest, returning the ordinary final summary."""
    return resume_runtime(manifest, ledger_path, **kwargs).run()
