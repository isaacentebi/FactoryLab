"""Polymarket event markets in a running world: tools, custody, intents, settlement.

A world that enables ``[polymarket]`` gets a surface, not a strategy: three reads
of the public market (search, one market's contract, one token's book), two
reads of its own pot (its positions, its open orders), and where the pot exists
two writes (a limit order and its cancellation): on the simulated venue, or signed
on Polymarket's live CLOB (``world/polymarket_clob.py``). Nothing here asks a seat
to use any of it, and no description says it would be good to (AGENTS.md: physics
is enforced, not announced).

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
  tick at the midpoint of the token's book (``mark``). At the consequence
  backstop a held position is scored at that mark, exactly as an open spot lot
  is: the price is the
  market's anticipatory settlement of the belief, the cure the essay names for
  learning death (II.IV.b: "the compensation period of any exploratory learner
  must be shorter than the lifetime of the things it is being compensated for
  discovering"). The resolution closes the lot later (``LotTable.redeem``) and
  its money reaches the owner through ``_settle_late``; the score is never
  revised. A token with no two-sided book is not marked, and its decision falls back
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

import json
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from factorylab.kernel.money import usd_to_micro

CUSTODY = "polymarket"
READS = ("polymarket.search", "polymarket.market", "polymarket.book")
ACCOUNT = "polymarket.positions"
#: The pot's resting orders, read from the same account read as the positions.
OPEN_ORDERS = "polymarket.open_orders"
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
    """The tools a ``[polymarket]`` world publishes: what each does, every call free.

    A read of the public market API (or of the seeded simulated venue) pays no one,
    so it carries no price: the wallet moves only when money moves. What a write
    costs is the market's own, paid from and settled into the polymarket pot: the
    price to the matched side and the market's fee, both real counterparties.
    """
    live = getattr(spec, "venue", "fake") == "live"
    token = {"type": "string", "pattern": r"^[0-9]{1,100}$", "minLength": 1}
    decimal = {"type": ["string", "number"]}
    tools = {
        "polymarket.search": (
            f"Search Polymarket event markets by text. Returns up to {MAX_SEARCH_RESULTS} "
            "markets: id, question, outcomes with their token ids and last prices, end date, "
            "resolution source, tick size, minimum order size, fee schedule and whether the "
            "market accepts orders. Market text is written by third parties. Free.",
            {"query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_CHARS},
             "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}},
            ["query"], [{"query": "election", "limit": 5}], 0),
        "polymarket.market": (
            "One Polymarket market by id: its contract fields and its resolution rules "
            "text, which third parties wrote. Free.",
            {"market_id": {"type": "string", "minLength": 1, "maxLength": 80}},
            ["market_id"], [{"market_id": "fake-1"}], 0),
        "polymarket.book": (
            "The order book of one outcome token, best price first on both sides, with "
            "its midpoint, tick size and minimum order size. Free.",
            {"token_id": token, "depth": {"type": "integer", "minimum": 1,
                                          "maximum": MAX_DEPTH}},
            ["token_id"], [{"token_id": "100000000000000000000", "depth": 5}], 0),
    }
    if writes:
        tools.update({
            ACCOUNT: (
                "The polymarket custody pot: USDC, what resting orders hold, outcome "
                "tokens held and open orders. Free.",
                {}, [], [{}], 0),
            OPEN_ORDERS: (
                "The polymarket pot's resting orders: order id, token id, side, price, "
                "size and what remains unfilled. Free.",
                {}, [], [{}], 0),
            "polymarket.place_limit": (
                "Place a good-until-cancelled limit order to buy outcome tokens of one "
                "Polymarket market, paid from and settled into the polymarket pot. The "
                "venue takes BUY orders only: a position is held until its market "
                "resolves, when each winning token pays 1 USDC and each losing token 0. "
                "size is in tokens, price in USDC per token strictly between 0 and 1 on "
                "the market's tick. A buy holds price x size USDC while it rests. An order "
                "that fills on arrival pays the market's taker fee. " + (
                    "The order is signed by the pot's wallet and sent to Polymarket's CLOB "
                    "on Polygon as a GTC order; its identity is its EIP-712 order hash. "
                    "The pot's collateral is pUSD, Polymarket's USDC-backed token. "
                    if live else "") + "Free to call.",
                {"token_id": token, "side": {"type": "string", "enum": ["buy"]},
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

    def __init__(self, spec: Any, venue: Any, *, writes: bool, live: bool = False) -> None:
        self.spec = spec
        self.venue = venue
        self.writes = writes
        # Live orders: the venue is Polymarket's CLOB (``world/polymarket_clob.py``),
        # journaled as an outside service, so its state lives here, not in the adapter.
        self.live = live
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
        # token id -> the id of the market that lists it, or None for a token no market
        # listed when it was looked up. Which market lists a token is fixed when the
        # market is made, so a token is looked up once for the world's life, and every
        # later read of its market is one GET by market id. Only a found market is
        # kept. It grows with the distinct listed tokens the world has seen claimed or
        # traded: the world's record of them.
        self.token_markets: dict[str, str] = {}
        # Each seat registration's open reads (``open_limit``):
        # "<registration>|due:<token>:<tick>" for the settlement of its forecasts on a
        # token due at one tick; None while open, else the wall ns (``wall_now``) until
        # which it still counts: one window, 10 s, after the kernel's last request for it.
        self.open_reads: dict[str, int | None] = {}
        # Codex on #152: the instant this venue's events feed ("events": its fills,
        # cancels and resolutions) and each held token's book were last read
        # successfully; what depends on a stream waits for its read.
        self.through: dict[str, int] = {}
        # Every order's filled size as booked here, by order id: a fill beyond what its
        # order ordered is quarantined, never owned (``_settle_fill``).
        self.filled: dict[str, str] = {}
        # The live venue's fill and resolution cursor (``LivePolymarket.poll``): carried
        # in, returned, checkpointed, so a replay reads what the run read.
        self.cursor: dict[str, Any] = {}
        # Whether the last reconciliation found money gone that the books do not explain
        # (live orders): no new risk until it agrees again.
        self.drifting = False
        # Fills whose fee their execution did not state (``execution_fee``): open until
        # the custodian's balance settles them (``reconcile``); no new risk meanwhile.
        self.open_fees: list[dict[str, Any]] = []
        # The tick's account read, keyed by ``_tick_key`` (transient, never checkpointed).
        self._account_memo: tuple | None = None
        # The market each write was last weighed against, by token (transient): the one
        # its live order is then built on.
        self.checked: dict[str, dict] = {}

    FIELDS = ("intents", "order_ids", "realized", "claimed", "claims", "booked", "settled",
              "opening", "token_markets", "open_reads", "through", "filled", "cursor", "open_fees",
              "drifting")

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
        if self.live:
            # Codex P2 on #177: the pot's request stamps live in the process that sent
            # them. The one that died may have sent a whole allowance in the 10 s before
            # it did, so a resumed pot counts its allowance as spent at the resume.
            self.venue.target.budget.spend_all()
        if saved.get("venue") is not None and self.venue.deterministic:
            self.venue.target.__dict__.clear()
            self.venue.target.__dict__.update(saved["venue"])

    def writes_of(self, handle: str) -> list[dict[str, Any]]:
        """The Polymarket writes a decision made, as durable intents, in submission order."""
        return [intent for intent in self.intents.values() if intent["handle"] == handle]

    def account(self, rt: Any = None) -> dict[str, Any] | None:
        """The pot as its custodian states it, or None in a world without one.

        The simulated pot is read from the venue's own books without I/O. The live pot
        is read through the journal (a replay reads what the run read), once per tick
        and Polymarket write (``_tick_key``): a write can move it, nothing else here can.
        """
        if not self.writes:
            return None
        if not self.live:
            return self.venue.target.account()
        key = None if rt is None else _tick_key(rt)
        if key is not None and self._account_memo is not None and self._account_memo[0] == key:
            return self._account_memo[1]
        account = self.venue.account(markets=dict(self.token_markets),
                                     resolved=dict(self.cursor.get("resolved", {})))
        if key is not None:
            self._account_memo = (key, account)
        return account


def install(rt: Any) -> None:
    """Build the surface a ``[polymarket] enabled`` world launches with, and publish its tools.

    Guarantees a world that does not enable the block is untouched: no attribute,
    no tool, no pot. The simulated venue is journalled as deterministic (a replay
    re-runs it); the live reader and the live order venue are journalled like every
    other outside service, so a replay returns what was read rather than reading
    again, and an order interrupted in flight resumes uncertain, never resent
    (``RecoveryJournal.call``). The live order venue signs only for an intent this
    surface ledgered first (``LivePolymarket.intent_of``).
    """
    spec = rt.m.polymarket
    if not spec.enabled:
        return
    from factorylab.runtime.resume import JournalProxy
    from factorylab.world.polymarket import FakePolymarket, PolymarketReader

    simulated = spec.venue == "fake"
    live = spec.venue == "live" and spec.orders
    writes = simulated or live
    if simulated:
        target = FakePolymarket(seed=spec.seed,
                                start_usdc=Decimal(spec.collateral_micro) / 1_000_000)
    elif live:
        from factorylab.world.polymarket_clob import live_venue

        target = live_venue(spec, identity=lambda: (rt.m.exchange.client_namespace,
                                                    getattr(rt, "launch_nonce", None)))
    else:
        target = PolymarketReader()
    venue = JournalProxy(target, rt.ledger, "polymarket", deterministic=simulated)
    venue.observer = lambda method, args, kwargs, result: observe_answer(
        rt, method, args, kwargs, result)
    rt.polymarket = PolymarketSurface(spec, venue, writes=writes, live=live)
    if live:
        # Chapter II §II.b: no order leaves this process without its durable intent.
        target.intent_of = lambda client_id: rt.polymarket.intents.get(client_id)
    # registration -> [[wall ns, requests]] of its reads (and claim lookups) in the
    # sliding 10 s, each at the instant its last request was sent (``wall_now``).
    rt.polymarket_read_use = {}
    rt._polymarket_tick_reads = None
    specs = tool_specs(spec, writes=writes)
    share = read_share(spec, rt.m.exchange.max_readers)
    for tool_id in READS:
        # A limit is a published fact (essay II.I.b), never advice.
        specs[tool_id]["description"] += (
            f" Held by seats with a venue read slot. Polymarket reads are bounded by "
            f"Polymarket's published rate limits: this world uses "
            f"{spec.read_requests_per_10s} requests in any sliding 10 s, of which "
            f"{spec.kernel_reserve_per_10s} are the kernel's own settlement reads, and "
            f"each slot has a fixed share of {share} requests in any sliding 10 s of "
            "wall time, each request counted when it is sent (on the simulated venue, "
            "which sends nothing, the world's clock). Every read is charged one "
            "request to your share; a read your remaining share cannot cover is "
            "refused and not sent. "
            "Within one world tick, a read identical to one already answered in that "
            "tick is answered from that answer, and sends no request.")
    rt.tool_specs.update(specs)


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
    # The same limits bind a rehearsal: the tick's answers and the charge per
    # dispatched read work on the simulated venue as on the live reader.
    surface.venue.observer = lambda method, args, kwargs, result: observe_answer(
        rt, method, args, kwargs, result)


# --- world-settled forecasts ----------------------------------------------------------------

#: The codes a live Polymarket reader is refused with (``runtime/reasons.py``).
IP_IN_USE = "polymarket_ip_in_use"
LIVE_REQUIRES_A_LEDGER = "polymarket_live_requires_a_ledger"
LIVE_REQUIRES_THE_WALL_CLOCK = "polymarket_live_requires_the_wall_clock"
LIVE_ON_A_TAPE = "polymarket_live_on_a_tape"
#: The host-wide lock's name, in the operator's lock directory.
IP_LOCK_NAME = "polymarket-ip"


class LiveReaderRefused(RuntimeError):
    """A live Polymarket reader the host or the world cannot run; ``code`` names why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ip_lock() -> Any:
    """Hold the host's one live Polymarket reader, or refuse with ``polymarket_ip_in_use``.

    Guarantees at most one live Polymarket reader runs on a host at a time, whatever
    directory its world runs in: the lock (``polymarket-ip.lock``) lives in the
    operator's one lock directory on the host (``capital_loop.default_lock_dir``, where
    wave 10's reserve lock lives), never beside a run. The request budget assumes the
    host's IP is the factory's own: two thirds of the tightest published limit is sized
    for one world a host. The returned lock is released by ``disarm`` or by process
    death (an ``flock`` dies with its process, so no stale lock survives a crash).
    """
    from factorylab.kernel.ledger import LedgerBusyError, LedgerLock
    from factorylab.runtime import capital_loop

    where = capital_loop.default_lock_dir()
    where.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        return LedgerLock(where / IP_LOCK_NAME)
    except LedgerBusyError:
        raise LiveReaderRefused(IP_IN_USE) from None


