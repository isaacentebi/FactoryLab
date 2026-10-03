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
    occasionally propose registrations. Evaluators return a verdict and two
    forecasts whose probabilities depend on the evaluator's own prompt, so
    evaluators differ.
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
    # counted in producer calls. Under per-seat entitlements (edition 2, C10) the seeded
    # trader spends its own share on its calls and its trading losses and runs it below
    # one call's ceiling, after which the router draws NOOP for most of its decisions:
    # a 500-event run makes about 1450 producer-side calls, so neither the 500-event
    # README run nor the 800-event diaries run reaches these. The schedule itself is
    # pinned by test_registers_observation_then_names_it_in_amendment.
    late_observation_call: int = 1600
    late_amendment_call: int = 1610

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        # The only descriptions read are the ones this population wrote itself (its
        # child requests). The world's own requests are told apart by structure.
        desc = _description_from_prompt(text)
        form = request_form(req, text, inputs)
        if desc == "A1 helper":
            reply = ({"emits": "Finding", "answer": 1} if "tool_results" in inputs else {
                "emits": "Finding", "requests": [{
                    # A kind of work, never a peer's id (primitive audit F5).
                    "target": "Funding", "description": "A1 grandchild", "inputs": {},
                    "outcome_schema": {"type": "object", "required": ["action"]}}]})
        elif desc == "A1 grandchild":
            reply = ({"action": "hold"} if "tool_results" in inputs else {
                "tool_calls": [{"tool": "catalogue.search",
                                "args": {"substring": "fake", "limit": 1}}]})
        elif form == "judge":
            reply = self._evaluate(req, inputs)
        elif form == "counter":
            # An adversarial judge's counter-verdict: the other side of what it read.
            read = (inputs.get("verdict") or {}).get("verdict")
            q = 1 - read if isinstance(read, int | float) else 0.5
            reply = {"verdict": q, "rationale": "scripted counter"}
        elif form == "meta":
            reply = self._meta(inputs)
        elif form == "vote":
            reply = {"vote": True, "reason": "scripted yes"}
        elif form == "testify":
            reply = {"assessment": "scripted testimony"}
        else:
            reply = self._produce(desc, inputs)
        reply = self._satisfy_contract(reply, req, text, inputs)
        reply = names_declined_trade(reply, text, inputs, self._producer_calls, req)
        return ModelResponse(
            req.model_id, json.dumps(reply), self.input_tokens, self.output_tokens, "end_turn"
        )

    def _satisfy_contract(self, reply: Any, req: ModelRequest, text: str,
                          inputs: dict[str, Any]) -> Any:
        """``reply`` answering the whole contract when it lacks a field every admitted
        shape obliges (``contract_requires``; R16b-7), else ``reply`` as it is.

        Guarantees a combined contract (a ballot that must also carry a verdict, say) is
        answered whole, never by the one form its classifier picked, and the answer is
        built from the admitted schema: only the fields an admitted shape names (a
        closed schema, ``additionalProperties: false``, gets no stray field), each
        value this population gives that field alone, fitted to that field's own
        bounds and enum; it is returned only if the original contract admits it
        (``_admits``, nested unions included), else ``reply`` is returned unchanged
        (Sol and Codex on #157). Harness only: the kernel never reads this.
        """
        if not isinstance(reply, dict):
            return reply
        required = contract_requires(req, text)
        if required <= set(reply):
            return reply
        schema = _contract_schema(req, text)
        if schema is None:
            return reply
        answers = {"conformity": lambda: self._meta(inputs),
                   "vote": lambda: {"vote": True, "reason": "scripted yes"},
                   "assessment": lambda: {"assessment": "scripted testimony"},
                   "verdict": lambda: self._evaluate(req, inputs)}
        for shape in _admitted_shapes(schema):
            properties = shape.get("properties") or {}
            closed = shape.get("additionalProperties") is False
            answer = {k: v for k, v in reply.items() if not closed or k in properties}
            for field in shape.get("required") or ():
                if field not in answer and field in answers:
                    value = answers[field]().get(field)
                    if value is not None:
                        answer[field] = value
            answer = {k: _fit(v, properties.get(k)) for k, v in answer.items()}
            if _admits(answer, schema):  # the original contract, never the flat shape
                return answer
        return reply

    @staticmethod
    def _trading_equity(seat: Any) -> Any:
        """The venue equity this seat reads to size a position, from its ``YOU`` block.

        R3-E moved it: edition 3's C4 put it under ``world_resources``, and §8's
        template puts the venue's own money under ``venue_accounts``, by custody.
        Both are read, newest first, because a recorded diary still carries
        prompts in the older shape and a replay of one must decide what it
        decided then. An account the venue would not give is unavailable, and an
        unavailable equity sizes nothing.
        """
        if not isinstance(seat, dict):
            return 0
        accounts = seat.get("venue_accounts")
        if isinstance(accounts, dict):
            perps = accounts.get("venue_perps")
            if (isinstance(perps, dict) and perps.get("status") == "observed"
                    and perps.get("equity_usd") is not None):
                return perps["equity_usd"]
        resources = seat.get("world_resources")
        if isinstance(resources, dict):
            return resources.get("trading_equity_usd", 0)
        return 0

    def _produce(self, desc: str, inputs: dict[str, Any]) -> dict[str, Any]:
        self._producer_calls += 1
        reply: dict[str, Any] = {"action": "hold", "payoff": 0.1}
        if inputs.get("kind") == "Tick":
            try:
                payload = inputs["payload"]
                # Edition 3 (C4) removed the root-wallet-only impression: what a
                # seat reads to size a position is the trading equity in its own
                # YOU block, which is the money the venue actually holds.
                equity = Decimal(str(self._trading_equity(inputs["seat"])))
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
                "learner": "blum_mansour",
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
                    "add": True,
                }
            ]
            reply["register"].extend([
                {"kind": "router", "event_kind": "Finding", "learner": "exp3"},
                {"kind": "retire", "assembly_id": "eval-a",
                 "predicted_effect": {"card_id": "forecast_skill", "direction": "increase",
                                      "window": 1}},
            ])
        if n == self.tool_at_calls[3]:
            reply["requests"] = [{
                "target": "Finding", "description": "A1 helper", "inputs": {},
                "outcome_schema": {"type": "object", "required": ["answer"]},
            }]
        # Offered on three calls rather than one: a registration carried by a child
        # invocation is not applied, and which call belongs to a child moves with the
        # schedule. Re-registering the same id supersedes it, so the world still ends
        # with one spread-check whichever offer lands first.
        if n in (45, 47, 49):
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
        # Offered on three calls for the reason spread-check is: which seat a call
        # belongs to moves with the reward line, and a seat whose entitlement is below
        # the trial amount cannot propose. A second offer of the same id is refused.
        if n in (55, 57, 59):
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
                {"kind": "router", "event_kind": "MarketMid", "learner": "exp3"},
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
        # The judged return's kernel status; a diary recorded before it was renamed
        # carries it as ``status``.
        status = producer.get("kernel_status", producer.get("status"))
        action = (producer.get("outputs") or {}).get("action")
        verdict = 1.0 if status == "ok" and action in ("order", "hold") else 0.3
        if action in ("noop", "hold"):
            verdict = 0.9 if req.model_id == "fake-haiku" else 0.1
        style = int(hashlib.sha256(req.system.encode()).hexdigest(), 16) % 4
        q = (0.3, 0.45, 0.6, 0.75)[style]
        # The haiku judge blesses inaction; the opus judge does not.
        return {
            "verdict": verdict,
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



def listed_coin(inputs: dict[str, Any], schema: Any = None) -> str | None:
    """A coin the prompt shows the world listing: BTC when shown, else the first shown.

    Guarantees the coin is read from the listing the seat was shown, never assumed:
    the coins the request's outcome schema publishes for ``counterfactual`` (the
    instruments the venue lists, quoted or not), else the world's ``recent_mids``;
    None when the prompt shows none.
    """
    shown = _published_coins(schema)
    if not shown:
        world = inputs.get("world")
        mids = world.get("recent_mids") if isinstance(world, dict) else None
        shown = {str(coin) for coin in mids} if isinstance(mids, dict) else set()
    return "BTC" if "BTC" in shown else (min(shown) if shown else None)


def _published_coins(schema: Any) -> set[str]:
    """Every coin an outcome schema's ``counterfactual`` field enumerates, or none."""
    found: set[str] = set()
    if isinstance(schema, dict):
        field = (schema.get("properties") or {}).get("counterfactual")
        coin = ((field or {}).get("properties") or {}).get("coin") if isinstance(
            field, dict) else None
        if isinstance(coin, dict) and isinstance(coin.get("enum"), list):
            found.update(str(c) for c in coin["enum"])
        for key in ("anyOf", "oneOf"):
            for shape in schema.get(key) or []:
                found |= _published_coins(shape)
    return found


def names_declined_trade(reply: dict[str, Any], text: str, inputs: dict[str, Any],
                         n: int, req: ModelRequest | None = None) -> dict[str, Any]:
    """``reply`` naming a declined trade when it is a final answer that orders nothing.

    The return contract of a producing kind (``runtime.grounded``): a final answer
    that executes no venue operation carries ``counterfactual {coin, side}``, and the
    request's outcome schema publishes the field. The side alternates with ``n``, so
    the scripted population names both. A reply to a schema that does not publish
    the field, and one that already names a trade, places an answer order, declines,
    or continues through tools or children, is unchanged. An ``order`` that only
    reports a tool's write names one too: the write may have been refused. The
    schema is read where only the kernel writes (``_schema_line``); a prompt whose
    contract leads is read through ``req``, which bounds that run.
    """
    line = _schema_line(text, req)
    if (line is None or '"counterfactual"' not in line
            or not isinstance(reply, dict) or "counterfactual" in reply
            or (reply.get("action") == "order"
                and all(k in reply for k in ("coin", "side", "size")))
            or reply.get("tool_calls") or reply.get("requests")
            or reply.get("status") == "cannot"):
        return reply
    try:
        published = json.loads(line)
    except ValueError:
        published = None
    coin = listed_coin(inputs, published)
    if coin is None:
        return reply
    return {**reply, "counterfactual": {"coin": coin, "side": "buy" if n % 2 else "sell"}}


#: The forms of request a scripted seat answers differently, read from structure.
REQUEST_FORMS = ("judge", "counter", "meta", "vote", "testify", "produce")


def outcome_required(req: ModelRequest | None, text: str) -> frozenset[str]:
    """Every field some admitted answer shape requires, read from the request's contract.

    Guarantees the fields come from the rendered ``OUTCOME SCHEMA`` section (a JSON
    schema, possibly ``anyOf`` shapes), falling back to the request's wire
    ``response_schema``; never from the request's prose. A request with neither
    reads as requiring nothing.
    """
    schema: Any = _rendered_schema(text, req)
    if not isinstance(schema, dict) and req is not None:
        schema = req.response_schema
    if not isinstance(schema, dict):
        return frozenset()
    return frozenset().union(*_shape_required(schema))


def _contract_schema(req: ModelRequest | None, text: str) -> dict | None:
    """The request's outcome schema, from the same trusted sources as
    ``outcome_required``: the rendered ``OUTCOME SCHEMA`` line, else the wire schema."""
    schema = _rendered_schema(text, req)
    if isinstance(schema, dict):
        return schema
    schema = req.response_schema if req is not None else None
    return schema if isinstance(schema, dict) else None


def _admitted_shapes(schema: dict) -> list[dict]:
    """The flat shapes a schema admits: along every path through its ``anyOf`` /
    ``oneOf`` unions, however deep (a local ``$ref`` resolved beside its siblings),
    each level's properties and requirements merged, and the shape closed when any
    level on the path is. Guarantees a nested branch's requirement (a ``verdict`` two
    unions down) is in the shape (Codex on #157); a union that refers back to a node
    already on its path is not walked again, so a recursive one terminates."""
    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}

    def target(node: dict) -> dict | None:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            found = defs.get(ref[len("#/$defs/"):])
            return found if isinstance(found, dict) else None
        return None

    def merge(a: dict, b: dict) -> dict:
        return {"properties": {**a["properties"], **b["properties"]},
                "required": [*a["required"], *b["required"]],
                "closed": a["closed"] or b["closed"]}

    def shapes(node: dict, path: frozenset[int]) -> list[dict]:
        own = {"properties": dict(node.get("properties") or {}),
               "required": [f for f in node.get("required") or () if isinstance(f, str)],
               "closed": node.get("additionalProperties") is False}
        found = [own]
        ref = target(node)
        if ref is not None and id(ref) not in path:
            found = [merge(a, b) for a in found for b in shapes(ref, path | {id(ref)})]
        alts = node.get("anyOf") or node.get("oneOf")
        if isinstance(alts, list):
            below = [shape for alt in alts if isinstance(alt, dict) and id(alt) not in path
                     for shape in shapes(alt, path | {id(alt)})]
            if below:
                found = [merge(a, b) for a in found for b in below]
        return found

    return [{"properties": shape["properties"], "required": shape["required"],
             **({"additionalProperties": False} if shape["closed"] else {})}
            for shape in shapes(schema, frozenset({id(schema)}))]


