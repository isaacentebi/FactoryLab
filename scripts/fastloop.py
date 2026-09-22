"""Fast iteration on the real launch path: run, score, compare — in seconds or minutes.

Three tiers, one scorecard:

  score    Read any diary (``events.json``) and print the scorecard. Use it on a
           past paid run to get the baseline every change is compared against.
  run      Run the edition-4 world — the rehearsal's frozen launch identity —
           on the seeded fake venue: virtual clock (no sleeping between ticks),
           a moving market, fills. ``--provider scripted`` is free and deterministic
           (plumbing, prompt size); ``--provider live`` buys real model calls through
           the prepaid guard under ``--cap-usd`` (behaviour).

Examples::

    uv run python scripts/fastloop.py score work/population-pr121/live/events.json
    uv run python scripts/fastloop.py run --provider scripted --ticks 20
    uv run python scripts/fastloop.py run --provider live --ticks 30 --cap-usd 2
    uv run python scripts/fastloop.py run --provider live --ticks 30 --seeds 1,2,3,4

Nothing here touches a real venue, a reserve or a transfer rail.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factorylab.runtime.worlds import load_manifest  # noqa: E402
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse  # noqa: E402
from factorylab.world.scripted import (  # noqa: E402
    ScriptedProvider,
    _description_from_prompt,
    _inputs_from_prompt,
)
from scripts import edition4_rehearsal as rehearsal  # noqa: E402

DEFAULT_WORLD = ROOT / "work/population-pr121/world-edition4-rehearsal.toml"
DEFAULT_OUT = ROOT / "work/fastloop"


# --- the free tier: a scripted population that exercises the real contracts ---------

class PolicyProvider(ScriptedProvider):
    """A deterministic stand-in population for the edition-4 contracts.

    It is not a model of behaviour. It exists so every institution a paid run
    reaches — tool rounds, a limit order reported as ``order``, a repeated order,
    provisional and grounded judgments, metas — is reached for free, and so the
    prompts the real seats would be sent are rendered and measured.
    """

    def __init__(self, world: Path) -> None:
        super().__init__()
        self.manifest = load_manifest(world)
        self.decisions = 0

    def catalogue(self) -> list[CatalogueEntry]:
        """Every manifest model, at its manifest price, with a native output window."""
        rows = []
        for model in self.manifest.models:
            price = model.price if hasattr(model, "price") else None
            prompt = getattr(price, "input_micro_per_token", None)
            rows.append(CatalogueEntry(
                id=model.id, name=model.id,
                prompt_usd_per_token=str(prompt or "0.0000002"),
                completion_usd_per_token="0.000001",
                context_length=200_000, max_completion_tokens=100_000))
        return rows

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        desc = _description_from_prompt(text)
        if desc.startswith("Evaluate") or "realized_consequence" in inputs:
            reply = self._judge(inputs)
        elif desc.startswith("Assess"):
            reply = {"conformity": 0.8, "rationale": "scripted meta"}
        elif desc.startswith("Vote"):
            reply = {"vote": True, "reason": "scripted yes"}
        else:
            reply = self._decide(inputs)
        return ModelResponse(req.model_id, json.dumps(reply), len(text) // 4, 60, "stop",
                             cost_micro=1)

    def _decide(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if "tool_results" in inputs:
            # The PR121 journey: after a tool trade, report it as "order" with no fields.
            wrote = any(str(r.get("tool", "")).startswith("venue.place")
                        for r in inputs["tool_results"])
            return ({"action": "order", "rationale": "limit order submitted via tool",
                     "working_state": {"pending": "limit resting"}} if wrote else
                    {"action": "hold", "rationale": "read the venue; nothing to do"})
        self.decisions += 1
        n = self.decisions
        mid = (inputs.get("payload") or {}).get("mids", {}).get("BTC")
        if n % 7 == 3 and mid:
            # The same resting sell twice (decisions 264 and 273), above the market.
            price = str((Decimal(str(mid)) * Decimal("1.2")).quantize(Decimal("1")))
            return {"action": "order", "tool_calls": [{"tool": "venue.place_limit", "args": {
                "coin": "BTC", "side": "sell", "size": "0.001", "price": price}}]}
        if n % 5 == 0:
            return {"action": "investigate", "tool_calls": [
                {"tool": "venue.positions", "args": {}}]}
        hold = {"action": "hold", "rationale": "no mechanism worth trading yet",
                "propensity": {"hold": 0.7, "investigate": 0.2, "order": 0.1}}
        if n % 2:
            hold["counterfactual"] = {"coin": "BTC", "side": "buy" if n % 4 == 1 else "sell"}
        return hold

    @staticmethod
    def _judge(inputs: dict[str, Any]) -> dict[str, Any]:
        grounded = inputs.get("realized_consequence")
        if isinstance(grounded, dict):
            refs = [row.get("ref") for row in grounded.get("evidence", [])
                    if isinstance(row, dict) and str(row.get("ref", "")).startswith(
                        ("execution:", "ExecutionReceipt"))]
            finding = ({"status": "supported", "score": 0.6, "evidence": refs[:1],
                        "reason": "an attributable execution receipt"} if refs else
                       {"status": "unknown", "evidence": [],
                        "reason": "no attributable evidence"})
            return {"verdict": 0.5, "rationale": "scripted final",
                    "realized_consequence": finding}
        # One seed-vocabulary claim per provisional verdict, as the paid judges seal
        # (PR121: 141 in 240 ticks). A holding population's payoff forecasts are all
        # refused as hindsight, so without it no forecast ever comes due, and a
        # forecast-windowed card (edition 5's censorship-bound) is never measured
        # or priced on the free tier.
        return {"verdict": 0.6, "payoff": 0.3, "rationale": "scripted provisional",
                "forecasts": [{"predicate": "wallet_up", "q": 0.4,
                               "params": {"horizon_events": 10}}]}


# --- the scorecard ------------------------------------------------------------------

def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def scorecard(events: list[dict[str, Any]]) -> dict[str, Any]:
    """What a run did to the learning loop, from its diary alone."""
    role = {}
    invocations = collections.defaultdict(collections.Counter)
    malformed = collections.Counter()
    sizes = collections.defaultdict(list)
    section_sizes = collections.defaultdict(lambda: collections.defaultdict(list))
    actions = collections.Counter()
    cost = 0
    for e in events:
        if e.get("kind") != "invocation":
            continue
        r = e.get("role", "?")
        role[e.get("handle")] = r
        invocations[r][e.get("status")] += 1
        cost += int(e.get("cost") or 0)
        sections = e.get("sections") or {}
        if sections.get("total"):
            sizes[r].append(sections["total"])
            for name, value in sections.items():
                if name != "total":
                    section_sizes[r][name].append(value)
        try:
            out = json.loads(e.get("outputs") or "{}")
        except json.JSONDecodeError:
            out = {}
        if e.get("status") == "malformed":
            malformed[str(out.get("validation_error") or out.get("reason"))[:60]] += 1
        if r == "producer" and e.get("status") == "ok" and isinstance(out, dict):
            actions[str(out.get("action", "?"))[:20]] += 1
    producer_settle = collections.Counter()
    for e in events:
        if e.get("kind") != "decision.settle":
            continue
        ret = e.get("return") or {}
        if role.get(ret.get("handle")) == "producer" and ret.get("channel") == "verdict":
            version = str(ret.get("definition_version"))
            label = ("provisional" if version.endswith("-provisional") else
                     "unknown" if version.endswith("-unknown") else version)
            producer_settle[f"{ret.get('status')}:{label}"] += 1
    kinds = collections.Counter(e.get("kind") for e in events)
    taken = [e for e in events if e.get("kind") == "exploration.taken"]
    findings = collections.Counter(e.get("status") for e in events
                                   if e.get("kind") == "consequence.finding")
    intents = collections.Counter(e.get("operation") for e in events
                                  if e.get("kind") == "order.intent")
    opportunity = [e for e in events if e.get("kind") == "consequence.opportunity"]
    scored = sum(v for k, v in producer_settle.items() if k.startswith("settled:"))
    total = sum(producer_settle.values())
    return {
        "ticks": sum(1 for e in events if e.get("kind") == "event"
                     and (e.get("event") or {}).get("kind") == "Tick"),
        "calls": sum(sum(c.values()) for c in invocations.values()),
        "cost_usd": str(Decimal(cost) / Decimal(1_000_000)),
        "invocations": {r: dict(c) for r, c in invocations.items()},
        "malformed_reasons": dict(malformed.most_common(6)),
        "prompt_bytes": {r: {"median": int(statistics.median(v)), "p90": _pct(v, 0.9),
                             "max": max(v)} for r, v in sizes.items()},
        "prompt_sections_median": {
            r: {name: int(statistics.median(v)) for name, v in sorted(
                secs.items(), key=lambda kv: -statistics.median(kv[1]))[:6]}
            for r, secs in section_sizes.items()},
        "producer_actions": dict(actions.most_common()),
        "producer_settlements": dict(producer_settle.most_common()),
        "learning_signal_rate": round(scored / total, 3) if total else None,
        "grounded_findings": dict(findings),
        "opportunity_cost": {
            "priced": len(opportunity),
            "mean_score": (round(statistics.fmean(e["score"] for e in opportunity), 3)
                           if opportunity else None),
            "named_declined": sum(1 for e in opportunity if e.get("declined")),
            "named_regret_rate": (
                round(sum(Decimal(e["regret_bps"]) > 0 for e in opportunity
                          if e.get("declined")) / named, 3)
                if (named := sum(1 for e in opportunity if e.get("declined"))) else None),
        },
        "judge_unmeasured": kinds.get("evaluation.unmeasured", 0),
        "exploration": {"draws": kinds.get("exploration.draw", 0),
                        "complied": sum(1 for e in taken if e.get("complied")),
                        "by_class": dict(collections.Counter(
                            f"{e['drawn']}->{e['taken']}" for e in taken))},
        "orders": {"intents": dict(intents),
                   "duplicates_refused": kinds.get("order.duplicate", 0),
                   "reported_not_placed": kinds.get("order.reported", 0),
                   "refused": kinds.get("order.refused", 0),
                   "infeasible": kinds.get("order.infeasible", 0)},
    }


def print_card(card: dict[str, Any]) -> None:
    print(json.dumps(card, indent=2, default=str))


# --- running ------------------------------------------------------------------------

def simulation_manifest(world: Path, seed: int, exploration: float | None = None,
                        vault_tools: bool = False) -> Any:
    """The launch identity with only the venue swapped for the deterministic fake.

    ``effective_manifest`` freezes the roster, charter, seed lenses, prompts and
    provider-native completion allowances exactly as a rehearsal would. Then the
    exchange becomes the seeded fake venue, which makes the whole world simulated:
    a virtual clock (no sleeping between ticks), a moving market whose resting
    orders fill, and the fake treasury. Everything the population is shown and
    graded by is the launch path's.
    """
    from dataclasses import replace

    base = load_manifest(world)
    # A hybrid capital-loop world keeps its Venice keys: on the fake venue they select
    # the scripted two-leg rail, so both conversion legs are rehearsed for free.
    hybrid = (base.treasury.venice_network == "base-mainnet"
              and base.treasury.reserve_address is not None)
    manifest = rehearsal.effective_manifest(base, native_completions=True,
                                            capital_loop=hybrid)
    exchange = replace(manifest.exchange, kind="fake", mainnet=False, seed=seed,
                       spot_pairs=(), client_namespace=None,
                       vault_tools=vault_tools or manifest.exchange.vault_tools)
    manifest = replace(manifest, exchange=exchange, seed=seed)
    if exploration is not None:
        manifest = replace(manifest, evaluation=replace(
            manifest.evaluation, exploration_share=exploration))
    manifest.validate()
    return manifest


def run(provider_kind: str, ticks: int, world: Path, out: Path, cap_usd: str,
        seed: int, exploration: float | None = None,
        vault_depositor_usd: str | None = None) -> dict[str, Any]:
    """``vault_depositor_usd`` opts the world into the vault surface and scripts one
    outside depositor into every vault the factory creates, who leaves ten steps later;
    the fake's vaults earn nothing on their own, so the depositor pays no commission
    unless a vault's equity moved."""
    from factorylab.runtime.loop import Runtime

    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out / f"{provider_kind}-{stamp}-s{seed}"
    target.mkdir(parents=True, exist_ok=True)
    manifest = simulation_manifest(world, seed, exploration,
                                   vault_tools=bool(vault_depositor_usd))
    admission = rehearsal.Admission(cap_micro=int(Decimal(cap_usd) * 1_000_000),
                                    max_calls=10_000, recover_provider_failures=True)
    inner = (PolicyProvider(world) if provider_kind == "scripted"
             else rehearsal.build_prepaid_provider(manifest))
    provider = rehearsal.PrepaidProvider(inner, manifest, admission)
    started = time.monotonic()
    card: dict[str, Any] = {"provider": provider_kind, "out": str(target)}
    try:
        runtime = Runtime(manifest, events=ticks, seed=manifest.seed,
                          initial_balance_micro=None,
                          ledger_path=str(target / "ledger.jsonl"), router_gamma=0.1,
                          provider=provider, kill_at_end=True)
        if vault_depositor_usd:
            runtime.exchange.vault_depositor_usd = Decimal(vault_depositor_usd)
            runtime.exchange.vault_depositor_steps = 10
        summary = runtime.run()
        card["status"] = "completed"
        card["terminated"] = summary.get("terminated")
    except Exception as exc:  # the scorecard still reads what was written
        card["status"] = "failed"
        card["error"] = f"{type(exc).__name__}: {exc}"[:500]
        runtime = locals().get("runtime")
    card["wall_seconds"] = round(time.monotonic() - started, 1)
    if runtime is not None:
        events = [i for i in runtime.ledger._recovery_items() if i.get("kind") != "snapshot"]
        (target / "events.json").write_text(json.dumps(events, default=str) + "\n")
        card.update(scorecard(events))
    card["billed_usd"] = str(Decimal(admission.known_micro) / Decimal(1_000_000))
    card["admission_stop"] = admission.stop_reason
    (target / "scorecard.json").write_text(json.dumps(card, indent=2, default=str) + "\n")
    return card


