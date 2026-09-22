"""Polymarket event markets in a running world: tools, custody, intents, settlement.

A world that enables ``[polymarket]`` gets a surface, not a strategy: three reads
of the public market (search, one market's contract, one token's book), an
account read of its own pot, and on the simulated venue two writes (a limit
order and its cancellation). Nothing here asks a seat to use any of it, and no
description says it would be good to (AGENTS.md: physics is enforced, not
announced).

Why the surface exists at all: the population already sells forecasts inside
the loop and is scored on them by Brier. An event market is the same kind of
claim priced by people outside the loop and settled by the world, so a position
there is a consequence the factory cannot grade for itself (essay II.III: the
realized-consequence signal "sits outside the factory's input entirely";
II.IV.a, Hanson's "vote on values, bet on beliefs").

What the kernel enforces, and where:

* **Outside text is jailed.** Market questions, descriptions, slugs and
  resolution sources are written by third parties. A round that read them runs
  population tools only (``ComputeMixin.OUTSIDE_TEXT_TOOLS``), and every prose
  field long enough to be text is kept off durable surfaces exactly as a fetched
  connector body is (``protect``).
* **Writes are intents first.** Every order and cancellation has a client id
  (``<handle>:<slot>``) and a durable ``polymarket.intent`` before the venue is
  called; a repeat reconciles and never resubmits; an unanswered intent is
  polled on the venue's bounded schedule and then released as unknown.
* **Custody is separate.** Collateral is the ``polymarket`` pot. Its own balance
  and holds are the only collateral an order is weighed against; it never
  borrows the Hyperliquid accounts or the Base reserve.
* **A position is settled by its resolution.** Fills enter the consequence book
  as ``event`` lots (``settlement/lots.py``); an outcome token is never marked,
  so the decision that holds one keeps an open consequence until the market
  resolves, and the resolution is what closes it (``LotTable.redeem``).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any

from factorylab.kernel.money import usd_to_micro

CUSTODY = "polymarket"
READS = ("polymarket.search", "polymarket.market", "polymarket.book")
ACCOUNT = "polymarket.positions"
WRITES = ("polymarket.place_limit", "polymarket.cancel")
KIND = "polymarket"
#: The tools whose answers carry text third parties wrote.
OUTSIDE_TEXT_TOOLS = frozenset(READS)
#: Fields of a read answer that are prose from outside. Ids and numbers are not:
#: a token id must be repeatable in the answer that trades it.
PROSE_FIELDS = ("question", "description", "slug", "resolution_source", "outcome")

#: Book depth a read may ask for; the venue's own book is deeper than any prompt needs.
MAX_DEPTH = 20
MAX_SEARCH_RESULTS = 10
MAX_QUERY_CHARS = 200


def coin_of(token_id: str) -> str:
    """The consequence book's name for an outcome token: its own namespace, never a coin."""
    return f"PM:{token_id}"


# --- contracts ------------------------------------------------------------------------