#: The JSON types a scripted answer's fields are checked against (``_admits``).
_JSON_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
               "number": (int, float), "integer": int, "null": type(None)}


def _admits(answer: Any, schema: dict, root: dict | None = None,
            path: frozenset[int] = frozenset()) -> bool:
    """Whether ``schema`` admits ``answer``, read level by level as the kernel's
    ``validate_schema`` reads it: a local ``$ref`` binds beside its siblings, some
    ``anyOf`` / ``oneOf`` alternative must admit it, however deep, and each level's
    type, enum, numeric bounds, required fields, closure and properties hold. The
    world layer imports nothing of the factory's validator (package boundary), so the
    scripted seat checks its own answer against the original contract here (Codex on
    #157). A node already on the path is not entered again: a recursive union ends."""
    if not isinstance(schema, dict):
        return True
    root = schema if root is None else root
    if id(schema) in path:
        return False
    path = path | {id(schema)}
    ref = schema.get("$ref")
    if isinstance(ref, str):
        defs = root.get("$defs") if isinstance(root.get("$defs"), dict) else {}
        found = defs.get(ref[len("#/$defs/"):]) if ref.startswith("#/$defs/") else None
        if not isinstance(found, dict) or not _admits(answer, found, root, path):
            return False
    alts = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alts, list) and not any(
            _admits(answer, alt, root, path) for alt in alts if isinstance(alt, dict)):
        return False
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds] if kinds else []
    if kinds and not any(isinstance(answer, _JSON_TYPES.get(k, ())) and not (
            k in ("number", "integer") and isinstance(answer, bool)) for k in kinds):
        return False
    if isinstance(schema.get("enum"), list) and answer not in schema["enum"]:
        return False
    if type(answer) in (int, float) and (
            ("minimum" in schema and answer < schema["minimum"])
            or ("maximum" in schema and answer > schema["maximum"])
            or ("exclusiveMinimum" in schema and answer <= schema["exclusiveMinimum"])
            or ("exclusiveMaximum" in schema and answer >= schema["exclusiveMaximum"])):
        return False
    if isinstance(answer, dict):
        properties = schema.get("properties") or {}
        if any(f not in answer for f in schema.get("required") or ()):
            return False
        if schema.get("additionalProperties") is False and set(answer) - set(properties):
            return False
        for key, value in answer.items():
            if key in properties and not _admits(value, properties[key], root):
                return False
    return True