def combine(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Several independent seeds as one card: counts summed, rates recomputed, seeds kept."""
    def add(into: dict, more: dict) -> None:
        for key, value in more.items():
            if isinstance(value, dict):
                add(into.setdefault(key, {}), value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                into[key] = into.get(key, 0) + value

    total: dict[str, Any] = {}
    for card in cards:
        add(total, {k: card.get(k) for k in (
            "ticks", "calls", "invocations", "malformed_reasons", "producer_actions",
            "producer_settlements", "grounded_findings", "judge_unmeasured", "orders",
            "exploration", "opportunity_cost")
            if card.get(k) is not None})
    # Averages and rates are recomputed from the seeds, never summed.
    priced = [c.get("opportunity_cost") or {} for c in cards]
    n = sum(p.get("priced") or 0 for p in priced)
    named = sum(p.get("named_declined") or 0 for p in priced)
    total["opportunity_cost"] = {
        "priced": n, "named_declined": named,
        "mean_score": (round(sum((p.get("mean_score") or 0) * (p.get("priced") or 0)
                                 for p in priced) / n, 3) if n else None),
        "named_regret_rate": (round(sum((p.get("named_regret_rate") or 0)
                                        * (p.get("named_declined") or 0)
                                        for p in priced) / named, 3) if named else None)}
    settled = total.get("producer_settlements", {})
    scored = sum(v for k, v in settled.items() if k.startswith("settled:"))
    total["learning_signal_rate"] = (round(scored / sum(settled.values()), 3)
                                     if settled else None)
    total["billed_usd"] = str(sum(Decimal(c.get("billed_usd", "0")) for c in cards))
    total["wall_seconds"] = max((c.get("wall_seconds", 0) for c in cards), default=0)
    total["prompt_bytes_median"] = {
        role: int(statistics.median(c["prompt_bytes"][role]["median"] for c in cards
                                    if role in c.get("prompt_bytes", {})))
        for role in {r for c in cards for r in c.get("prompt_bytes", {})}}
    total["seeds"] = [{k: c.get(k) for k in ("status", "error", "out",
                                             "learning_signal_rate", "billed_usd")}
                      for c in cards]
    return total


def run_seeds(args: argparse.Namespace, seeds: list[int]) -> dict[str, Any]:
    """Independent worlds in parallel processes: one wall time, several samples."""
    import subprocess

    procs = [subprocess.Popen(
        [sys.executable, __file__, "run", "--provider", args.provider,
         "--ticks", str(args.ticks), "--world", str(args.world), "--out", str(args.out),
         "--cap-usd", args.cap_usd, "--seed", str(seed),
         *(["--exploration", str(args.exploration)] if args.exploration is not None else []),
         *(["--vault-depositor-usd", args.vault_depositor_usd]
           if args.vault_depositor_usd else [])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT) for seed in seeds]
    cards = []
    for seed, proc in zip(seeds, procs, strict=True):
        out, err = proc.communicate()
        try:
            cards.append(json.loads(out))
        except json.JSONDecodeError:
            cards.append({"status": "failed", "seed": seed, "error": err[-500:]})
    return combine(cards)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="score an existing events.json")
    s.add_argument("events", type=Path)
    r = sub.add_parser("run", help="run the edition-4 world offline")
    r.add_argument("--provider", choices=("scripted", "live"), default="scripted")
    r.add_argument("--ticks", type=int, default=20)
    r.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    r.add_argument("--cap-usd", default="2")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--exploration", type=float, default=None,
                   help="evaluation.exploration_share for this run (the manifest's otherwise)")
    r.add_argument("--vault-depositor-usd", default=None,
                   help="publish the vault surface and script an outside depositor of this "
                        "many USD into each vault the factory creates")
    r.add_argument("--seeds", default=None,
                   help="comma-separated seeds run in parallel processes, e.g. 1,2,3,4")
    args = parser.parse_args(argv)
    if args.command == "score":
        print_card(scorecard(json.loads(args.events.read_text())))
        return 0
    if args.seeds:
        card = run_seeds(args, [int(x) for x in args.seeds.split(",")])
        print_card(card)
        return 0 if all(c.get("status") == "completed" for c in card["seeds"]) else 1
    card = run(args.provider, args.ticks, args.world, args.out, args.cap_usd, args.seed,
               args.exploration, args.vault_depositor_usd)
    print_card(card)
    return 0 if card.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