def tool_specs(spec: Any, *, writes: bool) -> dict[str, dict[str, Any]]:
    """The tools a ``[polymarket]`` world publishes: what each does and what it costs."""
    price = spec.read_price_micro
    usd = f"${Decimal(price) / 1_000_000:f}"
    token = {"type": "string", "pattern": r"^[0-9]{1,100}$", "minLength": 1}
    decimal = {"type": ["string", "number"]}
    tools = {
        "polymarket.search": (
            f"Search Polymarket event markets by text. Returns up to {MAX_SEARCH_RESULTS} "
            "markets: id, question, outcomes with their token ids and last prices, end date, "
            "resolution source, tick size, minimum order size, fee schedule and whether the "
            f"market accepts orders. Market text is written by third parties. {usd} a call.",
            {"query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
             "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}},
            ["query"], [{"query": "election", "limit": 5}], price),
        "polymarket.market": (
            "One Polymarket market by id: its contract fields and its resolution rules "
            f"text, which third parties wrote. {usd} a call.",
            {"market_id": {"type": "string", "minLength": 1, "maxLength": 80}},
            ["market_id"], [{"market_id": "fake-1"}], price),
        "polymarket.book": (
            "The order book of one outcome token, best price first on both sides, with "
            f"its midpoint, tick size and minimum order size. {usd} a call.",
            {"token_id": token, "depth": {"type": "integer", "minimum": 1,
                                          "maximum": MAX_DEPTH}},
            ["token_id"], [{"token_id": "100000000000000000000", "depth": 5}], price),
    }
    if writes:
        tools.update({
            ACCOUNT: (
                "The polymarket custody pot: USDC, what resting orders hold, outcome "
                "tokens held and open orders. Free.",
                {}, [], [{}], 0),
            "polymarket.place_limit": (
                "Place a good-until-cancelled limit order for outcome tokens of one "
                "Polymarket market, paid from and settled into the polymarket pot. size is "
                "in tokens, price in USDC per token strictly between 0 and 1 on the "
                "market's tick. A buy holds price x size USDC while it rests; a sell holds "
                "the tokens. An order that fills on arrival pays the market's taker fee. "
                "When the market resolves, each winning token pays 1 USDC and each losing "
                "token 0. Free to call.",
                {"token_id": token, "side": {"type": "string", "enum": ["buy", "sell"]},
                 "size": decimal, "price": decimal},
                ["token_id", "side", "size", "price"],
                [{"token_id": "100000000000000000000", "side": "buy", "size": "10",
                  "price": "0.35"}], 0),
            "polymarket.cancel": (
                "Cancel one resting Polymarket order this world placed. Free.",
                {"order_id": {"type": "string", "minLength": 1, "maxLength": 100}},
                ["order_id"], [{"order_id": "pm-1"}], 0),
        })
    return {
        tool_id: {
            "id": tool_id, "kind": KIND, "description": description,
            "args_schema": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False,
                            "examples": examples},
            "price_micro_per_call": cost,
        }
        for tool_id, (description, properties, required, examples, cost) in tools.items()
    }


# --- the surface a runtime holds ------------------------------------------------------------

class PolymarketSurface:
    """One world's Polymarket state. Guarantees everything a restore needs is in ``state``.

    ``venue`` is the journalled adapter the tools call; ``intents`` are the durable
    write identities by client id; ``order_ids`` names every order this world's
    writes placed (so a cancel can only reach this world's own order and an open
    order can hold its decision's consequence); ``window_orders`` is the order
    count of the current window, for ``max_orders_per_window``.
    """

    def __init__(self, spec: Any, venue: Any, *, writes: bool) -> None:
        self.spec = spec
        self.venue = venue
        self.writes = writes
        self.intents: dict[str, dict[str, Any]] = {}
        self.order_ids: dict[str, str] = {}  # order id -> client id
        self.window_orders: tuple[int, int] = (0, 0)

    def state(self) -> dict[str, Any]:
        """Intents, order ownership, the window count and a simulated venue's own state."""
        target = self.venue.target
        return {"intents": self.intents, "order_ids": self.order_ids,
                "window_orders": list(self.window_orders),
                "venue": dict(vars(target)) if self.venue.deterministic else None}

    def restore(self, saved: dict[str, Any]) -> None:
        """Rebind saved state to this process's adapter."""
        self.intents = dict(saved.get("intents") or {})
        self.order_ids = dict(saved.get("order_ids") or {})
        self.window_orders = tuple(saved.get("window_orders") or (0, 0))
        if saved.get("venue") is not None and self.venue.deterministic:
            self.venue.target.__dict__.clear()
            self.venue.target.__dict__.update(saved["venue"])

    def writes_of(self, handle: str) -> list[dict[str, Any]]:
        """The Polymarket writes a decision made, as durable intents, in submission order."""
        return [intent for intent in self.intents.values() if intent["handle"] == handle]

    def account(self) -> dict[str, Any] | None:
        """The simulated pot, read from the venue's own books without I/O, or None."""
        if not self.writes:
            return None
        return self.venue.target.account()


def install(rt: Any) -> None:
    """Build the surface a ``[polymarket] enabled`` world launches with, and publish its tools.

    Guarantees a world that does not enable the block is untouched: no attribute,
    no tool, no pot. The simulated venue is journalled as deterministic (a replay
    re-runs it); the live reader is journalled like every other outside read, so
    a replay returns what was read rather than reading again.
    """
    spec = rt.m.polymarket
    if not spec.enabled:
        return
    from factorylab.runtime.resume import JournalProxy
    from factorylab.world.polymarket import FakePolymarket, PolymarketReader

    writes = spec.venue == "fake"
    target = (FakePolymarket(seed=spec.seed,
                             start_usdc=Decimal(spec.collateral_micro) / 1_000_000)
              if writes else PolymarketReader())
    venue = JournalProxy(target, rt.ledger, "polymarket", deterministic=writes)
    rt.polymarket = PolymarketSurface(spec, venue, writes=writes)
    rt.tool_specs.update(tool_specs(spec, writes=writes))


