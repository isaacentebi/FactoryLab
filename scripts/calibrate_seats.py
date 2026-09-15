"""Calibrate candidate models against the contracts a seat actually runs (edition 2, C13).

Before the nine seats of the funded world are chosen, each candidate model is run
through the repository's own request path: every scenario is a rendered ``Request``
built by the runtime's schematics, invoked through ``Runtime._invoke`` so tool calls,
continuations and child requests execute exactly as they would in a living world,
and every cost is the metered one the wallet was charged. The exchange is always the
scripted one and the treasury the fake one: no production effect can follow a reply.

Offline by default: the scripted provider answers every scenario, so the harness runs
with zero spend. ``--paid --budget-usd N`` runs the same scenarios against the real
provider under a hard cap: no call starts whose ceiling exceeds what is left of the
budget, and the run stops once it is spent. Credentials come only from the CLI's own
``_load_dotenv``; this script never opens a key file.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from factorylab.charter.provenance import roster_hash
from factorylab.cortex.assembly import AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.money import usd_to_micro
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import build_provider
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_CONFORMITY, CH_FAST, CH_VERDICT
from factorylab.runtime.worlds import ExchangeSpec, WorldManifest, load_manifest
from factorylab.world.metering import Infeasible
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.scripted import (
    ScriptedProvider,
    _description_from_prompt,
    _inputs_from_prompt,
)

DEADLINE_NS = 10**15
ACTOR = "calibration"
SCENARIOS = ("produce", "judge", "meta", "tool", "fail", "continuation", "long_context")
ROLE_SCENARIOS = {"producer": "produce", "evaluator": "judge", "meta": "meta"}
#: Prompt bytes the long-context scenario renders to: a mature world whose registered
#: tool catalogue has grown, roughly 2.5 times the seed world's block.
LONG_CONTEXT_BYTES = 160_000

TOOL_DESCRIPTION = (
    "List the venue's markets. Call venue.instruments, then report how many perp markets "
    "the venue lists and name up to three of them."
)
FAIL_DESCRIPTION = (
    "Report the venue fill id and the settlement time of the order named in inputs."
)
HELPER_DESCRIPTION = "Count the perp markets in world.trading_markets and return the count."
COLUMNS = ("candidate", "trees", "completion", "well_formed", "task_met", "refusal_correct",
           "cost_p50_micro", "cost_p95_micro", "latency_p50_ms", "cached_share",
           "tick_cost_micro")


# --- offline provider ------------------------------------------------------------------


class CalibrationProvider(ScriptedProvider):
    """The scripted provider, extended for the three calibration-only contracts.

    Produce, judge and meta requests are answered by ``ScriptedProvider`` unchanged.
    The tool task, the delegation task and the failing task have no scripted-world
    counterpart, so this subclass answers them the way a capable seat would: call
    the tool then answer from its result; request the helper then answer from its
    return; decline the impossible schema with ``status: cannot``.
    """

    name: str = "scripted-calibration"

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        desc = _description_from_prompt(text)
        inputs = _inputs_from_prompt(text)
        results = inputs.get("tool_results")
        reply: dict[str, Any] | None = None
        if desc == TOOL_DESCRIPTION:
            if results is None:
                reply = {"tool_calls": [{"tool": "venue.instruments", "args": {}}]}
            else:
                perps = (results[0].get("result") or {}).get("perp") or []
                reply = {"perp_count": len(perps),
                         "examples": [str(p.get("coin")) for p in perps[:3]] or ["none"]}
        elif desc.startswith("Delegate this task."):
            if results is None:
                reply = {"requests": [{
                    "target": inputs.get("helper", "self"), "description": HELPER_DESCRIPTION,
                    "inputs": {}, "outcome_schema": ANSWER_SCHEMA}]}
            else:
                outputs = (results[0].get("result") or {}).get("outputs") or {}
                reply = {"answer": outputs.get("answer", 0)}
        elif desc == HELPER_DESCRIPTION:
            perps = ((inputs.get("world") or {}).get("trading_markets") or {}).get("perp") or []
            reply = {"answer": len(perps)}
        elif desc == FAIL_DESCRIPTION:
            reply = {"status": "cannot",
                     "reason": "no order with that client id was placed in this world"}
        if reply is None:
            return super().complete(req)
        return ModelResponse(req.model_id, json.dumps(reply), self.input_tokens,
                             self.output_tokens, "end_turn")


class MalformedProvider:
    """A candidate that never returns a JSON object; the harness must score it zero."""

    name = "malformed"

    def complete(self, req: ModelRequest) -> ModelResponse:
        return ModelResponse(req.model_id, "I would rather not answer in JSON.", 300, 12,
                             "end_turn")


# --- the budget cap ----------------------------------------------------------------------


class BudgetExhausted(Infeasible):
    """The remaining budget cannot cover this call's ceiling; the call did not start."""