def arm(rt: Any) -> None:
    """Admit this world's live Polymarket reader before it may send anything, or refuse.

    Guarantees, before the world's first event at genesis (``run``) and before a
    resume replays anything (``resume_runtime``): a world whose Polymarket reads go
    to the network has a ledger (``polymarket_live_requires_a_ledger``: every request
    it sends is journaled, with its wall-clock stamp, where a replay finds it), runs on
    the wall clock (``polymarket_live_requires_the_wall_clock``: Polymarket counts its
    limits in wall time, and a simulated clock's ticks are no measure of it), and
    holds the host's IP lock (``ip_lock``). A world whose reads are answered offline,
    by the simulated venue (``simulate_reads``) or any other stand-in, is admitted with
    no lock. Idempotent; ``disarm`` releases what it took.
    """
    from factorylab.runtime.live import wall_paced
    from factorylab.world.polymarket import PolymarketReader

    surface = getattr(rt, "polymarket", None)
    if (surface is None or getattr(rt, "_polymarket_ip_lock", None) is not None
            or not isinstance(surface.venue.target, PolymarketReader)):
        return
    if rt.m.exchange.tape is not None:
        # Today's event markets are the future of a recorded market: a replay reads
        # them only from the simulated venue (``simulate_reads``; critique C2).
        raise LiveReaderRefused(LIVE_ON_A_TAPE)
    if not getattr(rt, "ledger_path", None):
        raise LiveReaderRefused(LIVE_REQUIRES_A_LEDGER)
    if not wall_paced(rt.tick_clock):  # a LiveClock, or a wrapper declaring one
        raise LiveReaderRefused(LIVE_REQUIRES_THE_WALL_CLOCK)
    rt._polymarket_ip_lock = ip_lock()


def disarm(rt: Any) -> None:
    """Release the host's IP lock if this runtime holds it. Idempotent."""
    lock = getattr(rt, "_polymarket_ip_lock", None)
    rt._polymarket_ip_lock = None
    if lock is not None:
        lock.close()


def vocabulary(manifest: Any) -> tuple:
    """The event predicates a world offers: all of them where ``[polymarket]`` is enabled,
    none elsewhere. Fixed by the manifest for the world's life."""
    from factorylab.settlement.vocabulary import EVENT_VOCABULARY

    spec = getattr(manifest, "polymarket", None)
    return EVENT_VOCABULARY if spec is not None and spec.enabled else ()


def event_facts(rt: Any, predicate_id: str, token_id: str,
                snapshots: dict[str, dict[str, Any]] | None = None,
                due_tick: int | None = None) -> Any:
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

    ``snapshots`` holds the world's reads of each token for one settlement pass.
    Every claim on a token in that pass is answered from the same snapshot: the
    market is read once and the book at most once, so the judges of one question
    are graded against one state of the world, never against a resolution or a
    failed read that fell between their calls (``Settler`` counts them as one
    observation).

    The reads go through the surface's journal, so a replay settles on what was
    read. Only ids and numbers enter the facts; no market text does. They are never
    admitted, refused or deferred: the open-read limit (``open_limit``) makes the
    kernel's reads fit its reserve by construction, so every claim is graded on the
    world at its due pass. The token's market is known from the claim's sealing
    (``open_claim``): one GET by market id, and the book when a price claim needs it.
    A claim is admitted only once its token's market is found and cached, so a token
    with no cached market here is a kernel fault and raises ``KeyError``.
    """
    try:
        return _event_facts(rt, predicate_id, token_id,
                            {} if snapshots is None else snapshots)
    finally:
        # Whatever this call sent, it is charged here, never to the next seat read.
        sends = _sends(rt)
        if due_tick is not None:
            _read_for(rt, token_id, due_tick, sends)


def _read_for(rt: Any, token_id: str, due_tick: int, sends: list[int]) -> None:
    """Keep every open read that holds this settlement counting until one window (10 s
    of wall time) after the kernel's last request for it, so every request the kernel
    sends stays inside the window ``open_limit`` bounds. With no request in this call
    (the pass already read the token) the window runs from now, which is later."""
    surface = rt.polymarket
    suffix = f"|due:{token_id}:{due_tick}"
    keys = [key for key in surface.open_reads if key.endswith(suffix)]
    if not keys:
        return
    until = (max(sends) if sends else wall_now(rt)) + READ_WINDOW_NS
    for key in keys:
        held = surface.open_reads[key]
        surface.open_reads[key] = until if held is None else max(held, until)


def _event_facts(rt: Any, predicate_id: str, token_id: str,
                 snapshots: dict[str, dict[str, Any]]) -> Any:
    from factorylab.settlement.vocabulary import UNOBSERVABLE
    from factorylab.world.polymarket import payout

    surface = rt.polymarket

    def unavailable(read: str) -> Any:
        rt.ledger.append({"kind": "polymarket.event_unavailable", "token_id": token_id,
                          "predicate": predicate_id, "read": read, "ts": rt.clock.now_ns})
        return UNOBSERVABLE

    snapshot = snapshots.get(token_id)
    if snapshot is None:
        market_id = surface.token_markets[token_id]
        try:
            snapshot = {"market": surface.venue.market(market_id), "answered": True}
        except Exception:  # noqa: BLE001 - an unanswered read is an absent fact
            snapshot = {"market": None, "answered": False}
        snapshots[token_id] = snapshot
    if not snapshot["answered"]:
        return unavailable("market")
    market = snapshot["market"]
    facts: dict[str, Any] = {"listed": market is not None, "closed": None, "payout": None,
                             "midpoint": None}
    if market is not None:
        paid = payout(market, token_id)
        facts.update(closed=market["closed"], payout=None if paid is None else str(paid))
        if predicate_id == "event_price_above" and paid is None:
            if "midpoint" not in snapshot:
                # The midpoint of the book's best bid and ask, never the CLOB's
                # /midpoint, which answers 0.5 for an empty book (read 2026-09-23 on a
                # resolved market).
                try:
                    mid = None if market["closed"] else _decimal(
                        surface.venue.order_book(token_id, 1)["midpoint"])
                except Exception:  # noqa: BLE001
                    mid = None
                snapshot["midpoint"] = mid if mid is not None and 0 < mid < 1 else None
            if snapshot["midpoint"] is None:
                return unavailable("midpoint")
            facts["midpoint"] = str(snapshot["midpoint"])
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
    if tool_id in (ACCOUNT, OPEN_ORDERS):
        try:
            view = account_view(sanitized(surface.account(rt)))
        except Exception as exc:  # noqa: BLE001 - an unread pot states no amount
            view = {"status": "unavailable",
                    "reason": f"polymarket pot read failed: {type(exc).__name__}"}
        if tool_id == OPEN_ORDERS and view["status"] == "observed":
            view = {key: view[key] for key in ("status", "custody", "open_orders",
                                               "observed_at_ns") if key in view}
            # The world's own resting orders, never another signer's on the wallet.
            view["open_orders"] = _own_orders(surface, view["open_orders"])
        return {**view, "as_of_ns": rt.clock.now_ns}
    from factorylab.world.polymarket import SEAT_READ_REQUESTS

    method, call = _read_call(tool_id, args)
    # Admission, and the charge to the slot's share, are the same whether the tick
    # already holds the answer or not: a seat cannot tell the two apart, so another
    # seat's reads never reach it through its refusals or its share (AGENTS.md rule 4).
    refusal = _read_refusal(rt, action_id, SEAT_READ_REQUESTS)
    if refusal is not None:
        return _refused(rt, action_id, handle, tool_id, refusal)
    cached = _tick_answer(rt, method, call)
    if cached is not None:
        # No request: the tick already holds the answer to this very request.
        _charge_slot(rt, action_id, SEAT_READ_REQUESTS, [])
        result = _read_result(tool_id, cached)
        rt.ledger.append({"kind": "polymarket.read_answered", "handle": handle,
                          "assembly_id": action_id, "tool": tool_id, "ts": rt.clock.now_ns})
    else:
        try:
            result = _read_result(tool_id, getattr(surface.venue, method)(*call))
        except Exception:  # noqa: BLE001 - a read failure is a fact, not a crash
            return _refused(rt, action_id, handle, tool_id, "polymarket read unavailable")
        finally:
            _charge_slot(rt, action_id, SEAT_READ_REQUESTS, _sends(rt))
    protect(rt, result)
    result["as_of_ns"] = rt.clock.now_ns
    rt.ledger.append({"kind": "polymarket.read", "handle": handle, "assembly_id": action_id,
                      "tool": tool_id, "ts": rt.clock.now_ns})
    return result


# --- the read limit: Polymarket's published rate limits, shared by slot -------------------

READ_REFUSAL = "polymarket read share spent"
#: A seat's new open read beyond its own share of the kernel's open reads.
OPEN_LIMIT_REFUSAL = "polymarket open read share spent"
#: A Polymarket claim's lookup is a read, and a seat reads only through a slot.
NO_SLOT_REFUSAL = "a polymarket claim needs a venue read slot"
#: A claim or order on a token no market lists: refused, and looked up again next time.
NOT_LISTED_REFUSAL = "token not listed"
#: The window every Polymarket budget is counted over: any sliding 10 s of wall time
#: (``wall_now``), the window Polymarket counts its own published limits over.
READ_WINDOW_NS = 10_000_000_000
#: The most requests the kernel sends for one open read in any sliding 10 s: the
#: token's market by id, and its book when a price claim needs it.
KERNEL_READS_PER_OPEN = 2


def read_share(spec: Any, readers: int) -> int:
    """A reader slot's Polymarket requests per sliding 10 s: the budget less the
    kernel's reserve, over the reader slots. A manifest constant."""
    return (spec.read_requests_per_10s - spec.kernel_reserve_per_10s) // max(1, readers)


