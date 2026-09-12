"""Runtime compute method group."""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import ChildRequest, Request, Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.shared import CH_VERDICT, _to_plain
from factorylab.runtime.summary import _price_str
from factorylab.settlement import SEED_VOCABULARY
from factorylab.world.market import X402MeteredModel
from factorylab.world.metering import BillingUncertain, Metered, MeteredModel
from factorylab.world.models import ModelRequest, ModelResponse, TokenPrice


def _publishable(policy: dict[str, float]) -> dict[str, float]:
    """Return a readable copy of a distribution that is still a distribution.

    Guarantees the result sums to one within ``PROPENSITY_TOLERANCE``, so an
    agent that copies a published policy verbatim into its return declares
    something the same validator accepts. Rounding alone does not: three equal
    thirds rounded independently sum to 0.999999.
    """
    rounded = {action: round(p, 6) for action, p in policy.items()}
    if not rounded:
        return rounded
    top = max(rounded, key=lambda action: (rounded[action], action))
    adjusted = round(rounded[top] + (1.0 - math.fsum(rounded.values())), 6)
    if not 0.0 <= adjusted <= 1.0:
        return dict(policy)  # full precision rather than a rounding that left the simplex
    rounded[top] = adjusted
    return rounded


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


class ComputeMixin:
    """Preserve runtime state and behavior for compute operations."""

    def _instantiate(self, spec: AssemblySpec) -> Assembly:
        model = _ObservedMeteredModel(
            self.provider, self.prices, self.meter, record=self._record_market,
        )
        if spec.model_id.startswith("x402:"):
            model = _ObservedX402Model(
                self.market,
                self.prices,
                self.meter,
                record=self._record_market,
                on_unaffordable=self._compute_failure,
            )
        asm = Assembly(spec, model, validator=self._validate_output_contract)
        self.assemblies[spec.id] = asm
        if not self.ledger.bootstrap:
            self.stats.registered_window.setdefault(spec.id, self.stats.reserve_windows)
        return asm

    def _validate_output_contract(self, parsed: dict, req: Request) -> None:
        """Every tool argument and proposal bound is checked before any effect in a reply."""
        from factorylab.cortex.assembly import _positive_wire_decimal, _validate_schema
        from factorylab.world.venue_tools import _validate

        for call in parsed.get("tool_calls", []):
            spec = self.tool_specs.get(call["tool"])
            if spec is not None:
                if spec["kind"] == "venue":
                    _validate(call["args"], spec["args_schema"])
                    for key in ("size", "price"):
                        if call["args"].get(key) is not None:
                            _positive_wire_decimal(call["args"][key])
                else:
                    _validate_schema(call["args"], spec["args_schema"])
        known = {p.id: p for p in SEED_VOCABULARY}
        for forecast in parsed.get("forecasts", []):
            if forecast["predicate"] not in known:
                raise ValueError("unknown forecast predicate")
            _validate_schema(
                forecast["params"], _to_plain(known[forecast["predicate"]].param_schema)
            )

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
        self._record_market(
            {
                "kind": "treasury.insolvency",
                "event_id": ev.id,
                "consecutive_events": count,
                "unaffordable": self._compute_unaffordable,
            }
        )
        self.insolvency_count = count

    CONSEQUENCE_WRITES = frozenset({
        "venue.place_market", "venue.place_limit", "venue.close", "venue.cancel",
        "venue.set_leverage", "treasury.transfer",
    })
    WRITE_REFUSAL = ("venue and treasury writes belong to decisions with an open consequence "
                     "account (producer, antagonist and child returns); a judging decision "
                     "has none")

    def _allowed_tools(self, action_id: str) -> set[str]:
        """Every registered tool is a public primitive; schematics are public (v0.4 §1.6)."""
        return set(self.tool_specs)

    def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
                  slot: str = "tool:0") -> tuple[dict, int]:
        """Execute one tool call through metering. Returns (result, cost)."""
        tool_id = str(call.get("tool"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if tool_id not in self.tool_specs or tool_id not in self._allowed_tools(action_id):
            return {"error": "unknown or disallowed tool"}, 0
        if tool_id in self.CONSEQUENCE_WRITES and not self.consequences.account_open(handle):
            # No judge trades what it judges (essay II.III): the refusal is public.
            self.ledger.append({"kind": "tool.refused", "handle": handle,
                                "assembly_id": action_id, "tool": tool_id,
                                "reason": self.WRITE_REFUSAL, "ts": self.clock.now_ns})
            return {"error": self.WRITE_REFUSAL}, 0
        spec = self.tool_specs[tool_id]
        price = int(spec["price_micro_per_call"])

        def execute() -> dict:
            if spec["kind"] == "venue":
                from factorylab.world.venue_tools import _validate

                _validate(args, spec["args_schema"])
                if tool_id in ("venue.place_market", "venue.place_limit"):
                    reason = self._order_exclusion(
                        handle, str(args.get("coin")), Decimal(str(args.get("size"))),
                        args.get("side") == "buy",
                        Decimal(str(args["price"])) if "price" in args else None,
                        reduce_only=args.get("reduce_only") is True,
                    )
                    if reason:
                        return {"status": "rejected", "error": reason}
                if tool_id in ("venue.place_market", "venue.place_limit", "venue.close",
                               "venue.cancel"):
                    return self._venue_write(handle, tool_id, args, slot=slot)
                return self.venue_tools.call(tool_id, args)
            if spec["kind"] == "catalogue":
                return {
                    "models": self._catalogue_search(
                        str(args["substring"]), int(args.get("limit", 20))
                    )
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
                    "by": action_id,
                    "handle": handle,
                }
                self.ledger.append({"kind": "treasury.intent", **intent, "ts": self.clock.now_ns})
                self._emit(EventKind.TRANSFER_INTENT, intent, source="kernel")
                self.stats.transfer_intents += 1
                return self.treasury.transfer(
                    direction, usd, handle=handle, now_ns=self.clock.now_ns
                )
            tool = self.population_tools.get(tool_id)
            if tool is None:
                return {"error": "tool unavailable"}
            return self.tool_runner.run(tool, args)

        try:
            metered = self.meter.run(
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
            if hasattr(self.exchange, "drain_events"):
                self._settle_exchange_effects(self.exchange.drain_events())
        return metered.result, metered.cost

    def _invoke_compute(self, action_id: str, req: Request) -> Return:
        """Each attempted model invocation spends one lifetime trial, including follow-up calls."""
        model_id = self.assemblies[action_id].spec.model_id
        req = replace(req, cost_ceiling=min(
            req.cost_ceiling, max(0, self.wallet.available_for(req.handle, f"model:{model_id}"))
        ))
        ret = self.assemblies[action_id].invoke(req)
        count = self.stats.invocations_by_assembly.get(action_id, 0) + 1
        self.ledger.append({"kind": "novelty.invocation", "assembly_id": action_id,
                            "handle": req.handle, "count": count})
        self.stats.invocations_by_assembly[action_id] = count
        return ret

    def _invoke(self, action_id: str, req: Request, role: str, *, child: bool = False) -> Return:
        ret = self._invoke_compute(action_id, req)
        self._check_compute_return(req.handle, ret)
        if child and (ret.children or ret.tool_calls):
            self.ledger.append({"kind": "requests.refused", "handle": req.handle,
                                "reason": "child invocations answer once; no continuation"})
            from factorylab.cortex.assembly import _validate_schema

            try:
                _validate_schema(ret.outputs, req.outcome_schema)
            except (ValueError, TypeError, RecursionError):
                ret = replace(ret, outputs={"reason": "child answer requires continuation"},
                              status="malformed")
            ret = replace(ret, children=(), tool_calls=())
        if not self.wallet.dead and ret.status == "ok" and (ret.tool_calls or ret.children):
            results = []
            tool_cost = 0
            for index, call in enumerate(ret.tool_calls):
                if self.wallet.dead:
                    break
                price = self.tool_specs.get(call["tool"], {}).get("price_micro_per_call", 0)
                if price > max(0, req.cost_ceiling - ret.cost - tool_cost):
                    result, cost = {"error": "request cost ceiling exhausted"}, 0
                else:
                    result, cost = self._run_tool(action_id, req.handle, call, slot=f"tool:{index}")
                tool_cost += cost
                ok = not (isinstance(result, dict) and "error" in result)
                self.stats.tool_calls += 1
                if not ok:
                    self.stats.tool_call_failures += 1
                self.ledger.append(
                    {
                        "kind": "tool.call",
                        "handle": req.handle,
                        "assembly_id": action_id,
                        "tool": call.get("tool"),
                        "args": json.dumps(call.get("args"), default=str)[:1000],
                        "ok": ok,
                        "cost": cost,
                        "ts": self.clock.now_ns,
                    }
                )
                self.window.tool_calls += 1
                results.append(
                    {"tool": call.get("tool"), "args": call.get("args"), "result": result}
                )
            for item in ret.children:
                if self.wallet.dead:
                    break
                result, cost = self._invoke_child(
                    action_id, req, item, max(0, req.cost_ceiling - ret.cost - tool_cost)
                )
                tool_cost += cost
                results.append(result)
            follow = Request(
                handle=req.handle,
                description=req.description,
                inputs={**req.inputs, "tool_results": results},
                capability_versions=req.capability_versions,
                outcome_schema=req.outcome_schema,
                deadline_ns=req.deadline_ns,
                cost_ceiling=max(0, req.cost_ceiling - ret.cost - tool_cost),
                parent_handle=req.parent_handle,
                completion_criterion=req.completion_criterion,
                scoring_channel=req.scoring_channel,
                resource_liability=req.resource_liability,
            )
            second = (
                Return(req.handle, {"reason": "wallet exhausted"}, 0, "failed")
                if self.wallet.dead else self._invoke_compute(action_id, follow)
            )
            self._check_compute_return(req.handle, second)
            if second.tool_calls:
                self.ledger.append(
                    {"kind": "tool.calls_ignored", "handle": req.handle, "ts": self.clock.now_ns}
                )
            if second.children:
                self.ledger.append({"kind": "requests.refused", "handle": req.handle,
                                    "reason": "continuation already consumed"})
            if second.status == "ok":
                from factorylab.cortex.assembly import _validate_schema

                try:
                    _validate_schema(second.outputs, req.outcome_schema)
                except (ValueError, TypeError, RecursionError):
                    second = replace(second, status="malformed",
                                     outputs={"reason": "incomplete continuation answer"})
            ret = Return(
                req.handle,
                second.outputs,
                ret.cost + tool_cost + second.cost,
                second.status,
                served_by=second.served_by,
                stop_reason=second.stop_reason,
                provider=second.provider,
            )
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
                },
                "outputs": json.dumps(ret.outputs, default=str)[:4000],
                "ts": self.clock.now_ns,
            }
        )
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
        self._record_declared_propensity(action_id, req, ret, role)
        return ret

    # --- spec A10: the deciding agent's propensity rides on the request ---------

    def _assembly_learner_id(self, assembly_id: str) -> str:
        """One durable learning identity per assembly, distinct from any router's."""
        return f"assembly:{assembly_id}"

    def _action_policy(self, assembly_id: str) -> dict[str, Any] | None:
        """An assembly's own learner's current recommendation, private to that assembly.

        Local state stays local (essay II.I.b): this is the one learner whose rounds
        this assembly's own decisions opened, so it is its own running score and
        nobody else's. It is read from a detached copy, so disclosing it can never
        disturb a round that is waiting for its reward.
        """
        from factorylab.learners.base import restore_learner

        learner = self.assembly_learners.get(assembly_id)
        if learner is None:
            return None
        try:
            detached = restore_learner(learner.inner.state())
            policy = detached.distribution(tuple(detached.actions))
        except (ValueError, RuntimeError, TypeError, ArithmeticError):
            return None
        return {"over": _publishable(policy),
                "note": "your own learner's current policy over the action set you registered; "
                        "declare a propensity on your return to train it"}

    def _record_declared_propensity(self, action_id: str, req: Request, ret: Return, role: str):
        """Log the woken assembly's own distribution as a second propensity on the handle.

        Absent or unusable, it is recorded degenerate: the action taken at 1.0
        (spec A10). The reason an offered declaration could not be used goes back
        to the population, because a refusal nobody can read is repeated.
        """
        from factorylab.learners.base import state_bytes
        from factorylab.runtime.propensity import action_label, declared_record

        try:
            self.queue.get(req.handle)
        except KeyError:
            return None
        label = action_label("producer" if role == "child" else role, ret.outputs, ret.status)
        learner = self.assembly_learners.get(action_id)
        state_hash = (
            hashlib.sha256(state_bytes(learner.state())).hexdigest()
            if learner is not None else "declared"
        )
        record, reason = declared_record(
            label, ret.outputs.get("propensity") if isinstance(ret.outputs, dict) else None,
            learner_id=self._assembly_learner_id(action_id), state_hash=state_hash,
        )
        try:
            self.queue.record_propensity(req.handle, record)
        except (KeyError, ValueError):
            return None
        if reason is not None:
            self.ledger.append({"kind": "propensity.refused", "handle": req.handle,
                                "reason": reason, "ts": self.clock.now_ns})
            self.registration_feedback.append({"reason": f"propensity: {reason}"})
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
        """One parent-selected child has its own decision, liability and ordinary judgment route."""
        target = action_id if item.target == "self" else item.target
        actor = f"composition:{parent.handle}"
        handle = self.queue.open(
            actor=actor, event_id=f"child-{parent.handle}",
            propensity=PropensityRecord((target,), (1.,), target, 0, actor, "parent-selected"),
            channel=CH_VERDICT, deadline_ns=parent.deadline_ns,
            parent_handle=parent.handle, cost_ceiling=ceiling,
        )
        self.ledger.append({"kind": "request.child", "handle": handle, "target": target,
                            "resource_liability": parent.handle, "cost_ceiling": ceiling,
                            "description": item.description, "inputs": item.inputs,
                            "outcome_schema": item.outcome_schema})
        self.stats.decisions += 1
        self.consequences.start(handle, self.n)
        req = Request(handle, item.description, item.inputs, {}, item.outcome_schema,
                      parent.deadline_ns, ceiling, parent.handle,
                      "a JSON object satisfying the outcome schema", CH_VERDICT, parent.handle)
        if target in self.assemblies:
            self.handle_to_assembly[handle] = target
            ret = self._invoke(target, req, "child", child=True)
            self._execute_outputs(ret)
            self._apply_registrations(handle, ret)
            self.memory.setdefault(target, deque(maxlen=3)).append(
                {"handle": handle, "outputs": ret.outputs, "verdict": None}
            )
        else:
            ret = Return(handle, {"reason": "target assembly unavailable"}, 0, "failed")
            self.ledger.append({"kind": "request.failed", "handle": handle,
                                "reason": "target assembly unavailable"})
        self.consequences.finish(handle, ret.cost)
        self.pending[handle] = PendingJudgement(handle, CH_VERDICT, self.n)
        self.stats.producer_returns += 1
        self._emit(EventKind.PRODUCER_RETURN, {
            "about_handle": handle, "description": item.description, "inputs": item.inputs,
            "outputs": ret.outputs, "cost": ret.cost, "status": ret.status,
            "propensity": self._public_propensity(handle),  # A10
        })
        return {"tool": f"assembly:{target}", "args": item.inputs,
                "result": {"outputs": ret.outputs, "status": ret.status,
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
        """A10: a request about someone else's decision carries that decision's propensity."""
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
