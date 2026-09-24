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
* **The market's price settles early; its resolution settles late.** Fills enter
  the consequence book as ``event`` lots (``settlement/lots.py``), marked each
  tick at the CLOB midpoint. At the consequence backstop a held position is
  scored at that mark, exactly as an open spot lot is: the price is the
  market's anticipatory settlement of the belief, the cure the essay names for
  learning death (II.IV.b: "the compensation period of any exploratory learner
  must be shorter than the lifetime of the things it is being compensated for
  discovering"). The resolution closes the lot later (``LotTable.redeem``) and
  its money reaches the owner through ``_settle_late``; the score is never
  revised. A token with no midpoint is not marked, and its decision falls back
  on its provisional verdict like any other unobserved consequence.
* **Claims stay in their custody.** What a Polymarket position realises is a
  claim on the polymarket pot (``claim_share``), never on the venue, and
  financing converts only venue claims, so a profit made on Polygon is never
  withdrawn from Hyperliquid money. The pot reconciles against its own books
  every tick (``reconcile``).
* **The world settles claims on its markets.** An enabled block, on either venue,
  offers the forecast predicates ``event_pays`` and ``event_price_above``
  (``settlement/vocabulary.py``). A claim is settled on this surface's own read of
  the named token at its due tick (``event_facts``): the market's resolution or its
  midpoint, a fact the world measures, never another model's reading (essay
  II.III.b). An unanswered read is an excluded sample, never a zero.
* **Third-party labels are not ours to repeat.** Outcome names are written by
  market creators. Every surface outside the jailed reads (the pot, custody, the
  pots, receipts, outcomes, the ledger) carries token and market ids and a
  normalised outcome (``YES``, ``NO`` or ``outcome <n>``), never the label.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
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
        # The pot's own claim book, apart from the venue's (BudgetBook.claim_venue):
        # exact realised P&L per decision, what of it has been claimed, the claims
        # per seat, and what the pot settled, in micro and exactly.
        self.realized: dict[str, Fraction] = {}
        self.claimed: dict[str, int] = {}
        self.claims: dict[str, int] = {}
        self.booked = 0
        self.settled = Decimal(0)
        self.opening: Decimal | None = None

    FIELDS = ("intents", "order_ids", "realized", "claimed", "claims", "booked", "settled",
              "opening")

    def state(self) -> dict[str, Any]:
        """Intents, order ownership, the claim book, the window count and the venue's state."""
        target = self.venue.target
        return {**{name: getattr(self, name) for name in self.FIELDS},
                "window_orders": list(self.window_orders),
                "venue": dict(vars(target)) if self.venue.deterministic else None}

    def restore(self, saved: dict[str, Any]) -> None:
        """Rebind saved state to this process's adapter."""
        for name in self.FIELDS:
            if name in saved:
                setattr(self, name, saved[name])
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


def simulate_reads(rt: Any) -> None:
    """Answer a live-read world's Polymarket reads from the seeded simulated venue.

    For offline runs of a world whose ``venue = "live"`` (``scripts/fastloop.py``):
    the fake answers the same reads as ``PolymarketReader`` and moves on the world's
    clock. Guarantees the published surface is untouched: no write tool, no pot,
    and the manifest the world was launched with, so the run is the launch path's
    with only the outside answer simulated.
    """
    from factorylab.runtime.resume import JournalProxy
    from factorylab.world.polymarket import FakePolymarket

    surface = rt.polymarket
    if surface.writes:
        raise ValueError("simulate_reads replaces a live reader only")
    surface.venue = JournalProxy(FakePolymarket(seed=surface.spec.seed), rt.ledger,
                                 "polymarket", deterministic=True)


# --- world-settled forecasts ----------------------------------------------------------------

def vocabulary(manifest: Any) -> tuple:
    """The event predicates a world offers: all of them where ``[polymarket]`` is enabled,
    none elsewhere. Fixed by the manifest for the world's life."""
    from factorylab.settlement.vocabulary import EVENT_VOCABULARY

    spec = getattr(manifest, "polymarket", None)
    return EVENT_VOCABULARY if spec is not None and spec.enabled else ()