def open_limit(spec: Any) -> int:
    """N, the most open reads the kernel keeps: ``kernel_reserve_per_10s // 2``.

    An open read is a seat's claim to kernel reads: the settlement of its forecasts
    on one token due at one tick (``<registration>|due:<token>:<tick>``). Each seat
    registration holds its own, whether or not another seat holds the same token and
    tick, and at most ``seat_open_share`` of them (``open_claim``). One stays open
    while its forecasts are pending, and for one window (10 s of wall time) after the
    kernel's last request for it (``_read_for``). A seat holds open reads only through
    a venue read slot, and a freed slot is given again only once nothing its last
    holder held still counts (``reader_counts``).

    **Wall time.** Polymarket counts its limits in wall time, so every window here is
    wall time (``wall_now``): the live reader stamps each request with
    ``time.time_ns()`` just before it sends it, so a request counts from that stamp
    on, in flight or failed (``PolymarketReader.drain_sends``), the stamps
    are journaled, so a replay charges what the run charged, and a seat's admission
    and an open read's countdown are read against the same clock, never the world's.
    A live reader runs only on the wall clock (``arm``). A world's ticks can take any
    wall time, long or short, and the bound below never refers to them.

    **What reaches Polymarket.** A live world's public reads (the seats' and the
    kernel's settlement reads) are bounded here. A live world with orders
    (``orders = true``) also sends the pot's own requests (orders, cancels, lookups,
    fills, its account, held tokens' marks and a write's market read): they are
    counted by the pot's own budget, ``order_requests_per_10s`` in any sliding 10 s of
    wall time, each before it is sent (``LivePolymarket.budget``), never stamped as a
    public read, and the manifest holds ``read_requests_per_10s +
    order_requests_per_10s`` within the 300 Gamma ``/markets`` publishes. So the
    kernel's public requests are its settlement reads alone.

    **Open reads.** At any wall instant ``t`` at most ``max_readers ×
    seat_open_share <= N`` open reads count (admitted at or before ``t``, and still
    open or counting until after ``t``). A registration opens one only while fewer
    than its share count at that instant, and every other of its open reads counting
    at ``t`` was counting then too (still open, or its pass already run and its
    countdown final and past ``t``), so a registration holds at most its share. One
    registration a slot at a time: a slot goes to the next only once none of its
    last holder's open reads counts, so no instant holds both.

    **Kernel.** Every request the kernel sends is a settlement read (``event_facts``):
    the token's market by id, then its book for a price claim on an open market, at
    most ``KERNEL_READS_PER_OPEN`` = 2 for a token in a pass, sent in the one pass
    that settles that due tick (every forecast due at a tick is settled in the first
    pass at or after it, and none is ever deferred). Take any window ``(t - 10 s, t]``
    of wall time. A request stamped ``s`` in it leaves every open read that holds its
    settlement counting until at least ``s + 10 s > t``, and each was admitted before
    its pass, so every settlement the kernel read for in the window is held by an open
    read counting at ``t``: at most N of them, 2 requests each. The kernel sends at
    most ``2 N <= kernel_reserve_per_10s`` requests in any 10 s of wall time.

    **Seats.** A seat's read (and a claim's token lookup, 3 requests at its most) is
    admitted at wall time ``a`` only if the requests charged to its registration in
    ``(a - 10 s, a]`` leave room for it, and it is charged, at least what it sent, at
    the stamp of the last request it sent: not before ``a`` and not after the
    registration's next admission. Take the last of its reads with a request in a
    window ``(t - 10 s, t]``, admitted at ``a <= t``: every earlier read with a request
    in the window is charged in ``(t - 10 s, a]``, inside ``(a - 10 s, a]``, so all of
    them fit the share. A slot goes to the next registration only once its last
    holder's last charge is 10 s old. The seats send at most ``max_readers × share <=
    read_requests_per_10s - kernel_reserve_per_10s`` in any 10 s of wall time.

    **Worst case.** With the defaults (200 per 10 s, 100 of them the kernel's),
    N = 100 // 2 = 50; each of 16 slots holds up to 3 open reads and sends up to
    (200 - 100) // 16 = 6 requests per 10 s, one return's two claim lookups. In any
    10 s of wall time the kernel sends at most 2 × 50 = 100 and the seats at most
    16 × 6 = 96: 196 of the 300 Polymarket publishes for ``/markets``, its tightest
    limit. These counts are of stamps; Polymarket counts a request when it arrives,
    after its stamp. The 104 left cover only what the world cannot see: the difference
    between this host's clock and Polymarket's, and a request's time in flight from its
    stamp to its arrival. One live
    Polymarket world runs a host (``ip_lock``).
    """
    return spec.kernel_reserve_per_10s // KERNEL_READS_PER_OPEN


def seat_open_share(spec: Any, readers: int) -> int:
    """How many open reads one seat registration may hold: ``N // max_readers``, a
    manifest constant computed at load (a world whose share is under 1 is refused)."""
    return open_limit(spec) // max(1, readers)


def _expire(rt: Any) -> None:
    """Drop the open reads that no longer count (see ``open_limit``), in wall time."""
    surface = rt.polymarket
    now = None
    for key, until in list(surface.open_reads.items()):
        if until is None and int(key.rsplit(":", 1)[1]) < rt.ticks_consumed:
            # Its pass has run and read nothing for it (a claim whose sealing did not
            # complete): nothing will read for it, so it counts no more.
            now = wall_now(rt) if now is None else now
            surface.open_reads[key] = until = now
        if until is not None:
            now = wall_now(rt) if now is None else now
            if until <= now:
                del surface.open_reads[key]


def wall_now(rt: Any) -> int:
    """The instant every Polymarket window is counted at: the live reader's wall clock,
    read through the journal (so a replay reads what the run read), or the world's
    clock where the reads are answered offline, which sends nothing."""
    venue = _live(rt)
    return rt.clock.now_ns if venue is None else int(venue.wall_ns())


def _live(rt: Any) -> Any:
    """The journalled reader that sends to the network and stamps what it sends, or None."""
    venue = rt.polymarket.venue
    if venue.deterministic or not hasattr(venue.target, "drain_sends"):
        return None
    return venue


def _sends(rt: Any) -> list[int]:
    """The wall stamps of the requests sent since the last drain (none offline)."""
    venue = _live(rt)
    return [] if venue is None else [int(stamp) for stamp in venue.drain_sends()]