def _fit(value: Any, schema: Any) -> Any:
    """``value`` moved inside ``schema``'s own enum and numeric bounds, when it has any:
    the first enum member for a value outside it, a number clamped to its inclusive
    bounds, or the midpoint of its bounds for an exclusive one it crosses."""
    if not isinstance(schema, dict):
        return value
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        return enum[0]
    if type(value) not in (int, float):
        return value
    lo = schema.get("minimum", schema.get("exclusiveMinimum"))
    hi = schema.get("maximum", schema.get("exclusiveMaximum"))
    if "minimum" in schema and value < schema["minimum"]:
        value = schema["minimum"]
    if "maximum" in schema and value > schema["maximum"]:
        value = schema["maximum"]
    crosses = (("exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"])
               or ("exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]))
    if crosses and lo is not None and hi is not None:
        value = (lo + hi) / 2
    return value


def _shape_required(schema: dict) -> list[frozenset[str]]:
    """Each admitted answer shape's required fields, read from the complete schema.

    Guarantees every level is read, however deep: nested ``anyOf`` / ``oneOf``
    alternatives are flattened (as ``_schema_definition`` recurses into them, with no
    depth bound of its own), and each level's own ``required`` is merged into every
    alternative beneath it, since an alternative is admitted only beside every
    enclosing level's constraints (Codex on b1c8590 and b56e793).

    A local ``$ref`` (``#/$defs/<name>``) binds together with its siblings, as the
    kernel's ``validate_schema`` reads it. The walk is iterative, with an explicit
    stack and a visited set, so a recursive union terminates; its shapes are the least
    fixed point, and a union that only recurses admits no shape of its own (Codex on
    3309478: a cut at depth 32 read a deeper ``verdict`` as absent)."""
    defs = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}

    def target(node: dict) -> dict | None:
        ref = node.get("$ref")
        prefix = "#/$defs/"
        if isinstance(ref, str) and ref.startswith(prefix):
            found = defs.get(ref[len(prefix):])
            return found if isinstance(found, dict) else None
        return None

    def alternatives(node: dict) -> list[dict]:
        alts = node.get("anyOf") or node.get("oneOf")
        return [a for a in alts if isinstance(a, dict)] if isinstance(alts, list) else []

    nodes: dict[int, dict] = {}
    stack = [schema]
    while stack:
        node = stack.pop()
        if id(node) in nodes:
            continue
        nodes[id(node)] = node
        stack.extend(alternatives(node))
        if (ref := target(node)) is not None:
            stack.append(ref)
    shapes: dict[int, set[frozenset[str]]] = {key: set() for key in nodes}
    changed = True
    while changed:  # monotone over a finite lattice of field-name sets: it settles
        changed = False
        for key, node in nodes.items():
            own = frozenset(f for f in node.get("required", ()) if isinstance(f, str))
            alts = alternatives(node)
            below = (set().union(*(shapes[id(a)] for a in alts)) if alts
                     else {frozenset()})
            ref = target(node)
            through = shapes[id(ref)] if ref is not None else {frozenset()}
            found = {own | a | t for a in below for t in through}
            if not found <= shapes[key]:
                shapes[key] |= found
                changed = True
    root = shapes[id(schema)]
    own = frozenset(f for f in schema.get("required", ()) if isinstance(f, str))
    return sorted(root, key=sorted) or [own]


