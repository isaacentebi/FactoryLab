"""Fast iteration on the real launch path: run, score, compare — in seconds or minutes.

Three tiers, one scorecard:

  score    Read any diary (``events.json``) and print the scorecard. Use it on a
           past paid run to get the baseline every change is compared against.
  run      Run the edition-4 world — the rehearsal's frozen launch identity —
           on the seeded fake venue: virtual clock (no sleeping between ticks),
           a moving market, fills. ``--provider scripted`` is free and deterministic
           (plumbing, prompt size); ``--provider live`` buys real model calls through
           the prepaid guard under ``--cap-usd`` (behaviour).

The scorecard measures plumbing, cost and prompt size. ``producer_actions`` is an
observation and never a target: no prompt change may be justified by an action-mix
delta (Chapter II §I.a, Carroll's robust simplicity; Chapter II rulings R12).

``--gaps-from events.json`` replays the tick gaps a real diary delivered instead
of ticking at exactly the declared interval: the virtual clock at the declared
tick hid every timing failure the wall clock produced (Chapter II §IV.b-c; time
audit T3). The scorecard's ``clock`` block reads the loops, cutoffs and rounds.

Examples::

    uv run python scripts/fastloop.py score work/population-pr121/live/events.json
    uv run python scripts/fastloop.py run --provider scripted --ticks 20
    uv run python scripts/fastloop.py run --ticks 120 --gaps-from work/population-e5a/events.json
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
from factorylab.world.clock import ClockSource  # noqa: E402
from factorylab.world.events import WorldEvent, WorldEventKind  # noqa: E402
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

#: The one task the scripted population requests of a kind, and the one tool it
#: registers: fixtures that reach the composition path (W4), not prompts.
CHILD_TASK = "Summarise the funding state of the named coin."
HALF_SPREAD: dict[str, Any] = {
    "kind": "tool", "id": "half-spread",
    "description": "Half the spread in price units for a mid and a width in basis points.",
    "args_schema": {"type": "object", "properties": {
        "mid": {"type": "number"}, "bps": {"type": "integer"}}, "required": ["mid", "bps"]},
    "returns_schema": {"type": "object", "properties": {"half_spread": {"type": "number"}},
                       "required": ["half_spread"]},
    "code": ("import json,sys\na=json.load(sys.stdin)\n"
             "print(json.dumps({'half_spread': a['mid']*a['bps']/20000}))"),
    "timeout_s": 2,
}


class PolicyProvider(ScriptedProvider):
    """A deterministic stand-in population for the edition-4 contracts.

    It is not a model of behaviour. It exists so every institution a paid run
    reaches — tool rounds, a limit order reported as ``order``, a repeated order,
    verdicts scored against the world, metas — is reached for free, and so the
    prompts the real seats would be sent are rendered and measured.
    """

    #: Test plumbing for the charter's markets (charter audit M1, M2): two lambda
    #: motions the scripted seats vote through and down, so both branches resolve.
    MOTIONS = (("market-enact", 0.2, "decrease"), ("market-reject", 0.05, "increase"))

    def __init__(self, world: Path) -> None:
        super().__init__()
        self.manifest = load_manifest(world)
        self.decisions = 0
        cards = self.manifest.charter.cards
        self.card = cards[0].id if cards else None
        self.proposed: list[str] = []

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
        if desc.startswith(("Give verdict", "Evaluate")):
            reply = self._judge(inputs, req.model_id)
        elif desc.startswith("Give your own verdict"):
            # Plumbing (Wave 5a): an adversarial judge's counter-verdict, one step off
            # the verdict it read, so both the counter and its settlement are reached.
            read = (inputs.get("verdict") or {}).get("verdict")
            q = 0.3 if not isinstance(read, int | float) or read >= 0.5 else 0.7
            reply = {"verdict": q, "rationale": "scripted counter"}
        elif desc.startswith("Assess"):
            reply = {"conformity": 0.8, "rationale": "scripted meta"}
        elif desc.startswith("Vote"):
            # Plumbing: a motion named for rejection is voted down, every other up.
            motion = str((inputs.get("amendment") or {}).get("id", ""))
            reply = {"vote": not motion.endswith("reject"), "reason": "scripted ballot"}
        elif desc.startswith("Testify"):
            reply = {"assessment": "scripted testimony"}
        elif desc == CHILD_TASK:
            reply = {"summary": "scripted summary", "action": "hold"}
        else:
            # Whether the world already lists the scripted tool, read off this prompt.
            self.tool_listed = f'"{HALF_SPREAD["id"]}"' in text
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
        reply = self._decide_trade(inputs, n)
        return {**reply, **self._markets(n)}

    def _markets(self, n: int) -> dict[str, Any]:
        """Plumbing for the charter's markets: a lambda post, motions, branch forecasts.

        Every third decision posts a price for the first charter card; decisions 3
        and 6 propose the two lambda motions; every other decision forecasts both
        branches of the motions proposed so far. Refusals (a second post in a window,
        a motion already decided) are the kernel's to ledger.
        """
        if self.card is None:
            return {}
        out: dict[str, Any] = {}
        if n % 3 == 0:
            out["shadow_prices"] = {self.card: round(0.05 + 0.05 * (n % 4), 2)}
        index = n // 3 - 1
        if n % 3 == 0 and 0 <= index < len(self.MOTIONS):
            motion, price, direction = self.MOTIONS[index]
            self.proposed.append(motion)
            out["register"] = [{"kind": "amendment", "id": motion,
                                "lambda": {self.card: price},
                                "predicted_effect": {"card_id": self.card,
                                                     "direction": direction, "window": 1}}]
        elif n % 2 == 0 and self.proposed:
            out["motion_forecasts"] = [
                {"motion": motion, "branch": branch, "q": q}
                for motion in self.proposed for branch, q in (("enact", 0.7), ("reject", 0.4))]
        return out

    def _decide_trade(self, inputs: dict[str, Any], n: int) -> dict[str, Any]:
        tool_listed = getattr(self, "tool_listed", False)
        mid = (inputs.get("payload") or {}).get("mids", {}).get("BTC")
        # W4 plumbing, not a behaviour: one population tool is offered until it is
        # listed, then called now and then by whichever seat is drawn, and a request
        # for a kind of work carries a forwarded propensity, so the composition path
        # and both credits are reached on the free tier.
        if not tool_listed and n % 5 == 2:
            return {"action": "build", "register": [HALF_SPREAD]}
        if n % 9 == 4:
            return {"action": "investigate", "requests": [{
                "target": "ProducerReturn", "description": CHILD_TASK,
                "inputs": {"coin": "BTC"}, "outcome_schema": {
                    "type": "object", "properties": {"summary": {"type": "string"}},
                    "required": ["summary"]},
                "propensity": {"request": 0.5, "hold": 0.5}, "chosen": "request"}]}
        if tool_listed and n % 3 == 1:
            return {"action": "investigate", "tool_calls": [{
                "tool": HALF_SPREAD["id"],
                "args": {"mid": float(mid) if mid else 100.0, "bps": 10}}]}
        if n % 7 == 3 and mid:
            # The same resting sell twice (decisions 264 and 273), above the market;
            # the venue takes both (Chapter II rulings, R6).
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
    def _judge(inputs: dict[str, Any], model_id: str = "") -> dict[str, Any]:
        # One seed-vocabulary claim per verdict, as the paid judges seal (PR121: 141 in
        # 240 ticks), so a forecast-windowed card (edition 5's censorship-bound) is
        # measured and priced on the free tier. The verdict itself is the prediction
        # the world scores (ruling R1). It differs by model, so two judges of one
        # return disagree and ensemble disagreement is reached (Wave 5a).
        verdict = (0.6, 0.45, 0.75)[sum(map(ord, model_id)) % 3]
        return {"verdict": verdict, "rationale": "scripted verdict",
                "forecasts": [{"predicate": "wallet_up", "q": 0.4,
                               "params": {"horizon_events": 10}}]}


# --- delivered tick gaps -------------------------------------------------------------

def delivered_gaps(path: Path) -> list[int]:
    """The gaps, in nanoseconds, between the ticks a diary actually delivered."""
    events = json.loads(Path(path).read_text())
    stamps = [int((e.get("event") or {}).get("ts_ns") or e.get("ts") or 0) for e in events
              if e.get("kind") == "event" and (e.get("event") or {}).get("kind") == "Tick"]
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False) if b > a]
    if not gaps:
        raise ValueError(f"{path} delivered fewer than two ticks")
    return gaps


class ReplayClock(ClockSource):
    """The seeded virtual clock, ticking at a real diary's delivered gaps in order.

    It cycles through the recorded gaps, so a run longer than the diary keeps the
    same distribution. Like the wall clock it reports the mean of its latest
    delivered gaps as ``measured_interval_ns`` while ``interval_ns`` stays the
    declared tick, so every conversion sees what a live world would.
    """

    def __init__(self, start_ns: int, interval_ns: int, count: int, gaps: list[int]) -> None:
        super().__init__(start_ns, interval_ns, count)
        self.recorded = list(gaps)
        self.delivered: list[int] = []

    def _events(self, drips=None):
        while self.index < self.count:
            gap = self.recorded[(self.index - 1) % len(self.recorded)]
            ts = self.start_ns if self.last_ns is None else self.last_ns + gap
            if self.last_ns is not None:
                self.delivered = [*self.delivered[-63:], ts - self.last_ns]
            self.last_ns = self.last_event_ns = ts
            i = self.index
            self.index += 1
            yield WorldEvent(WorldEventKind.TICK, ts, self.source, {"index": i})

    def measured_interval_ns(self) -> int:
        """The mean of the latest delivered gaps, the declared interval before any."""
        if not self.delivered:
            return self.interval_ns
        return max(1, sum(self.delivered) // len(self.delivered))

    def intervals(self) -> dict:
        return {"declared_ns": self.interval_ns, "measured_ns": self.measured_interval_ns(),
                "samples": len(self.delivered)}


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
            producer_settle[f"{ret.get('status')}:{ret.get('definition_version')}"] += 1
    kinds = collections.Counter(e.get("kind") for e in events)
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
        # The measured y of holds that named a declined trade (ruling R2): its mean is
        # what a hold earns its judges' predictions against.
        "opportunity_cost": {"priced": len(opportunity),
                             "y_sum": sum(e["score"] for e in opportunity),
                             "mean_y": (round(statistics.fmean(e["score"] for e in opportunity),
                                              3) if opportunity else None)},
        "reward_chain": reward_chain(events),
        "evaluation_layer": evaluation_layer(events),
        "composition": composition(events),
        "charter_markets": charter_markets(events),
        "orders": {"intents": dict(intents),
                   "reported_not_placed": kinds.get("order.reported", 0),
                   "refused": kinds.get("order.refused", 0),
                   "infeasible": kinds.get("order.infeasible", 0)},
        "clock": clock(events),
    }


def clock(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The factory's loops and cutoffs, from the diary alone (time audit T1-T8).

    Plumbing counts, never targets (rulings R12): delivered tick gaps; each
    derived loop's firings and the periods drawn, in ticks and as a ratio to its
    inner loop; decision cutoffs by channel; rounds a cutoff credited whose real
    score arrived later; rounds that trained nothing; price moves and holds; the
    immune organ's diagnoses and acts; deferred epochs; governance viability.
    """
    stamps = [int((e.get("event") or {}).get("ts_ns") or 0) for e in events
              if e.get("kind") == "event" and (e.get("event") or {}).get("kind") == "Tick"]
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:], strict=False))
    channel = {e.get("handle"): e.get("channel") for e in events
               if e.get("kind") == "decision.open"}
    timed_out = [((e.get("return") or {}).get("handle")) for e in events
                 if e.get("kind") == "decision.timeout"]
    late = {(e.get("return") or {}).get("handle") for e in events
            if e.get("kind") == "decision.settle"} & set(timed_out)
    loops: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("kind") != "clock.loop":
            continue
        row = loops.setdefault(e["loop"], {"fires": 0, "periods": [], "inner": []})
        row["fires"] += 1
        row["periods"].append(e["period_ticks"])
        row["inner"].append(e["inner_ticks"])
    table = {name: {"fires": row["fires"],
                    "period_ticks_mean": round(statistics.fmean(row["periods"]), 2),
                    "inner_ticks_mean": round(statistics.fmean(row["inner"]), 2),
                    "ratio_min": round(min(p / i for p, i in zip(row["periods"], row["inner"],
                                                                 strict=True)), 2)}
             for name, row in sorted(loops.items())}
    skipped = collections.Counter(e.get("reason") for e in events
                                  if e.get("kind") == "price.skipped")
    immune = [e for e in events if e.get("kind") == "immune.window"]
    return {
        "tick_gap_s": ({"p50": round(gaps[len(gaps) // 2] / 1e9, 2),
                        "mean": round(statistics.fmean(gaps) / 1e9, 2),
                        "p90": round(_pct(gaps, 0.9) / 1e9, 2),
                        "max": round(gaps[-1] / 1e9, 2)} if gaps else None),
        "loops": table,
        "price_windows": sum(1 for e in events if e.get("kind") == "price.window"),
        "price_updates": sum(1 for e in events if e.get("kind") == "price.update"),
        "price_skipped": dict(skipped),
        "immune": {"diagnosed": len(immune),
                   "acted": sum(1 for e in immune if e.get("acts", True)),
                   "gain_steps": sum(1 for e in events if e.get("kind") == "immune.gain")},
        "sampling_moves": sum(1 for e in events
                              if e.get("kind") in ("sampling.raise", "sampling.lower")),
        "timeouts": dict(collections.Counter(str(channel.get(h)) for h in timed_out)),
        "timeouts_total": len(timed_out),
        "cutoff_then_scored": len(late),
        "rounds_unlearned": sum(1 for e in events if e.get("kind") == "propensity.unlearned"),
        "epochs_deferred": sum(1 for e in events if e.get("kind") == "epoch.deferred"),
        "novelty_grants": sum(1 for e in events if e.get("kind") == "novelty.grant"),
        "governance": {k: sum(1 for e in events if e.get("kind") == f"governance.{k}")
                       for k in ("nonviable", "viable", "probe", "settling")},
    }


def reward_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How far the reward chain of ruling R1 reached, from the diary alone.

    A judge decision is an invocation in the ``evaluator`` role. It received a
    consequence score when a ``verdict.consequence`` (or, on diaries written
    before wave 2, a ``verdict.opportunity``) names it, and a meta grade when a
    tier above read it: an ``evaluator.meta_grade`` entry, or on older diaries a
    conformity settlement a meta's handle signed. The NOOP share is, per router,
    the fraction of its own draws that woke nobody.
    """
    judges = {e.get("handle") for e in events
              if e.get("kind") == "invocation" and e.get("role") == "evaluator"}
    metas = {e.get("handle") for e in events
             if e.get("kind") == "invocation" and e.get("role") == "meta"}
    # A settlement carried a real signal when it is a score the tier above or the world
    # gave: the reward chain's own definition, or on older diaries a meta's signed
    # conformity or a meta's consequence. Censored, declined and kernel-zero ones did not.
    signalled = set()
    for e in events:
        ret = e.get("return") or {}
        version = ret.get("definition_version")
        if e.get("kind") == "decision.settle" and ret.get("status") == "settled" and (
                version in ("evaluation-v1", "meta-consequence-v1")
                or version == "conformity-v1" and ret.get("sampling_ref")):
            signalled.add(ret.get("handle"))
    # The mean reward of first-tier judge decisions by which signal they settled on.
    by_source: dict[str, list[float]] = {"meta_only": [], "world_only": [], "both": []}
    for e in events:
        if e.get("kind") != "evaluator.settled" or e.get("tier") != 1 or e.get("reward") is None:
            continue
        source = ("both" if e.get("grade") is not None and e.get("consequence") is not None
                  else "meta_only" if e.get("grade") is not None else "world_only")
        by_source[source].append(e["reward"])
    consequence = {e.get("judge_handle") if e.get("kind") == "verdict.opportunity"
                   else e.get("handle") for e in events
                   if e.get("kind") in ("verdict.consequence", "verdict.opportunity")}
    graded = {e.get("handle") for e in events if e.get("kind") == "evaluator.meta_grade"}
    for e in events:
        ret = e.get("return") or {}
        if (e.get("kind") == "decision.settle" and ret.get("status") == "settled"
                and ret.get("definition_version") == "conformity-v1"
                and ret.get("sampling_ref")):
            graded.add(ret.get("handle"))
    exposures = [e for e in events if e.get("kind") == "exposure.settled"]
    draws: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for e in events:
        if e.get("kind") != "decision.open" or e.get("parent_handle") is not None:
            continue
        prop = e.get("propensity") or {}
        actor = str(e.get("actor", ""))
        if not actor.startswith("router:"):
            continue
        draws[actor]["draws"] += 1
        draws[actor]["noop"] += int(prop.get("chosen") == "NOOP")
    n = len(judges)
    evaluators = judges | metas
    return {
        "judge_decisions": n,
        "judge_and_meta_decisions": len(evaluators),
        "judge_signalled": len(evaluators & signalled),
        "judge_learning_signal_rate": (round(len(evaluators & signalled) / len(evaluators), 3)
                                       if evaluators else None),
        "judge_reward_by_source": {
            source: {"n": len(v), "sum": round(sum(v), 6),
                     "mean": round(statistics.fmean(v), 3) if v else None}
            for source, v in by_source.items()},
        "judge_consequence_share": (round(len(judges & consequence) / n, 3) if n else None),
        "judge_meta_grade_share": round(len(judges & graded) / n, 3) if n else None,
        "meta_consequence_events": sum(1 for e in events
                                       if e.get("kind") == "meta.consequence"),
        "exposures_settled": len(exposures),
        "exposures_nonzero": sum(1 for e in exposures if (e.get("score") or 0) > 0),
        "noop_share_by_router": {actor: round(c["noop"] / c["draws"], 3)
                                 for actor, c in sorted(draws.items()) if c["draws"]},
    }


def evaluation_layer(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How far the evaluation layer reached (Wave 5a), from the diary alone.

    Plumbing counts and shares, never targets (rulings R12): the share of judged
    returns that two or more judges read (``verdict.mean``), the grades each tier
    above the judges gave, the adversarial layer's settled rewards, the chaos faults
    injected, the windows whose early-warning table had a supported series, and the
    evaluators' share of the run's metered compute (evaluator, meta and adversary
    invocations over all of them).
    """
    judged = sum(1 for e in events if e.get("kind") == "decision.settle"
                 and (e.get("return") or {}).get("status") == "settled"
                 and (e.get("return") or {}).get("definition_version") == "verdict-v1")
    multi = sum(1 for e in events if e.get("kind") == "verdict.mean")
    grades = collections.Counter(e.get("tier") for e in events
                                 if e.get("kind") == "evaluator.meta_grade")
    exposures = [e for e in events if e.get("kind") == "exposure.settled"]
    counters = [e for e in events if e.get("kind") == "counter.settled"]
    cost, calls = collections.Counter(), collections.Counter()
    for e in events:
        if e.get("kind") == "invocation":
            cost[e.get("role", "?")] += int(e.get("cost") or 0)
            calls[e.get("role", "?")] += 1
    evaluator_calls = sum(calls[r] for r in ("evaluator", "meta", "adversary"))
    evaluator_cost = sum(cost[r] for r in ("evaluator", "meta", "adversary"))
    total_cost = sum(cost.values())
    ews = [e for e in events if e.get("kind") == "ews.window"]
    barred = [e for e in events if e.get("kind") == "route.barred"]
    return {
        # Draws whose every eligible reader was barred by family: a subject nobody
        # could judge (the #132 review, item 3).
        "route_barred": len(barred),
        "route_barred_by_kind": dict(collections.Counter(e.get("event_kind") for e in barred)),
        "judged_returns": judged,
        "multi_judged_returns": multi,
        "multi_judged_share": round(multi / judged, 3) if judged else None,
        "tier_grades": {str(tier): n for tier, n in sorted(grades.items())},
        "tier3_grades": sum(n for tier, n in grades.items() if (tier or 0) >= 3),
        "adversarial": {
            "exposures_settled": len(exposures),
            "exposures_rewarded": sum(1 for e in exposures if (e.get("score") or 0) > 0),
            "exposures_won": sum(1 for e in exposures if (e.get("score") or 0) > 0.5),
            "counters_opened": sum(1 for e in events if e.get("kind") == "counter.opened"),
            "counters_settled": len(counters),
            "counters_rewarded": sum(1 for e in counters if (e.get("score") or 0) > 0),
            "counters_won": sum(1 for e in counters if (e.get("score") or 0) > 0.5),
        },
        "chaos_faults": dict(collections.Counter(e.get("fault") for e in events
                                                 if e.get("kind") == "chaos.fault")),
        "ews_windows": len(ews),
        "ews_published": sum(1 for e in ews if e.get("supported_series")),
        "compute_micro_by_role": dict(cost),
        "evaluator_compute_share": (round(evaluator_cost / total_cost, 3)
                                    if total_cost else None),
        "invocations_by_role": dict(calls),
        "evaluator_invocation_share": (round(evaluator_calls / sum(calls.values()), 3)
                                       if calls else None),
    }


def composition(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How far composition through contracts reached (W4), from the diary alone.

    Plumbing counts, never targets (rulings R12): requests by the kind they named,
    those refused before any decision opened, executor credits paid through the
    reward channel, population-tool calls by a seat of another lineage than the
    tool's builder, and the credits those calls carried to the builders.
    """
    children = [e for e in events if e.get("kind") == "request.child"]
    settled = [e for e in events if e.get("kind") == "composed.settled"]
    paid = [e for e in settled if e.get("credit") is not None]
    builders = [e for e in settled if e.get("tool_use_credit") is not None]
    calls = [e for e in events if e.get("kind") == "tool.population_call"]
    # A use that reached a builder's held decision (or, unheld, its inbox); a use that
    # settled after the builder's window closed is ledgered "late" and credits nothing.
    tool_credits = [e for e in events if e.get("kind") == "credit.tool"
                    and e.get("applied", "hold") != "late"]
    return {
        "child_requests_by_kind": dict(collections.Counter(
            str(e.get("requested", e.get("target"))) for e in children)),
        "child_requests_forwarding_propensity": sum(
            1 for e in children if e.get("forwarded_propensity")),
        "requests_refused": sum(1 for e in events if e.get("kind") == "requests.refused"),
        "executor_credits_held": sum(1 for e in events if e.get("kind") == "credit.composed"),
        "executor_credits_withheld_same_lineage": sum(
            1 for e in events if e.get("kind") == "credit.withheld"),
        "executor_credits_paid": len(paid),
        "executor_credit_sum": round(sum(e["credit"] for e in paid), 6),
        "population_tool_calls": len(calls),
        "population_tool_calls_by_non_builder": sum(1 for e in calls if e.get("across_lineage")),
        "tool_builder_credits": len(tool_credits),
        "tool_builder_credit_sum": round(sum(e.get("credit") or 0 for e in tool_credits), 6),
        # Registering decisions that settled on their verdict and their tool's use.
        "tool_builder_settlements_credited": len(builders),
    }


def charter_markets(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How far the charter's markets reached (charter audit M1, M2, P1), from the diary.

    Posted lambda: posts, their settlements and mean score, refusals. Motions: the
    conditional forecasts and ballots graded on each branch, the voided ones, and
    the feed-forward term's reach into the price law.
    """
    def kinds(kind: str) -> list[dict[str, Any]]:
        return [e for e in events if e.get("kind") == kind]

    settled = [e for e in kinds("lambda_post.settled") if e.get("status") == "settled"]
    outcomes = kinds("policy.outcome")
    graded: dict[str, dict[str, Any]] = {}
    for e in outcomes:
        if e.get("status") != "settled":
            continue
        key = f"{'forecast' if e.get('forecast') else 'ballot'}:{e.get('branch', 'enact')}"
        row = graded.setdefault(key, {"n": 0, "score_sum": 0.0})
        row["n"] += 1
        row["score_sum"] = round(row["score_sum"] + float(e.get("score") or 0.0), 6)
    anticipated = [e for e in kinds("price.update") if "anticipated" in e]
    moved = [e for e in anticipated if e["f"]]
    return {
        "lambda_posts": {"posted": len(kinds("lambda_post.posted")), "settled": len(settled),
                         "mean_score": (round(statistics.fmean(e["score"] for e in settled), 4)
                                        if settled else None),
                         "censored": sum(e.get("status") == "censored"
                                         for e in kinds("lambda_post.settled")),
                         "refused": len(kinds("lambda_post.refused")),
                         "aggregates": len(kinds("lambda_post.aggregate")),
                         "targets": sorted({round(e["realized"], 4) for e in settled})},
        "margins": {"read": len(kinds("price.margin")),
                    "identified": sum(e.get("shadow_price") is not None
                                      for e in kinds("price.margin"))},
        "motions": {"proposed": len(kinds("charter.propose")),
                    "enacted": len(kinds("charter.activate")),
                    "rejected": len(kinds("policy.rejected")),
                    "forecasts": len(kinds("policy.forecast")),
                    "forecasts_refused": len(kinds("motion_forecast.refused")),
                    "void": dict(collections.Counter(e.get("branch")
                                                     for e in kinds("policy.void"))),
                    "graded": graded},
        "feed_forward": {"price_updates": len(anticipated), "moved": len(moved),
                         "f_min": min((e["f"] for e in moved), default=None),
                         "f_max": max((e["f"] for e in moved), default=None)},
    }


def print_card(card: dict[str, Any]) -> None:
    print(json.dumps(card, indent=2, default=str))


# --- running ------------------------------------------------------------------------

def simulation_manifest(world: Path, seed: int, vault_tools: bool = False) -> Any:
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
    manifest.validate()
    return manifest


def run(provider_kind: str, ticks: int, world: Path, out: Path, cap_usd: str,
        seed: int, vault_depositor_usd: str | None = None,
        gaps_from: Path | None = None) -> dict[str, Any]:
    """``vault_depositor_usd`` opts the world into the vault surface and scripts one
    outside depositor into every vault the factory creates, who leaves ten steps later;
    the fake's vaults earn nothing on their own, so the depositor pays no commission
    unless a vault's equity moved."""
    from factorylab.runtime.loop import Runtime

    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out / f"{provider_kind}-{stamp}-s{seed}"
    target.mkdir(parents=True, exist_ok=True)
    manifest = simulation_manifest(world, seed, vault_tools=bool(vault_depositor_usd))
    admission = rehearsal.Admission(cap_micro=int(Decimal(cap_usd) * 1_000_000),
                                    max_calls=10_000, recover_provider_failures=True)
    inner = (PolicyProvider(world) if provider_kind == "scripted"
             else rehearsal.build_prepaid_provider(manifest))
    provider = rehearsal.PrepaidProvider(inner, manifest, admission)
    started = time.monotonic()
    card: dict[str, Any] = {"provider": provider_kind, "out": str(target)}
    clock_source = None
    if gaps_from is not None:
        interval = manifest.tick_interval_ns
        clock_source = ReplayClock(interval, interval, ticks, delivered_gaps(gaps_from))
        card["gaps_from"] = str(gaps_from)
    try:
        runtime = Runtime(manifest, events=ticks, seed=manifest.seed,
                          initial_balance_micro=None,
                          ledger_path=str(target / "ledger.jsonl"), router_gamma=0.1,
                          provider=provider, kill_at_end=True, clock_source=clock_source)
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
            "producer_settlements", "orders", "opportunity_cost", "composition")
            if card.get(k) is not None})
    total["clock_by_seed"] = [c.get("clock") for c in cards]
    # Averages and rates are recomputed from the seeds, never summed.
    holds = total.get("opportunity_cost", {})
    total["opportunity_cost"] = {
        "priced": holds.get("priced", 0),
        "mean_y": (round(holds["y_sum"] / holds["priced"], 3)
                   if holds.get("priced") else None)}
    settled = total.get("producer_settlements", {})
    scored = sum(v for k, v in settled.items() if k.startswith("settled:"))
    total["learning_signal_rate"] = (round(scored / sum(settled.values()), 3)
                                     if settled else None)
    chains = [c["reward_chain"] for c in cards if c.get("reward_chain")]
    judges = sum(r["judge_decisions"] for r in chains)
    evaluators = sum(r.get("judge_and_meta_decisions", 0) for r in chains)
    sources = {}
    for source in ("meta_only", "world_only", "both"):
        rows = [r["judge_reward_by_source"][source] for r in chains
                if r.get("judge_reward_by_source")]
        count = sum(row["n"] for row in rows)
        sources[source] = {"n": count, "mean": (round(sum(row["sum"] for row in rows) / count,
                                                      3) if count else None)}
    total["reward_chain"] = {
        "judge_decisions": judges,
        "judge_learning_signal_rate": (
            round(sum(r.get("judge_signalled", 0) for r in chains) / evaluators, 3)
            if evaluators else None),
        "judge_reward_by_source": sources,
        **{share: (round(sum((r[share] or 0) * r["judge_decisions"] for r in chains)
                         / judges, 3) if judges else None)
           for share in ("judge_consequence_share", "judge_meta_grade_share")},
        **{count: sum(r[count] for r in chains)
           for count in ("meta_consequence_events", "exposures_settled", "exposures_nonzero")},
        "noop_share_by_router_by_seed": [r["noop_share_by_router"] for r in chains],
    }
    layers = [c["evaluation_layer"] for c in cards if c.get("evaluation_layer")]
    if layers:
        layer: dict[str, Any] = {}
        for row in layers:
            add(layer, {k: v for k, v in row.items()
                        if k not in ("multi_judged_share", "evaluator_compute_share",
                                     "evaluator_invocation_share")})
        judged, multi = layer.get("judged_returns", 0), layer.get("multi_judged_returns", 0)
        layer["multi_judged_share"] = round(multi / judged, 3) if judged else None
        cost = layer.get("compute_micro_by_role", {})
        spent = sum(cost.values())
        layer["evaluator_compute_share"] = (round(sum(cost.get(r, 0) for r in (
            "evaluator", "meta", "adversary")) / spent, 3) if spent else None)
        calls = layer.get("invocations_by_role", {})
        made = sum(calls.values())
        layer["evaluator_invocation_share"] = (round(sum(calls.get(r, 0) for r in (
            "evaluator", "meta", "adversary")) / made, 3) if made else None)
        total["evaluation_layer"] = layer
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
         *(["--vault-depositor-usd", args.vault_depositor_usd]
           if args.vault_depositor_usd else []),
         *(["--gaps-from", str(args.gaps_from)] if args.gaps_from else [])],
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
    r.add_argument("--vault-depositor-usd", default=None,
                   help="publish the vault surface and script an outside depositor of this "
                        "many USD into each vault the factory creates")
    r.add_argument("--seeds", default=None,
                   help="comma-separated seeds run in parallel processes, e.g. 1,2,3,4")
    r.add_argument("--gaps-from", type=Path, default=None,
                   help="replay the tick gaps this diary (events.json) delivered, in order")
    args = parser.parse_args(argv)
    if args.command == "score":
        print_card(scorecard(json.loads(args.events.read_text())))
        return 0
    if args.seeds:
        card = run_seeds(args, [int(x) for x in args.seeds.split(",")])
        print_card(card)
        return 0 if all(c.get("status") == "completed" for c in card["seeds"]) else 1
    card = run(args.provider, args.ticks, args.world, args.out, args.cap_usd, args.seed,
               args.vault_depositor_usd, args.gaps_from)
    print_card(card)
    return 0 if card.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
