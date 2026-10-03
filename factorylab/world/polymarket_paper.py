"""Polymarket paper orders: the pot simulated against Polymarket's live books.

The venue is the world (AGENTS.md: a venue, a market or a data source is the world, not
architecture). ``venue = "paper"`` gives a world Polymarket's real markets and real
prices with no money on Polygon: the seats' reads and the kernel's settlement reads are
the live public reads (``PolymarketReader``), and the pot's orders are simulated here,
against the books those reads return. Polymarket publishes no testnet: its contracts
are "deployed on Polygon mainnet (Chain ID: 137)" alone (docs.polymarket.com,
resources/contracts, read 2026-10-03), and its one CLOB host lists mainnet markets.

The order physics are the live venue's, as the simulated venue (``FakePolymarket``)
keeps them: every order is a GTC post-only BUY; one priced at or above the book's best
ask when it is placed is rejected before it executes (``invalid post-only order: order
crosses book``); a resting buy fills at its own price, as a maker, once the book's best
ask is at or below it, for no more than that ask's size, and no fill is charged a fee
("Makers are never charged fees", docs.polymarket.com, trading/fees). A market that
closes cancels the orders resting on it; a resolution leaves the tokens in the pot,
resolved and worth their payout (Gamma's ``outcomePrices`` once UMA has resolved), as
on the live venue, where redemption is an operator's on-chain transaction.

What the simulation cannot see, stated as fact (``docs/manifest.md``, "Paper orders"):
an order of this pot never enters Polymarket's book, so it moves no price, takes no
queue position and is matched by no one; a fill is inferred from the best ask at the
instant the book is read, so a price that touches a resting order between two reads
fills nothing, and the same standing ask is counted again at the next read.

Replay determinism: the pot's state changes only in this module's methods, each a
function of that state, its arguments and the answers of its own reads. Those reads
(``PaperReads``) are journaled as an outside service, so a replay returns what the run
read and never reads again; the venue itself is journaled as deterministic, so a replay
re-runs it on those answers, and its state is checkpointed whole (``pot_state``).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from factorylab.kernel.money import usd_to_micro
from factorylab.world.polymarket import PolymarketReader, amount_refusal, payout
from factorylab.world.polymarket_clob import LivePolymarket, RequestBudget

#: Outcome-token quantities carry at most two decimals on every tick
#: (``polymarket.ROUNDING``): a fill is quantized down to them.
SIZE_QUANTUM = Decimal("0.01")
MICRO = Decimal(1_000_000)


class PaperReads(PolymarketReader):
    """The paper pot's own reads of the public market: a held or resting token's book and
    a market by id, each a public GET inside the pot's ``RequestBudget``
    (``[polymarket] order_requests_per_10s``), never stamped as a seat's or the kernel's
    read. Guarantees a read past the budget is refused locally and never sent
    (``BudgetSpent``), and that no call signs anything or moves funds."""

    name: str = "polymarket-paper-reads"

    def __init__(self, *, budget: int, **reader: Any) -> None:
        super().__init__(**reader)
        self.name = "polymarket-paper-reads"
        self.budget = RequestBudget(budget, wall=self.wall)

    def _public(self, url: str) -> Any:
        """One public GET inside the pot's budget, counted before it is sent."""
        self.budget.take()
        return self.get(url)

    # The live order venue's own reads, on the same door (``polymarket_wire``): one
    # market by id, a token's market, a token's book at depth 1.
    write_market = LivePolymarket.write_market
    write_market_of_token = LivePolymarket.write_market_of_token
    mark_book = LivePolymarket.mark_book