def contract_requires(req: ModelRequest | None, text: str) -> frozenset[str]:
    """The fields EVERY admitted answer shape requires: what the contract obliges any
    answer to carry, from the same trusted sources as ``outcome_required``. A mixed
    contract (a producer's return or a verdict) obliges neither."""
    schema: Any = _rendered_schema(text, req)
    if not isinstance(schema, dict) and req is not None:
        schema = req.response_schema
    if not isinstance(schema, dict):
        return frozenset()
    shapes = _shape_required(schema)
    return frozenset.intersection(*shapes)


#: The kernel-written preamble of a request's SCORING section (``Request.sections``).
_SCORING_MARK = ("\nSCORING\nHow the answer to this request settles, as world.scoring "
                 "publishes it.\n")


def _kernel_tail(text: str) -> str:
    """The part of a prompt after its INPUTS line: only the kernel writes there.

    The description (``REQUEST``) is an author's for a commission and may hold any
    text, headers included; the inputs are one JSON line, which holds no raw newline.
    Everything after that line (the propensity, SCORING, OUTCOME SCHEMA, the outcome
    contract, the completion criterion) is rendered by ``Request.sections``. Read from
    the last INPUTS header, so a header an author wrote earlier never counts."""
    head = text.rfind("\n\nINPUTS\n")
    if head < 0:
        return text
    start = head + len("\n\nINPUTS\n")
    newline = text.find("\n", start)
    return "" if newline < 0 else text[newline:]