def event_facts(rt: Any, predicate_id: str, token_id: str) -> Any:
    """One outcome token as the world reads it at settlement, for an event predicate.

    Returns ``{listed, closed, payout, midpoint}`` (numbers as decimal strings) or
    ``UNOBSERVABLE`` when the world did not answer: the market read failed, or a
    price claim met an unresolved market with no midpoint (a closed market, an
    empty or one-sided book, a failed read). That absence is the world's,
    not the forecaster's, so the claim settles censored and is excluded from
    accountable resolution. A token the venue does not list is a fact
    (``listed: false``): the claim named nothing, and it settles censored against
    its owner. The book is read only for a price claim on an unresolved market, and
    a midpoint exists only where it has both a bid and an ask.

    The reads go through the surface's journal, so a replay settles on what was
    read. Only ids and numbers enter the facts; no market text does.
    """
    from factorylab.settlement.vocabulary import UNOBSERVABLE
    from factorylab.world.polymarket import payout

    surface = rt.polymarket

    def unavailable(read: str) -> Any:
        rt.ledger.append({"kind": "polymarket.event_unavailable", "token_id": token_id,
                          "predicate": predicate_id, "read": read, "ts": rt.clock.now_ns})
        return UNOBSERVABLE

    try:
        market = surface.venue.market_of_token(token_id)
    except Exception:  # noqa: BLE001 - an unanswered read is an absent fact
        return unavailable("market")
    facts: dict[str, Any] = {"listed": market is not None, "closed": None, "payout": None,
                             "midpoint": None}
    if market is not None:
        paid = payout(market, token_id)
        facts.update(closed=market["closed"], payout=None if paid is None else str(paid))
        if predicate_id == "event_price_above" and paid is None:
            # The midpoint of the book's best bid and ask, never the CLOB's /midpoint,
            # which answers 0.5 for an empty book (read 2026-09-23 on a resolved market).
            try:
                mid = None if market["closed"] else _decimal(
                    surface.venue.order_book(token_id, 1)["midpoint"])
            except Exception:  # noqa: BLE001
                mid = None
            if mid is None or not 0 < mid < 1:
                return unavailable("midpoint")
            facts["midpoint"] = str(mid)
    rt.ledger.append({"kind": "polymarket.event_read", "token_id": token_id,
                      "predicate": predicate_id, **facts, "ts": rt.clock.now_ns})
    return facts


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
        return {**account_view(sanitized(surface.account())), "as_of_ns": rt.clock.now_ns}
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


def outcome_label(index: int, name: Any) -> str:
    """A normalised outcome name: ``YES``, ``NO`` or ``outcome <n>``, never third-party text."""
    text = str(name or "").strip().lower()
    if text in ("yes", "no"):
        return text.upper()
    return f"outcome {int(index)}"


def sanitized(account: dict | None) -> dict | None:
    """A pot read with every third-party label replaced by its normalised outcome."""
    if account is None:
        return None
    positions = []
    for position in account["positions"]:
        row = {k: v for k, v in position.items() if k != "outcome_name"}
        row["outcome"] = outcome_label(position["outcome_index"], position.get("outcome_name"))
        positions.append(row)
    return {**account, "positions": positions}


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


def taker_fee(market: dict, size: Decimal, price: Decimal) -> Decimal:
    """The most a buy can pay in fees: the taker fee at the market's own rate."""
    fees = market.get("fees") or {}
    rate = _decimal(fees.get("rate")) if fees.get("enabled") else Decimal(0)
    return size * (rate or Decimal(0)) * price * (1 - price)


