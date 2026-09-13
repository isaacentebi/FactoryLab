"""Deterministic model responses for the scripted world."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from factorylab.world.models import ModelRequest, ModelResponse


@dataclass
class ScriptedProvider:
    """Deterministic stand-in for seed contracts and the A1 composition exercise.

    Producers cycle buy / hold / sell / hold on ticks (sized to the world wallet) and
    occasionally propose registrations; every produce reply carries a low
    ``payoff`` self-forecast, which the runtime seals only for antagonists.
    Evaluators return a verdict, a payoff probability and two forecasts whose
    probabilities depend on the evaluator's own prompt, so evaluators differ.
    Metas return a conformity score. Token usage is declared so costs are
    exact. It exists to close the loop, not to be clever.
    """

    name: str = "scripted"
    notional_fraction: str = "0.8"
    leverage: str = "3"
    input_tokens: int = 300
    output_tokens: int = 40
    register_at_calls: tuple[int, ...] = (40, 60, 80)
    tool_at_calls: tuple[int, ...] = (30, 50, 70, 90, 110, 130, 150)
    treasury_at_call: int = 120
    router_add_at_call: int = 100
    _producer_calls: int = 0
    spot_pair: str | None = None
    # The population's own observation and the amendment that puts it on a card. They are
    # counted in producer calls, and the world makes several of those per event: a
    # 260-event run makes about 1220, a 500-event run about 2520, an 800-event run about
    # 3950. These sit past the short runs — which assert on the one amendment the world
    # proposes early — and well inside the long ones, which watch this one activate.
    late_observation_call: int = 1600
    late_amendment_call: int = 1610

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        desc = _description_from_prompt(text)
        if desc == "A1 helper":
            reply = ({"emits": "Finding", "answer": 1} if "tool_results" in inputs else {
                "emits": "Finding", "requests": [{
                    "target": "funding-watcher", "description": "A1 grandchild", "inputs": {},
                    "outcome_schema": {"type": "object", "required": ["action"]}}]})
        elif desc == "A1 grandchild":
            reply = ({"action": "hold"} if "tool_results" in inputs else {
                "tool_calls": [{"tool": "catalogue.search",
                                "args": {"substring": "fake", "limit": 1}}]})
        elif desc.startswith("Evaluate"):
            reply = self._evaluate(req, inputs)
        elif desc.startswith("Assess"):
            reply = self._meta(inputs)
        elif desc.startswith("Vote"):
            reply = {"vote": True, "reason": "scripted yes"}
        else:
            reply = self._produce(desc, inputs)
        return ModelResponse(
            req.model_id, json.dumps(reply), self.input_tokens, self.output_tokens, "end_turn"
        )

    def _produce(self, desc: str, inputs: dict[str, Any]) -> dict[str, Any]:
        self._producer_calls += 1
        reply: dict[str, Any] = {"action": "hold", "payoff": 0.1}
        if "event Tick" in desc:
            try:
                payload = inputs["payload"]
                equity = Decimal(str(inputs["world"]["wallet_balance_usd"]))
                mid = Decimal(str(payload["mids"]["BTC"]))
                phase = int(payload["index"]) % 4
            except (KeyError, ValueError, ArithmeticError, TypeError):
                equity, mid, phase = Decimal(0), Decimal(0), 0
            if mid > 0 and equity > 0 and phase in (1, 3):
                notional = equity * Decimal(self.leverage) * Decimal(self.notional_fraction)
                size = (notional / mid).quantize(Decimal("0.000001"))
                if size > 0:
                    side = "buy" if phase == 1 else "sell"
                    reply = {"action": "order", "coin": "BTC", "side": side, "size": str(size)}
        n = self._producer_calls
        if "tool_results" in inputs:
            for row in inputs["tool_results"]:
                if row.get("tool") == "connector.fetch" and "body" in row.get("result", {}):
                    return {"tool_calls": [{"tool": "connector-parser",
                             "args": {"body": row["result"]["body"]}}]}
                if row.get("tool") == "connector-parser":
                    reply["connector_value"] = row.get("result", {}).get("value")
            reply["seen_tool_results"] = len(inputs["tool_results"])
            return reply
        if n == 35:
            reply["register"] = [{
                "kind": "learner", "assembly_id": "seed-decider",
                "learner": "blum_mansour", "gamma": 0.2,
                "actions": ["hold", *[
                    f"{side}:BTC:{band}" for side in ("buy", "sell")
                    for band in ("xs", "s", "m", "l", "xl")
                ]],
            }]
        # Late calls leave the scripted world's first window available for preflight.
        if n == self.late_observation_call:
            reply["register"] = [{
                "kind": "observation", "id": "scripted-fill-count",
                "description": "Number of fills in the closed window.",
                "unit": "count", "range": [0, 10000],
                "code": "def observe(facts):\n    return facts['fills']\n",
            }]
        if n == self.late_amendment_call:
            reply["register"] = [{
                "kind": "amendment", "id": "scripted-fill-card",
                "add": [{
                    "id": "scripted-fills", "norm": "care with scarce resources",
                    "description": "Fills per closed window.", "units": "count",
                    "window": {"kind": "windows", "n": 1, "per": None},
                    "acceptable_region": "below 1000",
                    "observation": "scripted-fill-count", "answers_for": "producer",
                }],
                "replace": [], "remove": [],
                "predicted_effect": {"card_id": "scripted-fills", "direction": "decrease",
                                     "window": 1},
            }]
        if n in self.tool_at_calls:
            # first the venue, then the population tool once it exists, then both
            calls = [{"tool": "venue.candles", "args": {"coin": "BTC", "interval": "1m", "n": 5}}]
            if n >= 50:
                calls.append({"tool": "spread-check", "args": {"mid": 100.0, "bps": 3}})
            reply["tool_calls"] = calls
        if n == self.treasury_at_call:
            reply["tool_calls"] = [
                {
                    "tool": "treasury.transfer",
                    "args": {"direction": "to_venue", "usd": 5, "reason": "scripted"},
                }
            ]
        if self.spot_pair and n in (5, 15, 25):
            reply = {"action": "hold", "payoff": 0.1, "tool_calls": [
                {"tool": "treasury.transfer", "args": {
                    "direction": "perps_to_spot", "usd": "10"}}
                if n == 5 else {"tool": "venue.place_market", "args": {
                    "coin": self.spot_pair, "market": "spot",
                    "side": "buy" if n == 15 else "sell", "size": "0.0001"}}
            ]}
        if n == 160:
            reply["register"] = [
                {"kind": "connector", "id": "scripted-source", "description": "Scripted data",
                 "origin": "https://example.org",
                 "predicted_effect": {"card_id": "forecast_skill", "direction": "increase",
                                      "window": 1}},
                {"kind": "tool", "id": "connector-parser", "description": "Parse a data value",
                 "args_schema": {"type": "object", "properties": {"body": {"type": "string"}},
                                 "required": ["body"]},
                 "code": "import json,sys\na=json.load(sys.stdin)\n"
                         "print(json.dumps({'value': json.loads(a['body'])['value']}))",
                 "timeout_s": 2},
            ]
        if n == 161:
            reply["tool_calls"] = [{"tool": "connector.fetch",
                                    "args": {"id": "scripted-source", "path": "/data"}}]
        if n == self.router_add_at_call:
            reply["register"] = [
                {
                    "kind": "router",
                    "event_kind": "Tick",
                    "learner": "exp3",
                    "gamma": 0.3,
                    "add": True,
                }
            ]
            reply["register"].extend([
                {"kind": "router", "event_kind": "Finding", "learner": "exp3", "gamma": 0.3},
                {"kind": "retire", "assembly_id": "eval-a",
                 "predicted_effect": {"card_id": "forecast_skill", "direction": "increase",
                                      "window": 1}},
            ])
        if n == self.tool_at_calls[3]:
            reply["requests"] = [{
                "target": "composition-helper", "description": "A1 helper", "inputs": {},
                "outcome_schema": {"type": "object", "required": ["answer"]},
            }]
        if n == 45:
            reply["register"] = [
                {
                    "kind": "tool",
                    "id": "spread-check",
                    "description": "Return the half-spread in price units for a mid and bps.",
                    "args_schema": {
                        "type": "object",
                        "properties": {"mid": {"type": "number"}, "bps": {"type": "integer"}},
                        "required": ["mid", "bps"],
                    },
                    "code": (
                        "import json,sys\na=json.load(sys.stdin)\n"
                        "print(json.dumps({'half_spread': a['mid']*a['bps']/20000}))"
                    ),
                    "timeout_s": 2,
                }
            ]
        if n == 55:
            reply["register"] = [
                {
                    "kind": "amendment",
                    "id": "turnover-card",
                    "add": [
                        {
                            "id": "turnover",
                            "norm": "care with scarce resources",
                            "description": "Notional traded per window relative to equity.",
                            "units": "ratio",
                            "window": {"kind": "windows", "n": 1, "per": None},
                            "acceptable_region": "below 5",
                            "observation": "turnover",
                            "answers_for": "producer",
                            "lambda": 0.6,
                        }
                    ],
                    "replace": [],
                    "remove": [],
                    "predicted_effect": {"card_id": "turnover", "direction": "decrease",
                                         "window": 1},
                }
            ]
        if n == 65:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": "web-observer",
                    "role": "producer",
                    "model_id": "fake-haiku:online",
                    "system_prompt": "Search for context on funding moves; reply with JSON.",
                    "accepts": ["MarketMid"],
                    "max_tokens": 128,
                }
            ]
        if n == self.register_at_calls[0]:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": "funding-watcher",
                    "role": "producer",
                    "model_id": "fake-haiku",
                    "system_prompt": "Watch funding and mids; reply with a JSON action.",
                    "accepts": ["Funding", "MarketMid"],
                    "max_tokens": 128,
                }
            ]
        elif n == self.register_at_calls[1]:
            reply["register"] = [{"kind": "model", "openrouter_id": "meta/muse-spark-1.3"}]
        elif n == self.register_at_calls[2]:
            reply["register"] = [
                {"kind": "router", "event_kind": "MarketMid", "learner": "exp3", "gamma": 0.2},
                {"kind": "assembly", "id": "composition-helper", "role": "producer",
                 "model_id": "fake-haiku", "system_prompt": "Answer the requested helper task.",
                 "accepts": ["CompositionRequest"], "emits": ["Finding"], "max_tokens": 128,
                 "schemas": {"Finding": {
                     "type": "object", "properties": {"answer": {"type": "integer"}},
                     "required": ["answer"], "additionalProperties": False}}},
                {"kind": "assembly", "id": "return-observer", "role": "producer",
                 "model_id": "fake-haiku", "system_prompt": "Reply with a JSON action.",
                 "accepts": ["ProducerReturn", "Finding"], "emits": ["ProducerReturn"],
                 "max_tokens": 128},
            ]
        return reply

    @staticmethod
    def _evaluate(req: ModelRequest, inputs: dict[str, Any]) -> dict[str, Any]:
        producer = inputs.get("producer", {})
        status = producer.get("status")
        action = (producer.get("outputs") or {}).get("action")
        verdict = 1.0 if status == "ok" and action in ("order", "hold") else 0.3
        if action in ("noop", "hold"):
            verdict = 0.9 if req.model_id == "fake-haiku" else 0.1
        style = int(hashlib.sha256(req.system.encode()).hexdigest(), 16) % 4
        q = (0.3, 0.45, 0.6, 0.75)[style]
        return {
            "verdict": verdict,
            # The haiku judge blesses inaction as paying off; the opus judge does not.
            "payoff": verdict,
            "rationale": "scripted judgement",
            "forecasts": [
                {"predicate": "wallet_up", "params": {"horizon_events": 10}, "q": q},
                {"predicate": "fill_within", "params": {"horizon_events": 10}, "q": 1 - q},
            ],
        }

    @staticmethod
    def _meta(inputs: dict[str, Any]) -> dict[str, Any]:
        v = inputs.get("verdict", {})
        ok = isinstance(v.get("verdict"), int | float) and bool(v.get("rationale"))
        return {"conformity": 0.8 if ok else 0.1, "rationale": "scripted meta"}



def _description_from_prompt(text: str) -> str:
    try:
        start = text.index("REQUEST\n") + len("REQUEST\n")
        end = text.index("\n\nINPUTS", start)
        return text[start:end]
    except ValueError:
        return ""



def _world_from_prompt(text: str) -> dict[str, Any]:
    """Read the stable world block the request renders ahead of everything else.

    The world facts that hold still between calls lead the prompt so a provider's
    prefix cache can hold them; the ones that move stay in ``INPUTS``. A reader of
    the prompt wants the whole world, so it reads both and joins them.
    """
    if not text.startswith("WORLD\n"):
        return {}
    try:
        start = text.index("{")
        return json.loads(text[start:text.index("\n\nREQUEST\n", start)])
    except (ValueError, json.JSONDecodeError):
        return {}


def _inputs_from_prompt(text: str) -> dict[str, Any]:
    try:
        start = text.index("INPUTS\n") + len("INPUTS\n")
        # The request renders further sections after the inputs (a propensity
        # declaration sits between the inputs and the schema); stop at whichever
        # comes first.
        end = min(
            (text.index(header, start) for header in ("\n\nPROPENSITY", "\n\nOUTCOME SCHEMA")
             if header in text[start:]),
            default=-1,
        )
        if end < 0:
            return {}
        inputs = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}
    stable = _world_from_prompt(text)
    if stable:
        moving = inputs.get("world")
        inputs["world"] = {**stable, **moving} if isinstance(moving, dict) else stable
    return inputs