def prune_read_use(rt: Any) -> None:
    """Drop the registrations whose every read has left the sliding 10 s of wall time,
    so the books do not grow with the registrations the world has ever had."""
    uses = rt.polymarket_read_use
    if not uses:
        return
    since = wall_now(rt) - READ_WINDOW_NS
    for key in [key for key, rows in uses.items() if all(row[0] <= since for row in rows)]:
        del uses[key]


def reader_counts(rt: Any, reader: str | None) -> bool:
    """Whether anything of the registration ``reader`` still counts against a Polymarket
    window: an open read, or a read charged in the last 10 s of wall time. A slot it
    held is given to no one before neither does (``_assign_reader_slot``)."""
    if reader is None:
        return False
    if seat_open_reads(rt, reader) > 0:
        return True
    rows = rt.polymarket_read_use.get(reader)
    return bool(rows) and max(row[0] for row in rows) > wall_now(rt) - READ_WINDOW_NS


def seat_open_reads(rt: Any, reader: str) -> int:
    """How many open reads the registration ``reader`` holds that still count."""
    _expire(rt)
    prefix = f"{reader}|"
    return sum(1 for key in rt.polymarket.open_reads if key.startswith(prefix))


def _open(rt: Any, reader: str, kind: str) -> str | None:
    """Open one of ``reader``'s own open reads, or say why its share refuses it.

    A key the registration already holds, open or still counting down, is not
    counted again.
    """
    surface = rt.polymarket
    key = f"{reader}|{kind}"
    if key in surface.open_reads:
        surface.open_reads[key] = None
        return None
    share = seat_open_share(surface.spec, rt.m.exchange.max_readers)
    if seat_open_reads(rt, reader) >= share:
        return f"{OPEN_LIMIT_REFUSAL}: {share} open reads"
    surface.open_reads[key] = None
    return None


def open_claim(rt: Any, seat: str, token_id: str, due_tick: int) -> str | None:
    """Admit a Polymarket forecast on ``token_id`` due at ``due_tick``, or say why not.

    Guarantees: the claim counts against the sealing registration's own share of
    open reads, whether or not another seat holds the same token and tick, so its
    admission depends on its own open reads alone (AGENTS.md rules 4 and 5). The
    claim's lookup is the seat's own read, through its venue read slot, charged to
    its share at the lookup's most (3 requests) and always sent, whether or not the
    world already knew the token, so sealing behaves alike either way (an outage
    refuses every claim alike); the world's cache of a token's market serves only
    the kernel's settlement reads. A found market is cached; a token no market
    lists is refused (``token not listed``). A refusal before the lookup changes
    nothing.
    """
    from factorylab.world.polymarket import read_requests

    surface = rt.polymarket
    lookup = read_requests("market_of_token")
    if seat not in getattr(rt, "venue_readers", ()):
        return NO_SLOT_REFUSAL
    refused = _read_refusal(rt, seat, lookup)
    if refused is not None:
        return refused
    reader = rt._reader_id(seat)
    kind = f"due:{token_id}:{due_tick}"
    if f"{reader}|{kind}" not in surface.open_reads:
        share = seat_open_share(surface.spec, rt.m.exchange.max_readers)
        if seat_open_reads(rt, reader) >= share:
            return f"{OPEN_LIMIT_REFUSAL}: {share} open reads"
    try:
        listed = surface.venue.market_of_token(token_id)
    except Exception:  # noqa: BLE001 - the lookup is the seat's read; it failed
        return "polymarket read unavailable"
    finally:
        _charge_slot(rt, seat, lookup, _sends(rt))
    if listed is None:
        return NOT_LISTED_REFUSAL
    surface.token_markets[token_id] = str(listed["market_id"])
    return _open(rt, reader, kind)


def _read_call(tool_id: str, args: dict) -> tuple[str, tuple]:
    if tool_id == "polymarket.search":
        return "search_markets", (args["query"].strip(), args.get("limit", 5))
    if tool_id == "polymarket.market":
        return "market", (args["market_id"],)
    return "order_book", (args["token_id"], args.get("depth", 10))


def _read_result(tool_id: str, value: Any) -> dict[str, Any]:
    from copy import deepcopy

    key = {"polymarket.search": "markets", "polymarket.market": "market"}.get(tool_id, "book")
    return {key: deepcopy(value)}


def _read_used(rt: Any, seat: str) -> int:
    """The Polymarket requests charged to this registration's own reads in the sliding
    10 s of wall time ending now."""
    reader = rt._reader_id(seat)
    uses = rt.polymarket_read_use
    if not uses.get(reader):
        return 0
    since = wall_now(rt) - READ_WINDOW_NS
    kept = [row for row in uses[reader] if row[0] > since]
    if kept:
        uses[reader] = kept
    else:
        uses.pop(reader, None)
    return sum(requests for _ts, requests in kept)


def _charge_slot(rt: Any, seat: str, requests: int, sends: list[int]) -> None:
    """Charge a seat's read to its registration's share, after it was sent.

    Guarantees the charge is at least the requests the read sent (``requests``, the
    read's most, or more should the reader have sent more), counted at the wall stamp
    of the last of them: no earlier than any request it sent and no later than the
    seat's next admission, which is all ``open_limit``'s bound needs. A read that sent
    nothing (answered from the tick's answer) is charged the same, now.
    """
    at = max(sends) if sends else wall_now(rt)
    rt.polymarket_read_use.setdefault(rt._reader_id(seat), []).append(
        [at, max(requests, len(sends))])


def _read_refusal(rt: Any, seat: str, requests: int) -> str | None:
    """Refuse a seat read its own share cannot cover, before anything is sent.

    Guarantees: the share is a manifest constant and the registration's own reads
    alone count against it (AGENTS.md rules 4 and 5); with one registration a slot at
    a time the seats together stay within ``read_requests_per_10s -
    kernel_reserve_per_10s``, so the kernel's reserve is never a seat's.
    """
    share = read_share(rt.m.polymarket, rt.m.exchange.max_readers)
    used = _read_used(rt, seat)
    if used + requests > share:
        return (f"{READ_REFUSAL}: {used} of {share} requests in the last 10 s; "
                f"this read sends {requests}")
    return None


def _write_market(rt: Any, surface: PolymarketSurface, token_id: str) -> dict | None:
    """The market listing ``token_id`` for a write's checks, through the journal and the
    world's lookup of the token (one GET by market id once it is known; a token no
    market lists is not cached). The simulated venue sends Polymarket nothing; the live
    order venue sends these reads inside its own request budget
    (``[polymarket] order_requests_per_10s``), never a seat's share or the kernel's
    settlement reserve."""
    market_id = surface.token_markets.get(token_id)
    live = surface.live
    if market_id is None:
        listed = (surface.venue.write_market_of_token(token_id) if live
                  else surface.venue.market_of_token(token_id))
        if listed is not None:
            surface.token_markets[token_id] = str(listed["market_id"])
        return listed
    return surface.venue.write_market(market_id) if live else surface.venue.market(market_id)


def _tick_key(rt: Any) -> tuple | None:
    """The tick and the Polymarket writes that can move an answer (net of drains)."""
    writes = getattr(rt.ledger, "writes", None)
    if type(writes) is not dict:
        return None
    drains = getattr(rt, "_polymarket_drains", 0)
    return (rt.ticks_consumed, writes.get("polymarket", 0) - drains)


def _answer_key(method: str, call: tuple) -> str:
    return json.dumps([method, list(call)], sort_keys=True, default=str)


def observe_answer(rt: Any, method: str, args: tuple, kwargs: dict, result: Any) -> None:
    """Keep the tick's first answer to each Polymarket read, the kernel's own included.

    Fed the journal's own result, so a replay keeps what the recording kept.
    """
    from copy import deepcopy

    if method == "drain_events":
        rt._polymarket_drains = getattr(rt, "_polymarket_drains", 0) + 1
        return
    if method not in ("search_markets", "market", "order_book") or kwargs:
        return
    key = _tick_key(rt)
    if key is None:
        return
    cache = getattr(rt, "_polymarket_tick_reads", None)
    if cache is None or cache["key"] != key:
        cache = rt._polymarket_tick_reads = {"key": key, "answers": {}}
    cache["answers"].setdefault(_answer_key(method, args), deepcopy(result))


def _tick_answer(rt: Any, method: str, call: tuple) -> Any:
    cache = getattr(rt, "_polymarket_tick_reads", None)
    key = _tick_key(rt)
    if cache is None or key is None or cache["key"] != key:
        return None
    return cache["answers"].get(_answer_key(method, call))


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
    if args.get("side") != "buy":
        # Version 1 of the venue takes BUY orders only (the schema says so first): a
        # sale's cost basis would rest on an execution order the venue reveals only
        # piecemeal, so a position is held to its resolution.
        return BUY_ONLY_REFUSAL
    size, price = _decimal(args.get("size")), _decimal(args.get("price"))
    if size is None or price is None or size <= 0 or not 0 < price < 1:
        return "size must be positive and price strictly between 0 and 1"
    if surface.live and surface.opening is None:
        # Codex P1 on #177: the pot's opening is its baseline; an order before it is
        # read would put its own fill and fee inside the baseline, unbooked.
        return OPENING_REFUSAL
    window, count = surface.window_orders
    count = window_count if window_count is not None else (
        count if window == rt.window.index else 0)
    if count >= spec.max_orders_per_window:
        return "polymarket order window cap reached"
    try:
        market = _write_market(rt, surface, args["token_id"])
    except Exception:  # noqa: BLE001 - an unread market blocks new risk
        return "polymarket read unavailable"
    if market is None:
        return NOT_LISTED_REFUSAL
    # The market this write was weighed against is the one its order is built on.
    surface.checked[args["token_id"]] = market
    if not market["accepting_orders"]:
        return "market is not accepting orders"
    try:
        account = surface.account(rt)
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
    if surface.open_fees:
        # Sol P1 on #177: a fee its trade did not state has no bound valid at its
        # execution, so no principal is added until it is established.
        return FEE_OPEN_REFUSAL
    if surface.live and surface.drifting:
        return DRIFT_REFUSAL
    above = principal_excess(surface, account)
    if above is not None:
        return above
    fee = taker_fee(market, size, price)
    exposure, available = _open_exposure(account), Decimal(account["usdc_available"])
    if surface.live:
        # Astra P1 on #177: the venue's listings (open orders, positions, balance)
        # are separate reads that lag each other; the world's own durable records
        # (its intents not yet booked, its booked inventory) bound them from below.
        reserved, book = local_commitments(surface)
        exposure = max(exposure, reserved + book)
        available = min(available, Decimal(account["usdc"]) - reserved)
    if usd_to_micro(exposure + committed + notional,
                    rounding="ceil") > spec.max_open_micro:
        return "open exposure would exceed [polymarket] max_open_usd"
    if notional + fee + committed > available:
        return "order collateral exceeds the polymarket pot's available USDC"
    return None