# --- dispatch -------------------------------------------------------------------------------

def _refused(rt: Any, action_id: str, handle: str, tool_id: str, reason: str) -> dict:
    rt.ledger.append({"kind": "polymarket.refused", "handle": handle,
                      "assembly_id": action_id, "tool": tool_id, "reason": reason,
                      "ts": rt.clock.now_ns})
    return {"error": reason}


def protect(rt: Any, value: Any) -> None:
    """Keep the prose of a read answer off every durable surface, as a fetched body is.

    The posture is the connector's and ``web.search``'s: text long enough to be
    prose is redacted from the ledger and refuses a final return that repeats it
    verbatim; ids, prices and anything short stay repeatable facts.
    """
    from factorylab.runtime.compute import MIN_PROTECTED_BODY_CHARS

    if isinstance(value, dict):
        for key, item in value.items():
            if key in PROSE_FIELDS and isinstance(item, str):
                if len(item) >= MIN_PROTECTED_BODY_CHARS:
                    rt.ledger.protect_connector_body(item)
            else:
                protect(rt, item)
    elif isinstance(value, list):
        for item in value:
            protect(rt, item)


def execute(rt: Any, action_id: str, handle: str, tool_id: str, args: dict,
            slot: str) -> dict[str, Any]:
    """Run one validated Polymarket tool inside the seat's metered call.

    Guarantees a read answers with parsed, bounded fields or a fixed refusal (a
    remote status or body never reaches the seat verbatim), and that a write goes
    through ``_write`` and nowhere else.
    """
    surface = rt.polymarket
    invalid = check_args(rt.tool_specs[tool_id]["args_schema"], args)
    if invalid:
        return _refused(rt, action_id, handle, tool_id, invalid)
    if tool_id in WRITES:
        return _write(rt, surface, action_id, handle, tool_id, args, slot)
    if tool_id == ACCOUNT:
        return {**account_view(surface.account()), "as_of_ns": rt.clock.now_ns}
    try:
        if tool_id == "polymarket.search":
            limit = args.get("limit", 5)
            result = {"markets": surface.venue.search_markets(args["query"].strip(), limit)}
        elif tool_id == "polymarket.market":
            result = {"market": surface.venue.market(args["market_id"])}
        else:
            result = {"book": surface.venue.order_book(args["token_id"],
                                                        args.get("depth", 10))}
    except Exception:  # noqa: BLE001 - a read failure is a fact, not a crash
        return _refused(rt, action_id, handle, tool_id, "polymarket read unavailable")
    protect(rt, result)
    result["as_of_ns"] = rt.clock.now_ns
    rt.ledger.append({"kind": "polymarket.read", "handle": handle, "assembly_id": action_id,
                      "tool": tool_id, "ts": rt.clock.now_ns})
    return result


def check_args(schema: dict, args: Any) -> str | None:
    """Why ``args`` do not satisfy a Polymarket tool schema, or None.

    ``validate_schema`` checks types, enums and numeric bounds; this adds the
    string bounds and the token-id pattern it does not read, and the decimal
    reading of ``size`` and ``price``, so a malformed call is refused with a
    reason and never reaches the venue.
    """
    import re

    from factorylab.cortex.assembly import validate_schema

    try:
        validate_schema(args, schema)
    except (ValueError, TypeError, RecursionError) as exc:
        return f"invalid polymarket arguments: {exc}"
    for key, rule in schema["properties"].items():
        value = args.get(key)
        if not isinstance(value, str):
            continue
        if not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 10_000):
            return f"invalid polymarket arguments: {key} length"
        if "pattern" in rule and re.fullmatch(rule["pattern"], value) is None:
            return f"invalid polymarket arguments: {key} format"
    for key in ("size", "price"):
        if key in args and (isinstance(args[key], bool) or _decimal(args[key]) is None):
            return f"invalid polymarket arguments: {key} is not a decimal"
    return None


def account_view(account: dict | None) -> dict[str, Any]:
    """What the pot tool publishes; an unreadable pot says so and states no amount."""
    if account is None:
        return {"status": "unavailable", "reason": "no polymarket custody in this world"}
    return {"status": "observed", "custody": CUSTODY, **account}


# --- writes ---------------------------------------------------------------------------------