@dataclass
class BudgetGuard:
    """A hard cap on metered spend, checked before every model call by its ceiling.

    Guarantees no completion starts whose ceiling exceeds what remains, and that
    ``spent`` is what the meter committed — reconciled against the wallet after
    every decision tree, so tool prices and child calls count too.
    """

    budget_micro: int
    spent_micro: int = 0
    refusals: int = 0

    @property
    def remaining_micro(self) -> int:
        return max(0, self.budget_micro - self.spent_micro)

    def admit(self, ceiling: int) -> None:
        if ceiling > self.remaining_micro:
            self.refusals += 1
            raise BudgetExhausted(
                f"budget: ceiling {ceiling} exceeds remaining {self.remaining_micro} micro-USD")

    def charge(self, cost: int) -> None:
        self.spent_micro += cost

    def reconcile(self, wallet_spent_micro: int) -> None:
        """The wallet is the meter; whatever it debited is what the budget lost."""
        self.spent_micro = max(self.spent_micro, wallet_spent_micro)


@dataclass
class GuardedModel:
    """Wraps a seat's metered model so the guard sees every ceiling before the meter does."""

    inner: Any
    guard: BudgetGuard

    def ceiling(self, req: ModelRequest) -> int:
        return self.inner.ceiling(req)

    def complete(self, req: ModelRequest, *, handle: str):
        self.guard.admit(self.ceiling(req))
        metered = self.inner.complete(req, handle=handle)
        self.guard.charge(metered.cost)
        return metered

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


# --- scenarios ---------------------------------------------------------------------------

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object", "properties": {"answer": {"type": "integer", "minimum": 0}},
    "required": ["answer"],
}
LISTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"perp_count": {"type": "integer", "minimum": 0},
                   "examples": {"type": "array", "items": {"type": "string"},
                                "minItems": 1, "maxItems": 3}},
    "required": ["perp_count", "examples"],
}
FILL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"fill_id": {"type": "string"}, "settled_at_ns": {"type": "integer"}},
    "required": ["fill_id", "settled_at_ns"],
}
# Each calibration seat with a custom contract declares its own event kind: a named
# event keeps one public schema across the world.
CUSTOM_KINDS = {"tool": ("CalibrationListing", LISTING_SCHEMA),
                "fail": ("CalibrationFill", FILL_SCHEMA),
                "parent": ("CalibrationAnswer", ANSWER_SCHEMA),
                "helper": ("CalibrationAnswer", ANSWER_SCHEMA)}


@dataclass(frozen=True)
class Scenario:
    name: str
    seat: str  # which of the candidate's seats runs it
    role: str  # the runtime role label passed to _invoke
    channel: str
    expected: str  # "ok" or "refused"
    request: Request
    check: str | None = None  # "tool" | "child": evidence the tree must show


def seat_id(candidate: str, kind: str) -> str:
    return f"cal:{candidate}:{kind}"


def calibration_manifest(manifest: WorldManifest) -> WorldManifest:
    """The same menu and seats on the scripted exchange: no order can reach a venue."""
    if manifest.exchange.kind == "fake":
        return manifest
    ex = manifest.exchange
    return replace(manifest, exchange=ExchangeSpec(
        kind="fake", coins=ex.coins, spot_pairs=ex.spot_pairs, seed=ex.seed,
        start_cash_usd=ex.start_cash_usd))