#: A fill whose fee its trade did not state: its amount is not established.
FEE_OPEN_REFUSAL = "a polymarket fill's fee is not established"
#: Version 1 of the venue: BUY orders only; a position is held to its resolution.
BUY_ONLY_REFUSAL = "the polymarket venue takes BUY orders only"
PRINCIPAL_REFUSAL = "the polymarket pot holds more principal than [polymarket] principal_usd"
#: The live pot's opening, the baseline its reconciliation is measured from, is not read.
OPENING_REFUSAL = "the polymarket pot's opening is not yet read"


def held_at_cost(account: dict) -> Decimal:
    """The pot's value on its own books: USDC, plus every open token at cost, plus every
    resolved token not yet redeemed at its payout (``payout`` on the position), which
    is what it redeems for."""
    tokens = Decimal(0)
    for position in account["positions"]:
        size = Decimal(position["size"])
        paid = position.get("payout")
        tokens += size * (Decimal(paid) if paid is not None else Decimal(position["avg_px"]))
    return Decimal(account["usdc"]) + tokens


def principal_excess(surface: PolymarketSurface, account: dict) -> str | None:
    """Why the pot holds more principal than the manifest's cap, or None.

    The pot's principal is what it holds beyond what it has itself settled: its value
    on its own books less the P&L its fills and resolutions settled (``settled``), so
    what the world earned never counts against the cap and a deposit always does.
    Guarantees no new risk is taken on principal above ``principal_micro`` (essay
    II.II.b, the hard cast); a cancellation and a sell are never refused by it.
    """
    cap = getattr(surface.spec, "principal_micro", None)
    if cap is None:
        return None
    held = held_at_cost(account)
    if surface.live:
        # A token the positions listing does not show yet (index lag, truncation) is
        # still the world's: each token counts at the larger of the two valuations.
        held = Decimal(account["usdc"]) + _live_tokens_value(surface, account)
    principal = held - surface.settled
    if usd_to_micro(principal, rounding="ceil") > cap:
        return PRINCIPAL_REFUSAL
    return None


#: The pot does not agree with its custodian: money left it that its books do not explain.
DRIFT_REFUSAL = "the polymarket pot does not reconcile with its custodian"


def _cancelled(surface: PolymarketSurface) -> dict[str, Decimal | None]:
    """Each order an acknowledged cancel proved terminal: order id -> the quantity the
    cancel's read-back says it matched (None where it did not say)."""
    cancelled: dict[str, Decimal | None] = {}
    for intent in surface.intents.values():
        if (intent["operation"] == "polymarket.cancel"
                and intent["result"].get("status") == "cancelled"):
            matched = intent["result"].get("filled_size")
            cancelled[str(intent["args"]["order_id"])] = (
                None if matched is None else Decimal(str(matched)))
    return cancelled


def local_commitments(surface: PolymarketSurface) -> tuple[Decimal, Decimal]:
    """(USDC the world's buys may still take, the world's booked inventory at cost).

    From the world's own durable records, never a venue listing (Astra P1 on #177): a
    buy placement not rejected reserves its price, and the taker fee its schedule could
    charge, on every quantity it may still fill or has matched that is not yet booked
    from a CONFIRMED trade (its size while it rests or is unanswered, its matched size
    once terminal); the booked inventory of each unresolved token is held at its
    average cost (``LivePolymarket.poll``'s book). An order proven terminal reserves
    only what it matched and is not yet booked: an acknowledged cancel (its matched
    size as the cancel's read-back states it), or the poll's terminal read-back, which
    requires every matched quantity booked (Codex P1 on #177: a cancelled buy's
    notional stayed reserved forever).
    """
    cancelled = _cancelled(surface)
    finished = set(surface.cursor.get("terminal", ()))
    reserved = Decimal(0)
    for intent in surface.intents.values():
        args = intent["args"]
        if intent["operation"] != "polymarket.place_limit" or args.get("side") != "buy":
            continue
        result = intent["result"]
        if result.get("status") == "rejected" or intent.get("terminal"):
            continue
        order_id = intent.get("order_hash") or result.get("order_id")
        booked = Decimal(surface.filled.get(str(order_id), "0"))
        quantity = (Decimal(str(result.get("filled_size") or "0"))
                    if result.get("status") in ("filled", "cancelled")
                    else Decimal(str(args["size"])))
        if str(order_id) in finished:
            quantity = booked
        elif cancelled.get(str(order_id)) is not None:
            quantity = cancelled[str(order_id)]
        if result.get("status") in ("filled", "cancelled") or cancelled.get(
                str(order_id)) is not None:
            # A terminal order's legs that FAILED never settle (Sol P2 on #177).
            quantity -= Decimal(str(surface.cursor.get("failed", {}).get(str(order_id), "0")))
        price = Decimal(str(args["price"]))
        identity = intent.get("order_identity") or {}
        rate = Decimal(str(identity.get("fee_rate") or "0"))
        remaining = max(Decimal(0), quantity - booked)
        reserved += remaining * price * (1 + rate * (1 - price))
    resolved = surface.cursor.get("resolved", {})
    book = sum((Decimal(size) * Decimal(avg)
                for token, (size, avg) in surface.cursor.get("book", {}).items()
                if token not in resolved), Decimal(0))
    return reserved, book


def _live_tokens_value(surface: PolymarketSurface, account: dict) -> Decimal:
    """Each token at the larger of what the positions listing and the world's own book
    say of it: a lagging or truncated listing never makes the pot look smaller."""
    listed: dict[str, Decimal] = {}
    for p in account["positions"]:
        paid = p.get("payout")
        listed[p["token_id"]] = Decimal(p["size"]) * (
            Decimal(paid) if paid is not None else Decimal(p["avg_px"]))
    resolved = surface.cursor.get("resolved", {})
    for token, (size, avg) in surface.cursor.get("book", {}).items():
        value = Decimal(size) * (Decimal(resolved[token]) if token in resolved
                                 else Decimal(avg))
        listed[token] = max(listed.get(token, Decimal(0)), value)
    return sum(listed.values(), Decimal(0))


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
                market = _write_market(rt, surface, args["token_id"]) or {}
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
    if surface.live and tool_id == "polymarket.place_limit":
        # The CLOB takes no client id: an order's identity is its EIP-712 hash, fixed by
        # its fields and a salt derived from the client id. It is durable in the intent
        # before anything is sent, so a lost answer is looked up by it, never resent.
        try:
            identity = surface.venue.order_identity(
                client_id=client_id, token_id=args["token_id"], is_buy=args["side"] == "buy",
                size=Decimal(str(args["size"])), price=Decimal(str(args["price"])),
                market=_order_facts(surface.checked.get(args["token_id"])))
        except Exception as exc:  # noqa: BLE001 - an order that cannot be built is refused
            rt.venue_attempts[handle] = str(exc)[:200]
            return rt._refuse_order(handle, f"polymarket order not built: {str(exc)[:200]}",
                                    kind="polymarket.refused")
        intent["order_hash"] = identity["order_hash"]
        intent["order_identity"] = identity
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
    _drain(rt, surface)
    return answer


def _drain(rt: Any, surface: PolymarketSurface) -> None:
    """Settle what the simulated venue did during a write. The live venue's events come
    only through ``poll`` (its ``drain_events`` answers nothing), so it is not called:
    a crash inside it would leave the journal a call no resume completes (Codex P1)."""
    if not surface.live:
        settle(rt, surface.venue.drain_events())


def _recover(rt: Any, surface: PolymarketSurface, client_id: str) -> dict[str, Any]:
    """Ask the venue what it holds under an identity; never resubmit it.

    A live order is looked up by the order hash its intent recorded before submission
    (a cancel by the hash of the order it cancels); the simulated venue by client id.
    """
    intent = surface.intents[client_id]
    try:
        if surface.live:
            target = (intent.get("order_hash") if intent["operation"] == "polymarket.place_limit"
                      else intent["args"]["order_id"])
            result = surface.venue.lookup(client_id, order_id=target,
                                          cancel=intent["operation"] == "polymarket.cancel")
        else:
            result = surface.venue.lookup(client_id)
    except Exception as exc:  # noqa: BLE001
        result = {"status": "uncertain", "error": f"recovery exception: {type(exc).__name__}"}
    answer = _record(rt, surface, client_id, result)
    _drain(rt, surface)
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
    elif result.get("order_id") is not None and result["status"] != "rejected":
        # A rejected placement names its hash (the live venue's answer always does) but
        # is no order of this world's: nothing is looked up or read for it (Codex P2).
        surface.order_ids[str(result["order_id"])] = client_id
        attributed = result
        if result["status"] == "cancelled" and Decimal(str(result["filled_size"])) > 0:
            attributed = {**result, "status": "filled"}
        rt.consequences.order_result(intent["handle"], attributed,
                                     {"size": str(intent["args"]["size"])}, rt.n,
                                     coin=coin_of(intent["args"]["token_id"]))
    before = rt.consequences.table
    rt._replay_deferred(rt.consequences.order_acknowledged(client_id), before)
    return dict(result)