def refusal(rt: Any, surface: PolymarketSurface, seat: str | None, handle: str,
            tool_id: str, args: dict, *, committed: Decimal = Decimal(0),
            window_count: int | None = None) -> str | None:
    """Why this write would be refused before any intent, or None.

    Guarantees new exposure is weighed against the polymarket pot alone: a buy
    needs its notional and the taker fee it could pay in the pot's available
    USDC (less ``committed``, what earlier writes of the same batch need); a sell
    needs the tokens. It also enforces the manifest's caps (one order's notional,
    the pot's open exposure, orders a window) and the market's own tick and
    minimum order size. An order identical to one an earlier decision left
    resting is not refused: the venue allows it and fees price it (Chapter II
    rulings, R6). An unreadable pot refuses new risk and never a cancellation.
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
    tick, minimum = _decimal(market.get("tick_size")), _decimal(market.get("min_order_size"))
    if tick is not None and tick > 0 and price % tick:
        return f"price is not on the market's {tick} tick"
    if minimum is not None and size < minimum:
        return f"size is below the market's minimum order of {minimum} tokens"
    notional = size * price
    if usd_to_micro(notional, rounding="ceil") > spec.max_order_micro:
        return "order notional exceeds [polymarket] max_order_usd"
    buy = args["side"] == "buy"
    if buy:
        fee = taker_fee(market, size, price)
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
    return None


def batch_refusal(rt: Any, seat: str, handle: str,
                  writes: list[tuple[str, str, dict]]) -> tuple[int, str] | None:
    """The first Polymarket write of a batch that would be refused, and why, or None.

    Guarantees a batch is weighed whole before any of it is submitted, and more
    strictly than one write alone: the collateral and exposure earlier buys in the
    batch would need, their fees included, is counted against the later ones, and
    the window cap counts
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
                size, price = Decimal(str(args["size"])), Decimal(str(args["price"]))
                market = surface.venue.target.market_of_token(args["token_id"]) or {}
                committed += size * price + taker_fee(market, size, price)
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
        if surface.venue.deterministic:
            # A simulated read-only venue (``simulate_reads``) still moves and resolves,
            # so what an event forecast settles on changes with the world's clock. With
            # no writes it holds nothing, so its events are empty.
            settle(rt, surface.venue.advance(rt.clock.now_ns))
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
    mark(rt)
    reconcile(rt)


def mark(rt: Any) -> None:
    """Give the consequence book this tick's midpoint of every token a lot holds.

    Guarantees a mark is the market's own price strictly between 0 and 1. A token
    whose midpoint is unavailable loses its mark rather than keeping a stale one,
    so its lot is not marked and its decision falls back as any unobserved
    consequence does.
    """
    surface = rt.polymarket
    for coin in sorted({lot.coin for lot in rt.consequences.table.lots
                        if lot.market == "event"}):
        try:
            mid = _decimal(surface.venue.midpoint(coin.removeprefix("PM:")))
        except Exception:  # noqa: BLE001 - an unread price is an absent price
            mid = None
        if mid is not None and 0 < mid < 1:
            rt.consequences.observe("MarketMid", {"coin": coin, "mid": str(mid)}, rt.n)
        elif rt.consequences.mids.pop(coin, None) is not None:
            rt.ledger.append({"kind": "polymarket.mark_unavailable", "coin": coin,
                              "ts": rt.clock.now_ns})


def reconcile(rt: Any) -> dict[str, Any] | None:
    """Check the pot against its own books: opening + settled == USDC + tokens at cost.

    Every fill and resolution the pot settled is ledgered exactly; the venue's
    account is the other side. Guarantees a disagreement larger than one
    micro-USD is ledgered as ``polymarket.drift``, as the treasury reconciler
    ledgers ``reconcile.drift``; the first observation is ledgered as the baseline.
    """
    surface = rt.polymarket
    try:
        account = surface.account()
    except Exception as exc:  # noqa: BLE001 - an unreadable pot is not reconciled
        rt.ledger.append({"kind": "polymarket.reconcile_unavailable",
                          "reason": type(exc).__name__, "ts": rt.clock.now_ns})
        return None
    held = Decimal(account["usdc"]) + sum(
        (Decimal(p["size"]) * Decimal(p["avg_px"]) for p in account["positions"]), Decimal(0))
    if surface.opening is None:
        surface.opening = held - surface.settled
        rt.ledger.append({"kind": "polymarket.opening", "usdc": str(surface.opening),
                          "ts": rt.clock.now_ns})
    drift = held - (surface.opening + surface.settled)
    result = {"opening": str(surface.opening), "settled": str(surface.settled),
              "held_at_cost": str(held), "drift": str(drift)}
    if abs(drift) > Decimal("0.000001"):
        rt.ledger.append({"kind": "polymarket.drift", **result, "ts": rt.clock.now_ns})
    return result


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
    rt.polymarket.settled += Decimal(event["realized_usd"]) - Decimal(event["fee_usd"])
    delta = (usd_to_micro(event["realized_usd"], rounding="nearest")
             - usd_to_micro(event["fee_usd"], rounding="nearest"))
    owner_handle = rt._order_owner(order_id)
    _book_pot(rt, delta, f"fill:{order_id}", "exchange_pnl", owner_handle)
    rt.consequences.observe("Fill", payload, rt.n)
    _tell(rt, owner_handle, {"kind": "polymarket_fill", "order_id": order_id,
                             "token_id": event["token_id"], "market_id": event["market_id"],
                             "side": "buy" if event["is_buy"] else "sell",
                             "size": event["size"], "px": event["px"],
                             "fee_usd": event["fee_usd"]})