def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _open_exposure(account: dict) -> Decimal:
    """USDC the pot has committed: tokens held at cost plus what resting buys hold."""
    held = sum((Decimal(p["size"]) * Decimal(p["avg_px"]) for p in account["positions"]),
               Decimal(0))
    resting = sum((Decimal(o["price"]) * Decimal(o["remaining"])
                   for o in account["open_orders"] if o["side"] == "buy"), Decimal(0))
    return held + resting


def refusal(rt: Any, surface: PolymarketSurface, seat: str | None, handle: str,
            tool_id: str, args: dict, *, committed: Decimal = Decimal(0),
            window_count: int | None = None) -> str | None:
    """Why this write would be refused before any intent, or None.

    Guarantees new exposure is weighed against the polymarket pot alone: a buy
    needs its notional and the taker fee it could pay in the pot's available
    USDC (less ``committed``, what earlier writes of the same batch need); a sell
    needs the tokens. It also enforces the manifest's caps (one order's notional,
    the pot's open exposure, orders a window) and refuses an exact repeat of a
    resting order the same seat already has. An unreadable pot refuses new risk
    and never a cancellation.
    """
    spec = surface.spec
    if tool_id == "polymarket.cancel":
        if args["order_id"] not in surface.order_ids:
            return "no order with that id was placed by this world"
        return None
    size, price = _decimal(args.get("size")), _decimal(args.get("price"))
    if size is None or price is None or size <= 0 or not 0 < price < 1:
        return "size must be positive and price strictly between 0 and 1"
    window, count = surface.window_orders
    count = window_count if window_count is not None else (
        count if window == rt.window.index else 0)
    if count >= spec.max_orders_per_window:
        return "polymarket order window cap reached"
    market = surface.venue.target.market_of_token(args["token_id"])
    if market is None:
        return "token is not listed"
    if not market["accepting_orders"]:
        return "market is not accepting orders"
    try:
        account = surface.account()
    except Exception as exc:  # noqa: BLE001 - unknown collateral blocks new risk
        return f"polymarket pot unavailable: {type(exc).__name__}"
    notional = size * price
    if usd_to_micro(notional, rounding="ceil") > spec.max_order_micro:
        return "order notional exceeds [polymarket] max_order_usd"
    buy = args["side"] == "buy"
    if buy:
        rate = Decimal(market["fees"]["rate"] or 0) if market["fees"]["enabled"] else Decimal(0)
        fee = size * rate * price * (1 - price)
        if usd_to_micro(_open_exposure(account) + committed + notional,
                        rounding="ceil") > spec.max_open_micro:
            return "open exposure would exceed [polymarket] max_open_usd"
        if notional + fee + committed > Decimal(account["usdc_available"]):
            return "order collateral exceeds the polymarket pot's available USDC"
    else:
        held = next((Decimal(p["available"]) for p in account["positions"]
                     if p["token_id"] == args["token_id"]), Decimal(0))
        if size > held:
            return "sell exceeds the tokens the polymarket pot holds"
    for order in account["open_orders"]:
        client = surface.order_ids.get(order["order_id"])
        prior = surface.intents.get(client or "")
        if prior is None:
            continue
        owner = rt.handle_to_assembly.get(prior["handle"]) or rt.outcomes.seat_of(
            prior["handle"])
        same = (order["token_id"] == args["token_id"] and order["side"] == args["side"]
                and Decimal(order["size"]) == size and Decimal(order["price"]) == price)
        if same and (prior["handle"] == handle or owner == seat):
            return (f"an identical {args['side']} {size} order on this token from you is "
                    f"already resting (order_id {order['order_id']}); cancel or change it "
                    "before placing another")
    return None


def batch_refusal(rt: Any, seat: str, handle: str,
                  writes: list[tuple[str, str, dict]]) -> tuple[int, str] | None:
    """The first Polymarket write of a batch that would be refused, and why, or None.

    Guarantees a batch is weighed whole before any of it is submitted, and more
    strictly than one write alone: the collateral and exposure earlier buys in the
    batch would need is counted against the later ones, and the window cap counts
    the batch's own orders, so no leg is submitted that the pot could not carry
    beside the others.
    """
    surface = rt.polymarket
    committed, placed = Decimal(0), set()
    window, count = surface.window_orders
    count = count if window == rt.window.index else 0
    for index, (slot, tool_id, args) in enumerate(writes):
        if f"{handle}:{slot}" in surface.intents:
            continue  # a retry reconciles; it is not a new write
        if tool_id == "polymarket.place_limit":
            key = (args.get("token_id"), args.get("side"), str(args.get("size")),
                   str(args.get("price")))
            if key in placed:
                return index, "the same order is placed twice in one batch"
            placed.add(key)
        reason = refusal(rt, surface, seat, handle, tool_id, args, committed=committed,
                         window_count=count)
        if reason:
            return index, reason
        if tool_id == "polymarket.place_limit":
            count += 1
            if args.get("side") == "buy":
                committed += Decimal(str(args["size"])) * Decimal(str(args["price"]))
    return None


