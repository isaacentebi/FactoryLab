"""Runtime compute method group."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from factorylab.cortex.assembly import Assembly, AssemblySpec, ProgramAssembly
from factorylab.cortex.request import (
    ChildRequest,
    Request,
    Return,
    public_child_inputs,
    public_return,
)
from factorylab.kernel.artifacts import PRIVATE_REFUSAL
from factorylab.kernel.budget import SeatWallet
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.router import Sample
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.grounded import freeze_contract
from factorylab.runtime.reasons import Reason
from factorylab.runtime.shared import CH_EXPOSURE, CH_VERDICT, _to_plain
from factorylab.runtime.summary import _price_str
from factorylab.settlement import SEED_VOCABULARY
from factorylab.settlement.consequence import ReturnConsequences
from factorylab.world.market import X402MeteredModel
from factorylab.world.metering import (
    BillingUncertain,
    Meter,
    Metered,
    MeteredModel,
    provider_namespace,
)
from factorylab.world.models import ModelRequest, ModelResponse, TokenPrice
from factorylab.world.venue_tools import VAULT_READS, VAULT_WRITES

# A fetched body at least this long is text and stays off every durable surface;
# a shorter one (a price, "OK", a count) is a fact the population may repeat.
MIN_PROTECTED_BODY_CHARS = 32


def _prompt_cache_identity(assembly: Assembly, req: Request) -> dict[str, str]:
    """Hash the exact stable prompt bytes and message ordering without retaining prose.

    The effective-leading hash includes the system message, any handle-scoped
    messages that precede this request, and the stable prefix at the head of the
    final user message. It deliberately excludes the moving suffix. Two live
    invocations can therefore distinguish local prefix drift from an upstream
    cache miss without putting prompt bodies on the ledger.
    """
    stamped = replace(req, inputs={**req.inputs, "you": assembly.spec.id})
    stable = stamped.stable_prefix()
    model_request = assembly.build_model_request(req)
    leading = [{"role": "system", "content": model_request.system},
               *[dict(message) for message in model_request.messages[:-1]]]
    if stable and model_request.messages:
        final = model_request.messages[-1]
        content = final.get("content")
        if not isinstance(content, str) or not content.startswith(stable):
            raise ValueError("model request does not begin with its stable prefix")
        leading.append({"role": final.get("role"), "content": stable})

    def digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    return {
        "stable_prefix_sha256": hashlib.sha256(stable.encode("utf-8")).hexdigest(),
        "effective_leading_messages_sha256": digest(leading),
    }


def _safe_prompt_cache_identity(assembly: Assembly, req: Request) -> dict[str, str] | None:
    """Keep diagnostic hashing from changing whether an otherwise bad request returns."""
    try:
        return _prompt_cache_identity(assembly, req)
    except Exception:
        return None



def _call_signature(call: dict[str, Any]) -> str:
    """Identify one tool call by what it asked for, so a repeat of it is recognisable."""
    from factorylab.kernel.ledger import canonical

    return hashlib.sha256(canonical({"tool": call.get("tool"),
                                     "args": call.get("args")})).hexdigest()


def _compacted_result(entry: dict[str, Any], retrieved: dict[str, bytes]) -> dict[str, Any]:
    """Keep an exact result addressable only until this invocation returns."""
    from factorylab.kernel.ledger import canonical

    data = canonical(entry)
    sha = hashlib.sha256(data).hexdigest()
    retrieved[sha] = data
    return {"tool": entry.get("tool"), "retrieved_earlier": True,
            "bytes": len(data), "sha": sha, "expires": "end of this decision",
            "read_with": {"tool": "artifact.get", "args": {"sha": sha}}}


def _bounded_result_history(
        entries: list[dict[str, Any]], retrieved: dict[str, bytes], byte_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the newest exact prior results within ``byte_limit`` canonical bytes.

    Every prior result remains exactly addressable for this invocation. Results
    that do not fit travel as references; no result has both representations in
    the returned working set. The second return value is the all-reference form
    used when the model cannot afford the exact working set.
    """
    references = [_compacted_result(entry, retrieved) for entry in entries]
    carried = list(references)
    remaining = max(0, byte_limit)
    for index in range(len(entries) - 1, -1, -1):
        size = references[index]["bytes"]
        if size <= remaining:
            carried[index] = entries[index]
            remaining -= size
    return carried, references


def _transient_snapshot(section: str, value: Any,
                        retrieved: dict[str, bytes]) -> dict[str, Any]:
    """Address an exact public context value for this decision and no longer.

    The wrapper keeps the section name beside the value, so an ``artifact.get``
    result is self-describing and remains a mapping like every other transient
    retrieval body. Canonical bytes make the reference content-addressed; no
    summary, durable artifact, or second representation of the value is made.
    """
    from factorylab.kernel.ledger import canonical

    data = canonical({"section": section, "value": value})
    sha = hashlib.sha256(data).hexdigest()
    retrieved[sha] = data
    return {
        "not_carried": section,
        "bytes": len(data),
        "sha": sha,
        "expires": "end of this decision",
        "read_with": {"tool": "artifact.get", "args": {"sha": sha}},
    }


#: Hyperliquid's own SDK names for the venue arguments this world publishes. Models
#: trained on that SDK write them (PR121 seqs 282 and 7343; edition 5 testnet), and
#: each voided a whole batch. They name the same quantities, so they are translated
#: before validation, never guessed: an argument given under both names is left alone.
_VENUE_ALIASES = {"limit_px": "price", "sz": "size", "reduceOnly": "reduce_only"}
_ALIASED_TOOLS = ("venue.place_market", "venue.place_limit", "venue.close")


def _venue_aliases(call: dict) -> None:
    """Rewrite the venue SDK's argument names to this world's, in place."""
    if call.get("tool") not in _ALIASED_TOOLS:
        return
    args = call["args"]
    for alias, name in _VENUE_ALIASES.items():
        if alias in args and name not in args:
            args[name] = args.pop(alias)
    if "is_buy" in args and "side" not in args and isinstance(args["is_buy"], bool):
        args["side"] = "buy" if args.pop("is_buy") else "sell"

class ArtifactListing:
    """Each owner's directory rows in listing order, kept sorted as the archive changes.

    Listing order is newest reference first, then by hash, then by the order the
    references were made. Rows are indexed by owner only: a seat lists what it
    owns and nothing else (essay II.I.b: "the local state of a given agent ...
    should be absolutely private"). Rows are shared with the listing; the runtime
    hands out copies.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self.epoch: int | None = None
        self.row: dict[tuple, dict[str, Any]] = {}  # sort key -> row
        self.by_sha: dict[str, list[tuple]] = {}
        self.by_owner: dict[str, list[tuple]] = {}  # each sorted

    def sync(self) -> None:
        """Fold every change the store reports into the listing."""
        store = self.store
        if not hasattr(store, "drain_changes"):
            self._rebuild(self._rows_from_list(store))
            return
        epoch, changed = store.drain_changes()
        if epoch != self.epoch:
            self.epoch = epoch
            self._rebuild(row for sha in list(store.index) for row in self._rows_for_sha(sha))
            return
        for sha in sorted(changed):
            for key in self.by_sha.pop(sha, ()):
                self._remove(key)
            for key, row in self._rows_for_sha(sha):
                self._insert(key, row)

    def rows_for(self, owner: str) -> list[dict[str, Any]]:
        return [self.row[key] for key in self.by_owner.get(owner, ())]

    @staticmethod
    def _key(row: dict[str, Any], position: int) -> tuple:
        return (-(row["updated_ns"] or 0), str(row["sha"]), position)

    @staticmethod
    def _row(sha: str, owner: str, kind: Any, size: int, ts: int) -> dict[str, Any]:
        return {"sha": sha, "owner": owner, "bytes": size, "updated_ns": ts,
                "title": str(sha)[:12], "type": kind, "kind": kind}

    def _rows_for_sha(self, sha: str):
        store = self.store
        record = store.index.get(sha)
        if record is None:
            return
        references = store.references(sha, record)
        for position, (owner, reference) in enumerate(references.items()):
            row = self._row(sha, owner, record.get("kind"), record["bytes"],
                            reference.get("ts", record["ts"]))
            yield self._key(row, position), row

    def _rows_from_list(self, store: Any):  # pragma: no cover - a store from before C1
        kinds = {sha: record.get("kind") for sha, record in store.index.items()}
        for position, row in enumerate(store.list()):
            row = self._row(row["sha"], row["owner"], kinds.get(row["sha"]), row["bytes"],
                            row["ts"])
            yield self._key(row, position), row

    def _rebuild(self, keyed) -> None:
        self.row, self.by_sha, self.by_owner = {}, {}, {}
        for key, row in keyed:
            self.row[key] = row
            self.by_sha.setdefault(row["sha"], []).append(key)
            self.by_owner.setdefault(row["owner"], []).append(key)
        for keys in self.by_owner.values():
            keys.sort()

    def _insert(self, key: tuple, row: dict[str, Any]) -> None:
        self.row[key] = row
        self.by_sha.setdefault(row["sha"], []).append(key)
        bisect.insort(self.by_owner.setdefault(row["owner"], []), key)

    def _remove(self, key: tuple) -> None:
        row = self.row.pop(key)
        owned = self.by_owner[row["owner"]]
        _discard_sorted(owned, key)
        if not owned:
            del self.by_owner[row["owner"]]


def _discard_sorted(keys: list[tuple], key: tuple) -> None:
    del keys[bisect.bisect_left(keys, key)]


@dataclass
class _ObservedMeteredModel(MeteredModel):
    record: Any = None

    def complete(self, req: ModelRequest, *, handle: str) -> Metered[ModelResponse]:
        """Expose already-debited vendor overruns to runtime evidence before returning."""
        metered = super().complete(req, handle=handle)
        if metered.overrun:
            self.record({"kind": "compute.overrun", "handle": handle,
                         "model_id": metered.result.model_id, "cost": metered.cost,
                         "overrun": metered.overrun})
        return metered


class _ObservedX402Model(X402MeteredModel):
    """Paid completions enter observations after metering, including during journal replay."""

    def complete(self, req: ModelRequest, *, handle: str) -> Metered[ModelResponse]:
        """A positive committed request yields exactly one ledgered purchase observation."""
        result = super().complete(req, handle=handle)
        if result.cost > 0:
            self.record({"kind": "observation.market_purchase", "handle": handle})
        return result


def _provider_fault(ret: Return) -> str | None:
    """Name a completion the provider billed but could not deliver: hidden reasoning
    consumed the whole budget and the visible reply is empty."""
    reasoning, budget = ret.provider.get("reasoning_tokens"), ret.provider.get("max_tokens")
    if (
        ret.status == "malformed"
        and ret.outputs.get("raw") == ""
        and type(reasoning) is int and type(budget) is int
        and budget > 0 and reasoning >= budget
    ):
        return "hidden reasoning consumed the whole completion budget; no visible reply"
    return None


class ContractConsequences(ReturnConsequences):
    """All decisions keep cost accounts; producing outcomes alone deliver producer trials.

    Judges already receive their consequence trials through their sealed forecasts
    and terminal meta scores. Their additional cost accounts must not double-count
    those trials. Every account still resolves in the underlying economic table.
    """

    def __init__(self, ledger, backstop, runtime):
        super().__init__(ledger, backstop)
        self.runtime = runtime

    def _tick(self, event: int) -> int:
        """The runtime's world ticks consumed: the unit the backstop counts (defect 1)."""
        return self.runtime.ticks_consumed

    def observe(self, kind, payload, event):
        if kind != "Fill" or payload.get("market") != "event":
            return super().observe(kind, payload, event)
        from factorylab.runtime.polymarket import credit_realized

        # What an event fill realises is the polymarket pot's, kept apart from the
        # venue's (runtime/polymarket.py); a deferred fill replays through here too.
        before = {r.handle: r.realized_micro for r in self.table.returns}
        super().observe(kind, payload, event)
        credit_realized(self.runtime, {
            r.handle: r.realized_micro - before.get(r.handle, 0)
            for r in self.table.returns if r.realized_micro != before.get(r.handle, 0)})

    def resolve(self, event):
        resolved = super().resolve(event)
        return [payoff for payoff in resolved
                if self.runtime.queue.get(payoff.handle).channel in (CH_VERDICT, "exposure")]