def _settle_resolution(rt: Any, event: dict) -> None:
    token = event["token_id"]
    facts = {"market_id": event["market_id"], "condition_id": event.get("condition_id"),
             "token_id": token,
             "outcome": outcome_label(event["outcome_index"], event.get("outcome_name")),
             "resolved_at_ns": event.get("ts_ns")}
    rt.ledger.append({"kind": "polymarket.resolution", **facts, "payout": event["payout"],
                      "size": event["size"], "ts": rt.clock.now_ns})
    holders = sorted({lot.handle for lot in rt.consequences.table.lots
                      if lot.coin == coin_of(token) and lot.market == "event"
                      and lot.handle is not None})
    before = {r.handle: r.realized_micro for r in rt.consequences.table.returns}
    realized = rt.consequences.redeem(coin_of(token), event["payout"], rt.n, facts)
    credit_realized(rt, {r.handle: r.realized_micro - before.get(r.handle, 0)
                         for r in rt.consequences.table.returns
                         if r.realized_micro != before.get(r.handle, 0)})
    # The venue's own realised figure for the redemption, booked once in the pot it
    # landed in. Its owner is the one decision that held the token, when only one
    # did; several holders share one unattributed row, and each is told its own
    # FIFO share through the consequence book instead.
    rt.polymarket.settled += Decimal(event["realized_usd"])
    _book_pot(rt, usd_to_micro(event["realized_usd"], rounding="nearest"),
              f"resolution:{token}", "resolution", holders[0] if len(holders) == 1 else None)
    for handle, micro in realized.items():
        _tell(rt, handle, {"kind": "polymarket_resolution", **facts,
                           "payout": event["payout"], "realized_micro": micro})


def _book_pot(rt: Any, amount: int, reference: str, reason: str,
              handle: str | None) -> None:
    """Book P&L the pot settled on the pot's own books, never on the venue's.

    ``BudgetBook.book_venue`` is what venue claims are backed by and what
    financing converts; the polymarket pot has no conversion route, so its
    settlements are ledgered as ``venue.settled`` with ``custody = "polymarket"``
    and summed here, beside that book and never inside it.
    """
    if not amount:
        return
    rt.polymarket.booked += amount
    rt.ledger.append({"kind": "venue.settled", "custody": CUSTODY, "amount": amount,
                      "reference": reference, "reason": reason, "handle": handle,
                      "event": rt.n, "ts": rt.clock.now_ns})
    if handle is not None:
        by_custody = rt.venue_deltas.setdefault(handle, {})
        by_custody[CUSTODY] = by_custody.get(CUSTODY, 0) + amount


def credit_realized(rt: Any, deltas: dict[str, Fraction]) -> None:
    """Record, exactly, what event fills and resolutions realised for each decision."""
    surface = rt.polymarket
    for handle, delta in deltas.items():
        surface.realized[handle] = surface.realized.get(handle, Fraction(0)) + delta


def claim_share(rt: Any, owner: str, handle: str, micro: int, reason: str) -> int:
    """Claim a booking's Polymarket share on the pot; return the share claimed.

    Guarantees the share is what the decision's event positions realised and has
    not yet been claimed, bounded by the booking itself (it never exceeds the
    booking or runs against its sign), and that it lands in the pot's own claim
    book, where financing cannot reach it.
    """
    surface = rt.polymarket
    exact = surface.realized.get(handle, Fraction(0))
    owed = exact.numerator // exact.denominator - surface.claimed.get(handle, 0)
    share = max(min(owed, max(0, micro)), min(0, micro))
    if share:
        surface.claimed[handle] = surface.claimed.get(handle, 0) + share
        surface.claims[owner] = surface.claims.get(owner, 0) + share
        rt.ledger.append({"kind": "polymarket.claim", "assembly_id": owner, "handle": handle,
                          "amount": share, "reason": reason,
                          "claim_after": surface.claims[owner], "ts": rt.clock.now_ns})
    return share


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