def build_runtime(manifest: WorldManifest, provider: Any, *, seed: int) -> Runtime:
    return Runtime(calibration_manifest(manifest), events=1, seed=seed,
                   initial_balance_micro=None, ledger_path=None, drip=False,
                   router_gamma=0.1, provider=provider)


def _seed_spec(rt: Runtime, role: str) -> AssemblySpec | None:
    """The manifest's own seat of a role: its prompt, budget and effort are the calibration's."""
    for asm in rt.assemblies.values():
        if asm.spec.role == role and not asm.spec.id.startswith("cal:"):
            return asm.spec
    return None


def install_seats(rt: Runtime, candidate: str, guard: BudgetGuard | None) -> dict[str, str]:
    """Register one seat per scenario for a candidate, cloned from the manifest's seats."""
    seats: dict[str, str] = {}
    for role in ("producer", "evaluator", "meta"):
        base = _seed_spec(rt, role)
        kwargs = ({"max_tokens": base.max_tokens, "effort": base.effort,
                   "system_prompt": base.system_prompt, "memory_policy": base.memory_policy}
                  if base is not None else {})
        sid = seat_id(candidate, role)
        rt._instantiate(AssemblySpec(id=sid, version=1, model_id=candidate, role=role, **kwargs))
        seats[role] = sid
    producer = rt.assemblies[seats["producer"]].spec
    for kind, (event_kind, schema) in CUSTOM_KINDS.items():
        sid = seat_id(candidate, kind)
        rt._instantiate(AssemblySpec(
            id=sid, version=1, model_id=candidate, role="producer", emits=(event_kind,),
            schemas={event_kind: schema}, max_tokens=producer.max_tokens,
            effort=producer.effort, system_prompt=producer.system_prompt))
        seats[kind] = sid
    if guard is not None:
        for sid in seats.values():
            asm = rt.assemblies[sid]
            asm.model = GuardedModel(asm.model, guard)
    return seats


def open_handle(rt: Runtime, seat: str, channel: str, tag: str) -> str:
    return rt.queue.open(
        actor=ACTOR, event_id=f"calibration:{tag}",
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, ACTOR, ACTOR),
        channel=channel, deadline_ns=DEADLINE_NS, parent_handle=None,
        cost_ceiling=max(0, rt.wallet.available))


def _tick_payload(rt: Runtime, index: int) -> dict[str, Any]:
    acct = rt.exchange.account()
    return {
        "index": index,
        "account": {"equity_usd": str(acct.equity_usd),
                    "positions": [{"coin": p.coin, "size": str(p.size),
                                   "entry_px": str(p.entry_px)} for p in acct.positions]},
        "mids": {c: str(m) for c, m in rt.exchange.mids().items()},
    }


def _subject_return() -> dict[str, Any]:
    return {
        "description": "Respond to event Tick on clock.",
        "inputs": {"kind": "Tick", "payload": {"index": 3}},
        "outputs": {"action": "hold",
                    "rationale": "Mids moved less than the spread since the last tick."},
        "cost_micro_usd": 1800, "status": "ok",
        "propensity": {"over": {"hold": 0.7, "order": 0.3}, "chosen": "hold"},
    }


def _judge_schema(rt: Runtime) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "number", "minimum": 0, "maximum": 1},
            "payoff": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
            "propensity": {"type": "object"},
            "forecasts": rt._forecast_schema(),
            "register": rt._register_schema(),
            "about_handle": {"type": "string"},
        },
        "required": ["verdict", "payoff", "rationale", "forecasts"],
    }


def _judge_description(rt: Runtime) -> str:
    return (
        "Evaluate a producer return. Give two numbers: verdict = its quality against "
        "the charter (0 to 1); payoff = your probability that return_paid_off, the "
        "kernel's consequence predicate, resolves true for the return. Then give "
        f"{rt.ev.max_forecasts_per_verdict} forecasts: for each, a predicate from the "
        "list and q = your probability it happens within its horizon."
    )