class ComputeMixin:
    """Preserve runtime state and behavior for compute operations."""

    def _protected_share(self, assembly_id: str) -> int:
        """The exploration an unhistoried seat may draw beyond its entitlement, and
        nothing for a historied one: the larger of the unallocated pool and this
        window's remaining novelty share.

        This is the C10 reading of the wallet's own rule, where a protected call may
        use ``unhistoried_available`` and an ordinary one only what the novelty share
        leaves: the commons funds a seat's trial, the seat funds its career. The
        novelty share is money the wallet withholds from ordinary calls whatever the
        book's classification says, so it counts even when shared spending has run
        the pool negative. Routing reads this through ``BudgetBook.cover`` and the
        reservation enforces the same figure, so the two never disagree.
        """
        if not self._unhistoried(assembly_id):
            return 0
        return max(0, self.budget.unallocated(), self.reserve.remaining())

    def _novelty_protection(self, handle: str, reason: str) -> int:
        """The protected share for one reservation: the seat's, for exactly the calls
        the wallet already classifies as protected (an unhistoried seat's own model
        calls), or the pool bridge routing granted this call where that is larger; the
        bridge alone otherwise."""
        bridged = self.entitlement_bridges.get(handle, 0)
        if not self._novelty_compute(handle, reason):
            return bridged
        # Both are drawn on the same unallocated pool, so the cover is the larger of
        # the two, never their sum: adding them let one call spend the pool twice.
        return max(self._protected_share(self.queue.get(handle).propensity.chosen), bridged)

    def _world_chars(self, world: Any) -> int:
        """The rendered size of a request's world block, the part of every prompt that
        grows with the factory (registrations, artifacts, charter).

        Compact worlds are measured through the same pure projection an invocation
        receives. Routing therefore prices growth in inline context, not history
        that the next call will replace with a fixed-size transient reference.
        """
        if not isinstance(world, dict):
            return 0
        world = self._compact_world_context(world, {})
        return len(json.dumps(world, sort_keys=True, indent=2, default=str))

    def _liable_seat(self, handle: str) -> str | None:
        """The seat whose entitlement pays for a decision, or None for the caller's own.

        A child request is its parent's subcontracting and spends the parent's
        entitlement, exactly as ``_novelty_compute`` classifies it; a policy ballot
        is the kernel's request and stays the voter's own.
        """
        try:
            decision = self.queue.get(handle)
        except KeyError:
            return None
        while decision.parent_handle is not None and decision.channel != "policy":
            decision = self.queue.get(decision.parent_handle)
        owner = self.handle_to_assembly.get(decision.handle)
        return owner if owner in self.assemblies else None

    def _seat_wallet(self, assembly_id: str) -> SeatWallet:
        return SeatWallet(self.wallet, self.budget, assembly_id,
                          protected=self._novelty_protection, payer=self._liable_seat)

    def _provider_balance(self, model_id: str) -> int | None:
        """The balance of the provider that pays for ``model_id``, journaled as a read."""
        if not hasattr(self.provider, "balance_of"):
            raise LookupError("provider exposes no balance")
        return self.provider.balance_of(model_id)

    def _refresh_settlement_references(self) -> None:
        """Read each live provider's balance once so the next uncertain bill can settle."""
        seen = set()
        for tier in self.m.models:
            if tier.provider not in ("openrouter", "venice"):
                continue
            namespace = provider_namespace(tier.id)
            if namespace in seen or not hasattr(self.provider, "balance_of"):
                continue
            seen.add(namespace)
            self.bill_settlement.refresh(tier.id)

    def _seat_meter(self, assembly_id: str | None) -> Meter:
        """A seat's priced work is covered by the wallet and by its own entitlement (C10).

        Work no seat authored keeps the shared meter.
        """
        if assembly_id is None or assembly_id not in self.assemblies:
            return self.meter
        return Meter(self._seat_wallet(assembly_id))

    def _instantiate(self, spec: AssemblySpec) -> Assembly:
        self._init_connectors()
        self._check_event_schemas(spec)
        meter = Meter(self._seat_wallet(spec.id))
        if spec.model_id == "program":
            from factorylab.cortex.assembly import ProgramAssembly

            # The seat's executor is its own code in the jail; its flat price is
            # reserved and committed through the same meter as a model call.
            asm = ProgramAssembly(
                spec, self.program_runner, meter, self.m.prices.program_micro_per_call,
                artifacts=self.artifacts, validator=self._validate_output_contract,
                record=lambda entry: self.ledger.append(entry),
            )
            self.assemblies[spec.id] = asm
            self.event_schemas.update(spec.schemas)
            if not self.ledger.bootstrap:
                self.stats.registered_window.setdefault(spec.id, self.stats.reserve_windows)
            return asm
        model = _ObservedMeteredModel(
            self.provider, self.prices, meter, record=self._record_market,
            settlement=self.bill_settlement,
        )
        if spec.model_id.startswith("x402:"):
            model = _ObservedX402Model(
                self.market,
                self.prices,
                meter,
                record=self._record_market,
                on_unaffordable=self._compute_failure,
            )
        asm = Assembly(spec, model, validator=self._validate_output_contract)
        self.assemblies[spec.id] = asm
        self.event_schemas.update(spec.schemas)
        if not self.ledger.bootstrap:
            self.stats.registered_window.setdefault(spec.id, self.stats.reserve_windows)
        return asm

    def _check_event_schemas(self, spec: AssemblySpec) -> None:
        """A named event keeps one public meaning; a changed schema needs a new kind."""
        for kind, schema in spec.schemas.items():
            if kind in self.event_schemas and self.event_schemas[kind] != schema:
                raise ValueError(f"event schema already declared differently: {kind}")

    def _validate_output_contract(self, parsed: dict, req: Request) -> None:
        """Every tool argument and proposal bound is checked before any effect in a reply.

        A fault that belongs to one tool call, child request or forecast raises
        ``SectionError`` naming it, so the return validator drops that item and
        keeps the answer; a fault in the answer itself (its declared kind, its
        custom schema) is a ``ValueError`` and voids the return.
        """
        from factorylab.cortex.assembly import (
            SectionError,
            positive_wire_decimal,
            reserved_return_fields,
            validate_schema,
        )
        from factorylab.world.venue_tools import _validate

        live = {**parsed}
        if isinstance(parsed.get("tool_calls"), list):
            # A call refused in validation is answered in its slot, never dispatched;
            # it does not count against the turn's limit.
            live["tool_calls"] = [c for c in parsed["tool_calls"]
                                  if not (isinstance(c, dict) and c.get("invalid"))]
        from factorylab.runtime.propensity import EFFECT_TOOLS

        calls = parsed.get("tool_calls", [])
        # A batch that writes (the venue, the treasury) runs whole
        # or not at all; a batch of reads loses only the read that cannot run.
        writes = any(str(call.get("tool")) in EFFECT_TOOLS
                     or str(call.get("tool")).startswith("treasury.")
                     or call.get("tool") in self.CONSEQUENCE_WRITES
                     for call in calls if isinstance(call, dict))
        for section, limit in (("requests", self.m.tools.max_children),
                               ("tool_calls", self.m.tools.max_tool_calls)):
            items = live.get(section)
            if isinstance(items, list) and len(items) > limit:
                if section == "tool_calls" and not writes:
                    # The first live call past the limit is refused in its own slot.
                    over = [i for i, c in enumerate(calls)
                            if not (isinstance(c, dict) and c.get("invalid"))][limit]
                    raise SectionError(section, f"more than {limit} {section} in one turn; "
                                       "call it again next round", over, atomic=False)
                raise SectionError(section, f"more than {limit} {section}", limit)
        validate_schema(live, {"type": "object", "properties": reserved_return_fields(
            max_children=self.m.tools.max_children, max_tool_calls=self.m.tools.max_tool_calls)})
        binding = self.return_bindings.get(req.handle)
        if binding is not None:
            emits = parsed.get("emits")
            if emits is None and len(binding["channels"]) == 1:
                emits = next(iter(binding["channels"]))
            if emits not in binding["channels"]:
                raise ValueError("select a declared emits kind")
            if binding["selected"] not in (None, emits):
                raise ValueError("return kind cannot change after tools or children run")
        owner = self.handle_to_assembly.get(req.handle)
        # The request's own channel says whether this is a contract return; a policy ballot
        # is not one, and a handle the kernel queue never opened cannot be looked up at all.
        if owner in self.assemblies and req.scoring_channel != "policy":
            spec = self.assemblies[owner].spec
            emits = parsed.get("emits", spec.emits[0] if len(spec.emits) == 1 else None)
            if emits not in spec.emits:
                raise ValueError("select a declared emits kind")
            if emits in spec.schemas:
                # The caller's outcome schema cannot weaken a custom event's declaration.
                validate_schema(
                    {k: v for k, v in parsed.items()
                     if k not in ("emits", "register", "requests", "tool_calls", "about_handle",
                                  "status", "reason", "working_state", "ack_through")},
                    spec.schemas[emits],
                    partial=bool(parsed.get("requests") or parsed.get("tool_calls")
                                 or parsed.get("status") == "cannot"),
                )
        refused = next((i for i, c in enumerate(calls)
                        if isinstance(c, dict) and c.get("invalid")), None)
        if writes and refused is not None:
            raise SectionError("tool_calls", "a batch that writes cannot run beside a "
                               f"refused call: {calls[refused]['invalid']}", refused)
        for call in calls:
            if isinstance(call, dict) and isinstance(call.get("args"), dict):
                _venue_aliases(call)
        for index, call in enumerate(calls):
            spec = self.tool_specs.get(call["tool"])
            if spec is None or call.get("invalid"):
                continue
            try:
                if spec["kind"] == "venue":
                    _validate(call["args"], spec["args_schema"])
                    for key in ("size", "price"):
                        if call["args"].get(key) is not None:
                            positive_wire_decimal(call["args"][key])
                else:
                    validate_schema(call["args"], spec["args_schema"])
            except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
                raise SectionError("tool_calls", f"{call['tool']}: {exc}", index,
                                   atomic=writes) from None
        known = {p.id: p for p in SEED_VOCABULARY}
        for index, forecast in enumerate(parsed.get("forecasts", [])):
            if forecast["predicate"] not in known:
                raise SectionError("forecasts", "unknown forecast predicate", index)
            try:
                validate_schema(
                    forecast["params"], _to_plain(known[forecast["predicate"]].param_schema)
                )
            except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
                raise SectionError("forecasts", str(exc), index) from None

    def _catalogue_search(self, substring: str, limit: int) -> list[dict[str, Any]]:
        """Case-insensitive substring over every catalogue the provider exposes plus registered
        prices; a world fact, never a recommendation. Unavailable catalogues yield nothing."""
        needle = substring.lower()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        entries: list[Any] = []
        if hasattr(self.provider, "catalogue"):
            try:
                entries = list(self.provider.catalogue())
            except Exception:  # catalogue unavailable: the search is simply empty
                entries = []
        for e in entries:
            if needle in e.id.lower() or needle in (e.name or "").lower():
                seen.add(e.id)
                p = e.price()
                out.append(
                    {
                        "id": e.id,
                        "name": e.name,
                        "usd_per_million_input_tokens": _price_str(p.input_micro),
                        "usd_per_million_output_tokens": _price_str(p.output_micro),
                        "context_length": e.context_length,
                    }
                )
        for mid, p in self.prices.prices.items():
            if mid not in seen and needle in mid.lower():
                out.append(
                    {
                        "id": mid,
                        "name": mid,
                        "usd_per_million_input_tokens": _price_str(p.input_micro),
                        "usd_per_million_output_tokens": _price_str(p.output_micro),
                        "per_request_micro": p.per_request_micro,
                    }
                )
        out.sort(key=lambda m: m["id"])
        return out[: max(1, min(limit, 50))]

    def _tool_schema_search(self, substring: str, limit: int) -> list[dict[str, Any]]:
        """Registered tools whose id or description matches, with their full args_schema.

        This is the other half of the compact affordance index: the world block
        publishes every tool's id, kind, description, price and argument names,
        and the schema a caller must satisfy is read here, once, by the seat that
        is about to call it. Nothing is hidden — an empty substring matches every
        tool — and the specs returned are exactly the ones dispatch validates
        against, taken through ``_published_tool_specs(full=True)`` so the venue's
        thousand-instrument enum is named rather than spelled here too.
        """
        needle = substring.lower()
        specs = [spec for spec in self._published_tool_specs(full=True)
                 if needle in str(spec.get("id", "")).lower()
                 or needle in str(spec.get("description", "")).lower()]
        specs.sort(key=lambda spec: str(spec.get("id")))
        return specs[: max(1, min(limit, 50))]

    def _proposal_shape_search(self, substring: str) -> dict[str, Any]:
        """Full proposal shapes whose kind or one-line index entry matches the substring."""
        needle = substring.lower()
        lines = self._proposal_index()
        return {kind: shape for kind, shape in self.PROPOSAL_SHAPES.items()
                if needle in kind.lower() or needle in str(lines.get(kind, "")).lower()}

    def _record_market(self, item: dict) -> None:
        """Payment and pricing evidence is ledgered before dependent runtime state changes."""
        self.ledger.append({**item, "ts": self.clock.now_ns})
        if item["kind"] == "x402.unresolved":
            self.unresolved_x402[item["reservation_id"]] = dict(item)
        if item["kind"] == "observation.market_purchase":
            self.window.market_purchases += 1

    def _reconcile_x402(self) -> None:
        """Observe the reserve after uncertain debits without inventing payment attribution."""
        if not self.unresolved_x402:
            return
        try:
            balance = self.market.reserve_balance()
        except Exception:
            return  # unavailable reserve is weather; retry on the next tick
        pending = list(self.unresolved_x402.values())
        self._record_market({
            "kind": "x402.reconciled", "reserve_micro": balance,
            "reservation_ids": [item["reservation_id"] for item in pending],
            "provisional_micro": sum(item["reserved_micro"] for item in pending),
            "status": "balance_observed_payment_unattributed",
            "payments": [{
                "reservation_id": item["reservation_id"],
                "reserve_before_micro": item.get("reserve_before_micro"),
                "observed_delta_micro": (balance - item["reserve_before_micro"]
                                         if item.get("reserve_before_micro") is not None else None),
                "expected_delta_micro": -item["reserved_micro"],
            } for item in pending],
        })
        # A balance cannot prove which authorization settled, or that an unexpired
        # authorization will never settle. Keep the wallet's uncertain bills;
        # neither an automatic refund nor a second debit follows this observation.
        self.unresolved_x402.clear()

    def _discover_market(self, url_substring=None, query=None, limit=20) -> list[dict]:
        """One bounded index read per reserve window serves every discovery query."""
        from factorylab.world.market import filter_sellers

        filter_sellers([], url_substring, query, limit)
        if self.market_index is None:
            self.market_index = self.market.discover_index()
        return filter_sellers(self.market_index, url_substring, query, limit)

    def _seller_price(self, model_id: str) -> tuple[TokenPrice, dict]:
        """A seller ceiling is affordable by policy before any registration effect."""
        price, seller = self.market.registration_price(model_id)
        ceiling = price.per_request_micro
        if type(ceiling) is not int or not 0 <= ceiling <= self.m.treasury.max_request_micro:
            raise ValueError("Per-request ceiling exceeds treasury.max_request_micro")
        return price, seller

    def _record_seller(self, model_id: str, price: TokenPrice, seller: dict) -> None:
        """Registered seller metadata and the provider ceiling follow durable pricing evidence."""
        self._record_market({"kind": "market.registered", "model_id": model_id, **seller})
        self.market.register(model_id, price.per_request_micro)
        self.prices.register(model_id, price)
        self.sellers[model_id] = seller

    def _compute_failure(self, handle: str) -> None:
        """A reserve shortfall counts once in the enclosing routed event, after its ledger item."""
        self._record_market({"kind": "compute.unaffordable", "handle": handle})
        self._compute_unaffordable = True

    def _record_insolvency_event(self, ev: Event) -> None:
        """Routed affordable events reset the streak; events without decisions leave it alone."""
        if not self._compute_routed:
            return
        count = self.insolvency_count + 1 if self._compute_unaffordable else 0
        if (bool(count) != bool(self.insolvency_count)
                or count == self.m.treasury.insolvency_events):
            self._record_market(
                {
                    "kind": "treasury.insolvency",
                    "event_id": ev.id,
                    "consecutive_events": count,
                    "unaffordable": self._compute_unaffordable,
                }
            )
        self.insolvency_count = count

    def _init_connectors(self) -> None:
        """The proxy matches the immutable manifest; restored accounting is never reset."""
        if not hasattr(self, "connector_proxy"):
            from factorylab.world.connector import ConnectorProxy, FakeConnectorTransport

            offline = all(m.provider == "fake" for m in self.m.models)
            self.connector_proxy = ConnectorProxy(
                self.m.connectors, FakeConnectorTransport() if offline else None,
                sellers=lambda: [s["seller"] for s in self.sellers.values() if s.get("seller")],
            )
            # Scripted transports re-run in replay; live reads return their journalled result.
            self.connector_deterministic = offline
        if not hasattr(self, "connector_calls"):
            self.connector_calls = {}  # assembly -> (reserve window, attempted calls)
            self.connector_calls_day = {}  # UTC day -> count, public aggregate

    def _ensure_connector_tool(self) -> None:
        """Expose the fixed fetch tool without provisioning a connector origin."""
        from factorylab.cortex.tools import connector_spec

        self._init_connectors()
        self.tool_specs.setdefault(
            "connector.fetch", connector_spec(self.m.connectors.call_price_micro))
        self._ensure_web_tool()
        self._ensure_calc_tool()
        self._ensure_directory_tools()

    def _ensure_calc_tool(self) -> None:
        """Publish ``calc`` wherever the fixed primitives are published (R3-E).

        It rides here rather than in ``bootstrap`` for one reason: every path
        that can dispatch a tool or render a world block already calls
        ``_ensure_connector_tool``, a resume included, so a world restored from a
        diary has the arithmetic capability a live world has. Nothing about the
        manifest, the roster or the manifest hash changes — the spec is a
        constant of the runtime, not a committed parameter.

        The price is ``prices.tool_micro_per_call`` where a world commits one
        (GPT-6 §7 allows free or the flat tool price) and free otherwise, which
        is what every world in this repository is today.
        """
        from factorylab.cortex.tools import calc_spec

        price = getattr(self.m.prices, "tool_micro_per_call", 0)
        self.tool_specs.setdefault("calc", calc_spec(price if type(price) is int else 0))

    def _ensure_web_tool(self) -> None:
        """Register ``web.search`` exactly when the manifest names a search route.

        A world with no ``[web]`` block registers no tool, so nothing about its
        prompt, its manifest identity or its roster changes: the outside is reachable
        only where a launch decided what one look at it costs and on which route.
        """
        from factorylab.cortex.tools import web_search_spec

        if self.m.web.search_model is None:
            return
        cap = (Decimal(self.m.web.max_call_micro) / 1_000_000).normalize()
        self.tool_specs.setdefault(
            "web.search", web_search_spec(self.m.web.call_price_micro, f"{cap}"))

    #: What one page of a shared-directory listing returns before a cursor.
    DIRECTORY_PAGE = 50

    def _ensure_directory_tools(self) -> None:
        """Expose the caller's own archive index: sha, kind, bytes and when, never contents.

        ``artifact.get`` returns contents, at its own price.
        """
        page = self.DIRECTORY_PAGE
        self.tool_specs.setdefault("artifact.list", {
            "id": "artifact.list",
            "kind": "artifact",
            "description": f"Index your own archived artifacts: up to {page} rows of sha, "
            "kind, bytes and when it was archived, newest first, with a cursor for the "
            "next page. Free, like artifact.get.",
            "args_schema": {
                "type": "object",
                "properties": {"cursor": {"type": "string", "maxLength": 128}},
                "additionalProperties": False,
                # Every published tool carries examples its own schema accepts (B1).
                "examples": [{}, {"cursor": "0" * 64}],
            },
            "price_micro_per_call": 0,
        })

    def _artifact_listing(self) -> ArtifactListing:
        """The directory's sorted view of the archive, brought up to date with it.

        One listing lives as long as its store; each call folds in only the hashes
        put or collected since the last one (``ArtifactStore.drain_changes``), so
        a world block that lists each seat's own rows does not re-read and re-sort
        the archive once per seat. A store without change tracking is listed from
        scratch on every call, as it always was.
        """
        store = self.artifacts
        listing = self.__dict__.get("_artifact_listing_view")
        if listing is None or listing.store is not store:
            listing = ArtifactListing(store)
            if hasattr(store, "drain_changes"):
                self._artifact_listing_view = listing
        listing.sync()
        return listing

    def _artifact_index(self, owner: str, cursor: str | None = None) -> list[dict[str, Any]]:
        """One owner's rows, newest first, after ``cursor`` when one is named.

        Guarantees no row another seat owns is returned: the index is keyed by
        owner and there is no unscoped read (information audit C4).
        """
        rows = self._artifact_listing().rows_for(owner)
        if cursor:
            shas = [row["sha"] for row in rows]
            start = shas.index(cursor) + 1 if cursor in shas else len(rows)
            rows = rows[start:]
        return [{k: v for k, v in row.items() if k != "owner"} for row in rows]

    def _artifacts_owned_by(self, seat: str, limit: int) -> tuple[int, list[dict[str, Any]]]:
        """How many rows ``seat`` owns, and the newest ``limit`` of them."""
        rows = self._artifact_index(seat)
        return len(rows), rows[:limit]

    def _artifact_page(self, owner: str, args: dict) -> dict[str, Any]:
        """One ``artifact.list`` page of the caller's own rows, the total, and the cursor.

        ``count`` is the caller's whole listing, not the remainder, so a reader
        knows how much it has not seen; an unknown cursor ends the listing rather
        than restarting it, so paging can never loop.
        """
        cursor = args.get("cursor") if isinstance(args.get("cursor"), str) else None
        total = len(self._artifact_index(owner))
        remaining = self._artifact_index(owner, cursor)
        page = remaining[:self.DIRECTORY_PAGE]
        return {"items": page, "count": total,
                "next_cursor": page[-1]["sha"] if len(remaining) > len(page) else None}

    def _connector_catalogue(self) -> list[dict]:
        """Public connector contracts expose latest versions, descriptions and origins."""
        return [{"id": c.id.removeprefix("connector:"), "version": c.version,
                 "description": c.description, "origin": c.input_schema["origin"],
                 "pay": c.input_schema.get("pay"),
                 "max_call_micro": c.input_schema.get("max_call_micro", 0)}
                for c in self.registry.available("connector")]

    def _connector_refused(self, handle: str, reason: str, **fields) -> tuple[dict, int]:
        """Refusals are ledgered before their public reason is returned."""
        self.ledger.append({"kind": "connector.refused", "handle": handle,
                            "reason": reason, **fields, "ts": self.clock.now_ns})
        return {"error": reason}, 0

    def _fetch_connector(self, action_id: str, handle: str, args: dict, *,
                         origin: str | None = None) -> tuple[dict, int]:
        """Reserve first; count attempts durably; return text only after the flat debit."""
        from factorylab.cortex.tools import _validate_args, connector_spec
        from factorylab.world.connector import ConnectorRefused

        error = _validate_args(connector_spec(0)["args_schema"], args)
        if error:
            return self._connector_refused(handle, error)
        preflight = origin is not None
        paid_cap = None
        cid, path = args["id"], args["path"]
        fields = {"id": cid, "path": path, "assembly_id": action_id}
        try:
            if origin is None:
                contract = self.registry.get(f"connector:{cid}")
                if contract.kind != "connector":
                    raise KeyError(cid)
                origin = contract.input_schema["origin"]
                if contract.input_schema.get("pay") == "x402":
                    paid_cap = contract.input_schema["max_call_micro"]
            self.connector_proxy.validate(origin, path)
        except (KeyError, ConnectorRefused, ValueError) as exc:
            reason = "unknown connector" if isinstance(exc, KeyError) else str(exc)
            return self._connector_refused(handle, reason, **fields)
        window, count = self.connector_calls.get(action_id, (self.window.index, 0))
        if window != self.window.index:
            count = 0
        if count >= self.m.connectors.max_calls_per_window:
            return self._connector_refused(handle, "connector window call cap reached", **fields)
        price = self.m.connectors.call_price_micro
        if price + (paid_cap or 0) > self.wallet.available_for(handle, "tool:connector.fetch"):
            return self._connector_refused(handle, "connector call unaffordable", **fields)
        data_cost = 0

        def execute():
            nonlocal data_cost
            self.connector_calls[action_id] = (self.window.index, count + 1)
            day = self.clock.now_ns // 86_400_000_000_000
            self.connector_calls_day[day] = self.connector_calls_day.get(day, 0) + 1
            # Recovery evidence, exactly as for a paid model call or an x402 purchase:
            # the io.call/io.result pair reproduces the read without fetching it again
            # and without repeating the debit, so replay stays deterministic.
            if paid_cap is not None:
                result, data_cost = self._fetch_paid_data(handle, origin, path, paid_cap)
            else:
                result = self.ledger.call("connector.fetch", self.connector_proxy.fetch,
                                          (origin, path), {},
                                          deterministic=self.connector_deterministic)
            if preflight:
                result = {key: value for key, value in result.items() if key != "body"}
            if len(result.get("body") or "") >= MIN_PROTECTED_BODY_CHARS:
                # A body long enough to be text is kept off every durable surface. A
                # number, a status word or a token shorter than this is a fact the
                # population fetched in order to say; protecting it would refuse
                # every later return that mentions it.
                self.ledger.protect_connector_body(result["body"])
            return result

        try:
            paid = self._seat_meter(action_id).run(
                handle=handle, reason="tool:connector.fetch", ceiling=price,
                execute=execute, cost_of=lambda _: price)
        except BillingUncertain as exc:
            result, cost = {"error": str(exc), "status": "uncertain", "bytes": 0}, exc.cost
        except Exception:
            return self._connector_refused(handle, "connector call unaffordable", **fields)
        else:
            result, cost = paid.result, paid.cost
        cost += data_cost
        self.ledger.append({"kind": "connector.call", "handle": handle, **fields,
                            "status": result["status"], "bytes": result["bytes"], "cost": cost,
                            "ts": self.clock.now_ns})
        if "error" in result:
            self._connector_refused(handle, result["error"], **fields)
        return result, cost

    def _fetch_paid_data(self, handle: str, origin: str, path: str, cap: int):
        """The existing x402 wallet buys data through one bounded, non-repeating journal call."""
        from factorylab.world.market import metered_data

        def fetch(origin, path, cap, *, record):
            return self.market.target.fetch_data(
                origin, path, cap, transport=self.connector_proxy.payment_transport, record=record)

        def execute(record):
            return self.ledger.call("connector.paid_fetch", fetch, (origin, path, cap),
                                    {"record": record})

        meter = self._seat_meter(self.handle_to_assembly.get(handle))
        return metered_data(meter, handle, cap, execute, self._record_market)

    def _tool_price_bound(self, call: dict) -> int:
        """Variable tool prices fit the remaining request ceiling before dispatch."""
        tool = call["tool"]
        price = self.tool_specs.get(tool, {}).get("price_micro_per_call", 0)
        try:
            if tool == "connector.fetch":
                contract = self.registry.get(f"connector:{call['args']['id']}")
                price += contract.input_schema.get("max_call_micro", 0)
            if tool == "web.search":
                # The flat price is not what a search costs: the metered completion
                # rides with it, and the manifest's ceiling is what must fit.
                price = self.m.web.max_call_micro
        except (KeyError, ValueError):
            pass  # The normal dispatcher supplies the shape or identity refusal.
        return price

    CONSEQUENCE_WRITES = frozenset({
        "venue.place_market", "venue.place_limit", "venue.close", "venue.cancel",
        "venue.set_leverage", "treasury.transfer",
        # The vault surface ([venue] vault_tools): money moving between perps
        # collateral and a vault is a consequence exactly as an order is.
        "venue.vault_create", "venue.vault_deposit", "venue.vault_withdraw",
        # Polymarket event markets (runtime/polymarket.py), on the simulated venue.
        "polymarket.place_limit", "polymarket.cancel",
    })

    #: Reads whose answers carry text third parties wrote, jailed like a fetched
    #: body: Polymarket's market questions, rules and slugs (runtime/polymarket.py).
    OUTSIDE_TEXT_TOOLS = frozenset({"connector.fetch", "web.search", "polymarket.search",
                                    "polymarket.market", "polymarket.book"})

    #: The most continuation calls one decision can buy, whatever it retrieves.
    #: The budget is the live limit; this is the backstop that makes the worst
    #: case finite even where a call is free.
    MAX_TOOL_ROUNDS = 5

    #: Exact older tool results retained beside the latest batch. Canonical byte
    #: accounting makes the prompt bound independent of JSON rendering choices;
    #: every result outside the window remains available by its transient ref.
    RECENT_RESULT_BYTES = 16 * 1024

    #: Tool kinds a round earned by text from outside may still run. Fetched or
    #: searched bytes cannot reach the venue, the treasury or a transport inside
    #: the same wake that read them.
    PARSE_KINDS = frozenset({"population", "artifact", "outcome"})

    #: Tool kinds that answer with state and change none. Reading one can be worth
    #: another round, because what it returned arrives after the answer that asked
    #: for it, and a round that ran only these has not acted.
    READ_ONLY_KINDS = frozenset({
        "institution", "catalogue", "outcome", "artifact", "market", "venue",
        "connector", "web", "polymarket",
    })

    def _read_only_call(self, tool_id: str) -> bool:
        """Whether this tool answers with state without changing any.

        Guarantees the answer is False for anything this runtime does not know to
        be a read: a write, a transport, population code and any
        tool the population registers later. Retrieval is extended by reads and
        ended by everything else, so a new capability cannot become a way to buy
        more rounds of acting.
        """
        if tool_id in self.CONSEQUENCE_WRITES:
            return False
        return self.tool_specs.get(tool_id, {}).get("kind") in self.READ_ONLY_KINDS

    def _call_reserve(self, assembly: Any, req: Request) -> int | None:
        """The most one more call on this request can cost, at its own price.

        Guarantees an upper bound or ``None``: the estimate is the metered model's
        own ceiling over the rendered prompt, so nothing here invents a price.
        ``None`` means this executor could not price the call at all — a program
        seat, or a request that did not render — and an unpriced call is refused a
        round rather than treated as free.
        """
        if isinstance(assembly, ProgramAssembly):
            return assembly.price
        model = getattr(assembly, "model", None)
        build = getattr(assembly, "build_model_request", None)
        if model is None or build is None:
            return None
        try:
            return max(0, int(model.ceiling(build(req))))
        except Exception:
            return None

    WRITE_REFUSAL = ("venue and treasury writes require a producing return kind and an open "
                     "consequence account; judging decisions and their children cannot write")

    # The channels a decision settles on when it produced something the world can
    # price. Every other channel (conformity, fast, policy, an unselected sum type)
    # names a judgement or a ballot, and nothing under one may write.
    WRITING_CHANNELS = frozenset({CH_VERDICT, CH_EXPOSURE})

    def _may_write(self, handle: str) -> bool:
        """Write authority is a property of the decision chain, never of a return's kind.

        Guarantees that a decision, and every decision it descends from, settles on
        a producing channel with an open consequence account before a venue or
        treasury write is executed: a policy ballot, a judgement, or a child of
        either has no such authority whatever output kind its assembly declares.
        """
        ancestors = []
        cursor = handle
        while cursor is not None:
            ancestors.append(cursor)
            try:
                cursor = self.queue.get(cursor).parent_handle
            except KeyError:
                return False
        for ancestor in ancestors:
            decision = self.queue.get(ancestor)
            if decision.channel not in self.WRITING_CHANNELS:
                return False
            if self.return_kinds.get(ancestor) in ("Verdict", "MetaVerdict"):
                return False
            if not self.consequences.account_open(ancestor):
                return False
        return True

    def _allowed_tools(self, action_id: str) -> set[str]:
        """Every registered tool is a public primitive; schematics are public."""
        return set(self.tool_specs)

    def _weigh_venue_batch(self, action_id: str, handle: str, ret: Return,
                           tool_round: int) -> Return:
        """Refuse a batch's venue writes together when any one of them would be refused.

        Guarantees no venue write in a batch is submitted unless every write in it
        passes the tests it would meet alone: a hedge never leaves one leg standing.
        Each refused write is answered in its own slot with the reason, reads in the
        batch still run, and nothing is ledgered as an intent.
        """
        writes = [(index, call) for index, call in enumerate(ret.tool_calls)
                  if not call.get("invalid") and call.get("tool") in self.CONSEQUENCE_WRITES
                  and self.tool_specs.get(str(call.get("tool")), {}).get("kind") in (
                      "venue", "polymarket")
                  and isinstance(call.get("args"), dict)]
        if len(writes) < 2 or not self._may_write(handle):
            return ret  # a lone write meets these same tests where it is dispatched
        # A tool the seat does not hold is refused where it is dispatched, as always;
        # it still holds back the writes beside it.
        allowed = self._allowed_tools(action_id)
        held = [(i, c) for i, c in writes if c["tool"] in allowed]
        stranger = next((i for i, c in writes if c["tool"] not in allowed), None)
        slot = (lambda i: f"tool:{i}") if tool_round == 0 else (
            lambda i: f"round{tool_round}:{i}")
        if stranger is not None:
            refusal, failing = (None, "unknown or disallowed tool"), stranger
        else:
            refusal, failing = None, None
            # Each venue weighs its own legs; the first leg either would refuse holds
            # back every write in the batch, on both venues.
            for legs, weigh in (
                    ([(i, c) for i, c in held if not str(c["tool"]).startswith("polymarket.")],
                     self.venue_batch_refusal),
                    ([(i, c) for i, c in held if str(c["tool"]).startswith("polymarket.")],
                     self._polymarket_batch_refusal)):
                found = weigh(action_id, handle,
                              [(slot(i), str(c["tool"]), c["args"]) for i, c in legs]
                              ) if legs else None
                if found is not None and (failing is None or legs[found[0]][0] < failing):
                    refusal, failing = found, legs[found[0]][0]
        if refusal is None:
            return ret
        reason = refusal[1]
        writes = held
        reason = (f"nothing in this batch was submitted: {ret.tool_calls[failing]['tool']} "
                  f"(call {failing}) would be refused: {reason}")
        self.venue_attempts[handle] = reason
        self._refuse_order(handle, reason, kind="order.batch_refused", index=failing)
        refused = {i for i, _ in writes}
        return replace(ret, tool_calls=tuple(
            {**call, "invalid": reason} if i in refused else call
            for i, call in enumerate(ret.tool_calls)))

    def _polymarket_batch_refusal(self, seat: str, handle: str,
                                  writes: list[tuple[str, str, dict]]):
        """The first Polymarket write of a batch that would be refused, and why, or None."""
        from factorylab.runtime.polymarket import batch_refusal

        return batch_refusal(self, seat, handle, writes)

    def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
                  slot: str = "tool:0") -> tuple[dict, int]:
        """Execute one tool call through metering. Returns (result, cost)."""
        self._ensure_connector_tool()
        tool_id = str(call.get("tool"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_id not in self.tool_specs or tool_id not in self._allowed_tools(action_id):
            return {"error": "unknown or disallowed tool"}, 0
        if tool_id == "connector.fetch":
            return self._fetch_connector(action_id, handle, args)
        if tool_id == "web.search":
            # A kernel call on the seat's own meter: one completion on the search
            # route, priced and held before it runs, booked to this seat's cost.
            from factorylab.runtime import websearch

            return websearch.run(self, action_id, handle, args)
        if tool_id == "artifact.list":
            result = self._artifact_page(action_id, args)
            self.ledger.append({"kind": "artifact.list", "handle": handle,
                                "assembly_id": action_id, "rows": len(result["items"]),
                                "count": result["count"], "ts": self.clock.now_ns})
            return result, 0
        if tool_id == "artifact.get":
            # Free by contract (C9) and scoped by contract (C1): a seat reads what it
            # wrote and a program's state within its own lineage.
            # Every read is ledgered, refusals included.
            result = self.artifacts.read(args.get("sha"), reader=action_id,
                                         lineage_of=self.budget.lineage)
            self.ledger.append({"kind": "artifact.get", "sha": str(args.get("sha"))[:64],
                                "handle": handle, "assembly_id": action_id,
                                "found": "error" not in result,
                                **({"reason": Reason.ARTIFACT_PRIVATE.value}
                                   if result.get("error") == PRIVATE_REFUSAL else {}),
                                "ts": self.clock.now_ns})
            return result, 0
        if tool_id == "outcome.get":
            # A kernel read of the seat's own inbox: no model call, priced like
            # artifact.get, and addressed — one seat cannot read another's outcomes.
            # ``outcome_id`` names one item exactly; ``handle`` is the fallback and
            # answers with the oldest unread item of that decision (R3-F).
            ident = args.get("outcome_id") if args.get("outcome_id") else args.get("handle")
            result = self.outcomes.get(action_id, ident, delivered=False)
            self.ledger.append({"kind": "outcome.get",
                                "handle": handle, "assembly_id": action_id,
                                "asked": str(ident)[:64],
                                "outcome_id": result.get("outcome_id"),
                                "found": "error" not in result, "ts": self.clock.now_ns})
            return result, 0
        if tool_id == "outcome.list":
            # The index of the same inbox: addresses and headers, never bodies, so
            # a seat can find the item worth spending a read on instead of guessing
            # a handle. Addressed by the authenticated caller exactly like
            # outcome.get - a seat cannot page another seat's queue by naming it -
            # and listing delivers nothing, so nothing here can be acknowledged
            # unread.
            def _bounded(value: Any, default: int, low: int, high: int) -> int:
                if type(value) is not int:
                    return default
                return max(low, min(value, high))

            result = self.outcomes.list(action_id,
                                        after=_bounded(args.get("after"), 0, 0, 2**31),
                                        limit=_bounded(args.get("limit"), 8, 1, 32))
            self.ledger.append({"kind": "outcome.list", "handle": handle,
                                "assembly_id": action_id,
                                "rows": len(result.get("items", ())),
                                "count": result.get("count"),
                                "after": result.get("after"), "ts": self.clock.now_ns})
            return result, 0
        if tool_id in self.CONSEQUENCE_WRITES and not self._may_write(handle):
            # No judge trades what it judges (essay II.III): the refusal is public.
            self.ledger.append({"kind": "tool.refused", "handle": handle,
                                "assembly_id": action_id, "tool": tool_id,
                                "reason": self.WRITE_REFUSAL, "ts": self.clock.now_ns})
            return {"error": self.WRITE_REFUSAL}, 0
        spec = self.tool_specs[tool_id]
        price = int(spec["price_micro_per_call"])

        def execute() -> dict:
            if spec["kind"] == "institution":
                from factorylab.world.venue_tools import _validate

                _validate(args, spec["args_schema"])
                section = args["section"]
                return {"section": section, "value": self.institution_section(section)}
            if spec["kind"] == "venue":
                from factorylab.world.venue_tools import _validate

                _validate(args, spec["args_schema"])
                if tool_id in ("venue.place_market", "venue.place_limit"):
                    reason = self._order_collateral(
                        handle, str(args.get("coin")), Decimal(str(args.get("size"))),
                        args.get("side") == "buy",
                        Decimal(str(args["price"])) if "price" in args else None,
                        reduce_only=args.get("reduce_only") is True,
                    )
                    if reason:
                        self.venue_attempts[handle] = reason
                        return {"status": "rejected", "error": reason}
                if tool_id in ("venue.place_market", "venue.place_limit", "venue.close",
                               "venue.cancel"):
                    result = self._venue_write(handle, tool_id, args, slot=slot)
                    if f"{handle}:{slot}" not in self.order_intents:
                        self.venue_attempts[handle] = str(result.get("error") or "refused")
                    return result
                if tool_id in VAULT_WRITES:
                    result = self._vault_write(handle, tool_id, args, slot=slot)
                    if f"{handle}:{slot}" not in self.vault_intents:
                        self.venue_attempts[handle] = str(result.get("error") or "refused")
                    return result
                if tool_id in VAULT_READS:
                    return self._vault_read(tool_id, args)
                return self.venue_tools.call(tool_id, args)
            if spec["kind"] == "catalogue":
                needle = str(args["substring"])
                limit = int(args.get("limit", 20))
                return {
                    "models": self._catalogue_search(needle, limit),
                    # The institutional catalogue, retrieved instead of carried:
                    # world.tools and world.proposal_shapes are one-line indexes,
                    # and this is where their full schemas are read when a seat
                    # actually means to call a tool or register something.
                    "tools": self._tool_schema_search(needle, limit),
                    "proposal_shapes": self._proposal_shape_search(needle),
                }
            if spec["kind"] == "market":
                return {
                    "sellers": self._discover_market(
                        url_substring=args.get("url_substring"),
                        query=args.get("query"),
                        limit=args.get("limit", 20),
                    )
                }
            if spec["kind"] == "treasury":
                direction = args.get("direction")
                usd = args.get("usd")
                intent = {
                    "direction": direction,
                    "usd": str(usd),
                    "reason": str(args.get("reason", ""))[:500],
                    "handle": handle,
                }
                self.ledger.append({"kind": "treasury.intent", **intent, "by": action_id,
                                    "ts": self.clock.now_ns})
                # The event every subscriber reads carries the handle, not the author's
                # seat (information audit C8; essay II.I.b, the author is private).
                self._emit(EventKind.TRANSFER_INTENT, intent, source="kernel")
                self.stats.transfer_intents += 1
                return self.treasury.transfer(
                    direction, usd, handle=handle, now_ns=self.clock.now_ns
                )
            if spec["kind"] == "polymarket":
                from factorylab.runtime import polymarket

                return polymarket.execute(self, action_id, handle, tool_id, args, slot)
            if spec["kind"] == "calc":
                # Deterministic arithmetic (R3-E): a pure function of its arguments,
                # no jail, no rail, no clock. It is metered and ledgered like any
                # other tool so its use is evidence in the diary, and at a price of
                # zero that costs the seat nothing but the record.
                from factorylab.cortex.calc import calc

                return calc(args)
            tool = self.population_tools.get(tool_id)
            if tool is None:
                return {"error": "tool unavailable"}
            return self.tool_runner.run(tool, args)

        try:
            metered = self._seat_meter(action_id).run(
                handle=handle,
                reason=f"tool:{tool_id}",
                ceiling=price,
                execute=execute,
                cost_of=lambda _r: price,
            )
        except BillingUncertain as exc:
            return {"error": str(exc)}, exc.cost
        except Exception as exc:  # reservation refused or execution known unbilled
            return {"error": f"{type(exc).__name__}: {exc}"[:200]}, 0
        if spec["kind"] == "venue":
            if tool_id in self.venue_tools.PUBLIC_READS:
                from factorylab.runtime.observations import record_venue_facts

                record_venue_facts(self.window, tool_id, args, metered.result, self.clock.now_ns)
            if hasattr(self.exchange, "drain_events"):
                self._settle_exchange_effects(self.exchange.drain_events())
        elif (spec["kind"] == "population"
              and getattr(self.ev, "producer_feedback", "verdict") == "realized"):
            self._record_tool_use(action_id, handle, tool_id, slot, metered.result, metered.cost)
        return metered.result, metered.cost

    def _record_tool_use(self, caller: str, handle: str, tool_id: str, slot: str,
                         result: Any, cost: int) -> None:
        """Address actual paid tool execution to maker and caller without publishing their data.

        A receipt establishes execution, version and provenance, never usefulness.
        Arguments and output bodies stay private; hashes bind the observed result.
        Same-lineage use remains explicitly distinguishable from independent use.
        """
        from factorylab.kernel.ledger import canonical
        from factorylab.settlement.receipts import execution_receipt

        tool = self.population_tools.get(tool_id)
        if tool is None:
            return
        maker = self.tool_owner.get(tool_id)
        caller_lineage = self.budget.lineage(caller)
        maker_lineage = self.budget.lineage(maker) if maker is not None else None
        relation = ("self" if caller == maker else "unknown" if maker is None
                    else "same_lineage" if caller_lineage == maker_lineage else "cross_lineage")
        facts = {
            "tool": tool_id, "maker": maker, "caller": caller,
            "maker_handle": tool.provenance, "caller_handle": handle, "slot": slot,
            "lineage_relation": relation,
            "version_sha256": hashlib.sha256(canonical({
                "code": tool.code, "args_schema": tool.args_schema,
                "timeout_s": tool.timeout_s})).hexdigest(),
            "result_sha256": hashlib.sha256(canonical(result)).hexdigest(),
            "status": "failed" if isinstance(result, dict) and result.get("error") else "executed",
            "cost_micro": cost,
        }
        for subject in sorted({handle, tool.provenance} - {"", None}):
            execution_receipt(self.consequences.receipts, kind="program_result",
                              handle=subject, owner=maker if subject == tool.provenance else caller,
                              at_event=self.n, facts=facts)

    def _invoke_compute(self, action_id: str, req: Request) -> Return:
        """Each model call is metered and counted; lifetime trials count settled consequences.

        The request is priced here, on its rendered prompt, before the seat's meter
        sees it. Routing admitted the seat on the ceiling it could see (its last
        recorded one plus the world block's growth since); when the rendered
        request is dearer than the seat's cover, the pool bridges the gap for this
        one call, ledgered, so a stale estimate never surfaces as a failed return.
        The seat's real ceiling and the world size it was priced at are recorded
        for the next routing decision. Compact-mode world sizing uses the same
        projection for this record and for future growth checks.
        """
        asm = self.assemblies[action_id]
        model_id = asm.spec.model_id
        reason = f"model:{model_id}"
        seat = self._liable_seat(req.handle) or action_id
        try:
            ceiling = asm.model.ceiling(asm.build_model_request(req))
        except Exception:  # an unrenderable request fails inside invoke, as before
            ceiling = None
        cover = self.budget.cover(seat, self._novelty_protection(req.handle, reason))
        if (ceiling is not None and cover > 0 and ceiling > cover
                and req.parent_handle is None and "continuation" not in req.inputs):
            backed = self.budget.bridge(seat, req.handle, ceiling - cover, "routing estimate")
            if backed:
                self.entitlement_bridges[req.handle] = backed
                cover += backed
        req = replace(req, cost_ceiling=min(
            req.cost_ceiling, max(0, self.wallet.available_for(req.handle, reason)), cover,
        ))
        try:
            ret = asm.invoke(req)
        finally:
            self.entitlement_bridges.pop(req.handle, None)
        if ceiling is not None:
            self.seat_ceilings[action_id] = {
                "ceiling": ceiling, "world_chars": self._world_chars(req.inputs.get("world")),
            }
        count = self.stats.invocations_by_assembly.get(action_id, 0) + 1
        self.ledger.append({"kind": "novelty.invocation", "assembly_id": action_id,
                            "handle": req.handle, "count": count})
        self.stats.invocations_by_assembly[action_id] = count
        return ret

    def _compact_world_context(self, world: dict[str, Any],
                               retrieved: dict[str, bytes]) -> dict[str, Any]:
        """Project one world exactly as a compact model invocation receives it."""
        if getattr(getattr(self.m, "prompt", None), "mode", "reference") != "compact":
            return world
        # A reference is disclosure only when this request can execute its read.
        # Zero is a valid tool-call limit; keep the exact context inline rather
        # than replacing it with an unreachable ``artifact.get`` address.
        if self.m.tools.max_tool_calls <= 0:
            return world
        update = world.get("world_update")
        if not isinstance(update, dict):
            return world

        compact = dict(update)
        charter = update.get("charter")
        if isinstance(charter, dict) and isinstance(charter.get("text"), str):
            charter = dict(charter)
            charter["text"] = _transient_snapshot(
                "world_update.charter.text", charter["text"], retrieved)
            compact["charter"] = charter

        observations = update.get("public_observations")
        mids = observations.get("recent_mids") if isinstance(observations, dict) else None
        has_history = isinstance(mids, dict) and any(
            isinstance(rows, (list, tuple)) and len(rows) > 1 for rows in mids.values()
        )
        if has_history:
            observations = dict(observations)
            observations["recent_mids"] = {
                coin: ([rows[-1]] if rows else [])
                if isinstance(rows, (list, tuple)) else rows
                for coin, rows in mids.items()
            }
            observations["full_history"] = _transient_snapshot(
                "world_update.public_observations", update["public_observations"], retrieved)
            compact["public_observations"] = observations
        return {**world, "world_update": compact}

    def _compact_invocation_context(self, req: Request,
                                    retrieved: dict[str, bytes]) -> Request:
        """Keep current operating facts inline and address exact public history.

        Only compact-mode requests with a runtime world are changed. The live
        charter edition, cards and pending changes stay inline while its full
        text is addressable as canonical bytes. Public observation history moves
        only when there is prior midpoint history to remove; the latest exact row
        for every market, freshness and closed-window values
        remain inline. The returned request owns the same immutable
        inputs for its first call and every continuation, while ``retrieved``
        survives for the whole invocation and nowhere else.
        """
        world = req.inputs.get("world")
        if not isinstance(world, dict):
            return req
        compact = self._compact_world_context(world, retrieved)
        if compact is world:
            return req
        return replace(req, inputs={**req.inputs, "world": compact})

    def _invoke(self, action_id: str, req: Request, role: str, *, child: bool = False) -> Return:
        from factorylab.runtime.propensity import effect_label

        self._ensure_connector_tool()
        body_mark = len(self.ledger.connector_bodies)
        self.handle_to_assembly[req.handle] = action_id
        assembly = self.assemblies[action_id]
        retrieved: dict[str, bytes] = {}  # same-handle only; never checkpointed or published
        # Programs receive the request directly on jailed stdin and cannot use a
        # model continuation's transient ``artifact.get`` map, so their inputs are
        # not compacted; only model assemblies receive same-handle snapshot
        # references. ``ProgramAssembly.build_stdin`` scopes the seats to their own.
        if isinstance(assembly, Assembly):
            req = self._compact_invocation_context(req, retrieved)
        prompt_cache = (_safe_prompt_cache_identity(assembly, req)
                        if isinstance(assembly, Assembly) else None)
        # Every request tells its executor who it is: an id is a public schematic,
        # and retirement, learner registration and requests are all keyed by it.
        # Nothing else about authorship travels; the judge of this return never
        # sees the name. The identity is stamped by the assembly that renders the
        # prompt (``Assembly.build_model_request``), so it is inside every ceiling
        # priced from this request and a parent cannot forge its child's.
        effects: list[str] = []  # venue and treasury writes, children: the action so far
        ret = self._invoke_compute(action_id, req)
        # The routing bridge buys only the routed call. Reads and children spend
        # the liable seat's remaining cover, never a fresh claim on the commons.
        seat = self._liable_seat(req.handle) or action_id
        cover = self.budget.cover(
            seat, self._novelty_protection(req.handle, f"model:{assembly.spec.model_id}"))
        req = replace(req, cost_ceiling=min(req.cost_ceiling, ret.cost + cover))
        dropped = list(ret.dropped)  # optional sections dropped while the answer stood
        self._check_compute_return(req.handle, ret)
        if (ret.status == "ok" and ret.outputs.get("status") == "cannot"
                and isinstance(ret.outputs.get("reason"), str)):
            ret = replace(ret, status="refused", children=(), tool_calls=())
        if ret.status == "ok" and req.scoring_channel != "policy":
            # The channel is the emitted kind of the contract this assembly declared.
            # A ballot is not a contract return: it binds no kind, so the queue's
            # policy channel alone says what the decision is.
            kinds = self.assemblies[action_id].spec.emits
            emitted = ret.outputs.get("emits", kinds[0] if len(kinds) == 1 else None)
            if emitted not in kinds:
                ret = replace(ret, status="malformed", outputs={"reason": "undeclared emits"},
                              children=(), tool_calls=())
            else:
                self.queue.bind(req.handle, emitted)
                self.return_kinds[req.handle] = emitted
        working_state_handled = False
        if (ret.status == "ok" and (ret.tool_calls or ret.children)
                and isinstance(ret.outputs, dict) and "working_state" in ret.outputs):
            working_state_handled = True
            if self._write_working_state(action_id, req.handle, ret.outputs):
                req = replace(req, inputs={**req.inputs,
                                          "your_state": self.working_state.render(action_id)})
        total_cost = ret.cost
        prior_results: list[dict] = []
        previous_results: list[dict] = []
        tool_round = 0
        # One decision reads, discovers and then acts inside its own wake. What
        # bounds it is not a round count but its own money: a further round is
        # bought only while what is left still covers the final answer, and only
        # while the last round learned something this decision had not been given.
        granted = True  # the first continuation always happens: results must be read
        outside_text = False
        answered: set[str] = set()  # lookups this decision has already been answered
        final_note = ("Return the final answer; this request's continuation has been "
                      "consumed. Further tool calls and requests are refused.")
        minimum_inputs = {**req.inputs, "continuation": final_note,
                          "context_notice": "Tool bodies were not loaded: insufficient budget."}
        minimum_answer = req.continuation(inputs=minimum_inputs, cost_ceiling=req.cost_ceiling)
        answer_reserve = self._call_reserve(assembly, minimum_answer) or 0
        while (not self.wallet.dead and ret.status == "ok" and (ret.tool_calls or ret.children)
               and granted):
            if req.cost_ceiling - total_cost < answer_reserve:
                self.ledger.append({"kind": "tool.rounds_exhausted", "handle": req.handle,
                                    "assembly_id": action_id, "round": tool_round,
                                    "reason": "answer unaffordable before dispatch",
                                    "remaining": max(0, req.cost_ceiling - total_cost),
                                    "reserve": answer_reserve, "ts": self.clock.now_ns})
                ret = Return(req.handle, {
                             "reason": "remaining budget cannot cover a tool-result answer"},
                             0, "failed")
                break
            results = []
            tool_cost = 0
            learned = False  # a lookup this decision had not already been given
            acted = False  # a write or a child: this decision has taken its action
            delivered_reads = []
            ret = self._weigh_venue_batch(action_id, req.handle, ret, tool_round)
            for index, call in enumerate(ret.tool_calls):
                if self.wallet.dead:
                    break
                if call.get("invalid"):
                    # Refused by validation, never dispatched: its error answers in
                    # its own slot so the next round can correct it.
                    results.append({"tool": call.get("tool"), "args": call.get("args"),
                                    "result": {"error": call["invalid"]}})
                    learned = True
                    continue
                price = self._tool_price_bound(call)
                # A slot is a client identity: it names the round as well as the
                # position, so two rounds of one decision cannot collide on one
                # order id and an intended second write is never read as a repeat.
                slot = f"tool:{index}" if tool_round == 0 else f"round{tool_round}:{index}"
                if price > max(0, req.cost_ceiling - total_cost - tool_cost - answer_reserve):
                    result, cost = {"error": "request cost ceiling exhausted"}, 0
                    dispatched = False
                    if call["tool"] == "connector.fetch":
                        self._connector_refused(req.handle, result["error"])
                    elif call["tool"] == "web.search":
                        self.ledger.append({
                            "kind": "web.refused", "handle": req.handle,
                            "assembly_id": action_id, "reason": result["error"],
                            "ts": self.clock.now_ns})
                else:
                    sha = call.get("args", {}).get("sha")
                    if call["tool"] == "artifact.get" and isinstance(sha, str) and sha in retrieved:
                        data = retrieved[sha]
                        result, cost = {"sha": sha, "kind": "retrieval.result", "bytes": len(data),
                                        "text": data.decode("utf-8"),
                                        "expires": "end of this decision"}, 0
                        delivered_reads.append(json.loads(data))
                        self.ledger.append({"kind": "artifact.get", "sha": sha,
                                            "handle": req.handle, "assembly_id": action_id,
                                            "found": True, "scope": "invocation",
                                            "ts": self.clock.now_ns})
                    else:
                        result, cost = self._run_tool(action_id, req.handle, call, slot=slot)
                    dispatched = True
                tool_cost += cost
                # A venue write the venue has not yet acknowledged is its own outcome:
                # the intent is durable and the reconciler finalises it under the
                # same client id, so it is neither a success nor a failure here.
                uncertain = (isinstance(result, dict) and result.get("status") == "uncertain"
                             and call["tool"] in self.CONSEQUENCE_WRITES)
                # An acknowledged venue result carries ``error: None``; only a stated
                # error is a failure.
                ok = uncertain or not (isinstance(result, dict)
                                       and result.get("error") is not None)
                # Catalogue model names can also come from a remote provider. Treat
                # any model-bearing result as outside text; schema-only discovery
                # retains the ordinary continuation. A round earned by outside text
                # runs jailed tools only, so a seat can read and then compose within
                # the same wake instead of spending another decision on it.
                if ok and (call["tool"] in self.OUTSIDE_TEXT_TOOLS
                           or (call["tool"] == "catalogue.search"
                               and isinstance(result, dict) and result.get("models"))):
                    outside_text = True
                # What a lookup returns arrives after the answer that asked for it,
                # so without a further round a seat can learn a contract, an index
                # or an outcome and never be able to use it. A lookup this decision
                # has already been answered buys nothing: repeating it returns the
                # same bytes, and a wake cannot be extended by asking twice. A read
                # of moving state is not refused by that rule, it simply does not
                # extend the wake a second time on the same question.
                signature = _call_signature(call)
                read_only = self._read_only_call(str(call["tool"]))
                if ok and signature not in answered and read_only:
                    learned = True
                answered.add(signature)
                if dispatched and not read_only:
                    # Anything that is not a known read ends the retrieval, whether or
                    # not it names an action: population
                    # code and an unrecognised tool all stop the wake at one round of
                    # doing, so nothing executed here can be executed again below.
                    acted = True
                self.stats.tool_calls += 1
                if not ok:
                    self.stats.tool_call_failures += 1
                # Parser arguments can contain a connector body. They are transient.
                visible = call.get("args")
                # A continuation's arguments are redacted once text from outside has
                # entered this wake, because from then on an argument can carry
                # fetched bytes. A retrieval that never left this world keeps its
                # arguments on the ledger, so a chain of reads stays auditable.
                logged_args = ("[connector continuation]" if outside_text and tool_round else
                               json.dumps(self.ledger.without_connector_bodies(visible),
                                          default=str)[:1000])
                self.ledger.append({
                    "kind": "tool.call", "handle": req.handle, "assembly_id": action_id,
                    "tool": call.get("tool"), "args": logged_args,
                    "ok": ok, "outcome": "uncertain" if uncertain else "ok" if ok else "failed",
                    **({"client_id": f"{req.handle}:{slot}"} if uncertain else {}),
                    "cost": cost, "ts": self.clock.now_ns,
                })
                self.window.tool_calls += 1
                results.append({"tool": call.get("tool"), "args": call.get("args"),
                                "result": result})
                if call["tool"] == "outcome.get":
                    delivered_reads.append(results[-1])
                # A write the venue accepted, or has not yet acknowledged, is an
                # action this return took; a rejected or refused one is not.
                label = effect_label(str(call.get("tool")), call.get("args"))
                if label is not None and ok and (
                        not isinstance(result, dict) or result.get("status") != "rejected"):
                    effects.append(label)
                    acted = True
            for item in ret.children:
                if self.wallet.dead:
                    break
                result, cost = self._invoke_child(
                    action_id, req, item,
                    max(0, req.cost_ceiling - total_cost - tool_cost - answer_reserve)
                )
                tool_cost += cost
                results.append(result)
                acted = True
                if "error" not in result["result"] and result["result"].get("status") != "failed":
                    effects.append(f"request:{item.target}"[:64])
            # The round just run travels in full under tool_results. Recent exact
            # results from older rounds form a bounded working set; older or large
            # results travel as references. Every result is also put in the
            # invocation's private retrieval map, so affordability can replace the
            # whole working set with references without losing exact evidence.
            prior_results.extend(previous_results)
            seen_results, referenced_seen_results = _bounded_result_history(
                prior_results, retrieved, self.RECENT_RESULT_BYTES)
            previous_results = results
            remaining = max(0, req.cost_ceiling - total_cost - tool_cost)
            # The continuation is the same request, and it is the billed call
            # that produces the final verdict — so everything the first call was
            # shown, the PROPENSITY block included, rides along unchanged.
            note = final_note
            follow_inputs = {**req.inputs, "tool_results": results,
                             "seen_tool_results": seen_results, "continuation": note}
            follow = req.continuation(inputs=follow_inputs, cost_ceiling=remaining)
            final_quote = self._call_reserve(assembly, follow)
            if final_quote is not None and final_quote > remaining:
                # Result size is unknown before dispatch. Keep it exact but unloaded
                # when its body would consume the answer's budget.
                follow_inputs = {**follow_inputs,
                                 "seen_tool_results": referenced_seen_results,
                                 "tool_results": [_compacted_result(entry, retrieved)
                                                  for entry in results],
                                 "context_notice": "Tool bodies were not loaded because their "
                                 "input cost exceeds the remaining decision budget."}
                delivered_reads = []
                follow = req.continuation(inputs=follow_inputs, cost_ceiling=remaining)
                compact_quote = self._call_reserve(assembly, follow)
                if compact_quote is not None and compact_quote > remaining:
                    follow_inputs = minimum_inputs
                    follow = req.continuation(inputs=follow_inputs, cost_ceiling=remaining)
                learned = False  # do not buy another read after withholding its body
            # A decision acts once: a write or a child ends the retrieval and the
            # next call is the answer. Nothing executed in one wake can therefore
            # be executed again in a later round of the same wake.
            granted = (not self.wallet.dead and learned and not acted
                       and tool_round + 1 < self.MAX_TOOL_ROUNDS)
            if granted:
                # Reading on is bought only while the answer is still affordable
                # afterwards. The reserve is the model's own ceiling for this
                # request, counted twice: the round asked for, and the final answer
                # that must follow it. A decision that cannot cover both answers now.
                reserve = self._call_reserve(assembly, follow)
                if reserve is None or remaining < 2 * reserve:
                    granted = False
                    self.ledger.append({"kind": "tool.rounds_exhausted", "handle": req.handle,
                                        "assembly_id": action_id, "round": tool_round + 1,
                                        "reason": "final answer reserved",
                                        "priced": reserve is not None,
                                        "remaining": remaining, "reserve": reserve,
                                        "ts": self.clock.now_ns})
            if granted:
                note = (
                    "You may call population tools again to parse what you retrieved, then "
                    "return the final answer. Requests are refused."
                    if outside_text else
                    "You may call tools again to use what you retrieved, then return the "
                    "final answer. Requests are refused."
                )
                follow = req.continuation(inputs={**follow_inputs, "continuation": note},
                                          cost_ceiling=remaining)
            if isinstance(assembly, Assembly):
                # The invocation's usage is the final provider call's usage. Keep
                # the diagnostic identity aligned with that same continuation.
                prompt_cache = _safe_prompt_cache_identity(assembly, follow)
            ret = (Return(req.handle, {"reason": "wallet exhausted"}, 0, "failed")
                   if self.wallet.dead else self._invoke_compute(action_id, follow))
            if ret.status != "failed":
                for entry in delivered_reads:
                    body = entry.get("result")
                    if entry.get("tool") == "outcome.get" and isinstance(body, dict):
                        self.outcomes.get(action_id, body.get("outcome_id"))
            dropped.extend(ret.dropped)
            total_cost += tool_cost + ret.cost
            self._check_compute_return(req.handle, ret)
            tool_round += 1
            if (ret.status == "ok" and ret.outputs.get("status") == "cannot"
                    and isinstance(ret.outputs.get("reason"), str)):
                ret = replace(ret, status="refused", children=(), tool_calls=())
            has_continuation = bool(ret.tool_calls or ret.children)
            if ret.children:
                self.ledger.append({"kind": "requests.refused", "handle": req.handle,
                                    "reason": "continuation already consumed"})
                ret = replace(ret, children=())
            state_changed = False
            working_state_handled = False
            if (ret.status == "ok" and has_continuation
                    and isinstance(ret.outputs, dict) and "working_state" in ret.outputs):
                working_state_handled = True
                if self._write_working_state(action_id, req.handle, ret.outputs):
                    req = replace(req, inputs={**req.inputs,
                                              "your_state": self.working_state.render(action_id)})
                    state_changed = True
            if state_changed:
                # The new private head is part of the next paid prompt. Reprice
                # the minimum answer before another tool dispatch can spend from
                # the same decision's remaining cover.
                minimum_inputs = {**req.inputs, "continuation": final_note,
                                  "context_notice": "Tool bodies were not loaded: "
                                                    "insufficient budget."}
                minimum_answer = req.continuation(
                    inputs=minimum_inputs, cost_ceiling=req.cost_ceiling)
                answer_reserve = self._call_reserve(assembly, minimum_answer) or 0
            # The extra round composes the retrieved text through ordinary jailed
            # tools. Text from outside keeps that narrow: fetched or searched bytes
            # cannot reach the venue, the treasury or a transport inside the same
            # wake that read them. A schema this world answered with itself is not
            # outside text, so a seat that looked a capability up may call the
            # capability it looked up, which is the whole point of looking.
            if (granted and ret.tool_calls and outside_text
                    and any(self.tool_specs.get(c["tool"], {}).get("kind") not in self.PARSE_KINDS
                            for c in ret.tool_calls)):
                granted = False
            if ret.tool_calls and not granted:
                self.ledger.append({"kind": "tool.calls_ignored", "handle": req.handle,
                                    "reason": "continuation already consumed",
                                    "ts": self.clock.now_ns})
            if not ret.tool_calls or not granted:
                from factorylab.cortex.assembly import validate_schema

                if ret.status == "ok":
                    try:
                        validate_schema(ret.outputs, req.outcome_schema)
                        self._validate_output_contract(ret.outputs, req)
                    except (ValueError, TypeError, RecursionError):
                        ret = replace(ret, status="malformed",
                                      outputs={"reason": "incomplete continuation answer"})
                break
        if self.ledger.without_connector_bodies(ret.outputs) != ret.outputs:
            # A body cannot become durable output. Refuse rather than rewriting an
            # action (for example, a short response that happens to equal its side).
            ret = replace(ret, status="malformed",
                          outputs={"reason": "connector body in durable output"})
        ret = replace(ret, cost=total_cost, tool_calls=(), children=())
        # Handle-scoped model memory is another durable surface: retain parsed outcomes only.
        for assembly in self.assemblies.values():
            assembly.memory = self.ledger.without_connector_bodies(assembly.memory)
        self.stats.invocations += 1
        self.stats.invocation_status[ret.status] = (
            self.stats.invocation_status.get(ret.status, 0) + 1
        )
        self.stats.invocations_by_role[role] = self.stats.invocations_by_role.get(role, 0) + 1
        sr = ret.stop_reason or "none"
        self.stats.stop_reasons[sr] = self.stats.stop_reasons.get(sr, 0) + 1
        self.ledger.append(
            {
                "kind": "invocation",
                "assembly_id": action_id,
                "role": role,
                "handle": req.handle,
                "cost": ret.cost,
                "status": ret.status,
                "stop_reason": sr,
                "served_by": ret.served_by,
                "finish_reason": ret.provider.get("finish_reason"),
                "usage": {
                    key: ret.provider.get(key)
                    for key in ("input_tokens", "output_tokens", "reasoning_tokens", "max_tokens")
                    # A provider that never reports its cache leaves the key out
                    # rather than claiming a miss; the shape stays additive, so a
                    # ledger written before this key resumes and reads unchanged.
                } | ({"cached_tokens": cached}
                     if type(cached := ret.provider.get("cached_tokens")) is int else {}),
                "outputs": json.dumps(ret.outputs, default=str)[:4000],
                # Rendered bytes per prompt section (edition 3, C4). Moving the
                # institutional catalogue behind catalogue.search is a claim about
                # bytes; the claim is recorded beside the bill it is supposed to
                # move, so the change is measured rather than assumed.
                "sections": replace(
                    req, inputs={**req.inputs, "you": action_id}).section_bytes(),
                **({"prompt_cache": prompt_cache} if prompt_cache is not None else {}),
                "ts": self.clock.now_ns,
            }
        )
        self._record_spend(action_id, ret.cost)
        fault = _provider_fault(ret)
        if fault is not None:
            # The vendor charged for tokens nobody can read. The bill stands (the
            # wallet mirrors what the provider took) and the diary names the fault
            # so a reader sees weather, not a mute assembly.
            self.ledger.append({
                "kind": "provider.fault", "assembly_id": action_id, "handle": req.handle,
                "served_by": ret.served_by, "reason": fault, "cost": ret.cost,
                "reasoning_tokens": ret.provider.get("reasoning_tokens"),
                "max_tokens": ret.provider.get("max_tokens"), "ts": self.clock.now_ns,
            })
        self.window.invocations += 1
        if ret.status == "ok":
            self.window.ok += 1
            if role == "producer":
                self.window.costs.append(ret.cost)
        self._record_declared_propensity(action_id, req, ret, role, effects=tuple(effects))
        self._apply_continuity(
            action_id, req.handle, ret, working_state_handled=working_state_handled)
        ret = replace(ret, dropped=tuple(dropped))
        if dropped and ret.status == "ok":
            self._report_dropped_sections(action_id, req.handle, ret.dropped)
        elif ret.status == "malformed" and isinstance(ret.outputs.get("validation_error"), str):
            self._report_dropped_sections(
                action_id, req.handle, tuple(ret.outputs.get("rejected_sections", ())),
                error=ret.outputs["validation_error"])
        del self.ledger.connector_bodies[body_mark:]
        return ret

    def _report_dropped_sections(self, seat: str, handle: str,
                                 dropped: tuple[dict[str, Any], ...], *,
                                 error: str | None = None) -> None:
        """Tell a seat which optional sections of its answer were dropped, and why.

        A failed answer retains its rejection reasons without admitting its effects.
        The diary and owning seat's inbox distinguish rejection from partial success.
        """
        items = [dict(d) for d in dropped]
        kind = "return.validation_failed" if error is not None else "return.sections_dropped"
        detail = {"reason": error} if error is not None else {}
        # Keep the first exact section fault discoverable without fetching the
        # whole outcome. The full list remains authoritative in the owned body.
        rejection = ({"rejected_section": items[0]["section"],
                      "rejection_reason": items[0]["reason"]} if items else {})
        self.ledger.append({"kind": kind, "assembly_id": seat,
                            "handle": handle, "dropped": items, **detail,
                            "ts": self.clock.now_ns})
        if seat in self.assemblies:
            self.outcomes.append(
                seat, handle=handle,
                outcome={"kind": ("return_rejected" if error is not None
                                  else "return_sections_dropped"),
                         "status": "malformed" if error is not None else "partial",
                         "dropped": items, **detail, **rejection},
                delta_micro=0,
                evidence={"kind": kind, "handle": handle,
                          "ts": self.clock.now_ns})

    def _apply_continuity(self, action_id: str, handle: str, ret: Return, *,
                          working_state_handled: bool = False) -> None:
        """Advance the seat's own head and inbox cursor from its answer (C1).

        Every answer of every shape passes here — producer, verdict, meta, child,
        program — so a seat's state and its cursor are the seat's own business and
        not a privilege of one return kind. Nothing here can fail the return: a
        state the archive refuses is ledgered and the head is left exactly as it
        was, and an ``ack_through`` naming no item of this seat's inbox does
        nothing at all.
        """
        if action_id not in self.assemblies or not isinstance(ret.outputs, dict):
            return
        if ret.status == "refused" and ret.outputs.get("status") == "cannot":
            # Paid work a seat declined (R3-F). Judge and meta commissions are not
            # covered by defer or the cadence floor — they are somebody else's
            # request arriving — so the only way to decline one is to answer
            # ``cannot``. It costs the call and nothing else, and it is a decision
            # the population can read, not a malformed return.
            self.ledger.append({"kind": "commission.declined", "assembly_id": action_id,
                                "handle": handle,
                                "reason": str(ret.outputs.get("reason"))[:200],
                                "ts": self.clock.now_ns})
        if self.assemblies[action_id].spec.model_id == "program":
            # R3-F: a program has no model to read an inbox, so what it produced
            # and what it cost reaches the lineage that put it in the world.
            self._deliver_program_result_to_inbox(action_id, handle, ret)
        self.outcomes.record_said(action_id, handle, ret.outputs)
        if not working_state_handled:
            self._write_working_state(action_id, handle, ret.outputs)
        if "ack_through" in ret.outputs:
            self.outcomes.ack_through(action_id, ret.outputs["ack_through"])

    def _write_working_state(self, action_id: str, handle: str, outputs: Any) -> bool:
        """Commit one accepted private head, or leave the prior head unchanged.

        This is shared by intermediate tool-call returns and the final continuity
        pass. It applies no inbox acknowledgement, said-record update, or program
        delivery, so committing memory between paid calls cannot duplicate any
        other return effect. A refusal is ledgered by the existing contract and
        returns false so the next prompt continues from the previous head.
        """
        if (action_id not in self.assemblies or not isinstance(outputs, dict)
                or "working_state" not in outputs):
            return False
        state = outputs["working_state"]
        if self.ledger.without_connector_bodies(state) != state:
            self.ledger.append({"kind": "state.refused", "assembly_id": action_id,
                                "handle": handle,
                                "reason": "connector body in working_state",
                                "ts": self.clock.now_ns})
            return False
        try:
            self.working_state.put(action_id, state, handle=handle)
        except ValueError as exc:
            self.ledger.append({"kind": "state.refused", "assembly_id": action_id,
                                "handle": handle, "reason": str(exc)[:200],
                                "ts": self.clock.now_ns})
            return False
        return True

    # --- the deciding agent's propensity rides on the request -------------------

    def _assembly_learner_id(self, assembly_id: str) -> str:
        """One durable learning identity per assembly, distinct from any router's."""
        return f"assembly:{assembly_id}"

    def _learner_policy(self, assembly_id: str) -> dict[str, float] | None:
        """An assembly's own learner's current policy, read from a detached copy.

        Guarantees nothing it returns can disturb a round that is waiting for its
        reward, and ``None`` when the assembly has no learner or its state cannot be
        read.
        """
        from factorylab.learners.base import restore_learner

        learner = self.assembly_learners.get(assembly_id)
        if learner is None:
            return None
        try:
            detached = restore_learner(learner.inner.state())
            return detached.distribution(tuple(detached.actions))
        except (ValueError, RuntimeError, TypeError, ArithmeticError):
            return None

    def _action_policy(self, assembly_id: str) -> dict[str, Any] | None:
        """One draw from an assembly's own learner, private to that assembly.

        Guarantees the seat is shown a sample and its probability, ``{recommended,
        p}``, and never the distribution (Chapter II rulings R4, information audit
        P3): a seat handed a distribution that then picks by judgement declares a
        behaviour policy it did not follow. The draw comes from the runtime's own
        reproducible stream. Local state stays local (essay II.I.b): this is the one
        learner whose rounds this assembly's own decisions opened.
        """
        policy = self._learner_policy(assembly_id)
        actions = tuple(a for a in (policy or {}) if policy[a] > 0)
        if not actions:
            return None
        recommended = self.rng.choices(actions, weights=[policy[a] for a in actions], k=1)[0]
        return {"recommended": recommended, "p": round(policy[recommended], 6),
                "note": "drawn by the kernel from your registered learner's current policy; "
                        "p is its probability there"}

    def _followed_recommendation(self, action_id: str, req: Request, taken: tuple[str, ...],
                                 state_hash: str) -> PropensityRecord | None:
        """The learner's own record when the seat took the action its learner drew, or None.

        Guarantees that a seat that did what its learner recommended is recorded at
        the learner's probability, over the learner's whole policy, so the round it
        opens is on-policy (R4: "if the seat obeys, record the learner's p"). The
        policy must still be the one the draw was disclosed from; when it is not,
        or the seat did something else, ``None``, and the seat's own declaration
        stands.
        """
        shown = req.inputs.get("your_action_policy") if isinstance(req.inputs, dict) else None
        if not isinstance(shown, dict) or shown.get("recommended") not in taken:
            return None
        recommended = shown["recommended"]
        policy = self._learner_policy(action_id)
        if policy is None or round(policy.get(recommended, 0.0), 6) != shown.get("p"):
            self.ledger.append({"kind": "propensity.recommendation_stale",
                                "handle": req.handle, "assembly_id": action_id,
                                "recommended": recommended, "ts": self.clock.now_ns})
            return None
        actions = tuple(a for a in policy if policy[a] > 0)
        total = math.fsum(policy[a] for a in actions)
        try:
            record = PropensityRecord(
                actions, tuple(policy[a] / total for a in actions), recommended, 0,
                self._assembly_learner_id(action_id), state_hash, source="declared")
        except (ValueError, TypeError):
            return None
        self.ledger.append({"kind": "propensity.learner", "handle": req.handle,
                            "assembly_id": action_id, "recommended": recommended,
                            "p": policy[recommended], "ts": self.clock.now_ns})
        return record

    def _refusal_to_owner(self, handle: str, kind: str, reason: str, **extra: Any) -> None:
        """Address one refusal to the inbox of the seat whose decision it was, and to no one else.

        Guarantees the reason reaches only the decision's owner, under its handle,
        through the stateful queue (essay II.I.b: reward "must find its way back to
        the exact decision"); a refusal with no owner is ledgered undeliverable by
        the inbox. Nothing is broadcast (information audit C5).
        """
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        self.outcomes.append(owner, handle=handle,
                             outcome={"kind": kind, "status": "rejected", "reason": reason,
                                      **extra},
                             delta_micro=0,
                             evidence={"kind": kind, "handle": handle,
                                       "ts": self.clock.now_ns})

    def _action_policy_input(self, assembly_id: str) -> dict[str, Any]:
        """``your_action_policy`` as a request input, or nothing when there is no learner.

        Guarantees the key is absent rather than null for an assembly without a
        registered learner (information audit U5): a null slot is a standing hint.
        """
        policy = self._action_policy(assembly_id)
        return {} if policy is None else {"your_action_policy": policy}

    def _record_declared_propensity(self, action_id: str, req: Request, ret: Return, role: str,
                                    *, effects: tuple[str, ...] = ()):
        """Log the woken assembly's own distribution as a second propensity on the handle.

        The action is named from what the return executed (``effects``: its venue
        and treasury writes and the children it requested) and then from its final
        answer, so a trade made through a tool is never learned as ``hold``.
        Absent or unusable, the declaration is recorded degenerate: the action
        taken at 1.0. A declaration that starves the action taken of mass is
        floored, and stays. The reason either way goes back to the population,
        because a refusal nobody can read is repeated.
        """
        from factorylab.learners.base import state_bytes
        from factorylab.runtime.propensity import action_class, action_label, declared_record

        try:
            self.queue.get(req.handle)
        except KeyError:
            return None
        label = action_label("producer" if role == "child" else role,
                             ret.outputs, ret.status, effects)
        # Edition 3, C2: the coarse verb beside the finer label, so investigation,
        # construction, governance and sleep are not all learned as "hold".
        taken_class = action_class(label, ret.outputs, tool_calls=len(ret.tool_calls))
        if taken_class != label:
            self.ledger.append({"kind": "action.classified", "handle": req.handle,
                                "assembly_id": action_id, "label": label,
                                "action": taken_class, "ts": self.clock.now_ns})
        learner = self.assembly_learners.get(action_id)
        state_hash = (
            hashlib.sha256(state_bytes(learner.state())).hexdigest()
            if learner is not None else "declared"
        )
        # R4: a seat that took the action its learner drew is recorded at the
        # learner's probability; otherwise its own declaration stands.
        record, reason = self._followed_recommendation(
            action_id, req, (label, taken_class), state_hash), None
        if record is None:
            declared = ret.outputs.get("propensity") if isinstance(ret.outputs, dict) else None
            record, reason = declared_record(
                label, declared,
                learner_id=self._assembly_learner_id(action_id), state_hash=state_hash,
                taken_class=taken_class,
            )
        try:
            self.queue.record_propensity(req.handle, record)
        except (KeyError, ValueError):
            return None
        if reason is not None:
            # A refused declaration is degenerate (the one action taken); a floored
            # one keeps the support the return declared.
            floored = len(record.action_ids) > 1
            self.ledger.append({"kind": "propensity.floored" if floored else "propensity.refused",
                                "handle": req.handle, "reason": reason, "ts": self.clock.now_ns})
            self._refusal_to_owner(req.handle, "propensity_floored" if floored
                                   else "propensity_refused", reason)
        self._open_assembly_round(action_id, req.handle, record)
        return record

    def _open_assembly_round(self, action_id: str, handle: str, record) -> None:
        """Freeze the assembly learner's own round against the policy the agent declared.

        The learner proposes; the agent decides. So the learner's round is scored
        off-policy: its frozen distribution is the target, the declared propensity
        is the behaviour, and the reward that eventually settles this handle trains
        it through the importance ratio. A declaration outside the registered
        action set trains nothing, and says so.
        """
        learner = self.assembly_learners.get(action_id)
        if learner is None or record.source != "declared":
            return
        support = tuple(a for a, p in zip(record.action_ids, record.probs, strict=True) if p > 0)
        universe = set(getattr(learner.inner, "actions", ()))
        if not set(support) <= universe:
            self.ledger.append({
                "kind": "propensity.unlearned", "handle": handle, "assembly_id": action_id,
                "reason": "declared actions outside the registered action set",
                "ts": self.clock.now_ns,
            })
            return
        executed = {a: p for a, p in zip(record.action_ids, record.probs, strict=True) if p > 0}
        total = sum(executed.values())
        executed = {a: p / total for a, p in executed.items()}
        try:
            learner.distribution_for(handle, support)
            learner.record_executed(handle, executed)
        except (KeyError, ValueError, RuntimeError, TypeError) as exc:
            self.ledger.append({"kind": "propensity.unlearned", "handle": handle,
                                "assembly_id": action_id, "reason": str(exc)[:200],
                                "ts": self.clock.now_ns})
            return
        self.assembly_rounds[handle] = action_id

    def _invoke_child(
        self, action_id: str, parent: Request, item: ChildRequest, ceiling: int,
    ) -> tuple[dict, int]:
        """A bounded child retains its own decision and spends only its parent's remaining cap."""
        target = action_id if item.target == "self" else item.target
        depth = 0
        cursor = parent.handle
        while self.queue.get(cursor).parent_handle is not None:
            depth += 1
            cursor = self.queue.get(cursor).parent_handle
        if depth >= self.m.tools.max_depth:
            reason = "tools.max_depth reached"
            self.ledger.append({"kind": "requests.refused", "handle": parent.handle,
                                "reason": reason, "depth": depth})
            return {"tool": f"assembly:{target}", "args": item.inputs,
                    "result": {"error": reason}}, 0
        # A child spends its parent's money: whatever the parent's remaining request
        # ceiling says, the ceiling never exceeds what the parent's own decision may
        # spend now, so a fresh target cannot be bought compute the parent lacks.
        ceiling = min(ceiling, max(0, self._compute_available(parent.handle)))
        # The child is opened under the learner that woke its parent, so its
        # returns have an addressable home. Its propensity is the parent's choice,
        # recorded as such ("parent-selected"): no router sampled it, so no router
        # is trained on it (defect 3, ``FeedbackMixin._router_sampled``).
        actor = self.queue.get(parent.handle).actor
        channels = self._return_channels(target) if target in self.assemblies else {}
        handle = self.queue.open(
            actor=actor, event_id=f"child-{parent.handle}",
            propensity=PropensityRecord((target,), (1.,), target, 0, actor, "parent-selected"),
            channel=next(iter(channels.values()), CH_VERDICT), deadline_ns=parent.deadline_ns,
            parent_handle=parent.handle, cost_ceiling=ceiling,
            return_channels=channels,
        )
        self.ledger.append({"kind": "request.child", "handle": handle, "target": target,
                            "resource_liability": parent.handle, "cost_ceiling": ceiling,
                            "description": item.description, "inputs": item.inputs,
                            "outcome_schema": item.outcome_schema})
        self.stats.decisions += 1
        self.consequences.start(handle, self.n)
        grounded_contract = None
        if (
            getattr(self.ev, "producer_feedback", "verdict") == "realized"
            and CH_VERDICT in channels.values()
            and target in self.assemblies
            and target not in self.retired_assemblies
        ):
            grounded_contract = freeze_contract(self, handle, target, {})
        req = Request(handle, item.description, {**item.inputs, "world": self._world_block()},
                      {}, item.outcome_schema,
                      parent.deadline_ns, ceiling, parent.handle,
                      "a JSON object satisfying the outcome schema", CH_VERDICT, parent.handle)
        if target in self.assemblies and target not in self.retired_assemblies:
            self.handle_to_assembly[handle] = target
            ret = self._invoke(target, req, "child", child=True)
        else:
            ret = Return(handle, {"reason": "target assembly unavailable"}, 0, "failed")
            self.ledger.append({"kind": "request.failed", "handle": handle,
                                "reason": "target assembly unavailable"})
        emitted = self.return_kinds.get(handle, next(iter(channels), "ProducerReturn"))
        if len(channels) > 1 and handle not in self.return_kinds:
            from factorylab.kernel.queue import SettleStatus

            self.consequences.finish(handle, ret.cost)
            self.queue.settle(handle, channel=self.queue.get(handle).channel, score=0.0,
                              status=SettleStatus.CENSORED,
                              definition_version="unselected-return-v1", sampling_ref=None)
            return {"tool": f"assembly:{target}", "args": item.inputs,
                    "result": {"outputs": public_return(ret.outputs), "status": ret.status,
                               "cost_micro": ret.cost}}, ret.cost
        if emitted in ("Verdict", "MetaVerdict"):
            sample = Sample((target,), (1.,), target, 0, actor, "parent-selected", ())
            event = Event(f"child-input-{handle}", EventKind.REGISTERED,
                          self.clock.now_ns, item.inputs, "request")
            step = self._evaluator_step if emitted == "Verdict" else self._meta_step
            step(event, handle, sample, parent.deadline_ns, returned=ret)
        else:
            if target in self.assemblies:
                if self._may_write(handle):
                    self._execute_outputs(ret)
                self._apply_registrations(handle, ret)
            self.consequences.finish(handle, ret.cost)
            if emitted == "Exposure":
                self.pending_exposure[handle] = self.ticks_consumed
                payoff = ret.outputs.get("payoff") if ret.status == "ok" else None
                if payoff is not None and self.consequences.seal_self_forecast(
                        self.book, self.queue, handle=handle, assembly_id=target, payoff=payoff,
                        event=self.n, now_ns=self.clock.now_ns,
                        tick_ns=self.tick_clock.interval_ns) is not None:
                    self.stats.forecasts_sealed += 1
            else:
                if (grounded_contract is not None
                        and self.queue.get(handle).channel == CH_VERDICT):
                    self.grounded_pending[handle] = grounded_contract.with_outputs(
                        public_return(ret.outputs), subject_kind=emitted)
                    contract = self.grounded_pending[handle]
                    self.ledger.append({
                        "kind": "consequence.contract", "handle": handle,
                        "opened_tick": contract.opened_tick, "due_tick": contract.due_tick,
                        "close_tick": contract.close_tick,
                        "charter_edition": contract.charter_edition,
                        "predicate_versions": list(contract.predicate_versions),
                        "ts": self.clock.now_ns,
                    })
                self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n,
                                                        opened_at_tick=self.ticks_consumed)
            self.stats.producer_returns += 1
            payload = {"about_handle": handle, "description": item.description,
                       # A parent may hand its child the text of a message to send.
                       # The judge that prices the child sees the task, not the body.
                       "inputs": public_child_inputs(item.inputs),
                       "outputs": public_return(ret.outputs),
                       "cost": ret.cost, "status": ret.status,
                       "propensity": self._public_propensity(handle)}
            self._emit("ProducerReturn" if emitted == "Exposure" else emitted, payload)
            if emitted == "Exposure" and self.routers.get("Exposure"):
                self._emit("Exposure", payload)
        return {"tool": f"assembly:{target}", "args": item.inputs,
                "result": {"outputs": public_return(ret.outputs), "status": ret.status,
                           "cost_micro": ret.cost}}, ret.cost

    def _check_compute_return(self, handle: str, ret: Return) -> None:
        """Assembly-wrapped affordability failures join the enclosing event's insolvency count."""
        reason = str(ret.outputs.get("reason", ""))
        if ret.status == "failed" and (
            reason == "ceiling exceeds request cost_ceiling"
            or reason.startswith(("InsufficientReserve:", "infeasible:"))
        ):
            self._compute_failure(handle)

    def _request(
        self,
        handle: str,
        description: str,
        inputs: dict[str, Any],
        schema: dict[str, Any],
        deadline: int,
        channel: str,
        propensity: dict[str, Any] | None = None,
    ) -> Request:
        """A request about someone else's decision carries that decision's propensity."""
        declared = chosen = None
        if isinstance(propensity, dict) and isinstance(propensity.get("over"), dict):
            declared, chosen = propensity["over"], propensity.get("chosen")
        return Request(
            handle=handle,
            description=description,
            inputs=inputs,
            capability_versions={},
            outcome_schema=schema,
            deadline_ns=deadline,
            cost_ceiling=max(0, self._compute_available(handle)),
            parent_handle=None,
            completion_criterion="a JSON object satisfying the outcome schema",
            scoring_channel=channel,
            resource_liability=handle,
            propensity=declared,
            propensity_chosen=chosen if isinstance(chosen, str) else None,
        )

    def _public_propensity(self, handle: str) -> dict[str, Any] | None:
        """The deciding agent's propensity as it travels forward on the next request."""
        from factorylab.runtime.propensity import as_public

        try:
            record = self.queue.declared_propensity(handle)
        except KeyError:
            return None
        return None if record is None else as_public(record)
