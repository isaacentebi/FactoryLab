"""Thinking control (edition 3, C2): what wakes a seat, and when.

A tick used to be one request per price print. It is now one *coalesced world
update* per seat: every ``MarketMid``, ``Funding`` and other world event since
that seat's last wake folded into one ``since_you_last_woke`` block. Fills and
safety events (order rejections, liquidation warnings, margin calls) still wake
the affected seat immediately, because they are the events a seat cannot afford
to read late.

Three things live here, and nothing else does:

* the **fold** — per seat, per coin: first, last, high, low, the funding prints
  with their timestamps, and how many prints there were;
* the **subscription** a seat owns — ``{kinds, coins, cadence_floor}``, changed
  by the seat's own answer field ``subscribe`` and ledgered as
  ``subscription.changed``, with no ballot, because it is the seat's own money;
  plus ``defer: <n>``, which keeps routine ticks from waking it for n ticks;
* the **watcher** triggers — a predicate over world state the kernel evaluates
  each tick without a model call, so a seat can name the one fact that would
  change its mind and then stop paying to look for it.

A subscription narrows; it never widens. ``kinds`` must lie inside the seat's
registered ``accepts``, because a kind no router would ever deliver to the seat
is not a subscription, it is a wish. Which kinds are *routine* — subject to the
cadence floor and to defer — is deliberately small: the world's own heartbeat
and its price and funding prints. Judgement work (a ``Verdict`` to write, a
``ProducerReturn`` to grade) is somebody else's request arriving, and a seat
that deferred its own market reading has not resigned from the cascade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

#: The world's own heartbeat and prints: the events a seat may sleep through.
ROUTINE_KINDS = frozenset({"Tick", "Drip", "MarketMid", "Funding", "WorldUpdate"})
#: Money and safety: these reach the affected seat whatever it deferred.
SAFETY_KINDS = frozenset({"Fill", "OrderRejected", "WatcherFired"})
#: Trigger predicates the kernel can settle from world state alone.
TRIGGER_KINDS = ("price_cross", "funding_sign", "equity_below", "equity_above")
#: How many funding prints one fold keeps per coin before the oldest fall out.
MAX_FUNDING_PRINTS = 16
#: The longest sleep a single answer may buy.
MAX_DEFER_TICKS = 1_000


def is_routine(kind: str) -> bool:
    """True for the world events a seat's cadence floor and defer may silence."""
    return str(kind) in ROUTINE_KINDS


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (ArithmeticError, AttributeError, InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


@dataclass
class Subscription:
    """What wakes one seat: kinds, coins, and the floor between routine wakes.

    ``None`` means *everything the seat accepts* for ``kinds`` and *every coin*
    for ``coins``; a seat that has never spoken is subscribed to its whole
    contract at a floor of one tick, which is exactly the behaviour that
    preceded this contract.
    """

    kinds: frozenset[str] | None = None
    coins: frozenset[str] | None = None
    cadence_floor: int = 1

    def state(self) -> dict[str, Any]:
        """Plain data, so a checkpoint carries a subscription without a record type."""
        return {
            "kinds": None if self.kinds is None else sorted(self.kinds),
            "coins": None if self.coins is None else sorted(self.coins),
            "cadence_floor": self.cadence_floor,
        }

    @classmethod
    def restore(cls, state: dict[str, Any]) -> Subscription:
        """Rebuild a subscription from its own checkpointed plain data."""
        kinds, coins = state.get("kinds"), state.get("coins")
        return cls(
            None if kinds is None else frozenset(kinds),
            None if coins is None else frozenset(coins),
            int(state.get("cadence_floor", 1)),
        )


def parse_subscribe(value: Any, current: Subscription, accepts: frozenset[str]) -> Subscription:
    """Read a seat's own ``subscribe`` field, or say why it cannot be read.

    Every field is optional; what the answer does not name it keeps. ``kinds``
    narrows within the seat's registered ``accepts`` — a kind outside the
    contract is refused rather than silently dropped, so the seat is told.
    """
    if not isinstance(value, dict):
        raise ValueError("subscribe must be an object")
    unknown = set(value) - {"kinds", "coins", "cadence_floor"}
    if unknown:
        raise ValueError(f"subscribe knows kinds, coins and cadence_floor, not {sorted(unknown)}")
    kinds, coins, floor = current.kinds, current.coins, current.cadence_floor
    if "kinds" in value:
        raw = value["kinds"]
        if raw is None:
            kinds = None
        else:
            if not isinstance(raw, list) or not raw or not all(isinstance(k, str) for k in raw):
                raise ValueError("subscribe kinds must be a non-empty list of event kinds")
            outside = sorted(set(raw) - set(accepts))
            if outside:
                raise ValueError(
                    f"a subscription narrows what you accept; {outside} is outside your contract")
            kinds = frozenset(raw)
    if "coins" in value:
        raw = value["coins"]
        if raw is None:
            coins = None
        else:
            if not isinstance(raw, list) or not raw or not all(isinstance(c, str) for c in raw):
                raise ValueError("subscribe coins must be a non-empty list of coin names")
            coins = frozenset(c.strip().upper() for c in raw)
    if "cadence_floor" in value:
        raw = value["cadence_floor"]
        if type(raw) is not int or not 1 <= raw <= MAX_DEFER_TICKS:
            raise ValueError(f"cadence_floor must be an int in [1, {MAX_DEFER_TICKS}]")
        floor = raw
    return Subscription(kinds, coins, floor)


def parse_defer(value: Any) -> int:
    """Read a seat's own ``defer`` field as a whole number of ticks."""
    if type(value) is not int or isinstance(value, bool):
        raise ValueError("defer must be an integer number of ticks")
    if not 0 <= value <= MAX_DEFER_TICKS:
        raise ValueError(f"defer must be in [0, {MAX_DEFER_TICKS}] ticks")
    return value


def validate_trigger(value: Any) -> dict[str, Any]:
    """Return a watcher's trigger in canonical form, or refuse it with a reason.

    A trigger the kernel cannot settle from world state without asking a model
    is not a watcher; it is a seat, and it is priced like one.
    """
    if not isinstance(value, dict):
        raise ValueError("trigger must be an object")
    kind = value.get("kind")
    if kind not in TRIGGER_KINDS:
        raise ValueError(f"trigger kind must be one of {list(TRIGGER_KINDS)}")
    unknown = set(value) - {"kind", "coin", "level"}
    if unknown:
        raise ValueError(f"trigger knows kind, coin and level, not {sorted(unknown)}")
    trigger: dict[str, Any] = {"kind": kind}
    if kind in ("price_cross", "funding_sign"):
        coin = value.get("coin")
        if not isinstance(coin, str) or not coin.strip():
            raise ValueError(f"a {kind} trigger names a coin")
        trigger["coin"] = coin.strip().upper()
    elif "coin" in value:
        raise ValueError(f"a {kind} trigger is about the account, not a coin")
    if kind in ("price_cross", "equity_below", "equity_above"):
        level = _decimal(value.get("level"))
        if level is None:
            raise ValueError(f"a {kind} trigger names a numeric level")
        trigger["level"] = str(level)
    elif "level" in value:
        raise ValueError("a funding_sign trigger has no level")
    return trigger


def _sign(value: Decimal) -> int:
    return 0 if value == 0 else (1 if value > 0 else -1)


def evaluate_trigger(trigger: dict[str, Any], observed: dict[str, Any],
                     last: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Settle one trigger against world state; return the fact it fired on, or None.

    Every trigger is edge-triggered against the last state this watcher saw: a
    price that is already above the level has not crossed it, and an equity that
    was below it yesterday does not fire again today. The first evaluation
    therefore never fires — it only records where the world was — which is what
    makes a watcher a monitor rather than a standing alarm.
    """
    kind = trigger["kind"]
    seen: dict[str, Any] = {}
    fact: dict[str, Any] | None = None
    if kind == "price_cross":
        coin = trigger["coin"]
        level = Decimal(trigger["level"])
        now = _decimal((observed.get("mids") or {}).get(coin))
        before = _decimal((last or {}).get("mid"))
        seen = {"mid": None if now is None else str(now)}
        if now is not None and before is not None and (
                (before < level <= now) or (before > level >= now)):
            fact = {"kind": kind, "coin": coin, "level": str(level),
                    "previous": str(before), "observed": str(now)}
    elif kind == "funding_sign":
        coin = trigger["coin"]
        now = _decimal((observed.get("funding") or {}).get(coin))
        before = _decimal((last or {}).get("rate"))
        seen = {"rate": None if now is None else str(now)}
        if now is not None and before is not None and _sign(now) != _sign(before):
            fact = {"kind": kind, "coin": coin, "previous": str(before), "observed": str(now)}
    else:
        level = Decimal(trigger["level"])
        now = _decimal(observed.get("equity_usd"))
        before = _decimal((last or {}).get("equity_usd"))
        seen = {"equity_usd": None if now is None else str(now)}
        if now is not None and before is not None:
            crossed = (before >= level > now) if kind == "equity_below" else (
                before <= level < now)
            if crossed:
                fact = {"kind": kind, "level": str(level),
                        "previous": str(before), "observed": str(now)}
    return fact, seen if now is not None else dict(last or {})


@dataclass
class _Fold:
    """One seat's unread world, since it last woke."""

    from_tick: int = 0
    prints: int = 0
    coins: dict[str, dict[str, Any]] = field(default_factory=dict)
    funding: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    kinds: dict[str, int] = field(default_factory=dict)

    def observe(self, kind: str, payload: dict[str, Any], ts_ns: int) -> None:
        """Fold one world event in, keeping first, last, high, low and the count."""
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        coin = str(payload.get("coin", "")).strip().upper()
        if kind == "MarketMid":
            mid = _decimal(payload.get("mid"))
            if not coin or mid is None:
                return
            self.prints += 1
            row = self.coins.get(coin)
            if row is None:
                self.coins[coin] = {"first": str(mid), "last": str(mid), "high": str(mid),
                                    "low": str(mid), "prints": 1,
                                    "first_t_s": ts_ns // 1_000_000_000,
                                    "last_t_s": ts_ns // 1_000_000_000}
                return
            row["last"] = str(mid)
            row["last_t_s"] = ts_ns // 1_000_000_000
            row["prints"] += 1
            if mid > Decimal(row["high"]):
                row["high"] = str(mid)
            if mid < Decimal(row["low"]):
                row["low"] = str(mid)
        elif kind == "Funding":
            rate = _decimal(payload.get("rate"))
            if not coin or rate is None:
                return
            prints = self.funding.setdefault(coin, [])
            prints.append({"t_s": ts_ns // 1_000_000_000, "rate": str(rate)})
            del prints[:-MAX_FUNDING_PRINTS]

    def absorb(self, older: _Fold) -> None:
        """Take an older fold back in, so nothing it held is lost (R3-F).

        Used when a delivery failed: the fold that was rendered into the failed
        request is merged back under the fold that accumulated while the request
        was out. The older fold's prints came first, so its ``first`` and its
        ``from_tick`` win, its ``last`` loses, and the extremes are the extremes
        of both. Funding prints interleave by time and keep the same bound.
        """
        self.from_tick = min(self.from_tick, older.from_tick)
        self.prints += older.prints
        for kind, count in older.kinds.items():
            self.kinds[kind] = self.kinds.get(kind, 0) + count
        for coin, old in older.coins.items():
            row = self.coins.get(coin)
            if row is None:
                self.coins[coin] = dict(old)
                continue
            row["first"], row["first_t_s"] = old["first"], old["first_t_s"]
            row["prints"] += old["prints"]
            row["high"] = str(max(Decimal(row["high"]), Decimal(old["high"])))
            row["low"] = str(min(Decimal(row["low"]), Decimal(old["low"])))
        for coin, prints in older.funding.items():
            merged = sorted([*prints, *self.funding.get(coin, ())], key=lambda p: p["t_s"])
            self.funding[coin] = merged[-MAX_FUNDING_PRINTS:]

    def filtered(self, coins: frozenset[str] | None) -> _Fold:
        """This fold as a seat subscribed to ``coins`` reads it (R3-F).

        A coin filter used to decide only whether a seat was in the draw; the
        fold it was then handed carried every coin the world printed. A seat
        subscribed to BTC sees BTC, and the event counts it is shown are the
        counts of what it is shown.
        """
        if coins is None:
            return self
        kept = {c: dict(row) for c, row in self.coins.items() if c in coins}
        funding = {c: [dict(p) for p in prints]
                   for c, prints in self.funding.items() if c in coins}
        prints = sum(row["prints"] for row in kept.values())
        kinds = dict(self.kinds)
        if "MarketMid" in kinds:
            kinds["MarketMid"] = prints
        if "Funding" in kinds:
            kinds["Funding"] = sum(len(p) for p in funding.values())
        return _Fold(self.from_tick, prints, kept, funding,
                     {k: c for k, c in kinds.items() if c})

    def rendered(self, to_tick: int) -> dict[str, Any]:
        """The block a seat reads: one object for everything it slept through."""
        return {
            "from_tick": self.from_tick,
            "to_tick": to_tick,
            "prints": self.prints,
            "events": dict(sorted(self.kinds.items())),
            "coins": {c: dict(row) for c, row in sorted(self.coins.items())},
            "funding": {c: [dict(p) for p in prints]
                        for c, prints in sorted(self.funding.items())},
        }

    def state(self) -> dict[str, Any]:
        """Plain data for the checkpoint."""
        return {"from_tick": self.from_tick, "prints": self.prints,
                "coins": {c: dict(row) for c, row in self.coins.items()},
                "funding": {c: [dict(p) for p in prints] for c, prints in self.funding.items()},
                "kinds": dict(self.kinds)}

    @classmethod
    def restore(cls, state: dict[str, Any]) -> _Fold:
        """Rebuild a fold from the checkpoint's plain data."""
        return cls(int(state.get("from_tick", 0)), int(state.get("prints", 0)),
                   {c: dict(row) for c, row in (state.get("coins") or {}).items()},
                   {c: [dict(p) for p in prints]
                    for c, prints in (state.get("funding") or {}).items()},
                   dict(state.get("kinds") or {}))


class SubscriptionBook:
    """Every seat's subscription, sleep, unread world and watcher, in one place.

    Persisted whole through checkpoints as plain data: a restored world knows
    which seats are still asleep, how much world each has not read, and where
    each watcher last saw the price.
    """

    def __init__(self) -> None:
        self.subs: dict[str, Subscription] = {}
        self.deferred_until: dict[str, int] = {}
        self.last_wake: dict[str, int] = {}
        self.folds: dict[str, _Fold] = {}
        # seat -> the fold rendered into a request that was invoked and has not yet
        # come back ok. Its presence is the ``delivered`` state (R3-F); ``offered``
        # is a fold in ``folds`` with nothing here; ``acknowledged`` is neither.
        self.delivering: dict[str, _Fold] = {}
        self.watchers: dict[str, dict[str, Any]] = {}
        self.funding: dict[str, str] = {}  # the latest funding rate per coin

    # -- subscriptions ----------------------------------------------------
    def subscription(self, seat: str) -> Subscription:
        """The seat's own subscription, or the whole contract it registered under."""
        return self.subs.get(seat, Subscription())

    def set_subscription(self, seat: str, sub: Subscription) -> None:
        """Adopt a seat's changed subscription; it is the seat's own money."""
        self.subs[seat] = sub

    def defer(self, seat: str, ticks: int, *, now: int) -> int:
        """Sleep through the next ``ticks`` routine ticks; return the last one slept.

        An answer given during tick *n* that defers six ticks is absent from
        ticks *n+1* through *n+6* and is in the draw again on the seventh.
        """
        until = now + max(0, ticks)
        self.deferred_until[seat] = until
        return until

    def woke(self, seat: str, *, now: int) -> None:
        """Record a routine paid wake, which is what the cadence floor measures."""
        self.last_wake[seat] = now
        self.deferred_until.pop(seat, None)

    def absent(self, seat: str, kind: str, *, now: int, coins: frozenset[str],
               jitter=None) -> str:
        """Why this seat is not in the draw for this event, or "" when it is awake.

        Only routine kinds can leave a seat absent. A fill, an order rejection
        or a fired watcher reaches a seat that deferred every tick it had.
        ``jitter(seat, last_wake, floor)`` lengthens the seat's floor by a few
        ticks of its own after each routine wake, so seats are not phase-locked
        to one tick (essay II.IV.c; time audit T15).
        """
        if not is_routine(kind):
            return ""
        sub = self.subscription(seat)
        if sub.kinds is not None and kind not in sub.kinds:
            return f"asleep: {kind} is not in this seat's subscription"
        if sub.coins is not None and coins and not (coins & sub.coins):
            return "asleep: no subscribed coin in this update"
        until = self.deferred_until.get(seat)
        if until is not None and now <= until:
            return f"asleep: deferred through tick {until}"
        last = self.last_wake.get(seat)
        floor = sub.cadence_floor
        extra = jitter(seat, last, floor) if jitter is not None and last is not None else 0
        if floor + extra > 1 and last is not None and now - last < floor + extra:
            return (f"asleep: cadence floor {floor} ticks"
                    + (f", jittered by {extra}" if extra else ""))
        return ""

    # -- the coalesced update ---------------------------------------------
    def observe(self, seats, kind: str, payload: dict[str, Any], ts_ns: int, *,
                now: int) -> None:
        """Fold one world event into every live seat's unread world."""
        if kind == "Funding":
            coin = str(payload.get("coin", "")).strip().upper()
            rate = _decimal(payload.get("rate"))
            if coin and rate is not None:
                self.funding[coin] = str(rate)
        for seat in seats:
            fold = self.folds.get(seat)
            if fold is None:
                fold = self.folds[seat] = _Fold(from_tick=now)
            fold.observe(kind, payload, ts_ns)

    def fold_coins(self, seat: str) -> frozenset[str]:
        """The coins this seat has unread prints for."""
        fold = self.folds.get(seat)
        return frozenset(fold.coins) | frozenset(fold.funding) if fold else frozenset()

    def fold_state(self, seat: str) -> str:
        """``offered``, ``delivered`` or ``acknowledged`` for this seat's fold (R3-F)."""
        if seat in self.delivering:
            return "delivered"
        fold = self.folds.get(seat)
        holds = fold is not None and (fold.prints or fold.coins or fold.funding or fold.kinds)
        return "offered" if holds else "acknowledged"

    def take(self, seat: str, *, now: int) -> dict[str, Any]:
        """Render this seat's unread world for delivery, and hold it until it lands.

        The fold moves from ``offered`` to ``delivered``: it is rendered into the
        request about to be invoked and *kept* here, because a request that fails
        or comes back malformed never showed the seat anything. ``acknowledge``
        drops it; ``return_to_offered`` folds it back under whatever arrived in the
        meantime, so the next wake sees the world it slept through (R3-F).

        A delivery still outstanding when a second one starts — a step that raised
        between the two — is returned to offered first rather than discarded.
        """
        self.return_to_offered(seat)
        fold = self.folds.pop(seat, None) or _Fold(from_tick=now)
        self.folds[seat] = _Fold(from_tick=now)
        self.delivering[seat] = fold
        return fold.filtered(self.subscription(seat).coins).rendered(now)

    def acknowledge(self, seat: str) -> bool:
        """The invocation returned ok: what was delivered is read. True when one was."""
        return self.delivering.pop(seat, None) is not None

    def return_to_offered(self, seat: str) -> bool:
        """The invocation failed: the delivered fold is unread again. True when one was."""
        pending = self.delivering.pop(seat, None)
        if pending is None:
            return False
        current = self.folds.get(seat)
        if current is None:
            self.folds[seat] = pending
        else:
            current.absorb(pending)
        return True

    def peek(self, seat: str, *, now: int) -> dict[str, Any]:
        """Render this seat's unread world without consuming it (for a judge or a test)."""
        fold = self.folds.get(seat) or _Fold(from_tick=now)
        return fold.filtered(self.subscription(seat).coins).rendered(now)

    # -- watchers ----------------------------------------------------------
    def watch(self, seat: str, *, owner: str | None, trigger: dict[str, Any]) -> None:
        """Register a watcher's trigger and the seat it answers to."""
        self.watchers[seat] = {"owner": owner, "trigger": dict(trigger), "last": None}

    def forget(self, seat: str) -> None:
        """A retired watcher stops being evaluated and stops being charged."""
        self.watchers.pop(seat, None)

    def evaluate(self, seat: str, observed: dict[str, Any]) -> dict[str, Any] | None:
        """Settle one watcher against world state, advancing what it has seen."""
        record = self.watchers[seat]
        fact, seen = evaluate_trigger(record["trigger"], observed, record["last"])
        record["last"] = seen
        return fact

    # -- persistence --------------------------------------------------------
    def state(self) -> dict[str, Any]:
        """The whole book as plain JSON data, in sorted order."""
        return {
            "subs": {seat: sub.state() for seat, sub in sorted(self.subs.items())},
            "deferred_until": dict(sorted(self.deferred_until.items())),
            "last_wake": dict(sorted(self.last_wake.items())),
            "folds": {seat: fold.state() for seat, fold in sorted(self.folds.items())},
            # The fold a request is carrying right now (R3-F): checkpointed, so a
            # restore between the request and its answer resumes with that world
            # still owed to the seat rather than silently consumed.
            "delivering": {seat: fold.state()
                           for seat, fold in sorted(self.delivering.items())},
            "watchers": {seat: {"owner": w["owner"], "trigger": dict(w["trigger"]),
                                "last": None if w["last"] is None else dict(w["last"])}
                         for seat, w in sorted(self.watchers.items())},
            "funding": dict(sorted(self.funding.items())),
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Adopt a checkpoint's book in place, losing nothing it carried."""
        self.subs = {seat: Subscription.restore(s)
                     for seat, s in (state.get("subs") or {}).items()}
        self.deferred_until = {k: int(v) for k, v in (state.get("deferred_until") or {}).items()}
        self.last_wake = {k: int(v) for k, v in (state.get("last_wake") or {}).items()}
        self.folds = {seat: _Fold.restore(f) for seat, f in (state.get("folds") or {}).items()}
        self.delivering = {seat: _Fold.restore(f)
                           for seat, f in (state.get("delivering") or {}).items()}
        self.watchers = {seat: {"owner": w.get("owner"), "trigger": dict(w["trigger"]),
                                "last": None if w.get("last") is None else dict(w["last"])}
                         for seat, w in (state.get("watchers") or {}).items()}
        self.funding = dict(state.get("funding") or {})


class ThinkingMixin:
    """The runtime's side of thinking control: folding, watchers, and the seat's answer.

    ``_route`` (routing.py) asks this mixin who is awake; the loop asks it to
    fold each world event and to settle the watchers once a tick. Nothing here
    decides what a seat should want — only when it is asked.
    """

    @property
    def subscriptions(self) -> dict[str, Any]:
        """The book as plain checkpoint data; assignment restores it in place."""
        return self.subscription_book.state()

    @subscriptions.setter
    def subscriptions(self, state: dict[str, Any]) -> None:
        book = getattr(self, "subscription_book", None)
        if book is None:
            book = self.subscription_book = SubscriptionBook()
        book.restore(state if isinstance(state, dict) else {})

    @property
    def tick_index(self) -> int:
        """Ticks are what a cadence floor and a defer are counted in."""
        return self.ticks_consumed

    def _live_seats(self) -> list[str]:
        return [aid for aid in self.assemblies if aid not in self.retired_assemblies]

    def _wake_jitter(self, seat: str, last: int, floor: int) -> int:
        """Whole ticks this seat's next routine wake waits beyond its floor.

        Essay II.IV.c: the deferral between loops "should also be diversified
        (jittered) to intentionally obfuscate entrainment"; every seat woke on one
        shared tick (time audit T15). Guarantees a draw of its own per seat and per
        wake (the world seed, the seat, the tick it last woke: a resumed world draws
        the same), of ``floor × timing.jitter_fraction × u`` ticks, rounded up with
        the probability of its fraction, so a seat at a floor of one sits out the
        next tick about ``jitter_fraction / 2`` of the time. A seat's own floor and
        defer are unchanged; a safety event still reaches it at once.
        """
        from factorylab.runtime.clockwork import jitter_draw

        span = floor * self.clockwork.jitter_fraction * jitter_draw(
            self.clockwork.seed, f"wake:{seat}", last)
        whole = int(span)
        return whole + int(jitter_draw(self.clockwork.seed, f"wake-round:{seat}", last)
                           < span - whole)

    @property
    def inbox_delivery(self) -> dict[str, Any]:
        """The inbox's delivery and retention bookkeeping, as plain checkpoint data."""
        return self.outcomes.delivery_state()

    @inbox_delivery.setter
    def inbox_delivery(self, state: dict[str, Any]) -> None:
        self.outcomes.restore_delivery(state if isinstance(state, dict) else {})

    def _settle_fold_delivery(self, seat: str, ret) -> None:
        """Acknowledge the delivered fold, or return it to offered (R3-F).

        The fold this seat was handed counts as read only when the invocation it
        rode on came back ok. A failed or malformed invocation showed the seat
        nothing, so its world goes back under the fold that accumulated since and
        the next wake sees it; a ledger item records which happened, because a
        seat that silently lost a window of prices cannot tell that it did.
        """
        book = self.subscription_book
        if getattr(ret, "status", None) == "ok":
            if book.acknowledge(seat):
                self.ledger.append({"kind": "fold.acknowledged", "assembly_id": seat,
                                    "handle": ret.handle, "ts": self.clock.now_ns})
            return
        if book.return_to_offered(seat):
            self.ledger.append({"kind": "fold.offered", "assembly_id": seat,
                                "handle": ret.handle, "status": getattr(ret, "status", None),
                                "ts": self.clock.now_ns})

    def _fold_world_event(self, ev) -> None:
        """Fold one world event into every live seat's unread world."""
        kind = str(ev.kind)
        if kind == "Fill":
            # A fill is the consequence of one seat's order, and R3-F addresses it
            # to that seat's inbox with its own id rather than leaving it in the
            # public stream (§3: "fills not consistently addressed").
            self._address_fill_to_inbox(dict(ev.payload))
        if kind not in ROUTINE_KINDS or kind == "WorldUpdate":
            return
        if kind == "MarketMid" and getattr(self, "_chaos_active", lambda _f: False)("stale_mids"):
            # A stale-mids fault: this tick's prints do not reach the seats' folds
            # (runtime.chaos); the world's own record keeps them.
            return
        self.subscription_book.observe(
            self._live_seats(), kind, dict(ev.payload), ev.ts_ns, now=self.tick_index)

    def _wants_world_update(self) -> bool:
        return any("WorldUpdate" in self.assemblies[aid].spec.accepts
                   for aid in self._live_seats())

    def _emit_world_update(self) -> None:
        """One coalesced update per tick, for the seats that asked for one."""
        if not self._wants_world_update():
            return
        book = self.subscription_book
        seats = [a for a in self._live_seats()
                 if "WorldUpdate" in self.assemblies[a].spec.accepts]
        # The event's own payload is the world every seat could have read; the
        # seat the router picks is then handed its own fold, which reaches
        # further back if it has been asleep.
        merged = _Fold(from_tick=min((book.folds[s].from_tick for s in seats
                                      if s in book.folds), default=self.tick_index))
        for seat in seats:
            fold = book.folds.get(seat)
            if fold is None:
                continue
            merged.prints = max(merged.prints, fold.prints)
            for coin, row in fold.coins.items():
                merged.coins.setdefault(coin, dict(row))
            for coin, prints in fold.funding.items():
                merged.funding.setdefault(coin, [dict(p) for p in prints])
            for kind, count in fold.kinds.items():
                merged.kinds[kind] = max(merged.kinds.get(kind, 0), count)
        self._emit("WorldUpdate", {"tick": self.tick_index,
                                   "since_you_last_woke": merged.rendered(self.tick_index)})

    def _observed_world(self) -> dict[str, Any]:
        """What a trigger is settled against: mids, funding rates and equity."""
        try:
            mids = {c: str(m) for c, m in self.exchange.mids().items()}
        except Exception:
            mids = {}
        observed: dict[str, Any] = {"mids": mids,
                                    "funding": dict(self.subscription_book.funding)}
        try:
            account = self.exchange.account()
            # A fallback snapshot is not the equity now: a watcher settles on a live
            # read or keeps the last value it actually saw.
            if not getattr(account, "stale", False):
                observed["equity_usd"] = str(account.equity_usd)
        except Exception:
            # R3-F: a failed account read is not the compute wallet's balance. The
            # wallet is spending authority, not venue equity, and substituting it
            # would fire an ``equity_below`` watcher on a number the venue never
            # reported. An unread equity is simply absent, and ``evaluate_trigger``
            # keeps the last value it actually saw.
            pass
        return observed

    def _evaluate_watchers(self, *, sweep: str = "") -> None:
        """Settle every watcher once this tick, at the program price and no model call.

        A watcher is a seat like any other: the evaluation is reserved and
        committed against its own entitlement under ``model:program``, so a
        watcher nobody funds stops watching instead of watching for free.
        ``sweep`` names an extra evaluation inside one event (the safety pass
        between model calls, time audit T8), so its metered handle is its own.
        """
        from factorylab.world.metering import Meter

        book = self.subscription_book
        for seat in sorted(book.watchers):
            if seat not in self.assemblies or seat in self.retired_assemblies:
                continue
            record = book.watchers[seat]
            # A watcher pays once per world tick. A safety sweep inside a tick it has
            # already paid for settles it again at no further charge.
            paid = self.watcher_ticks.get(seat) == self.ticks_consumed
            price = 0 if sweep and paid else self.m.prices.program_micro_per_call
            meter = Meter(self._seat_wallet(seat))
            observed = self._observed_world()
            handle = f"watch-{seat}-{self.n}{'-' + sweep if sweep else ''}"
            if not price:
                fact, cost = book.evaluate(seat, observed), 0
            else:
                try:
                    metered = meter.run(handle=handle, reason="model:program", ceiling=price,
                                        execute=lambda s=seat, o=observed: book.evaluate(s, o),
                                        cost_of=lambda _r, p=price: p)
                except Exception as exc:  # an unfunded watcher simply does not look
                    self.ledger.append({"kind": "watcher.unaffordable", "watcher": seat,
                                        "reason": type(exc).__name__, "ts": self.clock.now_ns})
                    continue
                fact, cost = metered.result, metered.cost
                self.watcher_ticks[seat] = self.ticks_consumed
            self.ledger.append({"kind": "watcher.evaluated", "watcher": seat,
                                "owner": record["owner"], "cost": cost,
                                "fired": fact is not None, "ts": self.clock.now_ns})
            if fact is None:
                continue
            self._emit("WatcherFired", {"watcher": seat, "owner": record["owner"],
                                        "trigger": fact, "tick": self.tick_index})

    def _apply_thinking(self, handle: str, seat: str, ret) -> None:
        """Adopt the seat's own ``subscribe`` and ``defer``, or tell it why not.

        No ballot: a seat's subscription is the seat's own money. A refusal
        reaches the population the way a refused propensity does, because a
        refusal nobody can read is repeated.

        What deferral covers, exactly (R3-F, and the common contract says the
        same words to the seat): ``defer`` and ``cadence_floor`` silence *routine
        world wakes* — the heartbeat, the drip, the price and funding prints and
        the coalesced update. They do not silence a fill, an order rejection or a
        fired watcher, and they do not silence a judge or meta commission, which
        is somebody else's paid request arriving. A commission is declined the
        only way paid work can be: by answering ``{"status": "cannot", "reason":
        ...}``, which costs the call and nothing else, is not malformed, and is
        ledgered ``commission.declined``.
        """
        outputs = ret.outputs if isinstance(ret.outputs, dict) else {}
        if ret.status != "ok" or seat not in self.assemblies:
            return
        book = self.subscription_book
        if "subscribe" in outputs:
            try:
                sub = parse_subscribe(outputs["subscribe"], book.subscription(seat),
                                      frozenset(self.assemblies[seat].spec.accepts))
            except ValueError as exc:
                self._thinking_refused(handle, f"subscribe: {exc}")
            else:
                book.set_subscription(seat, sub)
                self.ledger.append({"kind": "subscription.changed", "handle": handle,
                                    "assembly_id": seat, **sub.state(),
                                    "ts": self.clock.now_ns})
        if "defer" in outputs:
            try:
                ticks = parse_defer(outputs["defer"])
            except ValueError as exc:
                self._thinking_refused(handle, f"defer: {exc}")
            else:
                until = book.defer(seat, ticks, now=self.tick_index)
                self.ledger.append({"kind": "seat.deferred", "handle": handle,
                                    "assembly_id": seat, "ticks": ticks, "until_tick": until,
                                    "ts": self.clock.now_ns})

    def _thinking_refused(self, handle: str, reason: str) -> None:
        self.ledger.append({"kind": "subscription.refused", "handle": handle,
                            "reason": reason, "ts": self.clock.now_ns})
        self._refusal_to_owner(handle, "subscription_refused", reason)