# --- time and settlement --------------------------------------------------------------------

def tick(rt: Any) -> None:
    """Reconcile unanswered writes, then read what the venue did and settle it.

    Guarantees an uncertain intent is asked about at most ``UNCERTAIN_ORDER_POLLS``
    times, each answer ledgered once, and is then released as unknown exactly as
    a Hyperliquid intent is (``VenueMixin._give_up_on_order``). The simulated venue
    moves on the world's clock; the live venue's confirmed fills and resolutions are
    read from the cursor the surface carries (``LivePolymarket.poll``), and the event
    stream counts as read through now only when that read was complete.
    """
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    surface = rt.polymarket
    # The tick's account is read afresh: what the venue did since is in it.
    surface._account_memo = None
    if not surface.writes:
        if surface.venue.deterministic:
            # A simulated read-only venue (``simulate_reads``) still moves and resolves,
            # so what an event forecast settles on changes with the world's clock. With
            # no writes it holds nothing, so its events are empty.
            settle(rt, surface.venue.advance(rt.clock.now_ns))
            surface.through["events"] = rt.clock.now_ns
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
    if surface.live and surface.opening is None:
        # The baseline comes first: no fill is booked before the pot's opening is read
        # (Codex P1 on #177), and no order is taken before it (``refusal``).
        reconcile(rt)
        if surface.opening is None:
            rt.ledger.append({"kind": "polymarket.poll_deferred",
                              "reason": "opening not read", "ts": rt.clock.now_ns})
            return
    if surface.live:
        _discover(rt, surface)
        _settle_cancels(rt, surface)
        # The live venue's fills and resolutions since the cursor, read through the
        # journal with the cursor carried in and out: a replay reads what the run read
        # and resumes from the cursor its checkpoint holds.
        try:
            answer = surface.venue.poll(now_ns=rt.clock.now_ns, cursor=surface.cursor,
                                        orders=_live_orders(surface))
        except Exception as exc:  # noqa: BLE001 - an unread stream holds what waits on it
            rt.ledger.append({"kind": "polymarket.poll_unavailable",
                              "reason": type(exc).__name__, "ts": rt.clock.now_ns})
        else:
            surface.cursor = answer["cursor"]
            settle(rt, answer["events"])
            if answer.get("complete"):
                # Every fill the venue confirmed through the cursor was handed over.
                surface.through["events"] = rt.clock.now_ns
    else:
        settle(rt, surface.venue.advance(rt.clock.now_ns))
        # Every event the venue held through now was handed over and accounted.
        surface.through["events"] = rt.clock.now_ns
    confirm_terminal(rt)
    mark(rt)
    reconcile(rt)


def _order_facts(market: dict | None) -> dict[str, Any]:
    """What an order is built on from its market: tick, neg-risk flag and fee schedule."""
    if market is None:
        raise ValueError("no market was read for this order")
    return {"tick_size": market.get("tick_size"), "neg_risk": bool(market.get("neg_risk")),
            "fees": dict(market.get("fees") or {})}


#: Released placements whose hash is looked up a tick, in turn (``_discover``).
DISCOVERIES_PER_TICK = 2


def _undiscovered(surface: PolymarketSurface) -> list[str]:
    """Live placements released unresolved whose order the venue has not yet answered
    for: signed and possibly sent, so possibly resting, filling or filled."""
    return sorted(client_id for client_id, intent in surface.intents.items()
                  if intent.get("unresolved") and intent.get("order_hash")
                  and intent["operation"] == "polymarket.place_limit"
                  and intent["order_hash"] not in surface.order_ids
                  and not intent.get("terminal"))


def _discover(rt: Any, surface: PolymarketSurface) -> None:
    """Keep looking up every released live placement by its hash until the venue answers.

    Codex P1 on #177: a placement that reached the venue while its answer and every
    scheduled lookup failed is released unresolved (its decision's consequence settles
    censored), but the order may still rest and fill. Its hash is durable in its intent,
    so it is looked up, ``DISCOVERIES_PER_TICK`` a tick in turn, for the world's life or
    until the venue answers; an answer binds the order to its decision
    (``_record``), whose fills are then its owner's, late (``LotTable.fill``). Its fills
    are read by the poll meanwhile (``_live_orders``). Nothing is ever resent.
    """
    waiting = _undiscovered(surface)
    if not waiting:
        return
    turn = int(surface.cursor.get("discover", 0))
    for step in range(min(DISCOVERIES_PER_TICK, len(waiting))):
        client_id = waiting[(turn + step) % len(waiting)]
        intent = surface.intents[client_id]
        try:
            answer = surface.venue.lookup(client_id, order_id=intent["order_hash"])
        except Exception:  # noqa: BLE001 - an unanswered lookup proves nothing
            continue
        if answer.get("status") in ("filled", "resting", "cancelled"):
            _record(rt, surface, client_id, answer)
        elif answer.get("status") == "rejected":
            surface.intents[client_id] = {**intent, "terminal": True}
            rt.ledger.append({"kind": "polymarket.discovered", "client_id": client_id,
                              "handle": intent["handle"], "result": dict(answer)})
    surface.cursor = {**surface.cursor, "discover": turn + 1}


def _settle_cancels(rt: Any, surface: PolymarketSurface) -> None:
    """Settle each cancel released unresolved from the state of the order it cancels.

    Codex P2 on #177: a cancel whose answer and every scheduled lookup failed is
    released unresolved, and its order kept its unfilled notional reserved and its
    decision's account pinned. The order's own status is read by its hash,
    ``DISCOVERIES_PER_TICK`` a tick in turn, until the venue answers: cancelled, the
    order's unfilled liability is released (``ReturnConsequences.cancel``) and its
    placement records what it matched; filled, the cancel came too late; resting, it
    did not take. The cancel is then settled; nothing is ever resent.
    """
    waiting = sorted(client_id for client_id, intent in surface.intents.items()
                     if intent["operation"] == "polymarket.cancel"
                     and intent.get("unresolved") and not intent.get("settled")
                     and str(intent["args"]["order_id"]) in surface.order_ids)
    if not waiting:
        return
    turn = int(surface.cursor.get("cancel_turn", 0))
    for step in range(min(DISCOVERIES_PER_TICK, len(waiting))):
        client_id = waiting[(turn + step) % len(waiting)]
        order_id = str(surface.intents[client_id]["args"]["order_id"])
        try:
            answer = surface.venue.lookup(client_id, order_id=order_id)
        except Exception:  # noqa: BLE001 - an unanswered lookup proves nothing
            continue
        status = answer.get("status")
        if status not in ("cancelled", "filled", "resting"):
            continue
        placement_id = surface.order_ids[order_id]
        placement = surface.intents[placement_id]
        if status == "cancelled":
            rt.consequences.cancel(order_id, rt.n)
        if status in ("cancelled", "filled") and answer.get("filled_size") is not None:
            surface.intents[placement_id] = {**placement, "result": {
                **placement["result"], "status": status,
                "filled_size": str(answer["filled_size"])}}
        surface.intents[client_id] = {**surface.intents[client_id], "settled": True}
        rt.ledger.append({"kind": "polymarket.cancel_settled", "client_id": client_id,
                          "order_id": order_id, "result": dict(answer),
                          "ts": rt.clock.now_ns})
    surface.cursor = {**surface.cursor, "cancel_turn": turn + 1}


def _live_orders(surface: PolymarketSurface) -> dict[str, dict[str, str]]:
    """This world's live orders the fill poll reads for: order hash -> token, side, size,
    price, market and fee schedule, each as its intent recorded them (an order is booked
    only against its own intent, never against the venue's word of what it was). An
    order whose fills are all booked and whose token has resolved reads nothing more."""
    orders = {}
    resolved = surface.cursor.get("resolved", {})
    cancelled = _cancelled(surface)
    known = dict(surface.order_ids)
    for client_id in _undiscovered(surface):
        # A released placement's fills are read too: a confirmed trade is the venue's
        # word that its order exists (``_settle_fill`` binds it then).
        known[surface.intents[client_id]["order_hash"]] = client_id
    for order_id, client_id in known.items():
        intent = surface.intents[client_id]
        if intent["result"].get("status") == "rejected":
            continue  # never an order the venue holds (Codex P2 on #177)
        args, identity = intent["args"], intent.get("order_identity") or {}
        token = str(args["token_id"])
        if token in resolved and order_id in surface.cursor.get("terminal", ()):
            continue
        result = intent["result"]
        matched = Decimal(str(result.get("filled_size") or "0"))
        status = result.get("status")
        if cancelled.get(order_id) is not None:
            status, matched = "cancelled", cancelled[order_id]
        # Whether the order can still change what the pot holds: it rests or is
        # unanswered, or it matched more than is booked (Codex P2 on #177).
        live = (status in ("resting", "uncertain")
                or matched > Decimal(surface.filled.get(order_id, "0")) + Decimal(str(
                    surface.cursor.get("failed", {}).get(order_id, "0"))))
        orders[order_id] = {"token_id": token, "side": str(args["side"]), "open": live,
                            "size": str(args["size"]), "price": str(args["price"]),
                            "market_id": surface.token_markets.get(token),
                            # The order's own signed timestamp (ms): no fill of it can
                            # precede it, so the fill read starts no later.
                            "timestamp": str((identity.get("order") or {}).get(
                                "timestamp", "0")),
                            "fee_rate": str(identity.get("fee_rate", "0")),
                            "fee_exponent": str(identity.get("fee_exponent", "1"))}
    return orders