def _kernel_lead(req: ModelRequest | None) -> str:
    """The cacheable run heading ``req``'s final message, as the kernel bounded it.

    Guarantees exactly the prefix ``Assembly.build_model_request`` stated
    (``cache_prefix_chars``: the stable block and, behind a block that ends its line,
    the reply contract), or the empty string when the request states none.
    """
    chars = getattr(req, "cache_prefix_chars", 0)
    messages = getattr(req, "messages", ())
    if type(chars) is not int or chars <= 0 or not messages:
        return ""
    content = messages[-1].get("content")
    return content[:chars] if isinstance(content, str) and chars <= len(content) else ""


def _schema_line(text: str, req: ModelRequest | None = None) -> str | None:
    """The kernel's rendered ``OUTCOME SCHEMA`` line, or None.

    Two places hold it, and each is read only where the kernel alone decides what
    follows. Where the contract leads, it is the LAST marker inside the kernel's
    own run (``_kernel_lead``): the stable block before it renders charter norms
    verbatim, which may carry any header (Codex on b8a9cf6), while after it the
    run holds only the schema's JSON line and the kernel's contract prose. Where
    it trails, it is the first marker after the last INPUTS line
    (``_kernel_tail``). Never an author's description, and never a norm.
    """
    marker = "OUTCOME SCHEMA\n"
    at = _kernel_lead(req).rfind("\n" + marker)
    if at >= 0:
        return _kernel_lead(req)[at + 1 + len(marker):].split("\n", 1)[0]
    tail = _kernel_tail(text)
    at = tail.find("\n" + marker)
    return None if at < 0 else tail[at + 1 + len(marker):].split("\n", 1)[0]