def custody(rt: Any) -> dict[str, Any] | None:
    """The ``polymarket`` custody account, or None in a world without one."""
    from factorylab.runtime.custody import observed, unavailable

    surface = getattr(rt, "polymarket", None)
    if surface is None or not surface.writes:
        return None
    try:
        account = sanitized(surface.account())
    except Exception as exc:  # noqa: BLE001 - an unreadable pot is unavailable, not zero
        return unavailable(f"polymarket pot read failed: {type(exc).__name__}")
    return observed(account["observed_at_ns"], network="polygon",
                    venue=surface.venue.target.name, usdc=account["usdc"],
                    usdc_available=account["usdc_available"],
                    positions=account["positions"], open_orders=len(account["open_orders"]))


def pots_view(rt: Any) -> dict[str, Any]:
    """The treasury's pots with the ``polymarket`` pot beside them, counted in the total.

    The pot is its USDC plus its tokens at cost, so a buy moves value from one to
    the other and the total does not dip; the tokens are also listed by count and
    cost. The pot's claims are shown beside it, apart from the venue's.
    """
    pots = rt.treasury.pots()
    account = custody(rt)
    if account is None:
        return pots
    observed = account["status"] == "observed"
    tokens = [] if not observed else [
        {"token_id": p["token_id"], "market_id": p["market_id"], "outcome": p["outcome"],
         "size": p["size"],
         "cost_micro": usd_to_micro(Decimal(p["size"]) * Decimal(p["avg_px"]),
                                    rounding="floor")}
        for p in account["positions"]]
    usdc = usd_to_micro(account["usdc"], rounding="floor") if observed else None
    value = None if usdc is None else usdc + sum(t["cost_micro"] for t in tokens)
    pots["polymarket"] = value
    pots["polymarket_usdc"] = usdc
    pots["polymarket_tokens"] = tokens
    pots["polymarket_claims"] = dict(sorted(rt.polymarket.claims.items()))
    if value is None:
        pots["complete"], pots["total_micro"] = False, None
    elif pots.get("complete"):
        pots["total_micro"] += value
    return pots


def wind_down(rt: Any) -> dict[str, Any]:
    """Cancel every resting order; leave every held token to resolve into the pot.

    Guarantees nothing here raises into a kill, every cancellation is ledgered
    before and after it is attempted, and the report names every token still
    held. A held outcome token is fully paid for: it cannot be liquidated, pays no
    funding and redeems into the pot at resolution, so selling it into a thin book
    at the kill would only destroy value and leave an order resting after death.
    It is residual exposure, reported as ``wind_down_pending``, never as flat.
    """
    from factorylab.runtime.winddown import FLAT, PENDING, UNKNOWN

    surface = rt.polymarket
    report: dict[str, Any] = {"cancelled": 0, "residual": []}
    try:
        for order in surface.account()["open_orders"]:
            client_id = f"kill:{order['order_id']}"
            rt.ledger.append({"kind": "polymarket.wind_down", "op": "cancel",
                              "client_id": client_id, "order_id": order["order_id"]})
            result = surface.venue.cancel(client_id=client_id, order_id=order["order_id"])
            rt.ledger.append({"kind": "polymarket.wind_down_result", "client_id": client_id,
                              "result": result})
            report["cancelled"] += result.get("status") == "cancelled"
        still = sanitized(surface.account())
        report["residual"] = [{key: p[key] for key in ("token_id", "market_id", "outcome",
                                                       "size", "avg_px")}
                              for p in still["positions"]]
        report["open_orders"] = len(still["open_orders"])
        report["exposure_state"] = (PENDING if still["positions"] or still["open_orders"]
                                    else FLAT)
    except Exception as exc:  # noqa: BLE001 - nothing may raise into a kill
        report["error"] = type(exc).__name__
        report["exposure_state"] = UNKNOWN
    rt.ledger.append({"kind": "polymarket.wind_down_report", **report})
    return report