def confirm_terminal(rt: Any) -> None:
    """Read back, from the venue's own order status, every Polymarket order that may be over.

    Wave 17b: the rule ``VenueMixin._confirm_terminal_orders`` keeps for Hyperliquid.
    Guarantees each order this world placed that the consequence book holds with no
    unfilled liability and unconfirmed is looked up under its client id until the
    venue answers ``filled``, ``cancelled`` or ``rejected`` and states its filled
    size, and that answer and size are recorded; anything else, an answer without
    the filled size included, leaves its account pinned until the next tick's read.
    """
    surface = rt.polymarket
    for order in rt.consequences.table.orders:
        client_id = surface.order_ids.get(order.order_id)
        if client_id is None or order.remaining or order.confirmed is not None:
            continue
        try:
            answer = (surface.venue.lookup(client_id, order_id=order.order_id)
                      if surface.live else surface.venue.lookup(client_id))
        except Exception:  # noqa: BLE001 - an unanswered read confirms nothing
            continue
        if (answer.get("status") in ("filled", "cancelled", "rejected")
                and answer.get("filled_size") is not None):
            rt.consequences.confirm_terminal(order.order_id, answer["status"],
                                             str(answer["filled_size"]), rt.n)


def mark(rt: Any) -> None:
    """Give the consequence book this tick's midpoint of every token a lot holds.

    Guarantees a mark is the market's own price strictly between 0 and 1: the
    midpoint of the book's best bid and ask, never the CLOB's ``/midpoint``, which
    answers 0.5 for an empty book (read 2026-09-23 on a resolved market). A token
    with no two-sided book, or whose book is unreadable, loses its mark rather than
    keeping a stale one or taking an invented one, so its lot is not marked and its
    decision falls back as any unobserved consequence does. It runs only where the
    pot holds positions (``tick``): the simulated venue sends Polymarket nothing, and
    the live order venue reads each mark inside the pot's own request budget
    (``LivePolymarket.mark_book``), never a seat's share or the settlement reserve.
    """
    surface = rt.polymarket
    # Every token a lot holds, and every token an open outcome held at any recorded
    # fact (its lot may since have been redeemed): each needs its mark.
    for coin in sorted({coin for coin, market in rt.consequences.graded_instruments()
                        if market == "event"}):
        try:
            # The live order venue reads a held token's book inside its own request
            # budget, never a seat's share or the settlement reserve.
            book = (surface.venue.mark_book(coin.removeprefix("PM:")) if surface.live
                    else surface.venue.order_book(coin.removeprefix("PM:"), 1))
        except Exception:  # noqa: BLE001 - an unread book advances nothing
            book = None
        if book is not None:
            # A successful read is the token's book stream read through now, whatever
            # it states (Codex on #152): an empty or one-sided book is read, and states
            # no price; only an unanswered read holds the lots on the token.
            surface.through[coin] = rt.clock.now_ns
        try:
            mid = _decimal(book["midpoint"]) if book is not None else None
        except Exception:  # noqa: BLE001 - an unreadable price is an absent price
            mid = None
        if mid is not None and 0 < mid < 1:
            rt.consequences.observe("MarketMid", {"coin": coin, "mid": str(mid),
                                                  "ts_ns": rt.clock.now_ns}, rt.n)
        elif rt.consequences.mids.pop(coin, None) is not None:
            rt.ledger.append({"kind": "polymarket.mark_unavailable", "coin": coin,
                              "ts": rt.clock.now_ns})


def reconcile(rt: Any) -> dict[str, Any] | None:
    """Check the pot against its own books: opening + settled == USDC + tokens at cost.

    Every fill and resolution the pot settled is ledgered exactly; the venue's
    account is the other side. Guarantees a disagreement larger than one
    micro-USD is ledgered as ``polymarket.drift``: like with like, the pot's books
    against its custodian; the first observation is ledgered as the baseline.
    """
    surface = rt.polymarket
    try:
        account = surface.account(rt)
    except Exception as exc:  # noqa: BLE001 - an unreadable pot is not reconciled
        rt.ledger.append({"kind": "polymarket.reconcile_unavailable",
                          "reason": type(exc).__name__, "ts": rt.clock.now_ns})
        return None
    # The custodian's word, never the larger of it and the world's own book (Sol P0 on
    # #177: taking the larger hid a cost basis booked too high, and its profit). A
    # listing that lags shows as drift until it catches up, which holds new risk.
    held = held_at_cost(account)
    if surface.opening is None:
        surface.opening = held - surface.settled
        rt.ledger.append({"kind": "polymarket.opening", "usdc": str(surface.opening),
                          "ts": rt.clock.now_ns})
    drift = held - (surface.opening + surface.settled)
    result = {"opening": str(surface.opening), "settled": str(surface.settled),
              "held_at_cost": str(held), "drift": str(drift)}
    if abs(drift) > Decimal("0.000001"):
        rt.ledger.append({"kind": "polymarket.drift", **result, "ts": rt.clock.now_ns})
    # Astra P1 on #177: money gone that the books do not explain leaves the pot's
    # reconciliation unknown, and new exposure waits on it. No allowance is made (Sol
    # P1: a blanket one hid real losses); a drift is never booked as a fee (Codex P1).
    surface.drifting = drift < Decimal("-0.000001")
    above = principal_excess(surface, account)
    if above is not None:
        result["principal_exceeded"] = True
        rt.ledger.append({"kind": "polymarket.principal_exceeded", **result,
                          "principal_micro": surface.spec.principal_micro,
                          "ts": rt.clock.now_ns})
    return result


#: A resolved token's book stream watermark: no book fact can follow a resolution.
RESOLVED_BOOK = 2**62


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
    surface = rt.polymarket
    if order_id not in surface.order_ids:
        found = next((cid for cid in _undiscovered(surface)
                      if surface.intents[cid]["order_hash"] == order_id), None)
        if found is not None:
            # A confirmed trade of a released placement: the venue accepted the order.
            # It is bound to its decision before its fill is booked.
            _record(rt, surface, found, {"order_id": order_id, "status": "resting",
                                         "filled_size": "0", "avg_px": None, "error": None,
                                         "evidence": "confirmed trade"})
    payload = {"order_id": order_id, "coin": coin_of(event["token_id"]),
               "is_buy": event["is_buy"], "size": event["size"], "px": event["px"],
               "fee_usd": event["fee_usd"], "realized_usd": event["realized_usd"],
               "liquidation": False, "market": "event", "inventory_size": event["size"],
               # The fill's own venue time, kept while it is held or drained late.
               **({"ts_ns": int(event["ts_ns"])} if event.get("ts_ns") is not None else {})}
    rt.ledger.append({"kind": "polymarket.fill", **payload, "market_id": event["market_id"],
                      "ts": rt.clock.now_ns})
    surface = rt.polymarket
    rt.polymarket.settled += Decimal(event["realized_usd"]) - Decimal(event["fee_usd"])
    delta = (usd_to_micro(event["realized_usd"], rounding="nearest")
             - usd_to_micro(event["fee_usd"], rounding="nearest"))
    owner_handle = rt._order_owner(order_id)
    # Per-order fill completeness: an order's fills never sum past what it ordered.
    # A fill that would is the venue's report disagreeing with the order it answers:
    # its money is the pot's (it moved), owned by no decision, and the consequence
    # book quarantines it with its evidence (``ReturnConsequences.observe``).
    client_id = surface.order_ids.get(order_id)
    booked = Decimal(surface.filled.get(order_id, "0")) + Decimal(str(event["size"]))
    if client_id is not None and booked > Decimal(str(surface.intents[client_id]["args"]["size"])):
        rt.ledger.append({"kind": "polymarket.fill_quarantined", "order_id": order_id,
                          "size": str(event["size"]),
                          "ordered": str(surface.intents[client_id]["args"]["size"]),
                          "booked": surface.filled.get(order_id, "0"), "ts": rt.clock.now_ns})
        owner_handle = None
    else:
        surface.filled[order_id] = str(booked)
    if event.get("fee_unresolved"):
        # Astra P0 and Codex P1 on #177: the execution did not state its fee (get-trades
        # lists fee_rate_bps as optional). Nothing is debited, from a schedule or from
        # the balance (a drift cannot tell a fee from a deposit or a withdrawal): the fee
        # stays an unresolved item, and no buy is taken while one is open (``refusal``).
        item = {"order_id": order_id, "handle": owner_handle, "size": str(event["size"]),
                "px": str(event["px"])}
        surface.open_fees.append(item)
        rt.ledger.append({"kind": "polymarket.fee_unresolved", **item, "ts": rt.clock.now_ns})
    try:
        rt.consequences.observe("Fill", payload, rt.n)
    except ValueError as exc:
        # Codex P1 on #177: a fill the consequence book cannot hold (a sale past the
        # inventory it holds, say) never raises out of the tick. Its money is the
        # pot's, owned by no decision, and it is quarantined with its evidence.
        rt.ledger.append({"kind": "polymarket.fill_quarantined", "order_id": order_id,
                          "size": str(event["size"]), "reason": str(exc)[:200],
                          "ts": rt.clock.now_ns})
        owner_handle = None
    _book_pot(rt, delta, f"fill:{order_id}", "exchange_pnl", owner_handle)
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
    # A lot a released decision's order opened realises into the pot too (wave 17b):
    # ``realized_by_handle`` counts retained and released handles alike.
    before = rt.consequences.table.realized_by_handle()
    realized = rt.consequences.redeem(coin_of(token), event["payout"], rt.n, facts,
                                      at_ns=event.get("ts_ns"))
    # A resolved token's book states nothing more, ever: its stream is complete.
    rt.polymarket.through[coin_of(token)] = RESOLVED_BOOK
    after = rt.consequences.table.realized_by_handle()
    credit_realized(rt, {handle: total - before.get(handle, 0)
                         for handle, total in after.items()
                         if total != before.get(handle, 0)})
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


