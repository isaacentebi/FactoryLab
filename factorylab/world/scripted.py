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
        desc = _description_from_prompt(text)
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
        elif desc.startswith(("Give verdict", "Evaluate")):
            reply = self._evaluate(req, inputs)
        elif desc.startswith("Give your own verdict"):
            # An adversarial judge's counter-verdict: the other side of what it read.
            read = (inputs.get("verdict") or {}).get("verdict")
            q = 1 - read if isinstance(read, int | float) else 0.5
            reply = {"verdict": q, "rationale": "scripted counter"}
        elif desc.startswith("Assess"):
            reply = self._meta(inputs)
        elif desc.startswith("Vote"):
            reply = {"vote": True, "reason": "scripted yes"}
        elif desc.startswith("Testify"):
            reply = {"assessment": "scripted testimony"}
        else:
            reply = self._produce(desc, inputs)
        reply = names_declined_trade(reply, text, inputs, self._producer_calls)
        return ModelResponse(
            req.model_id, json.dumps(reply), self.input_tokens, self.output_tokens, "end_turn"
        )

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
        if "event Tick" in desc:
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



def listed_coin(inputs: dict[str, Any]) -> str | None:
    """A coin the prompt shows the world listing: BTC when shown, else the first shown.

    Guarantees the coin is read from the listing the seat was shown (the world's
    ``recent_mids``, the record a declined trade is priced from), never assumed,
    and None when the prompt shows none.
    """
    world = inputs.get("world")
    mids = world.get("recent_mids") if isinstance(world, dict) else None
    shown = {str(coin) for coin in mids} if isinstance(mids, dict) else set()
    return "BTC" if "BTC" in shown else (min(shown) if shown else None)


def names_declined_trade(reply: dict[str, Any], text: str, inputs: dict[str, Any],
                         n: int) -> dict[str, Any]:
    """``reply`` naming a declined trade when it is a final answer that orders nothing.

    The return contract of a producing kind (``runtime.grounded``): a final answer
    that executes no venue operation carries ``counterfactual {coin, side}``, and the
    request's outcome schema publishes the field. The side alternates with ``n``, so
    the scripted population names both. A reply to a schema that does not publish
    the field, and one that already names a trade, places an answer order, declines,
    or continues through tools or children, is unchanged. An ``order`` that only
    reports a tool's write names one too: the write may have been refused.
    """
    schema = text.split("OUTCOME SCHEMA\n", 1)
    if (len(schema) < 2 or '"counterfactual"' not in schema[1].split("\n", 1)[0]
            or not isinstance(reply, dict) or "counterfactual" in reply
            or (reply.get("action") == "order"
                and all(k in reply for k in ("coin", "side", "size")))
            or reply.get("tool_calls") or reply.get("requests")
            or reply.get("status") == "cannot"):
        return reply
    coin = listed_coin(inputs)
    if coin is None:
        return reply
    return {**reply, "counterfactual": {"coin": coin, "side": "buy" if n % 2 else "sell"}}


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
                            "\n\nOUTCOME SCHEMA")
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