def _write(rt: Any, surface: PolymarketSurface, action_id: str, handle: str, tool_id: str,
           args: dict, slot: str) -> dict[str, Any]:
    """Every Polymarket write has a durable intent and a stable identity before submission.

    The discipline is ``VenueMixin._venue_write``'s: the client id is the decision
    and its tool slot; a repeat of the same identity reconciles with the answer
    already recorded and never submits twice; an answer that did not arrive is
    recovered by looking the identity up, never by resubmitting.
    """
    client_id = f"{handle}:{slot}"
    previous = surface.intents.get(client_id)
    if previous is not None:
        if previous["operation"] != tool_id or previous["args"] != args:
            return rt._refuse_order(handle, "client id already binds another intent",
                                    kind="polymarket.refused")
        if previous["result"]["status"] == "uncertain":
            return _recover(rt, surface, client_id)
        return dict(previous["result"])
    seat = rt.handle_to_assembly.get(handle) or rt.outcomes.seat_of(handle)
    reason = refusal(rt, surface, seat, handle, tool_id, args)
    if reason:
        # A refused write is not an action: the answer may not act in its place.
        rt.venue_attempts[handle] = reason
        return rt._refuse_order(handle, reason, kind="polymarket.refused")
    token = (args["token_id"] if tool_id == "polymarket.place_limit"
             else surface.intents[surface.order_ids[args["order_id"]]]["args"]["token_id"])
    intent = {"handle": handle, "client_id": client_id, "operation": tool_id,
              "args": dict(args), "result": {"status": "uncertain"}}
    rt.ledger.append({"kind": "polymarket.intent", **intent})
    surface.intents[client_id] = intent
    rt.consequences.order_intent(client_id, handle, coin_of(token))
    try:
        if tool_id == "polymarket.place_limit":
            window, count = surface.window_orders
            surface.window_orders = (rt.window.index,
                                     (count if window == rt.window.index else 0) + 1)
            result = surface.venue.place(
                client_id=client_id, token_id=args["token_id"], is_buy=args["side"] == "buy",
                size=Decimal(str(args["size"])), price=Decimal(str(args["price"])))
        else:
            result = surface.venue.cancel(client_id=client_id, order_id=args["order_id"])
    except Exception as exc:  # noqa: BLE001 - a lost answer is uncertain, never absent
        result = {"status": "uncertain", "error": f"write exception: {type(exc).__name__}"}
    if not isinstance(result, dict) or result.get("status") == "uncertain":
        _record(rt, surface, client_id, result if isinstance(result, dict) else {
            "status": "uncertain", "error": "write returned a non-object acknowledgement"})
        return _recover(rt, surface, client_id)
    answer = _record(rt, surface, client_id, result)
    settle(rt, surface.venue.drain_events())
    return answer


def _recover(rt: Any, surface: PolymarketSurface, client_id: str) -> dict[str, Any]:
    """Ask the venue what it holds under an identity; never resubmit it."""
    try:
        result = surface.venue.lookup(client_id)
    except Exception as exc:  # noqa: BLE001
        result = {"status": "uncertain", "error": f"recovery exception: {type(exc).__name__}"}
    answer = _record(rt, surface, client_id, result)
    settle(rt, surface.venue.drain_events())
    return answer


