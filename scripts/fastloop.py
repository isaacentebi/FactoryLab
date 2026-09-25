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

``--tape-from <diary>`` replays a past paid run's recorded market instead of the
seeded random walk: its mids, funding rates and delivered tick stamps
(``factorylab/world/tape.py``). The venue stays the fake one (``exchange.kind`` is
``fake``, never a live adapter), its prices a function of time read at or before
the world's own tick; the world's tick is the manifest's and the charter may amend
it; the run ends when the tape does, and the tape never loops. The tape's SHA-256
is fixed in the manifest (``[exchange.tape]``), so it is in the Launch record and a
resume on another tape is refused. ``tape <diary> -o tape.json`` cuts the compact
tape once, so later runs do not re-read a large diary.

A tape world runs on the idle-skipping clock: waiting is skipped, busy time stays
real, so a real model's latency costs the market exactly what it would live.
``--latency-from <diary>`` gives the scripted stand-in the per-call latencies that
diary measured, as modelled busy time. Fills are the recording's, a tick late; the
card's ``fill_band`` shows the P&L beside a pessimistic shadow of it, and its
``pace`` block the busy time, the delivered gaps and the idle skipped. Every model
on the menu must state a training cutoff the tape postdates, unless
``--allow-unknown-cutoff`` admits the unknown ones on the record; web access is off.
Tapes are listed by regime in ``worlds/tapes/library.toml``, whose sealed holdouts run
only with ``--release-candidate``.

``--provider live`` on a tape runs the edition-4 roster, cheap and of several
families, and nothing else: there is no single-family roster (Chapter II §III: a
shared foundation model is a forcing function). A different roster would be a
different world, and a roster is never chosen from what seats did on a tape.

A tape run's profit and loss is an observation about one recorded market. It is
never evidence for a code change: iterating code against a tape until its card
looks right is the architect optimizing toward its own "better" (AGENTS.md rule 2).

Examples::

    uv run python scripts/fastloop.py score work/population-pr121/live/events.json
    uv run python scripts/fastloop.py run --provider scripted --ticks 20
    uv run python scripts/fastloop.py run --ticks 120 --gaps-from work/population-e5a/events.json
    uv run python scripts/fastloop.py run --provider live --ticks 30 --cap-usd 2
    uv run python scripts/fastloop.py run --provider live --ticks 30 --seeds 1,2,3,4
    uv run python scripts/fastloop.py tape work/capital-loop/run3/events.json -o run3.tape.json
    uv run python scripts/fastloop.py run --tape-from run3.tape.json --allow-unknown-cutoff \\
        --latency-from work/capital-loop/longrun1-open
    uv run python scripts/fastloop.py resume work/fastloop/scripted-<stamp>-s1

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

from factorylab.runtime.live import IdleSkipClock  # noqa: E402
from factorylab.runtime.worlds import TapeSpec, load_manifest  # noqa: E402
from factorylab.world.clock import ReplayClock  # noqa: E402
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse  # noqa: E402
from factorylab.world.scripted import (  # noqa: E402
    ScriptedProvider,
    _description_from_prompt,
    _inputs_from_prompt,
    names_declined_trade,
)
from factorylab.world.tape import Tape, TapeVenue  # noqa: E402
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
            if f'"tool:{HALF_SPREAD["id"]}"' in text:
                # Plumbing (time audit T18): a judge forecasts the scripted tool's uptake
                # while it is open on world.uptake, so anticipatory settlement is reached.
                reply["uptake_forecasts"] = [{"registration": f"tool:{HALF_SPREAD['id']}",
                                              "q": 0.6}]
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
        # The return contract, not a behaviour: a producing final answer that executes
        # nothing names the trade it declined (factorylab.runtime.grounded).
        reply = names_declined_trade(reply, text, inputs, self.decisions)
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
        return {"action": "hold", "rationale": "no mechanism worth trading yet",
                "propensity": {"hold": 0.7, "investigate": 0.2, "order": 0.1}}

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