def _rendered_schema(text: str, req: ModelRequest | None = None) -> Any:
    """The kernel's rendered ``OUTCOME SCHEMA`` (``_schema_line``), parsed, or None."""
    line = _schema_line(text, req)
    if line is None:
        return None
    try:
        return json.loads(line)
    except (ValueError, json.JSONDecodeError):
        return None


def _scoring_keys(text: str) -> frozenset[str]:
    """The keys of the request's SCORING section: how its answer settles, as the
    kernel's judging steps state it (``_settlement_facts``: ``evaluator_return`` for a
    Verdict, ``meta_return`` for a MetaVerdict, ``counter_return`` for a
    CounterVerdict). Empty for a request that carries none."""
    tail = _kernel_tail(text)
    if _SCORING_MARK not in tail:
        return frozenset()
    line = tail.split(_SCORING_MARK, 1)[1].split("\n", 1)[0]
    try:
        facts = json.loads(line)
    except (ValueError, json.JSONDecodeError):
        return frozenset()
    return frozenset(facts) if isinstance(facts, dict) else frozenset()


def request_form(req: ModelRequest | None, text: str, inputs: dict[str, Any]) -> str:
    """Which of ``REQUEST_FORMS`` a request is, from trusted request metadata alone.

    Guarantees the form is a function of what the kernel wrote about the request: the
    settlement a judging step attaches (its SCORING section, which names the judging
    kind) and the outcome contract's required fields, never of the request's
    description or of any input value, so neither rewording a commission nor an
    author's ``kind``/``payload`` inputs can turn a judgement into a producer's wake
    (Chapter II §I.b: the request is self-describing; Codex on 4a0f61c). ``inputs`` is
    kept for the callers' signature and not read. A request whose contract obliges a
    verdict (every admitted shape requires one) is answered with one, whoever asked
    for it; a mixed contract (a seat woken on an event that may answer a producer's
    return or a verdict) is a producer's wake.
    """
    del inputs  # author-controlled: never a classifier
    scoring = _scoring_keys(text)
    if "counter_return" in scoring:
        return "counter"
    if "meta_return" in scoring:
        return "meta"
    if "evaluator_return" in scoring:
        return "judge"
    required = contract_requires(req, text)
    if "conformity" in required:
        return "meta"
    if "vote" in required:
        return "vote"
    if "assessment" in required:
        return "testify"
    if "verdict" in required:
        return "judge"
    return "produce"


def _description_from_prompt(text: str) -> str:
    try:
        start = text.index("REQUEST\n") + len("REQUEST\n")
        end = text.index("\n\nINPUTS", start)
        return text[start:end]
    except ValueError:
        return ""



def _world_from_prompt(text: str) -> dict[str, Any]:
    """Read the stable world block the request renders ahead of everything else.

    Guarantees the block is read from where the request puts it — the head of
    the user message — by its own extent rather than by the section that follows
    it, and that a prompt carrying no such block, or a damaged one, reads as an
    empty mapping rather than an error. The facts that move stay in ``INPUTS``;
    a reader of the prompt wants the whole world, so it reads both and joins
    them.
    """
    # R3-E: the head of the user message is the stable prefix — the WORLD
    # CONTRACT wrapper and the base capability index — which is prose and a small
    # index rather than the world block it used to be, so this finds nothing
    # there and says so. What the world block holds now arrives in
    # ``WORLD UPDATE`` and in ``INPUTS``, and ``_with_seat_block`` joins the
    # first of those back into the world a reader of the prompt sees. The guard
    # is kept because a recorded prompt from an earlier world still has one.
    if not text.startswith("WORLD\n") or (start := text.find("{")) < 0:
        return {}
    try:
        world, _ = json.JSONDecoder().raw_decode(text[start:])
    except (ValueError, json.JSONDecodeError):
        return {}
    return world if isinstance(world, dict) else {}