def _record(rt: Any, surface: PolymarketSurface, client_id: str,
            result: dict) -> dict[str, Any]:
    """Ledger the venue's answer, then attribute it in the consequence book."""
    intent = surface.intents[client_id]
    status = result.get("status")
    if status not in ("filled", "resting", "cancelled", "rejected"):
        result = {"status": "uncertain",
                  "error": str(result.get("error") or "venue acknowledgement unavailable")[:300]}
    result = {key: (str(value) if isinstance(value, Decimal) else value)
              for key, value in result.items()}
    uncertain = result["status"] == "uncertain"
    polls = int(intent.get("polls", 0)) + int(uncertain)
    rt.ledger.append({"kind": "polymarket.uncertain" if uncertain
                      else "polymarket.acknowledged", "client_id": client_id,
                      "handle": intent["handle"], "result": result,
                      **({"poll": polls} if uncertain else {})})
    surface.intents[client_id] = {**intent, "result": dict(result), "polls": polls}
    if uncertain:
        return dict(result)
    if intent["operation"] == "polymarket.cancel":
        if result["status"] == "cancelled":
            rt.consequences.cancel(intent["args"]["order_id"], rt.n)
    elif result.get("order_id") is not None:
        surface.order_ids[str(result["order_id"])] = client_id
        attributed = result
        if result["status"] == "cancelled" and Decimal(str(result["filled_size"])) > 0:
            attributed = {**result, "status": "filled"}
        rt.consequences.order_result(intent["handle"], attributed,
                                     {"size": str(intent["args"]["size"])}, rt.n)
    before = rt.consequences.table
    rt._replay_deferred(rt.consequences.order_acknowledged(client_id), before)
    return dict(result)


# --- time and settlement --------------------------------------------------------------------

def tick(rt: Any) -> None:
    """Reconcile unanswered writes, then move the simulated venue and settle what it did.

    Guarantees an uncertain intent is asked about at most ``UNCERTAIN_ORDER_POLLS``
    times, each answer ledgered once, and is then released as unknown exactly as
    a Hyperliquid intent is (``VenueMixin._give_up_on_order``).
    """
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    surface = rt.polymarket
    if not surface.writes:
        return
    for client_id, intent in list(surface.intents.items()):
        if intent["result"]["status"] != "uncertain" or intent.get("unresolved"):
            continue
        if int(intent.get("polls", 0)) >= UNCERTAIN_ORDER_POLLS:
            rt.ledger.append({"kind": "polymarket.unresolved", "client_id": client_id,
                              "handle": intent["handle"], "polls": intent.get("polls", 0)})
            surface.intents[client_id] = {**intent, "unresolved": True}
            before = rt.consequences.table
            rt._replay_deferred(rt.consequences.release_unresolved(client_id, rt.n), before)
            continue
        _recover(rt, surface, client_id)
    settle(rt, surface.venue.advance(rt.clock.now_ns))


def settle(rt: Any, events: list[dict[str, Any]]) -> None:
    """Book what the venue did: fills into lots and the pot, resolutions into consequences.

    Guarantees each effect lands where it happened. A fill's own realised P&L and
    fee are ledgered as ``venue.settled`` in the ``polymarket`` custody and never
    touch the compute wallet (C5); the fill enters the consequence book as an
    ``event`` lot owned by the decision that placed the order. A resolution is
    ledgered first, then closes every lot on the token at its payout
    (``ReturnConsequences.redeem``), writes one ``resolution`` receipt per
    decision it settled, and tells each owner what its position came to.
    """
    for event in events:
        kind = event.get("kind")
        if kind == "fill":
            _settle_fill(rt, event)
        elif kind == "cancelled":
            rt.ledger.append({"kind": "polymarket.cancelled", "order_id": event["order_id"],
                              "reason": "market resolved", "ts": rt.clock.now_ns})
            rt.consequences.cancel(str(event["order_id"]), rt.n)
        elif kind == "resolution":
            _settle_resolution(rt, event)


def _settle_fill(rt: Any, event: dict) -> None:
    order_id = str(event["order_id"])
    payload = {"order_id": order_id, "coin": coin_of(event["token_id"]),
               "is_buy": event["is_buy"], "size": event["size"], "px": event["px"],
               "fee_usd": event["fee_usd"], "realized_usd": event["realized_usd"],
               "liquidation": False, "market": "event", "inventory_size": event["size"]}
    rt.ledger.append({"kind": "polymarket.fill", **payload, "market_id": event["market_id"],
                      "ts": rt.clock.now_ns})
    delta = (usd_to_micro(event["realized_usd"], rounding="nearest")
             - usd_to_micro(event["fee_usd"], rounding="nearest"))
    if delta:
        rt._settle_venue([(delta, f"fill:{order_id}", "exchange_pnl", CUSTODY, order_id)])
    rt.consequences.observe("Fill", payload, rt.n)
    owner_handle = rt._order_owner(order_id)
    _tell(rt, owner_handle, {"kind": "polymarket_fill", "order_id": order_id,
                             "token_id": event["token_id"], "market_id": event["market_id"],
                             "side": "buy" if event["is_buy"] else "sell",
                             "size": event["size"], "px": event["px"],
                             "fee_usd": event["fee_usd"]})