def padded_world(rt: Runtime, seat: str, req: Request, target_bytes: int) -> Request:
    """Grow the stable world block until the rendered prompt reaches ``target_bytes``.

    The padding is registered-tool material of the shape the runtime publishes, in
    the stable prefix, so a provider that caches an identical prefix is exercised
    the way a mature world would exercise it.
    """
    asm = rt.assemblies[seat]

    def rendered(r: Request) -> int:
        return len(asm.build_model_request(r).messages[-1]["content"].encode())

    base = rendered(req)
    if base >= target_bytes:
        return req

    def pad_tool(i: int) -> dict[str, Any]:
        return {"id": f"population-tool-{i:04d}",
                "description": (f"Registered population tool {i}: derives one named "
                                "statistic from a coin series over a window of samples."),
                "args_schema": {"type": "object", "properties": {
                    "coin": {"type": "string"}, "window": {"type": "integer", "minimum": 1}},
                    "required": ["coin", "window"], "additionalProperties": False},
                "price_micro_per_call": rt.m.tools.population_tool_micro_per_call,
                "kind": "population"}

    world = dict(req.inputs["world"])
    tools = list(world.get("tools", []))
    world["tools"] = tools + [pad_tool(0)]
    one = rendered(replace(req, inputs={**req.inputs, "world": world})) - base
    count = max(1, -(-(target_bytes - base) // max(1, one)))
    world["tools"] = tools + [pad_tool(i) for i in range(count)]
    return replace(req, inputs={**req.inputs, "world": world})


def build_scenarios(rt: Runtime, candidate: str, seats: dict[str, str], *, sample: int,
                    long_context_bytes: int) -> list[Scenario]:
    """Every scenario is a real rendered ``Request``: what the seat would actually see."""
    tag = f"{candidate}:{sample}"
    world = rt._world_block()
    out: list[Scenario] = []

    producer = seats["producer"]
    handle = open_handle(rt, producer, CH_VERDICT, f"{tag}:produce")
    produce_inputs = {
        "kind": "Tick", "payload": _tick_payload(rt, sample + 1), "world": world,
        "your_recent_returns": list(rt.memory.get(producer, ())),
        "your_action_policy": rt._action_policy(producer),
    }
    produce_schema = {"type": "object", "properties": {
        "action": {"type": "string"}, "propensity": {"type": "object"},
        "register": rt._register_schema()}, "required": ["action"]}
    produce = rt._request(handle, "Respond to event Tick on clock.", produce_inputs,
                          produce_schema, DEADLINE_NS, CH_VERDICT)
    out.append(Scenario("produce", producer, "producer", CH_VERDICT, "ok", produce))

    judge = seats["evaluator"]
    handle = open_handle(rt, judge, CH_CONFORMITY, f"{tag}:judge")
    subject = _subject_return()
    judge_inputs = {
        "producer": subject,
        "charter": rt._charter_text(),
        "predicates": [{"predicate": p.id, "description": p.description,
                        "params": list(p.param_schema)} for p in rt.predicates.all()],
        "forecast_example": {"predicate": "wallet_up",
                             "params": {"horizon_events": rt.ev.forecast_horizon_events},
                             "q": 0.4},
        "world": world,
        "your_recent_returns": list(rt.memory.get(judge, ())),
        "your_consequence_standing": rt._standing_for(judge),
        "your_action_policy": rt._action_policy(judge),
    }
    verdict_req = rt._request(handle, _judge_description(rt), judge_inputs, _judge_schema(rt),
                              DEADLINE_NS, CH_CONFORMITY, propensity=subject["propensity"])
    out.append(Scenario("judge", judge, "evaluator", CH_CONFORMITY, "ok", verdict_req))

    meta = seats["meta"]
    handle = open_handle(rt, meta, CH_FAST, f"{tag}:meta")
    verdict = {"verdict": 0.6, "payoff": 0.35,
               "rationale": "A hold with no claimed payoff; consistent with the charter's "
                            "care with scarce resources.",
               "propensity": {"over": {"0.6": 0.5, "0.3": 0.3, "0.9": 0.2}, "chosen": "0.6"}}
    meta_inputs = {"verdict": verdict, "producer_outputs": subject["outputs"],
                   "charter": rt._charter_text(), "world": world,
                   "your_action_policy": rt._action_policy(meta)}
    meta_schema = {"type": "object", "properties": {
        "conformity": {"type": "number"}, "rationale": {"type": "string"},
        "propensity": {"type": "object"}, "register": rt._register_schema(),
        "about_handle": {"type": "string"}}, "required": ["conformity"]}
    meta_req = rt._request(
        handle, "Assess the released representative verdict for conformity with the charter, "
        "using its window as context.", meta_inputs, meta_schema, DEADLINE_NS, CH_FAST,
        propensity=verdict["propensity"])
    out.append(Scenario("meta", meta, "meta", CH_FAST, "ok", meta_req))

    tool = seats["tool"]
    handle = open_handle(rt, tool, CH_VERDICT, f"{tag}:tool")
    tool_req = rt._request(handle, TOOL_DESCRIPTION, {"world": world}, LISTING_SCHEMA,
                           DEADLINE_NS, CH_VERDICT)
    out.append(Scenario("tool", tool, "producer", CH_VERDICT, "ok", tool_req, check="tool"))

    fail = seats["fail"]
    handle = open_handle(rt, fail, CH_VERDICT, f"{tag}:fail")
    fail_req = rt._request(
        handle, FAIL_DESCRIPTION,
        {"order": {"client_id": "calibration-unplaced",
                   "note": "This client id was never sent to the venue."}, "world": world},
        FILL_SCHEMA, DEADLINE_NS, CH_VERDICT)
    out.append(Scenario("fail", fail, "producer", CH_VERDICT, "refused", fail_req))

    parent = seats["parent"]
    handle = open_handle(rt, parent, CH_VERDICT, f"{tag}:continuation")
    helper = seats["helper"]
    parent_req = rt._request(
        handle,
        f"Delegate this task. Issue exactly one request to target {helper!r} asking it to "
        "count the perp markets in world.trading_markets. Its return arrives in "
        "tool_results on your second call; then answer with its count as answer.",
        {"helper": helper, "world": world}, ANSWER_SCHEMA, DEADLINE_NS, CH_VERDICT)
    out.append(Scenario("continuation", parent, "producer", CH_VERDICT, "ok", parent_req,
                        check="child"))

    handle = open_handle(rt, producer, CH_VERDICT, f"{tag}:long_context")
    long_req = padded_world(rt, producer, replace(produce, handle=handle),
                            long_context_bytes)
    out.append(Scenario("long_context", producer, "producer", CH_VERDICT, "ok", long_req))
    return out


# --- running ---------------------------------------------------------------------------


def run_tree(rt: Runtime, scenario: Scenario, guard: BudgetGuard | None) -> dict[str, Any]:
    """Invoke one whole decision tree and read its evidence back from the ledger."""
    mark = rt.ledger.append({"kind": "calibration.mark", "scenario": scenario.name,
                             "seat": scenario.seat})
    before = rt.wallet.balance
    started = time.perf_counter()
    ret = rt._invoke(scenario.seat, scenario.request, scenario.role)
    latency_ms = (time.perf_counter() - started) * 1000
    wallet_spent = before - rt.wallet.balance
    if guard is not None:
        guard.reconcile(rt.initial - rt.wallet.balance)
    items = [i for i in rt.ledger.items() if i["seq"] > mark]
    invocations = [i for i in items if i["kind"] == "invocation"]
    tool_calls = [i for i in items if i["kind"] == "tool.call"]
    children = [i for i in items if i["kind"] == "request.child"]
    input_tokens = sum(i["usage"].get("input_tokens") or 0 for i in invocations)
    cached_reported = [i["usage"]["cached_tokens"] for i in invocations
                       if type(i["usage"].get("cached_tokens")) is int]
    reason = str(ret.outputs.get("reason", "")) if ret.status != "ok" else ""
    evidence = {"tool": any(t.get("tool") == "venue.instruments" and t.get("ok")
                            for t in tool_calls),
                "child": bool(children)}
    task_met = ret.status == scenario.expected and (
        scenario.check is None or evidence[scenario.check])
    return {
        "scenario": scenario.name, "seat": scenario.seat, "handle": scenario.request.handle,
        "status": ret.status, "expected": scenario.expected,
        "completed": ret.status != "failed",
        "well_formed": ret.status in ("ok", "refused"),
        "task_met": task_met,
        "budget_refused": reason.startswith("infeasible: budget"),
        "reason": reason[:200] or None,
        "cost_micro": ret.cost, "wallet_delta_micro": wallet_spent,
        "latency_ms": round(latency_ms, 1),
        "calls": len(invocations), "tool_calls": len(tool_calls), "children": len(children),
        "input_tokens": input_tokens,
        "output_tokens": sum(i["usage"].get("output_tokens") or 0 for i in invocations),
        "cached_tokens": sum(cached_reported) if cached_reported else None,
        "served_by": sorted({i["served_by"] for i in invocations if i.get("served_by")}),
        "finish_reasons": sorted({str(i.get("finish_reason")) for i in invocations}),
        "prompt_bytes": len(rt.assemblies[scenario.seat].build_model_request(
            scenario.request).messages[-1]["content"].encode()),
    }


def _percentile(values: list[int | float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    return float(statistics.quantiles(ordered, n=100, method="inclusive")[int(q * 100) - 1])


def summarise(candidate: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-candidate rates and cost quantiles over every decision tree it ran."""
    ran = [r for r in rows if not r["budget_refused"]]
    costs = [r["cost_micro"] for r in ran if r["completed"]]
    latencies = [r["latency_ms"] for r in ran if r["completed"]]
    fails = [r for r in ran if r["scenario"] == "fail"]
    reported = [r for r in ran if r["cached_tokens"] is not None]
    cached_share = (sum(r["cached_tokens"] for r in reported)
                    / max(1, sum(r["input_tokens"] for r in reported))) if reported else None
    by_scenario: dict[str, dict[str, Any]] = {}
    for name in SCENARIOS:
        group = [r for r in ran if r["scenario"] == name]
        done = [r for r in group if r["completed"]]
        by_scenario[name] = {
            "trees": len(group),
            "completion": _rate(group, "completed"),
            "well_formed": _rate(group, "well_formed"),
            "task_met": _rate(group, "task_met"),
            "cost_p50_micro": _percentile([r["cost_micro"] for r in done], 0.5),
            "cost_p95_micro": _percentile([r["cost_micro"] for r in done], 0.95),
            "latency_p50_ms": _percentile([r["latency_ms"] for r in done], 0.5),
            "prompt_bytes_max": max((r["prompt_bytes"] for r in group), default=None),
        }
    tick = [by_scenario[ROLE_SCENARIOS[role]]["cost_p50_micro"]
            for role in ("producer", "evaluator", "meta")]
    return {
        "candidate": candidate,
        "trees": len(ran),
        "budget_refused_trees": len(rows) - len(ran),
        "completion": _rate(ran, "completed"),
        "well_formed": _rate(ran, "well_formed"),
        "task_met": _rate(ran, "task_met"),
        "refusal_correct": _rate(fails, "task_met"),
        "cost_p50_micro": _percentile(costs, 0.5),
        "cost_p95_micro": _percentile(costs, 0.95),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "cached_share": None if cached_share is None else round(cached_share, 4),
        "cached_reported_trees": len(reported),
        # One full tick on a roster made of this candidate alone: the producer's
        # decision on the Tick, the judge's verdict on that return, the meta's
        # assessment of that verdict, each at this candidate's p50.
        "tick_cost_micro": None if any(t is None for t in tick) else int(sum(tick)),
        "served_by": sorted({s for r in ran for s in r["served_by"]}),
        "scenarios": by_scenario,
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    return None if not rows else round(sum(1 for r in rows if r[key]) / len(rows), 4)


def manifest_roster_tick(manifest: WorldManifest, summaries: dict[str, dict[str, Any]],
                         ) -> dict[str, Any]:
    """One full tick priced with the manifest's own seat models, where they were calibrated.

    A Tick wakes one producer seat, its return one evaluator seat, that verdict one
    meta seat; each role is priced at the p50 of the calibrated model the manifest
    seats first in that role. A role whose model was not calibrated leaves the number
    null rather than estimated.
    """
    parts: dict[str, Any] = {}
    for role in ("producer", "evaluator", "meta"):
        seat = next((a for a in manifest.assemblies if a.role == role), None)
        summary = summaries.get(seat.model_id) if seat is not None else None
        p50 = (summary["scenarios"][ROLE_SCENARIOS[role]]["cost_p50_micro"]
               if summary is not None else None)
        parts[role] = {"seat": seat.id if seat else None,
                       "model": seat.model_id if seat else None,
                       "cost_p50_micro": p50}
    total = [p["cost_p50_micro"] for p in parts.values()]
    return {"parts": parts,
            "tick_cost_micro": None if any(t is None for t in total) else int(sum(total))}


def planned_ceiling(rt: Runtime, scenarios: list[Scenario]) -> int:
    """The sum of first-call ceilings: an upper bound on what one pass could reserve."""
    return sum(rt.assemblies[s.seat].model.ceiling(
        rt.assemblies[s.seat].build_model_request(s.request)) for s in scenarios)


def calibrate(manifest: WorldManifest, candidates: list[str], *, provider: Any,
              repeats: int, seed: int, budget_micro: int | None, long_context_bytes: int,
              log=None) -> dict[str, Any]:
    """Run every scenario ``repeats`` times per candidate and return the report."""
    menu = {m.id for m in manifest.models}
    unknown = [c for c in candidates if c not in menu]
    if unknown:
        raise ValueError(f"candidates not on the manifest's model menu: {unknown}")
    guard = BudgetGuard(budget_micro) if budget_micro is not None else None
    rt = build_runtime(manifest, provider, seed=seed)
    rows: dict[str, list[dict[str, Any]]] = {c: [] for c in candidates}
    seats = {c: install_seats(rt, c, guard) for c in candidates}
    ceilings: dict[str, int] = {}
    stopped = None
    for sample in range(repeats):
        for candidate in candidates:
            scenarios = build_scenarios(rt, candidate, seats[candidate], sample=sample,
                                        long_context_bytes=long_context_bytes)
            ceilings[candidate] = planned_ceiling(rt, scenarios)
            for scenario in scenarios:
                if guard is not None and guard.remaining_micro == 0:
                    stopped = stopped or f"budget spent before {candidate}:{scenario.name}"
                    break
                row = run_tree(rt, scenario, guard)
                rows[candidate].append(row)
                if log is not None:
                    log(json.dumps({"candidate": candidate, **{
                        k: row[k] for k in ("scenario", "status", "task_met", "cost_micro",
                                            "latency_ms", "calls", "reason")}}))
    summaries = {c: summarise(c, rows[c]) for c in candidates}
    return {
        "manifest": {"name": manifest.name, "manifest_sha256": manifest.manifest_hash(),
                     "roster_sha256": roster_hash(manifest),
                     "menu": sorted(menu)},
        "provider": getattr(provider, "name", type(provider).__name__),
        "offline": isinstance(provider, ScriptedProvider) or provider is None,
        "scripted_exchange": True, "production_effects": False,
        "repeats": repeats, "seed": seed, "long_context_bytes": long_context_bytes,
        "budget": None if guard is None else {
            "budget_micro": guard.budget_micro, "spent_micro": guard.spent_micro,
            "remaining_micro": guard.remaining_micro, "refused_calls": guard.refusals,
            "stopped": stopped},
        "planned_first_call_ceiling_micro": ceilings,
        "wallet_spent_micro": rt.initial - rt.wallet.balance,
        "tick_cost": {
            "definition": "one producer decision on a Tick, one evaluator verdict on it, "
                          "one meta assessment of that verdict; each at the p50 metered "
                          "cost of its whole decision tree",
            "manifest_roster": manifest_roster_tick(manifest, summaries),
            "per_candidate": {c: summaries[c]["tick_cost_micro"] for c in candidates},
        },
        "columns": list(COLUMNS),
        "candidates": summaries,
        "trees": rows,
    }


# --- output ------------------------------------------------------------------------------


RATE_COLUMNS = frozenset({"completion", "well_formed", "task_met", "refusal_correct",
                          "cached_share"})


def _cell(key: str, value: Any) -> str:
    if value is None:
        return "n/a"
    if key in RATE_COLUMNS:
        return f"{value:.0%}"
    if isinstance(value, float):
        return f"{value:.1f}" if key.startswith("latency") else f"{value:.0f}"
    return str(value)


def markdown_table(report: dict[str, Any]) -> str:
    header = ("| candidate | trees | completion | well-formed | task met | refusal ok | "
              "cost p50 µ$ | cost p95 µ$ | latency p50 ms | cached share | tick µ$ |")
    lines = [header, "|" + "---|" * 11]
    for summary in report["candidates"].values():
        lines.append("| " + " | ".join(_cell(k, summary[k]) for k in COLUMNS) + " |")
    roster = report["tick_cost"]["manifest_roster"]["tick_cost_micro"]
    lines.append("")
    lines.append(f"Manifest roster tick: {_cell('tick_cost_micro', roster)} µ$ "
                 "(producer + evaluator + meta seats at their calibrated p50).")
    budget = report["budget"]
    if budget is not None:
        lines.append(f"Budget: spent {budget['spent_micro']} of {budget['budget_micro']} µ$; "
                     f"{budget['refused_calls']} call(s) refused by the cap"
                     + (f"; {budget['stopped']}" if budget["stopped"] else "") + ".")
    return "\n".join(lines)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--world", required=True, help="manifest: model menu, prices, seats")
    parser.add_argument("candidates", nargs="*", help="candidate model ids from the menu")
    parser.add_argument("--all-menu", action="store_true", help="every model on the menu")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true", default=True,
                      help="scripted provider, zero spend (default)")
    mode.add_argument("--paid", action="store_true", help="the real provider under a hard cap")
    parser.add_argument("--budget-usd", type=Decimal, help="hard cap on metered spend")
    parser.add_argument("--repeats", type=int, default=2, help="samples per scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--long-context-bytes", type=int, default=LONG_CONTEXT_BYTES)
    parser.add_argument("--out", type=Path, required=True, help="JSON report path")
    args = parser.parse_args(argv)
    if args.paid and args.budget_usd is None:
        parser.error("--paid requires --budget-usd")
    if args.budget_usd is not None and args.budget_usd <= 0:
        parser.error("--budget-usd must be positive")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if not args.candidates and not args.all_menu:
        parser.error("name candidate model ids or pass --all-menu")
    return args


def main(argv=None, *, provider: Any = None) -> int:
    args = parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    manifest = load_manifest(args.world)
    candidates = ([m.id for m in manifest.models] if args.all_menu
                  else list(dict.fromkeys(args.candidates)))
    budget_micro = (None if args.budget_usd is None
                    else usd_to_micro(args.budget_usd, rounding="floor"))
    if args.paid:
        if provider is None:
            _load_dotenv()
            provider = build_provider(manifest)
        if provider is None:
            raise RuntimeError("--paid needs a manifest whose menu names a real provider")
        print(f"paid calibration: hard cap {budget_micro} micro-USD; scripted exchange; "
              "no production effect can follow a reply", file=sys.stderr)
    elif provider is None:
        provider = CalibrationProvider()
    report = calibrate(
        manifest, candidates, provider=provider, repeats=args.repeats, seed=args.seed,
        budget_micro=budget_micro, long_context_bytes=args.long_context_bytes,
        log=lambda line: print(line, file=sys.stderr, flush=True))
    report["mode"] = "paid" if args.paid else "offline"
    report["world"] = str(args.world)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(markdown_table(report))
    print(f"\nJSON: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