def custody_books(rt: Any) -> dict[str, int]:
    """The pot's own books: ``claimed + unattributed == booked``, always.

    ``booked`` is every settlement the pot ledgered (``venue.settled`` with ``custody =
    "polymarket"``: fills net of fees, and resolutions), ``claimed`` what the owners'
    claims hold of it (``claim_share``), and ``unattributed`` what no return owns: a
    resolution several decisions shared, a quarantined fill, a claim a retired seat
    could not take. Like ``BudgetBook.venue_unattributed``, beside it and never in it.
    """
    surface = rt.polymarket
    claimed = sum(surface.claims.values())
    return {"booked_micro": surface.booked, "claimed_micro": claimed,
            "unattributed_micro": surface.booked - claimed,
            # Fills whose fee their trade did not state.
            "unresolved_fees": len(surface.open_fees)}


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
        account = sanitized(surface.account(rt))
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
                                    rounding="floor"),
         # A resolved token not yet redeemed is worth its payout, what it redeems for.
         **({"payout": p["payout"],
             "value_micro": usd_to_micro(Decimal(p["size"]) * Decimal(p["payout"]),
                                         rounding="floor")}
            if p.get("payout") is not None else {})}
        for p in account["positions"]]
    usdc = usd_to_micro(account["usdc"], rounding="floor") if observed else None
    value = None if usdc is None else usdc + sum(t.get("value_micro", t["cost_micro"])
                                                 for t in tokens)
    pots["polymarket"] = value
    pots["polymarket_usdc"] = usdc
    pots["polymarket_tokens"] = tokens
    pots["polymarket_claims"] = dict(sorted(rt.polymarket.claims.items()))
    if value is None:
        pots["complete"], pots["total_micro"] = False, None
    elif pots.get("complete"):
        pots["total_micro"] += value
    return pots


def _own_orders(surface: PolymarketSurface, orders: list[dict]) -> list[dict]:
    """The orders of a pot read that this world placed (its ``order_ids``). The live
    wallet is shared with whatever else signs for it; the simulated pot holds only the
    world's own."""
    if not surface.live:
        return list(orders)
    own = _own_hashes(surface)
    return [o for o in orders if str(o["order_id"]) in own]


def _own_hashes(surface: PolymarketSurface) -> set[str]:
    """Every order hash this world may own: those the venue acknowledged, and those of
    its placements only their durable intents know, uncertain or released unresolved
    (Sol P1 on #177), but never a rejected one."""
    own = set(surface.order_ids)
    for intent in surface.intents.values():
        if (intent["operation"] == "polymarket.place_limit" and intent.get("order_hash")
                and intent["result"].get("status") != "rejected"
                and not intent.get("terminal")):
            own.add(str(intent["order_hash"]))
    return own


def _unsettled(surface: PolymarketSurface) -> tuple[list[dict], list[str]]:
    """What the world may hold that no read shows yet: each of its orders that matched
    more than is booked from a CONFIRMED trade (Sol P1 on #177: matched quantity is in
    neither the open orders nor the positions), and each placement still unanswered."""
    cancelled = _cancelled(surface)
    failed = surface.cursor.get("failed", {})
    matched_rows, unanswered = [], []
    for _client_id, intent in sorted(surface.intents.items()):
        if intent["operation"] != "polymarket.place_limit" or not intent.get("order_hash"):
            continue
        order_id, result = str(intent["order_hash"]), intent["result"]
        if result.get("status") == "rejected" or intent.get("terminal"):
            continue
        if result.get("status") == "uncertain":
            unanswered.append(order_id)
            continue
        matched = (cancelled[order_id] if cancelled.get(order_id) is not None
                   else Decimal(str(result.get("filled_size") or "0")))
        booked = Decimal(surface.filled.get(order_id, "0")) + Decimal(
            str(failed.get(order_id, "0")))
        if matched > booked:
            matched_rows.append({"order_id": order_id, "size": str(matched),
                                 "booked": str(booked)})
    return matched_rows, unanswered


def _terminal(surface: PolymarketSurface, intent: dict) -> bool:
    """Whether a placement is proven over: rejected, filled or cancelled by the venue's
    word, cancelled by an acknowledged cancel, or read terminal by the poll."""
    order_id = str(intent.get("order_hash") or intent["result"].get("order_id"))
    return (intent["result"].get("status") in ("rejected", "filled", "cancelled")
            or bool(intent.get("terminal"))
            or _cancelled(surface).get(order_id) is not None
            or order_id in surface.cursor.get("terminal", ()))


def _live_targets(surface: PolymarketSurface) -> list[str]:
    """Every order hash of this world's that may still rest: each durable placement not
    proven over, from its intent alone, never from a listing (Sol P1 on #177)."""
    return sorted({str(intent["order_hash"]) for intent in surface.intents.values()
                   if intent["operation"] == "polymarket.place_limit"
                   and intent.get("order_hash") and not _terminal(surface, intent)})


def _live_residual(surface: PolymarketSurface, account: dict | None) -> list[dict]:
    """What the world holds, bounded below by its own confirmed book (Sol P1 on #177):
    an unresolved token cannot leave the pot but by resolution (the venue takes BUY
    orders only), so a listing that omits it proves nothing. A resolved token not yet
    redeemed is what the world held when paid, as far as the listing, when read, still
    shows it (a redemption takes it out)."""
    listed = {} if account is None else {p["token_id"]: p for p in sanitized(account)[
        "positions"]}
    rows = []
    resolved = surface.cursor.get("resolved", {})
    redeemable = surface.cursor.get("redeemable", {})
    for token, (size, avg) in sorted(surface.cursor.get("book", {}).items()):
        own = Decimal(size)
        if token in resolved:
            own = Decimal(redeemable.get(token, "0"))
            if account is not None:
                own = min(own, Decimal(listed[token]["size"]) if token in listed
                          else Decimal(0))
        if own > 0:
            rows.append({"token_id": token, "market_id": surface.token_markets.get(token),
                         "outcome": (listed.get(token) or {}).get("outcome"),
                         "size": str(own), "avg_px": str(avg)})
    return rows


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
    unknown = False
    try:
        # Only this world's orders (Codex P1 on #177), and every one of them: on the live
        # venue the targets are its durable placements that may still rest, whatever a
        # listing says (Sol P1: an omission from a listing never proves an order gone).
        targets = (_live_targets(surface) if surface.live else
                   [str(o["order_id"]) for o in surface.account(rt)["open_orders"]])
    except Exception as exc:  # noqa: BLE001 - nothing may raise into a kill
        report["error"], targets, unknown = type(exc).__name__, [], True
    for order_id in targets:
        # Each cancel on its own (Sol P1 on #177): one failure never stops the next.
        client_id = f"kill:{order_id}"
        try:
            rt.ledger.append({"kind": "polymarket.wind_down", "op": "cancel",
                              "client_id": client_id, "order_id": order_id})
            # The kernel's own cancellation is an intent like any other, durable above,
            # before the live venue will sign it (``LivePolymarket.intent_of``).
            if surface.live:
                surface.intents.setdefault(client_id, {
                    "handle": "kill", "client_id": client_id, "operation": "polymarket.cancel",
                    "args": {"order_id": order_id}, "result": {"status": "uncertain"}})
            result = surface.venue.cancel(client_id=client_id, order_id=order_id)
        except Exception as exc:  # noqa: BLE001 - an unanswered cancel is unknown
            result = {"order_id": order_id, "status": "uncertain",
                      "error": f"cancel exception: {type(exc).__name__}"}
        if client_id in surface.intents:
            surface.intents[client_id]["result"] = dict(result)
        rt.ledger.append({"kind": "polymarket.wind_down_result", "client_id": client_id,
                          "result": result})
        report["cancelled"] += result.get("status") == "cancelled"
        unknown |= result.get("status") == "uncertain"
    surface._account_memo = None  # the cancels may have moved it
    try:
        account = surface.account(rt)
    except Exception as exc:  # noqa: BLE001 - the report stands on the world's records
        report["error"], account, unknown = type(exc).__name__, None, True
    if surface.live:
        report["residual"] = _live_residual(surface, account)
        open_orders = _live_targets(surface)
        matched, unanswered = _unsettled(surface)
    else:
        still = sanitized(account) if account is not None else {"positions": [],
                                                                "open_orders": []}
        report["residual"] = [{key: p[key] for key in ("token_id", "market_id", "outcome",
                                                       "size", "avg_px")}
                              for p in still["positions"]]
        open_orders, matched, unanswered = still["open_orders"], [], []
    report["open_orders"] = len(open_orders)
    report["unsettled"], report["unanswered"] = matched, unanswered
    # Matched but unconfirmed quantity is exposure; an order, a cancel or a read the
    # venue has not answered leaves the pot unknown, never flat (Sol P1 on #177).
    report["exposure_state"] = (
        UNKNOWN if unknown or unanswered else
        PENDING if report["residual"] or open_orders or matched else FLAT)
    rt.ledger.append({"kind": "polymarket.wind_down_report", **report})
    return report