def _settle_resolution(rt: Any, event: dict) -> None:
    token = event["token_id"]
    facts = {"market_id": event["market_id"], "condition_id": event.get("condition_id"),
             "token_id": token, "outcome": event.get("outcome"),
             "resolved_at_ns": event.get("ts_ns")}
    rt.ledger.append({"kind": "polymarket.resolution", **facts, "payout": event["payout"],
                      "size": event["size"], "ts": rt.clock.now_ns})
    holders = sorted({lot.handle for lot in rt.consequences.table.lots
                      if lot.coin == coin_of(token) and lot.market == "event"
                      and lot.handle is not None})
    realized = rt.consequences.redeem(coin_of(token), event["payout"], rt.n, facts)
    # The venue's own realised figure for the redemption, booked once in the pot it
    # landed in. Its owner is the one decision that held the token, when only one
    # did; several holders share one unattributed row, and each is told its own
    # FIFO share through the consequence book instead.
    amount = usd_to_micro(event["realized_usd"], rounding="nearest")
    owner = holders[0] if len(holders) == 1 else None
    if amount:
        rt.budget.book_venue(amount, f"resolution:{token}")
        rt.ledger.append({"kind": "venue.settled", "custody": CUSTODY, "amount": amount,
                          "reference": f"resolution:{token}", "reason": "resolution",
                          "handle": owner, "event": rt.n, "ts": rt.clock.now_ns})
        if owner is not None:
            by_custody = rt.venue_deltas.setdefault(owner, {})
            by_custody[CUSTODY] = by_custody.get(CUSTODY, 0) + amount
    for handle, micro in realized.items():
        _tell(rt, handle, {"kind": "polymarket_resolution", **facts,
                           "payout": event["payout"], "realized_micro": micro})


def _tell(rt: Any, handle: str | None, outcome: dict[str, Any]) -> None:
    """A fact the venue produced is its decision's news; the money moves on the payoff."""
    if handle is None:
        return
    owner = rt.handle_to_assembly.get(handle) or rt.outcomes.seat_of(handle)
    if owner is not None:
        rt.outcomes.append(owner, handle=handle, outcome=outcome, delta_micro=0,
                           evidence={"kind": outcome["kind"], "handle": handle,
                                     "ts": rt.clock.now_ns})


# --- what the rest of the runtime reads -----------------------------------------------------

def held(rt: Any) -> tuple[str, ...]:
    """Decisions whose consequence waits on a Polymarket order still resting.

    A decision holding an outcome token is held by the lot book itself (an event
    lot is never marked); this names the ones whose order has not filled yet, so
    the backstop does not fix a no-fill outcome for an order that may still trade.
    """
    surface = getattr(rt, "polymarket", None)
    if surface is None:
        return ()
    return tuple(sorted({order.handle for order in rt.consequences.table.orders
                         if order.remaining and order.order_id in surface.order_ids}))


def awaiting_resolution(rt: Any, handle: str) -> bool:
    """Whether a decision's consequence is owed by an event market that has not resolved."""
    if getattr(rt, "polymarket", None) is None:
        return False
    table = rt.consequences.table
    return (any(lot.handle == handle and lot.market == "event" for lot in table.lots)
            or handle in held(rt))


def defer_grounded(rt: Any, contract: Any) -> Any:
    """Move a grounded contract's horizon to follow an unresolved event position.

    Guarantees the contract's interpretation (criteria, norms, predicates, the
    producer's outputs) is untouched; only its observation horizon moves, by a
    rule fixed before the decision was made: a position in an event market is
    observed when its market resolves, and not before. The final judge is
    therefore commissioned on evidence that includes the resolution, and never
    on a guess of it at a fixed tick (essay II.IV.b: anticipatory settlement is
    the futarchic answer to learning death, so the settlement must be the
    market's own).
    """
    now = rt.ticks_consumed
    if contract.due_tick > now:
        return contract
    span = contract.close_tick - contract.due_tick
    if contract.due_tick == contract.opened_tick + rt.ev.grounded_horizon_ticks:
        rt.ledger.append({"kind": "consequence.awaiting_resolution",
                          "handle": contract.handle, "custody": CUSTODY,
                          "due_tick": contract.due_tick, "ts": rt.clock.now_ns})
    return replace(contract, due_tick=now + 1, close_tick=now + 1 + span)