def call_latencies(path: Path) -> list[int]:
    """Each model call's duration, in milliseconds, as a paid diary measured it.

    A paid run reads the wall clock through its journal before every model call (the
    safety pass, time audit T8), so two consecutive reads inside one event with exactly
    one call between them bracket that call: the call's real busy time and the kernel's
    own work around it. ``path`` is a diary (``events.json`` or a directory split by
    kind) or a JSON list of milliseconds already extracted.
    """
    path = Path(path)
    if path.is_file() and path.read_text()[:64].lstrip().startswith("[") and path.stat(
            ).st_size < 1 << 20:
        data = json.loads(path.read_text())
        if all(type(x) is int for x in data):
            return data
    rows = _call_rows(path)
    out, last, between = [], None, 0
    for _, kind, value in sorted(rows):
        if kind == "input":
            last, between = None, 0
        elif kind == "call":
            between += 1
        else:
            if last is not None and between == 1 and value > last:
                out.append((value - last) // 1_000_000)
            last, between = value, 0
    if not out:
        raise ValueError(f"{path} journaled no wall reads around its model calls")
    return out


def _call_rows(path: Path) -> list[tuple[int, str, int]]:
    """(seq, kind, value) for every event boundary, model call and wall read of a diary."""
    import re

    from factorylab.world.tape import _array_items

    rows: list[tuple[int, str, int]] = []
    walls: set[int] = set()
    if path.is_dir():
        with open(path / "io.call.jsonl", encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if item["name"] == "provider.complete":
                    rows.append((item["seq"], "call", 0))
                elif item["name"] == "wall.now_ns":
                    walls.add(item["seq"])
        pattern = re.compile(r'^\{"call": (\d+)')
        with open(path / "io.result.jsonl", encoding="utf-8") as handle:
            for line in handle:
                match = pattern.match(line)
                if match and int(match.group(1)) in walls:
                    item = json.loads(line)
                    rows.append((item["seq"], "wall", int(item["result"])))
        with open(path / "runtime.input.jsonl", encoding="utf-8") as handle:
            rows.extend((json.loads(line)["seq"], "input", 0) for line in handle)
        return rows
    names: dict[int, str] = {}
    for item in _array_items(path):
        kind = item.get("kind")
        if kind == "io.call":
            names[item["seq"]] = item["name"]
            if item["name"] == "provider.complete":
                rows.append((item["seq"], "call", 0))
        elif kind == "io.result" and names.get(item.get("call")) == "wall.now_ns":
            rows.append((item["seq"], "wall", int(item["result"])))
        elif kind == "runtime.input":
            rows.append((item["seq"], "input", 0))
    return rows


class Latent:
    """A stand-in provider whose every call takes a latency a paid diary's calls took.

    The latency is modelled busy time on the world's idle-skipping clock, never slept
    (critique C1: a zero-latency stand-in on a paced clock is the virtual clock again,
    blind to every timing failure). A call whose drawn latency outlives the deadline
    the runtime stated for it (``ModelRequest.timeout_s``, time audit T8) takes the
    deadline and expires, as a real call would. ``clock`` is bound by the harness to
    the runtime's tick clock once there is one (and again after a restore).
    """

    def __init__(self, inner: Any, samples_ms: list[int], seed: int) -> None:
        import random

        if not samples_ms:
            raise ValueError("a latency model needs at least one measured call")
        self.inner, self.samples_ms = inner, list(samples_ms)
        self.rng = random.Random(seed)
        self.clock: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def complete(self, req: ModelRequest) -> ModelResponse:
        from factorylab.world.openai_wire import CALL_EXPIRED
        from factorylab.world.openrouter import OpenRouterError

        latency = self.rng.choice(self.samples_ms) * 1_000_000
        deadline = (None if req.timeout_s is None
                    else int(Decimal(str(req.timeout_s)) * 1_000_000_000))
        if deadline is not None and latency > deadline:
            self.clock.spend(deadline)
            raise OpenRouterError(None, CALL_EXPIRED)
        self.clock.spend(latency)
        return self.inner.complete(req)


#: Where a run keeps, beside its diary, what its provider side must carry across a crash.
PROVIDER_RECORD = "provider-record.json"
#: The scripted policy's own counters, which decide what it answers next.
POLICY_STATE = ("decisions", "proposed", "tool_listed")


class RecordedProvider:
    """The harness's provider, whose spend bound and stand-in state survive any crash.

    Guarantees (Codex review of #151):

    - The admission cap is one bound over the whole run, whatever number of crashes and
      resumes it takes. Before each call is dispatched the record is written with that
      call's quote pending; after it returns or fails, with its outcome. A resume
      restores the totals, and a call whose outcome the record never saw (the process
      died inside it) is counted at its whole quote as an uncertain bill, never as free.
    - A resumed scripted run continues the scripted policy's counters and the latency
      model's random stream where they stood, so it makes the calls the uninterrupted run
      would have made. A replayed call is answered from the diary and never reaches this
      object, so nothing is counted or drawn twice.

    Each write is a whole file replaced atomically and flushed to disk before the call
    it names is dispatched (``_write``).
    """

    def __init__(self, prepaid: Any, admission: Any, path: Path, *, policy: Any = None,
                 latent: Any = None) -> None:
        self.prepaid, self.admission, self.path = prepaid, admission, Path(path)
        self.policy, self.latent = policy, latent

    def __getattr__(self, name: str) -> Any:
        return getattr(self.prepaid, name)

    def complete(self, req: ModelRequest) -> ModelResponse:
        self._write(pending=self.prepaid._ceiling(req))  # written ahead of the dispatch
        try:
            return self.prepaid.complete(req)
        finally:
            self._write(pending=None)

    def state(self) -> dict[str, Any]:
        from dataclasses import asdict

        state: dict[str, Any] = {"admission": asdict(self.admission)}
        if self.policy is not None:
            state["policy"] = {k: getattr(self.policy, k) for k in POLICY_STATE
                               if hasattr(self.policy, k)}
        if self.latent is not None:
            version, internal, gauss = self.latent.rng.getstate()
            state["latency_rng"] = [version, list(internal), gauss]
        return state

    def _write(self, *, pending: int | None) -> None:
        import os

        data = json.dumps({"format": 1, **self.state(), "pending_quote_micro": pending},
                          sort_keys=True).encode()
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def restore(self) -> dict[str, Any]:
        """Continue the recorded state; return what the admission holds on resuming."""
        record = json.loads(self.path.read_text()) if self.path.exists() else {}
        for name, value in (record.get("admission") or {}).items():
            if name != "cap_micro":  # the cap is the run's, stated at launch
                setattr(self.admission, name, value)
        pending = record.get("pending_quote_micro")
        if pending is not None:
            # The process died inside a call: its bill is unknown, so its whole quote
            # counts against the cap, as a dispatched failure's does.
            self.admission.attempted += 1
            self.admission.unknown_bills += 1
            self.admission.uncertain_bills += 1
            self.admission.uncertain_micro += int(pending)
        for name, value in (record.get("policy") or {}).items():
            setattr(self.policy, name, value)
        if self.latent is not None and record.get("latency_rng") is not None:
            version, internal, gauss = record["latency_rng"]
            self.latent.rng.setstate((version, tuple(internal), gauss))
        return {"known_micro": self.admission.known_micro,
                "uncertain_micro": self.admission.uncertain_micro,
                "remaining_micro": self.admission.remaining_micro,
                "attempted": self.admission.attempted,
                "died_inside_a_call": pending is not None}


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
        "immune": immune(events),
        "orders": {"intents": dict(intents),
                   "reported_not_placed": kinds.get("order.reported", 0),
                   "refused": kinds.get("order.refused", 0),
                   "infeasible": kinds.get("order.infeasible", 0)},
        "clock": clock(events),
        "tape": tape_card(events),
        "fill_band": fill_band(events),
        "pace": pace(events),
    }


def pace(events: list[dict[str, Any]], tick_clock: Any = None,
         latency_model: str | None = None) -> dict[str, Any] | None:
    """How the world kept pace with its own declared tick (Chapter II §IV.c; critique C1).

    From the diary alone: the declared tick, the delivered gaps and the share of ticks
    that fired late. With the idle-skipping clock that ran it: each tick's busy time
    (its gap less the idle skipped before it), the share of world time that was idle
    and skipped, the busy time a latency model contributed, and which model it was
    (None: a zero-latency stand-in, so the pace measures only the kernel's own work).
    Observations, never targets.
    """
    manifest = _launch_manifest(events) or {}
    declared = manifest.get("tick_interval_ns")
    stamps = [int((e.get("event") or {}).get("ts_ns") or 0) for e in events
              if e.get("kind") == "event" and (e.get("event") or {}).get("kind") == "Tick"]
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    if not declared or not gaps:
        return None
    out: dict[str, Any] = {
        "declared_s": declared / 1e9,
        "delivered_s": {"p50": round(_pct(gaps, 0.5) / 1e9, 2),
                        "p90": round(_pct(gaps, 0.9) / 1e9, 2),
                        "max": round(max(gaps) / 1e9, 2)},
        "late_share": round(sum(g > declared for g in gaps) / len(gaps), 3)}
    idle = getattr(tick_clock, "idle", None)
    if idle is not None and hasattr(tick_clock, "skipped_ns"):
        # The idle list covers the gaps this process delivered, the latest ones.
        busy = [g - i for g, i in zip(gaps[-len(idle):] if idle else [], idle, strict=False)]
        span = stamps[-1] - stamps[0]
        out.update({
            "busy_per_tick_s": ({"p50": round(_pct(busy, 0.5) / 1e9, 2),
                                 "p90": round(_pct(busy, 0.9) / 1e9, 2)} if busy else None),
            "idle_skipped_share": round(sum(idle) / span, 3) if span else None,
            "modelled_busy_s": round(tick_clock.modelled_ns / 1e9, 1),
            "latency_model": latency_model})
    return out


def fill_band(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The venue's profit and loss on a tape, and the same recomputed pessimistically.

    Both numbers, always together (critique H1): ``pnl_usd`` is what the tape's fill
    rules booked -- realized P&L less fees less funding, plus what the positions still
    open were worth at the last recorded mid -- and ``pessimistic_pnl_usd`` charges every
    fill an extra adverse slippage of half the market's spread as the tape states it
    (``[exchange.tape] spread_bps``). An observation about one recorded market, never
    evidence for a code change (AGENTS.md rule 2). None off a tape. Integer micro-USD
    throughout; the extra slippage rounds against the factory.
    """
    from factorylab.kernel.money import money_to_usd, usd_to_micro

    tape = ((_launch_manifest(events) or {}).get("exchange") or {}).get("tape")
    if not isinstance(tape, dict):
        return None
    spreads = {market: Decimal(bps) for market, bps in (tape.get("spread_bps") or ())}
    realized = fees = funding = extra = notional = 0
    positions: dict[str, tuple[Decimal, Decimal]] = {}  # coin -> (signed size, entry)
    last_mid: dict[str, Decimal] = {}
    fills = 0
    for e in events:
        kind = e.get("kind")
        if kind == "event":
            event = e.get("event") or {}
            payload = event.get("payload") or {}
            if event.get("kind") == "MarketMid":
                last_mid[payload["coin"]] = Decimal(str(payload["mid"]))
            continue
        if kind == "venue.settled" and e.get("reason") == "funding":
            # The settled funding, the world's end's partial hour included (it is booked
            # but, the world being over, never delivered as an event).
            funding -= int(e.get("amount") or 0)
            continue
        if kind != "fill.counted":
            continue
        fills += 1
        realized += int(e.get("realized_micro") or 0)
        fees += int(e.get("fee_micro") or 0)
        notional += int(e.get("notional_micro") or 0)
        half = spreads.get(e["coin"], Decimal(0)) / 20_000
        cost = Decimal(int(e.get("notional_micro") or 0)) * half
        extra += int(cost.to_integral_value(rounding="ROUND_CEILING"))
        size, px = Decimal(str(e["size"])), Decimal(str(e["px"]))
        signed = size if e.get("is_buy") else -size
        held, entry = positions.get(e["coin"], (Decimal(0), Decimal(0)))
        new = held + signed
        if held == 0 or (held > 0) == (signed > 0):
            entry = (entry * abs(held) + px * size) / abs(new) if new else Decimal(0)
        elif (new > 0) != (held > 0) and new != 0:
            entry = px  # through zero: the residual opened at this fill
        positions[e["coin"]] = (new, entry if new else Decimal(0))
    unrealized = sum((usd_to_micro((last_mid[coin] - entry) * size, rounding="nearest")
                      for coin, (size, entry) in positions.items()
                      if size and coin in last_mid), 0)
    pnl = realized - fees - funding + unrealized
    return {"fills": fills, "notional_usd": str(money_to_usd(notional)),
            "realized_usd": str(money_to_usd(realized)), "fees_usd": str(money_to_usd(fees)),
            "funding_usd": str(money_to_usd(funding)),
            "unrealized_usd": str(money_to_usd(unrealized)),
            "pnl_usd": str(money_to_usd(pnl)),
            "pessimistic_pnl_usd": str(money_to_usd(pnl - extra)),
            "extra_slippage_usd": str(money_to_usd(extra)),
            "half_spread_bps": {m: str(bps / 2) for m, bps in sorted(spreads.items())}}


def tape_card(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The recorded market a diary's world replayed, from its Launch record alone.

    None for a world on the seeded random walk. The identity is the manifest's
    ``[exchange.tape]``: what the diary says it ran on, not what a harness claims.
    """
    manifest = _launch_manifest(events) or {}
    tape = (manifest.get("exchange") or {}).get("tape")
    if not isinstance(tape, dict):
        return None
    models = manifest.get("models") or []
    return {"sha256": tape["sha256"], "venue": f"tape:{tape['sha256'][:8]}",
            "start_ns": tape["start_ns"], "end_ns": tape["end_ns"],
            "hours": round((tape["end_ns"] - tape["start_ns"]) / 3.6e12, 3),
            "markets": tape.get("markets"),
            "spread_bps": dict(tape.get("spread_bps") or ()),
            # The look-ahead guard as the Launch record states it: the cutoffs the
            # tape postdates, the models admitted with none (only when the operator
            # allowed it), and the outside the world could read (none: web is off).
            "training_cutoffs": {m["id"]: m.get("training_cutoff") for m in models
                                 if m.get("training_cutoff")},
            "unknown_cutoffs": sorted(m["id"] for m in models if not m.get("training_cutoff")),
            "allow_unknown_cutoff": bool(tape.get("allow_unknown_cutoff")),
            "web": ("off" if not (manifest.get("web") or {}).get("search_model")
                    and not any(m["id"].endswith(":online") or m.get("web") for m in models)
                    else "on")}


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
        "governance": {k: sum(1 for e in events if e.get("kind") == f"governance.{k}")
                       for k in ("nonviable", "viable", "settling")},
    }


def _launch_manifest(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The genesis manifest a diary's Launch event carries, when it carries one."""
    for e in events:
        event = e.get("event") or {}
        if e.get("kind") == "event" and event.get("kind") == "Launch":
            manifest = (event.get("payload") or {}).get("manifest")
            return manifest if isinstance(manifest, dict) else None
    return None


def immune(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Versions, pathologies and their priced answers, from the diary alone (versioning M4).

    Observations, never targets (rulings R12): the live versions and each one's gap;
    the pathology flags by kind and the responses the organ applied; what the niche
    for unhistoried actions spent (ruling R5); the thrash price; the dependency
    concentration the window observations measured (time audit T15); the uptake
    market (T18); and the learning-death signals window by window, with whether the
    forensic replay of the organ's own records reproduces its flags. That replay is
    self-consistency (the same code over the same records), not a second detector:
    the detector's scenarios are pinned by tests.
    """
    windows = [e for e in events if e.get("kind") == "immune.window"]
    boundaries = [e for e in events if e.get("kind") == "version.boundary"]
    closed = [b["closed"] for b in boundaries]
    last = windows[-1] if windows else {}
    versions = [{"version": c["version"], "cause": c["cause"], "windows": [
        c["start_window"], c["end_window"]], "gap": c["gap"],
        "settling_ticks": c["settling_ticks"]} for c in closed]
    if last:
        versions.append({"version": last.get("version"), "cause": (
            boundaries[-1]["cause"] if boundaries else "launch"), "windows": [
            boundaries[-1]["window"] if boundaries else 1, last.get("window")],
            "gap": last.get("gap"), "settling_ticks": None, "open": True})
    flags = collections.Counter(kind for w in windows
                                for kind, on in (w.get("flags") or {}).items() if on)
    gains = collections.Counter(e.get("pathology") for e in events
                                if e.get("kind") == "immune.gain")
    kinds = collections.Counter(e.get("kind") for e in events)
    niche_handles: set[str] = set()
    niche_used = seat_used = 0
    for e in events:
        if e.get("kind") == "niche.action":
            niche_handles.add(e.get("handle"))
        elif e.get("kind") == "novelty.compute":
            if e.get("handle") in niche_handles:
                niche_used += int(e.get("used") or 0)
            else:
                seat_used += int(e.get("used") or 0)
    thrash = [w.get("thrash") or {} for w in windows]
    charged = [e for e in events if e.get("kind") == "thrash.charged"]
    concentration: dict[str, list[float]] = {"provider_concentration": [],
                                             "family_concentration": []}
    for e in events:
        if e.get("kind") == "price.window":
            for name, series in concentration.items():
                value = (e.get("observations") or {}).get(name)
                if value is not None:
                    series.append(value)
    return {
        "windows": len(windows),
        "versions": versions,
        "version_boundaries": dict(collections.Counter(b["cause"] for b in boundaries)),
        "settled": sum(1 for e in events if e.get("kind") == "version.settled"
                       and e.get("settled")),
        "pathology_windows": dict(flags),
        "responses": {"gain_steps": dict(gains),
                      "price_ratchets": kinds.get("immune.price_ratchet", 0),
                      "thrash_charges": len(charged),
                      "acted_windows": sum(1 for w in windows if w.get("acts"))},
        "niche": {"actions": kinds.get("niche.action", 0),
                  "action_spend_micro": niche_used,
                  "seat_trial_spend_micro": seat_used,
                  "registrations": kinds.get("novelty.reserve", 0)},
        "thrash_price": {
            "priced_windows": sum(1 for t in thrash if (t.get("penalty") or 0) > 0),
            "max_lambda": round(max((t.get("lambda") or 0 for t in thrash), default=0), 4),
            "max_penalty": round(max((t.get("penalty") or 0 for t in thrash), default=0), 4),
            "charged_sum": round(sum(e.get("charge") or 0 for e in charged), 4),
            "signals": {name: sum(1 for w in windows if w.get(field))
                        for name, field in (("unsettled", "unsettled"),
                                            ("periodic", "period"),
                                            ("abandoned", "abandoned"),
                                            ("short_lived", "short_lived"))}},
        "dependency_concentration": {
            name: ({"mean": round(statistics.fmean(v), 3), "max": round(max(v), 3)}
                   if v else None) for name, v in concentration.items()},
        "uptake": {kind: kinds.get(f"uptake.{kind}", 0)
                   for kind in ("open", "forecast", "anticipated", "taken", "settled")},
        "config_lifespans_short": sum(1 for e in events if e.get("kind") == "config.lifespan"
                                      and e.get("ratio", 1) < 1),
        "learning_death": _learning_death_signals(events, windows),
    }


def _learning_death_signals(events: list[dict[str, Any]],
                            windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The learning-death flags and the frontier signals under them, and replay consistency.

    Windows with a frontier router left uninvoked and with one quarantining its
    newcomers at the exploration floor; ``replay_self_consistency`` is the share of
    windows whose ledgered flag the forensic replay of the organ's own records
    (``versioning.versions.replay``) reproduces: a check that the reader and the
    organ run one predicate, not evidence that the predicate is right.
    """
    from factorylab.versioning.versions import replay

    manifest = _launch_manifest(events)
    if not windows or manifest is None or not isinstance(manifest.get("immune"), dict):
        return None
    spec = manifest["immune"]
    k = spec["k"]
    horizon = (manifest.get("timing") or {}).get("min_ratio", 3) * k
    records = [{"index": w["window"], "tick": w.get("tick", w["window"]),
                "charter_edition": w.get("charter_edition"), "terms": w.get("terms"),
                "profile": w.get("profile") or {}, "regions": w.get("regions") or {},
                **({"frontier_invocation": w["frontier_invocation"]}
                   if "frontier_invocation" in w else {}),
                "lifespans": w.get("lifespans") or []} for w in windows]
    readings = replay(records, k=k, horizon=horizon, tv_threshold=spec["tv_threshold"],
                      gap_threshold=spec["gap_threshold"],
                      registration_bins=tuple(spec["registration_bins"]),
                      revision_bins=tuple(spec["revision_bins"]))
    live = [bool((w.get("flags") or {}).get("learning_death")) for w in windows]
    forensic = [r["diagnosis"]["flags"]["learning_death"] for r in readings]
    uninvoked = sum(1 for w in windows if (w.get("frontier") or {}).get("uninvoked_routers"))
    quarantined = sum(1 for w in windows
                      if (w.get("frontier") or {}).get("quarantined_routers"))
    return {"flags": sum(live), "replayed_flags": sum(forensic),
            "replay_self_consistency": round(
                sum(a == b for a, b in zip(live, forensic, strict=True)) / len(live), 3),
            "uninvoked_windows": uninvoked, "quarantined_windows": quarantined}


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

def simulation_manifest(world: Path, seed: int, vault_tools: bool = False,
                        tape: Tape | None = None, allow_unknown_cutoff: bool = False) -> Any:
    """The launch identity with only the venue swapped for the deterministic fake.

    ``effective_manifest`` freezes the roster, charter, seed lenses, prompts and
    provider-native completion allowances exactly as a rehearsal would. Then the
    exchange becomes the seeded fake venue, which makes the whole world simulated:
    a virtual clock (no sleeping between ticks), a moving market whose resting
    orders fill, and the fake treasury. Everything the population is shown and
    graded by is the launch path's.

    With ``tape``, the fake venue replays that recorded market, and the tape's
    identity joins the manifest (``[exchange.tape]``): its SHA-256, span, markets and
    spreads, so the Launch record carries it and a resume on another tape is refused.
    A tape world reads nothing of today's web (critique C2; the choice is "off", not a
    declared confound): its ``[web]`` search route and every ``:online`` or
    search-plugin model leave the menu, and the runtime publishes no connector fetch.
    Every remaining model must state a training cutoff the tape postdates, unless
    ``allow_unknown_cutoff`` admits the unknown ones, which the manifest then records.
    """
    from dataclasses import replace

    from factorylab.runtime.worlds import WebSpec

    base = load_manifest(world)
    # A hybrid capital-loop world keeps its Venice keys: on the fake venue they select
    # the scripted two-leg rail, so both conversion legs are rehearsed for free.
    hybrid = (base.treasury.venice_network == "base-mainnet"
              and base.treasury.reserve_address is not None)
    manifest = rehearsal.effective_manifest(base, native_completions=True,
                                            capital_loop=hybrid)
    exchange = replace(manifest.exchange, kind="fake", mainnet=False, seed=seed,
                       spot_pairs=(), client_namespace=None,
                       vault_tools=vault_tools or manifest.exchange.vault_tools,
                       tape=None if tape is None else tape_spec(tape, allow_unknown_cutoff))
    manifest = replace(manifest, exchange=exchange, seed=seed)
    if tape is not None:
        manifest = replace(manifest, web=WebSpec(), models=tuple(
            m for m in manifest.models if not (m.id.endswith(":online") or m.web)))
    manifest.validate()
    return manifest


def tape_spec(tape: Tape, allow_unknown_cutoff: bool = False) -> TapeSpec:
    """The manifest's ``[exchange.tape]`` for a tape: its identity, fixed for the world."""
    return TapeSpec.of(tape, allow_unknown_cutoff)  # every field derived from the tape


#: The tape library: recorded markets by regime, and the sealed holdouts.
LIBRARY = ROOT / "worlds" / "tapes" / "library.toml"
#: The regimes a library entry may name (their rules are in the library's header).
REGIMES = frozenset({"trend", "chop", "jump", "funding_flip", "outage"})


def tape_library(path: Path = LIBRARY) -> dict[str, dict[str, Any]]:
    """The library's entries by tape SHA-256, each a known regime and a holdout flag."""
    import tomllib

    entries: dict[str, dict[str, Any]] = {}
    for entry in tomllib.loads(Path(path).read_text()).get("tape", []):
        sha = entry.get("sha256")
        if (not isinstance(sha, str) or len(sha) != 64 or sha in entries
                or not set(entry.get("regimes") or ()) <= REGIMES
                or type(entry.get("holdout")) is not bool):
            raise ValueError(f"tape library entry {entry.get('id')!r} is malformed")
        entries[sha] = entry
    return entries


def library_entry(tape: Tape, release_candidate: bool = False,
                  path: Path = LIBRARY) -> dict[str, Any] | None:
    """The library's entry for ``tape``, refusing a sealed holdout off a release candidate.

    Critique H4: a holdout tape runs only on a release candidate, and only for plumbing
    invariants, so no iteration ever sees it. A tape the library does not list runs,
    and the card says it is unlisted.
    """
    entry = tape_library(path).get(tape.sha256)
    if entry and entry["holdout"] and not release_candidate:
        raise ValueError(f"sealed_holdout: tape {entry['id']!r} runs only with "
                         "--release-candidate")
    return entry


def tape_venue(tape: Tape, manifest: Any) -> TapeVenue:
    """The fake venue replaying ``tape`` for the manifest's markets, funded as bootstrap
    funds the seeded fake."""
    from factorylab.kernel.money import money_to_usd

    return TapeVenue(tape, coins=manifest.exchange.coins,
                     spot_pairs=manifest.exchange.spot_pairs, seed=manifest.exchange.seed,
                     start_cash_usd=money_to_usd(manifest.initial_balance_micro))


def tape_clock(tape: Tape, manifest: Any, ticks: int | None) -> IdleSkipClock:
    """The world's own clock over a tape: the manifest's tick from the tape's first
    instant, idle skipped and busy time real, ending before the first tick past the
    tape's last (Chapter II §IV.c: the world keeps its own period, which the charter
    may amend, and is never slower than its environment; the tape is sampled at it)."""
    bound = (tape.end_ns - tape.start_ns) // manifest.clock.min_tick_ns + 1
    return IdleSkipClock(manifest.tick_interval_ns, bound if ticks is None else min(ticks, bound),
                         origin_ns=tape.start_ns, deadline_ns=tape.end_ns + 1)


def _world_parts(provider_kind: str, world: Path, seed: int, cap_usd: str,
                 vault_depositor_usd: str | None, tape: Tape | None,
                 latency_from: Path | None = None,
                 allow_unknown_cutoff: bool = False, record: Path | None = None) -> tuple:
    """The manifest, admission, provider, venue and latency model of one run.

    With ``record`` the provider is a ``RecordedProvider`` keeping its spend and its
    stand-in's state in that file, so a resume continues both."""
    manifest = simulation_manifest(world, seed, vault_tools=bool(vault_depositor_usd),
                                   tape=tape, allow_unknown_cutoff=allow_unknown_cutoff)
    admission = rehearsal.Admission(cap_micro=int(Decimal(cap_usd) * 1_000_000),
                                    max_calls=10_000, recover_provider_failures=True)
    inner = policy = (PolicyProvider(world) if provider_kind == "scripted"
                      else rehearsal.build_prepaid_provider(manifest))
    latent = None
    if latency_from is not None:
        if provider_kind != "scripted" or tape is None:
            raise ValueError("--latency-from models a scripted stand-in's calls on a tape's "
                             "idle-skipping clock; real calls take their real time")
        inner = latent = Latent(inner, call_latencies(latency_from), seed)
    provider = rehearsal.PrepaidProvider(inner, manifest, admission)
    if record is not None:
        provider = RecordedProvider(
            provider, admission, record, latent=latent,
            policy=policy if isinstance(policy, PolicyProvider) else None)
    exchange = None if tape is None else tape_venue(tape, manifest)
    return manifest, admission, provider, exchange, latent


def _prepare(runtime: Any, vault_depositor_usd: str | None, latent: Any = None) -> None:
    surface = getattr(runtime, "polymarket", None)
    if surface is not None and not surface.writes:
        # A live-read world's Polymarket reads are answered by the seeded simulated
        # venue, as its exchange is: the whole run stays simulated and offline.
        from factorylab.runtime.polymarket import simulate_reads

        simulate_reads(runtime)
    if vault_depositor_usd:
        runtime.exchange.vault_depositor_usd = Decimal(vault_depositor_usd)
        runtime.exchange.vault_depositor_steps = 10
    if latent is not None:
        latent.clock = runtime.tick_clock  # the restored clock, after a resume


def _finish(card: dict[str, Any], runtime: Any, target: Path, admission: Any,
            started: float) -> dict[str, Any]:
    card["wall_seconds"] = round(time.monotonic() - started, 1)
    if runtime is not None:
        events = [i for i in runtime.ledger._recovery_items() if i.get("kind") != "snapshot"]
        (target / "events.json").write_text(json.dumps(events, default=str) + "\n")
        card.update(scorecard(events))
        card["pace"] = pace(events, runtime.tick_clock, latency_model=card.get("latency_from"))
    card["billed_usd"] = str(Decimal(admission.known_micro) / Decimal(1_000_000))
    card["admission_stop"] = admission.stop_reason
    (target / "scorecard.json").write_text(json.dumps(card, indent=2, default=str) + "\n")
    return card


def run(provider_kind: str, ticks: int | None, world: Path, out: Path, cap_usd: str,
        seed: int, vault_depositor_usd: str | None = None,
        gaps_from: Path | None = None, tape_from: Path | None = None,
        latency_from: Path | None = None,
        allow_unknown_cutoff: bool = False,
        release_candidate: bool = False) -> dict[str, Any]:
    """``vault_depositor_usd`` opts the world into the vault surface and scripts one
    outside depositor into every vault the factory creates, who leaves ten steps later;
    the fake's vaults earn nothing on their own, so the depositor pays no commission
    unless a vault's equity moved.

    ``tape_from`` (a diary or a cut tape) replays that recorded market on the
    idle-skipping clock; ``ticks`` then bounds the run, which otherwise ends when the
    tape does. ``latency_from`` gives the scripted stand-in the call latencies a paid
    diary measured, as modelled busy time. ``allow_unknown_cutoff`` admits models that
    state no training cutoff onto a tape, and the manifest records that it did;
    ``release_candidate`` admits a sealed holdout tape (``worlds/tapes/library.toml``)."""
    from factorylab.runtime.loop import Runtime

    if tape_from is not None and gaps_from is not None:
        raise ValueError("a tape world keeps its own tick; --gaps-from does not apply")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out / f"{provider_kind}-{stamp}-s{seed}"
    target.mkdir(parents=True, exist_ok=True)
    tape = None if tape_from is None else Tape.load(tape_from)
    entry = None if tape is None else library_entry(tape, release_candidate)
    manifest, admission, provider, exchange, latent = _world_parts(
        provider_kind, world, seed, cap_usd, vault_depositor_usd, tape, latency_from,
        allow_unknown_cutoff, record=target / PROVIDER_RECORD)
    started = time.monotonic()
    card: dict[str, Any] = {"provider": provider_kind, "out": str(target)}
    if tape is not None:
        card["tape_library"] = ({k: entry[k] for k in ("id", "regimes", "holdout")}
                                if entry else "unlisted")
    clock_source = None
    if gaps_from is not None:
        ticks = 20 if ticks is None else ticks
        interval = manifest.tick_interval_ns
        clock_source = ReplayClock(interval, interval, ticks, delivered_gaps(gaps_from))
        card["gaps_from"] = str(gaps_from)
    elif tape is not None:
        clock_source = tape_clock(tape, manifest, ticks)
        ticks = clock_source.count
        card["tape_from"] = str(tape_from)
        card["latency_from"] = None if latency_from is None else str(latency_from)
    ticks = 20 if ticks is None else ticks
    # What a resume needs to rebuild this world exactly (``resume``).
    (target / "run.json").write_text(json.dumps({
        "provider": provider_kind, "world": str(world), "seed": seed, "cap_usd": cap_usd,
        "vault_depositor_usd": vault_depositor_usd,
        "tape_from": None if tape_from is None else str(tape_from),
        "tape_sha256": None if tape is None else tape.sha256,
        "gaps_from": None if gaps_from is None else str(gaps_from),
        "latency_from": None if latency_from is None else str(latency_from),
        "allow_unknown_cutoff": allow_unknown_cutoff,
        "release_candidate": release_candidate}, indent=2) + "\n")
    runtime = None
    try:
        runtime = Runtime(manifest, events=ticks, seed=manifest.seed,
                          initial_balance_micro=None,
                          ledger_path=str(target / "ledger.jsonl"), router_gamma=0.1,
                          provider=provider, exchange=exchange, kill_at_end=True,
                          clock_source=clock_source)
        _prepare(runtime, vault_depositor_usd, latent)
        summary = runtime.run()
        card["status"] = "completed"
        card["terminated"] = summary.get("terminated")
    except Exception as exc:  # the scorecard still reads what was written
        card["status"] = "failed"
        card["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return _finish(card, runtime, target, admission, started)


def resume(target: Path, tape_from: Path | None = None) -> dict[str, Any]:
    """Continue a killed run in ``target`` from its diary, on the world it launched.

    Guarantees the tape a tape world resumes on is the one it launched on: its SHA-256
    is compared with the one recorded at launch before the diary is touched, and the
    runtime compares it again with the manifest's ``[exchange.tape]``. The clock
    continues as the clock it was, from the world's saved instant.
    """
    from factorylab.runtime.bootstrap import TapeMismatch
    from factorylab.runtime.resume import resume_runtime

    spec = json.loads((target / "run.json").read_text())
    source = tape_from or (spec["tape_from"] and Path(spec["tape_from"]))
    tape = None if source is None else Tape.load(source)
    if (tape and tape.sha256) != spec.get("tape_sha256"):
        raise TapeMismatch(f"tape_mismatch: launched on {str(spec.get('tape_sha256'))[:12]}, "
                           f"resumed on {str(tape and tape.sha256)[:12]}")
    if tape is not None:
        library_entry(tape, spec.get("release_candidate", False))
    latency_from = spec.get("latency_from") and Path(spec["latency_from"])
    manifest, admission, provider, exchange, latent = _world_parts(
        spec["provider"], Path(spec["world"]), spec["seed"], spec["cap_usd"],
        spec.get("vault_depositor_usd"), tape, latency_from,
        spec.get("allow_unknown_cutoff", False), record=target / PROVIDER_RECORD)
    clock_source = None if tape is None else tape_clock(tape, manifest, None)
    if spec.get("gaps_from"):
        # The clock the run checkpointed: a replay of that diary's gaps. The saved state
        # (its place in the gaps, its sample, its budget) is restored over this one.
        interval = manifest.tick_interval_ns
        clock_source = ReplayClock(interval, interval, 1, delivered_gaps(spec["gaps_from"]))
    started = time.monotonic()
    card: dict[str, Any] = {"provider": spec["provider"], "out": str(target), "resumed": True,
                            "latency_from": spec.get("latency_from"),
                            # The whole run's spend so far: what the cap still allows.
                            "admission_at_resume": provider.restore()}
    runtime = None
    try:
        runtime = resume_runtime(
            manifest, str(target / "ledger.jsonl"), provider=provider, exchange=exchange,
            clock_source=clock_source,
            before_replay=lambda rt: _prepare(rt, spec.get("vault_depositor_usd"), latent))
        summary = runtime.run()
        card["status"] = "completed"
        card["terminated"] = summary.get("terminated")
    except Exception as exc:  # the scorecard still reads what was written
        card["status"] = "failed"
        card["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return _finish(card, runtime, target, admission, started)



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
    total["immune_by_seed"] = [c.get("immune") for c in cards]
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
    total["seeds"] = [{**{k: c.get(k) for k in ("status", "error", "out",
                                                "learning_signal_rate", "billed_usd")},
                       # Seeds on one tape vary the routers, not the market: each seed
                       # names its tape so seeds are read nested within tapes.
                       "tape": (c.get("tape") or {}).get("sha256")}
                      for c in cards]
    return total


def run_seeds(args: argparse.Namespace, seeds: list[int]) -> dict[str, Any]:
    """Independent worlds in parallel processes: one wall time, several samples."""
    import subprocess

    procs = [subprocess.Popen(
        [sys.executable, __file__, "run", "--provider", args.provider,
         *(["--ticks", str(args.ticks)] if args.ticks is not None else []),
         "--world", str(args.world), "--out", str(args.out),
         "--cap-usd", args.cap_usd, "--seed", str(seed),
         *(["--vault-depositor-usd", args.vault_depositor_usd]
           if args.vault_depositor_usd else []),
         *(["--gaps-from", str(args.gaps_from)] if args.gaps_from else []),
         *(["--tape-from", str(args.tape_from)] if args.tape_from else []),
         *(["--latency-from", str(args.latency_from)] if args.latency_from else []),
         *(["--allow-unknown-cutoff"] if args.allow_unknown_cutoff else []),
         *(["--release-candidate"] if args.release_candidate else [])],
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
    r.add_argument("--ticks", type=int, default=None,
                   help="ticks to run (default 20; on a tape, until the tape ends)")
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
    r.add_argument("--tape-from", type=Path, default=None,
                   help="replay the market this diary (events.json, a directory split by "
                        "kind, or a cut tape) recorded; the run ends when the tape does")
    r.add_argument("--latency-from", type=Path, default=None,
                   help="on a tape, give the scripted stand-in's calls the latencies this "
                        "diary's model calls took (or a JSON list of milliseconds)")
    r.add_argument("--allow-unknown-cutoff", action="store_true",
                   help="on a tape, admit models that state no training cutoff; the manifest "
                        "records it (exchange.tape.allow_unknown_cutoff)")
    r.add_argument("--release-candidate", action="store_true",
                   help="admit a sealed holdout tape (worlds/tapes/library.toml): a release "
                        "candidate's plumbing check, never an iteration")
    t = sub.add_parser("tape", help="cut a compact tape from a diary and print its identity")
    t.add_argument("diary", type=Path)
    t.add_argument("-o", "--output", type=Path, required=True)
    u = sub.add_parser("resume", help="continue a killed run from its directory")
    u.add_argument("target", type=Path)
    u.add_argument("--tape-from", type=Path, default=None,
                   help="the tape to resume on (default: the one it launched on)")
    args = parser.parse_args(argv)
    if args.command == "score":
        print_card(scorecard(json.loads(args.events.read_text())))
        return 0
    if args.command == "tape":
        tape = Tape.load(args.diary)
        tape.write(args.output)
        print_card(tape.summary())
        return 0
    if args.command == "resume":
        card = resume(args.target, args.tape_from)
        print_card(card)
        return 0 if card.get("status") == "completed" else 1
    if args.seeds:
        card = run_seeds(args, [int(x) for x in args.seeds.split(",")])
        print_card(card)
        return 0 if all(c.get("status") == "completed" for c in card["seeds"]) else 1
    card = run(args.provider, args.ticks, args.world, args.out, args.cap_usd, args.seed,
               args.vault_depositor_usd, args.gaps_from, args.tape_from, args.latency_from,
               args.allow_unknown_cutoff, args.release_candidate)
    print_card(card)
    return 0 if card.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