def _inputs_from_prompt(text: str) -> dict[str, Any]:
    try:
        start = text.index("INPUTS\n") + len("INPUTS\n")
        # The request renders further sections after the inputs (the subject's
        # propensity and the scoring facts sit between the inputs and the schema);
        # stop at whichever comes first.
        end = min(
            (text.index(header, start)
             for header in ("\n\nSUBJECT PROPENSITY", "\n\nPROPENSITY", "\n\nSCORING",
                            "\n\nOUTCOME SCHEMA", "\n\nCOMPLETION CRITERION")
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
    return _with_seat_block(text, inputs)


def _block_from_prompt(text: str, marker: str) -> dict[str, Any]:
    """One rendered block of the prompt, as an object again.

    Guarantees a prompt without that block, or one whose block is damaged, reads
    as an empty mapping rather than an error: a reader of a prompt is not a
    parser of a wire format, and a section that is absent is absent.
    """
    start = text.find(marker)
    if start < 0:
        return {}
    brace = text.find("{", start + len(marker))
    if brace < 0:
        return {}
    try:
        block, _ = json.JSONDecoder().raw_decode(text[brace:])
    except (ValueError, json.JSONDecodeError):
        return {}
    return block if isinstance(block, dict) else {}


def _seat_block_from_prompt(text: str) -> dict[str, Any]:
    """The rendered ``YOU`` block, as an object again."""
    return _block_from_prompt(text, "\nYOU\n")


def _world_update_from_prompt(text: str) -> dict[str, Any]:
    """The rendered ``WORLD UPDATE`` block (R3-E), as an object again."""
    return _block_from_prompt(text, "\nWORLD UPDATE\n")


def _with_seat_block(text: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Reassemble the inputs a seat was actually shown, across the prompt's sections.

    The rendered prompt splits one set of inputs across three blocks so that the
    cacheable part can hold still and nothing is carried twice: the stable world
    facts head the prompt, the seat's own account of itself follows in ``YOU``,
    and the rest arrives in ``INPUTS``. A reader of the prompt is shown all of it
    and should see all of it, so the continuity fields the ``YOU`` block carries
    are put back under the names they were rendered from, exactly as the world
    block above is put back together.
    """
    block = _seat_block_from_prompt(text)
    update = _world_update_from_prompt(text)
    if not block and not update:
        return inputs
    if block:
        inputs["seat"] = block
        # §8's ``YOU`` slots, back under the request-input names they were
        # rendered from. A slot that says ``unavailable`` is put back as absent,
        # because that is what it means: the request carried no such source.
        state = block.get("working_state")
        if isinstance(state, dict):
            inputs["your_state"] = state
        outcomes = block.get("outcomes")
        if isinstance(outcomes, dict) and type(outcomes.get("unread_count")) is int:
            inputs["unread_outcomes"] = {"count": outcomes["unread_count"],
                                         "items": list(outcomes.get("items") or ())}
    if update:
        inputs["world_update"] = update
        fold = update.get("changes_since_last_successful_delivery")
        if isinstance(fold, dict) and "status" not in fold and isinstance(
                inputs.get("payload"), dict):
            inputs["payload"] = {**inputs["payload"], "since_you_last_woke": fold}
        receipts = update.get("execution_receipts")
        if not (isinstance(receipts, dict) and receipts.get("status") == "unavailable"):
            inputs["execution_receipts"] = receipts
        # The world's moving facts the update block carries, joined back into the
        # world a reader of the prompt sees, under the names the block builds
        # them from.
        world = inputs.get("world")
        if isinstance(world, dict):
            charter = update.get("charter")
            if isinstance(charter, dict):
                world = {**world, "charter": charter.get("text"),
                         "charter_edition": charter.get("edition"),
                         "card_prices": charter.get("cards"),
                         "governance": charter.get("pending_changes")}
            public = update.get("public_observations")
            if isinstance(public, dict):
                world = {**world, "recent_mids": public.get("recent_mids")}
            inputs["world"] = world
    return inputs