def custody(rt: Any) -> dict[str, Any] | None:
    """The ``polymarket`` custody account, or None in a world without one."""
    from factorylab.runtime.custody import observed, unavailable

    surface = getattr(rt, "polymarket", None)
    if surface is None or not surface.writes:
        return None
    try:
        account = surface.account()
    except Exception as exc:  # noqa: BLE001 - an unreadable pot is unavailable, not zero
        return unavailable(f"polymarket pot read failed: {type(exc).__name__}")
    return observed(account["observed_at_ns"], network="polygon",
                    venue=surface.venue.target.name, usdc=account["usdc"],
                    usdc_available=account["usdc_available"],
                    positions=account["positions"], open_orders=len(account["open_orders"]))


def pots_view(rt: Any) -> dict[str, Any]:
    """The treasury's pots with the ``polymarket`` pot beside them, counted in the total.

    Outcome tokens are listed at their count and not at a price: until a market
    resolves nobody knows what a token is worth, so only the pot's USDC enters
    ``total_micro``.
    """
    pots = rt.treasury.pots()
    account = custody(rt)
    if account is None:
        return pots
    usdc = (usd_to_micro(account["usdc"], rounding="floor")
            if account["status"] == "observed" else None)
    pots["polymarket"] = usdc
    pots["polymarket_tokens"] = [] if usdc is None else [
        {"token_id": p["token_id"], "size": p["size"]} for p in account["positions"]]
    if usdc is None:
        pots["complete"], pots["total_micro"] = False, None
    elif pots.get("complete"):
        pots["total_micro"] += usdc
    return pots


def wind_down(rt: Any) -> dict[str, Any]:
    """Cancel every resting order and sell every token worth more than dust at the bid.

    Guarantees nothing here raises into a kill, every operation is ledgered
    before and after it is attempted, and the report states what is still held.
    A token the book will not buy above the kill's dust bound is left and named:
    it will still resolve into the pot, but no decision is alive to answer for it.
    """
    from factorylab.runtime.winddown import DUST, FLAT, PENDING, UNKNOWN

    surface = rt.polymarket
    report: dict[str, Any] = {"cancelled": 0, "sold": 0, "left": [], "dust": []}
    try:
        account = surface.account()
        for order in account["open_orders"]:
            client_id = f"kill:{order['order_id']}"
            rt.ledger.append({"kind": "polymarket.wind_down", "op": "cancel",
                              "client_id": client_id, "order_id": order["order_id"]})
            result = surface.venue.cancel(client_id=client_id, order_id=order["order_id"])
            rt.ledger.append({"kind": "polymarket.wind_down_result", "client_id": client_id,
                              "result": result})
            report["cancelled"] += result.get("status") == "cancelled"
        for position in surface.account()["positions"]:
            book = surface.venue.order_book(position["token_id"], 1)
            size = Decimal(position["available"])
            bid = Decimal(book["bids"][0]["price"]) if book["bids"] else None
            if bid is not None and usd_to_micro(bid * size,
                                                rounding="floor") <= rt.m.kill.dust_micro:
                report["dust"].append({"token_id": position["token_id"], "size": str(size)})
                continue
            if bid is None:
                report["left"].append({"token_id": position["token_id"], "size": str(size)})
                continue
            client_id = f"kill:sell:{position['token_id']}"
            rt.ledger.append({"kind": "polymarket.wind_down", "op": "sell",
                              "client_id": client_id, "token_id": position["token_id"],
                              "size": str(size), "price": str(bid)})
            result = surface.venue.place(client_id=client_id, token_id=position["token_id"],
                                         is_buy=False, size=size, price=bid)
            rt.ledger.append({"kind": "polymarket.wind_down_result", "client_id": client_id,
                              "result": result})
            if result.get("status") == "filled":
                report["sold"] += 1
            else:
                report["left"].append({"token_id": position["token_id"], "size": str(size)})
        still = surface.account()
        report["exposure_state"] = (PENDING if still["open_orders"] or report["left"] else
                                    DUST if report["dust"] else FLAT)
    except Exception as exc:  # noqa: BLE001 - nothing may raise into a kill
        report["error"] = type(exc).__name__
        report["exposure_state"] = UNKNOWN
    return report