class PaperPolymarket:
    """The ``FakePolymarket`` contract on Polymarket's live books, with a simulated pot.

    Guarantees: the pot's USDC is integer micro-USD, starting at ``start_micro``, and
    moves only by a fill's ``price x size`` (exact: a price has at most four decimals and
    a size two); identical state, arguments and read answers give identical answers,
    fills and resolutions; a client id is idempotent (a repeat returns the first answer
    and never trades twice); a fill never exceeds its order's remaining size nor the
    best ask's size it was read against, and the same ask is shared across this pot's
    orders on the token, highest price first; nothing is sent to Polymarket but the
    public reads of ``reads``.
    """

    name = "polymarket-paper"
    #: The state ``pot_state`` returns and ``load_pot_state`` restores: everything but the reads.
    STATE = ("start_micro", "cash_micro", "now_ns", "next_oid", "orders", "resting",
             "positions", "client_results", "events", "markets", "turn")

    def __init__(self, *, start_micro: int, reads: Any) -> None:
        if type(start_micro) is not int or start_micro < 0:
            raise ValueError("the paper pot opens with non-negative integer micro-USD")
        self.reads = reads
        self.start_micro = start_micro
        self.cash_micro = start_micro
        self.now_ns = 0
        self.next_oid = 1
        self.orders: dict[str, dict[str, Any]] = {}  # every accepted order, by order id
        self.resting: list[str] = []  # order ids still resting, in acceptance order
        self.positions: dict[str, dict[str, Any]] = {}  # token -> held tokens and cost
        self.client_results: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.markets: dict[str, dict[str, Any]] = {}  # market id -> its order facts
        self.turn = 0  # the market read's rotation

    # ---- state

    def pot_state(self) -> dict[str, Any]:
        """The pot's whole state, plain data: what a checkpoint holds."""
        from copy import deepcopy

        return deepcopy({name: getattr(self, name) for name in self.STATE})

    def load_pot_state(self, saved: dict[str, Any]) -> None:
        """Restore ``pot_state``'s state; the reads stay this process's."""
        from copy import deepcopy

        for name in self.STATE:
            setattr(self, name, deepcopy(saved[name]))

    # ---- the pot

    def _holds_micro(self) -> int:
        return sum(usd_to_micro(Decimal(o["price"]) * Decimal(o["remaining"]),
                                rounding="exact")
                   for o in (self.orders[i] for i in self.resting))

    def account(self) -> dict[str, Any]:
        """The pot as a custodian would state it: USDC, what resting buys hold, tokens
        held at their average cost (a resolved one with its payout), open orders."""
        held = self._holds_micro()
        positions = []
        for token, p in sorted(self.positions.items()):
            size = Decimal(p["size"])
            if size <= 0:
                continue
            positions.append({
                "token_id": token, "market_id": p["market_id"],
                "outcome_index": p["outcome_index"],
                # The market creator's label, for the runtime to normalise; it is
                # third-party text and never published as it is.
                "outcome_name": p["outcome_name"], "size": str(size),
                "avg_px": str(Decimal(p["cost_micro"]) / MICRO / size),
                "available": str(size),
                **({"payout": p["payout"]} if p.get("payout") is not None else {})})
        return {"usdc": str(Decimal(self.cash_micro) / MICRO),
                "usdc_available": str(Decimal(self.cash_micro - held) / MICRO),
                "positions": positions,
                "open_orders": [self._order_view(self.orders[i]) for i in self.resting],
                "observed_at_ns": self.now_ns}

    @staticmethod
    def _order_view(order: dict[str, Any]) -> dict[str, Any]:
        return {"order_id": order["order_id"], "token_id": order["token_id"], "side": "buy",
                "price": order["price"], "size": order["size"],
                "remaining": order["remaining"]}

    @staticmethod
    def _result(order: dict[str, Any]) -> dict[str, Any]:
        filled = Decimal(order["filled"])
        status = ("filled" if Decimal(order["remaining"]) == 0 else
                  "cancelled" if order.get("cancelled") else "resting")
        return {"order_id": order["order_id"], "status": status, "filled_size": str(filled),
                "avg_px": order["price"] if filled else None, "error": None}

    # ---- writes

    def place(self, *, client_id: str, token_id: str, is_buy: bool, size: Decimal,
              price: Decimal, market: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept or reject one GTC post-only buy, once per client id.

        ``market`` is the market the runtime weighed the write against (its id,
        condition, tick, minimum size, outcomes and state). Guarantees an order is
        accepted only on an open market that accepts orders, on its tick and at least
        its minimum size, priced below the best ask of the book read now, and within the
        pot's available USDC; anything else is the venue's refusal of the submission
        (``venue_refused``): the order never existed.
        """
        if client_id in self.client_results:
            return self.lookup(client_id)

        def reject(reason: str) -> dict[str, Any]:
            result = {"order_id": None, "status": "rejected", "filled_size": "0",
                      "avg_px": None, "error": reason, "venue_refused": True}
            self.client_results[client_id] = result
            return dict(result)

        if not is_buy:
            return reject("the venue takes BUY orders only")
        facts = _order_market(market, token_id)
        if facts is None:
            return reject("unknown token")
        if facts["closed"] or not facts["accepting_orders"]:
            return reject("market is not accepting orders")
        size, price = Decimal(str(size)), Decimal(str(price))
        refused = amount_refusal(size, price, Decimal(facts["tick_size"]))
        if refused is not None:
            return reject(refused)
        if size < Decimal(facts["min_order_size"]):
            return reject(f"size below the minimum order of {facts['min_order_size']}")
        try:
            book = self.reads.mark_book(token_id)
        except Exception:  # noqa: BLE001 - an unread book decides nothing
            return reject("polymarket read unavailable")
        if book["asks"] and price >= Decimal(book["asks"][0]["price"]):
            # Post-only, as the live venue's orders are (docs, error-codes): an order
            # that would cross is rejected before it executes, never filled as a taker.
            return reject("invalid post-only order: order crosses book")
        notional = usd_to_micro(price * size, rounding="exact")
        if notional > self.cash_micro - self._holds_micro():
            return reject("not enough balance / allowance")
        self.markets[facts["market_id"]] = {k: facts[k] for k in (
            "market_id", "condition_id", "outcomes")}
        order_id = f"paper-{self.next_oid}"
        self.next_oid += 1
        self.orders[order_id] = {
            "order_id": order_id, "client_id": client_id, "token_id": token_id,
            "market_id": facts["market_id"], "price": str(price), "size": str(size),
            "remaining": str(size), "filled": "0"}
        self.resting.append(order_id)
        self.client_results[client_id] = {"order_id": order_id}
        return self._result(self.orders[order_id])

    def cancel(self, *, client_id: str, order_id: str) -> dict[str, Any]:
        """Cancel one resting order; a repeat returns the first answer."""
        if client_id in self.client_results:
            return dict(self.client_results[client_id])
        if order_id not in self.resting:
            result = {"order_id": order_id, "status": "rejected",
                      "error": "order is not resting"}
        else:
            order = self.orders[order_id]
            self.resting.remove(order_id)
            order["cancelled"] = True
            result = {"order_id": order_id, "status": "cancelled", "error": None,
                      "filled_size": order["filled"]}
        self.client_results[client_id] = result
        return dict(result)

    def lookup(self, client_id: str) -> dict[str, Any]:
        """What the venue holds under a client id; unknown means it never arrived."""
        known = self.client_results.get(client_id)
        if known is None:
            return {"order_id": None, "status": "rejected", "filled_size": "0",
                    "avg_px": None, "error": "no order under this client id"}
        if "status" in known:
            return dict(known)  # a rejection or a cancellation answers as it did
        return self._result(self.orders[known["order_id"]])

    # ---- time

    def advance(self, now_ns: int) -> list[dict[str, Any]]:
        """Fill what the live books cross, close and resolve what the market read shows.

        Each token with a resting order has its book read once (depth 1); every order on
        it priced at or above the best ask fills at its own price, highest price first,
        for at most what remains of that ask's size, quantized down to the token's two
        size decimals. Then one market of those this pot rests or holds unresolved in
        is read, in turn: closed, its resting orders are cancelled; resolved (a payout,
        ``polymarket.payout``), every token held in it stays in the pot, resolved and
        worth its payout. An unanswered read changes nothing. Returns the events since
        the last call, oldest first: ``fill``, ``cancelled`` and ``resolution``.
        """
        if now_ns <= self.now_ns:
            return self.drain_events()
        self.now_ns = now_ns
        for token in sorted({self.orders[i]["token_id"] for i in self.resting}):
            self._match(token)
        watched = sorted({self.orders[i]["market_id"] for i in self.resting}
                         | {p["market_id"] for p in self.positions.values()
                            if p.get("payout") is None and Decimal(p["size"]) > 0})
        if watched:
            market_id = watched[self.turn % len(watched)]
            self.turn += 1
            try:
                market = self.reads.write_market(market_id)
            except Exception:  # noqa: BLE001 - an unread market changes nothing
                market = None
            if market is not None:
                self._settle_market(market_id, market)
        return self.drain_events()

    def _match(self, token: str) -> None:
        try:
            book = self.reads.mark_book(token)
        except Exception:  # noqa: BLE001 - an unread book fills nothing
            return
        if not book["asks"]:
            return
        ask, available = Decimal(book["asks"][0]["price"]), Decimal(book["asks"][0]["size"])
        crossing = sorted((self.orders[i] for i in self.resting
                           if self.orders[i]["token_id"] == token
                           and Decimal(self.orders[i]["price"]) >= ask),
                          key=lambda o: (-Decimal(o["price"]), self.resting.index(o["order_id"])))
        for order in crossing:
            quantity = min(Decimal(order["remaining"]),
                           available.quantize(SIZE_QUANTUM, rounding=ROUND_DOWN))
            if quantity <= 0:
                return
            self._fill(order, quantity)
            available -= quantity

    def _fill(self, order: dict[str, Any], quantity: Decimal) -> None:
        price = Decimal(order["price"])
        cost = usd_to_micro(price * quantity, rounding="exact")
        token = order["token_id"]
        market = self.markets[order["market_id"]]
        index = next(o["outcome_index"] for o in market["outcomes"] if o["token_id"] == token)
        position = self.positions.setdefault(token, {
            "size": "0", "cost_micro": 0, "market_id": order["market_id"],
            "outcome_index": index,
            "outcome_name": next(o["outcome"] for o in market["outcomes"]
                                 if o["token_id"] == token),
            "payout": None})
        position["size"] = str(Decimal(position["size"]) + quantity)
        position["cost_micro"] += cost
        self.cash_micro -= cost
        order["remaining"] = str(Decimal(order["remaining"]) - quantity)
        order["filled"] = str(Decimal(order["filled"]) + quantity)
        if Decimal(order["remaining"]) == 0:
            self.resting.remove(order["order_id"])
        self.events.append({
            "kind": "fill", "order_id": order["order_id"], "token_id": token,
            "market_id": order["market_id"], "is_buy": True, "size": str(quantity),
            "px": order["price"], "fee_usd": "0", "realized_usd": "0", "ts_ns": self.now_ns})

    def _settle_market(self, market_id: str, market: dict[str, Any]) -> None:
        tokens = {o["token_id"] for o in market["outcomes"]}
        if market["closed"]:
            for order_id in [i for i in self.resting if self.orders[i]["token_id"] in tokens]:
                self.resting.remove(order_id)
                self.orders[order_id]["cancelled"] = True
                self.events.append({"kind": "cancelled", "order_id": order_id,
                                    "token_id": self.orders[order_id]["token_id"],
                                    "market_id": market_id, "ts_ns": self.now_ns})
        for token in sorted(tokens):
            position = self.positions.get(token)
            if (position is None or position.get("payout") is not None
                    or Decimal(position["size"]) <= 0 or position["market_id"] != market_id):
                continue
            paid = payout(market, token)
            if paid is None:
                continue
            size = Decimal(position["size"])
            # The payout stays in custody as resolved tokens, worth their payout, as on
            # the live venue: nothing redeems them into spendable cash here.
            position["payout"] = str(paid)
            realized = paid * size - Decimal(position["cost_micro"]) / MICRO
            self.events.append({
                "kind": "resolution", "market_id": market_id,
                "condition_id": self.markets.get(market_id, {}).get("condition_id"),
                "token_id": token, "outcome_index": position["outcome_index"],
                "outcome_name": position["outcome_name"], "payout": str(paid),
                "size": str(size), "realized_usd": str(realized), "ts_ns": self.now_ns})

    def drain_events(self) -> list[dict[str, Any]]:
        """The events not yet handed over, oldest first; each is handed over once."""
        events, self.events = self.events, []
        return events


def _order_market(market: dict[str, Any] | None, token_id: str) -> dict[str, Any] | None:
    """The facts an order is weighed on, from the market its write was checked against,
    or None when that market does not list ``token_id``."""
    if not isinstance(market, dict):
        return None
    outcomes = [{"token_id": str(o.get("token_id")), "outcome_index": int(o["outcome_index"]),
                 "outcome": str(o.get("outcome") or "")}
                for o in market.get("outcomes") or ()]
    if token_id not in {o["token_id"] for o in outcomes}:
        return None
    if market.get("tick_size") is None or market.get("min_order_size") is None:
        return None
    return {"market_id": str(market["market_id"]), "condition_id": market.get("condition_id"),
            "outcomes": outcomes, "tick_size": str(market["tick_size"]),
            "min_order_size": str(market["min_order_size"]),
            "closed": bool(market.get("closed")),
            "accepting_orders": bool(market.get("accepting_orders"))}
