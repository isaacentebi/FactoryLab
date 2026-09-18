"""The wind-down executor: the only authority a dead world keeps over its venue.

Edition 3, R3-C (GPT-6 Pro's third reading, §3 and §6.D). Death and liquidation
are two states, ledgered separately:

``production_state``
    ``alive`` or ``killed``. A kill sets it ``killed`` first and irrevocably.
    Nothing here can move it back: this module holds an exchange and a ledger and
    has no way to reach the population at all.

``exposure_state``
    ``flat``, ``dust_within_precommitted_bound``, ``wind_down_pending`` or
    ``unknown``, decided by a final account reconciliation and never by an
    acknowledgement. A resting order is not flat; a partial fill is not flat; a
    read that failed is ``unknown``, never an empty account.

Every external operation has a durable identity derived from the launch nonce,
the coin, the market, the side and a sequence that names the *target* (the venue
order id for a cancel, the single position or balance otherwise), so the same
identity is derived again by a repeated kill or by a kill after a restart. The
identity is ledgered as ``winddown.op`` before submission and ``winddown.op_result``
after it, and is carried to the venue as the client order id, so the adapter
deduplicates it too. An attempt whose result is already in the diary is never
resubmitted; one that was submitted without a recorded result (a dropped ack, a
process death between the two records) or whose answer was ambiguous (a timeout,
an exception, a resting order) is *reconciled* by reading the venue, never by
sending it again. An attempt the venue definitively refused or only partly did
(rejected, an IOC that cancelled, a fill for less than the size) is followed by the
target's next attempt under the next sequence (``sequence_of``): up to
``ROUNDS_PER_KILL`` rounds in one kill, each re-reading the venue, and a later kill
of the same diary continues the numbering. A residual worth less than the venue's
order minimum can never be sold, so it is reported as dust
(``below_venue_minimum``) rather than left ``wind_down_pending`` forever.

The authority is narrow by construction: cancel, reduce, close, reconcile. Every
submission asserts that its side reduces the absolute size of what is there;
an operation that would open risk is refused by this module before the venue
sees it.

Nothing here may raise. A venue that refuses, times out or answers nothing is a
recorded failure; a ledger that refuses a record is counted, printed on stderr
and carried to the witness line. A kill a venue or a disk can block is not a kill.
"""

from __future__ import annotations

import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation

from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.shared import _to_plain
from factorylab.world.exchange import OrderResult

#: ``production_state``: the two values a world's cognition has.
ALIVE = "alive"
KILLED = "killed"

#: ``exposure_state``: what the venue still holds for a world that is already dead.
FLAT = "flat"
DUST = "dust_within_precommitted_bound"
PENDING = "wind_down_pending"
UNKNOWN = "unknown"

#: Diary kinds. ``kill.wind_down`` keeps its historical vocabulary (the summary,
#: the dust notes and the failed reads); each operation is its own pair.
OP = "winddown.op"
OP_RESULT = "winddown.op_result"
RECONCILIATION = "winddown.reconciliation"
STEP = "kill.wind_down"

#: What an operation's own answer has to say for it to have done what it names.
_SUCCESS = {"cancel": ("cancelled", "ok"), "close": ("filled",), "sell": ("filled",)}

#: The report key each operation counts under, in the historical spelling.
_COUNTER = {"cancel": "cancelled", "close": "closed", "sell": "sold"}


#: At most this many attempts on one target inside one kill: the first, and retries of
#: whatever the venue definitively refused or only partly did. A later kill of the same
#: diary (after a crash inside the wind-down window) continues the numbering.
ROUNDS_PER_KILL = 3


def operation_id(launch_nonce, coin: str, market: str, side: str, sequence) -> str:
    """The durable identity of one external operation.

    Derived from (launch nonce, coin, market, side, sequence) and nothing else, so
    a repeated kill and a kill after a restart derive the same identity for the
    same target and find its result already in the diary. ``sequence`` names the
    target -- the venue's own order id for a cancel, ``0`` for the one position or
    the one balance a coin and market can hold -- and, from the second attempt on
    that target, the attempt: ``sequence_of(target, n)``.
    """
    identity = "|".join(("winddown", str(launch_nonce or "unlaunched"), str(coin),
                         str(market), str(side), str(sequence)))
    return "wd-" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def sequence_of(target, attempt: int) -> str:
    """The sequence of attempt ``attempt`` on ``target``; attempt 0 is the historical one.

    Monotonic in ``attempt``, so a retry is a new identity the venue has never seen
    and the attempt a diary already holds is never sent again.
    """
    return str(target) if attempt == 0 else f"{target}.r{attempt}"


def _plain(value) -> dict:
    """A venue answers in Decimals and its own types; the diary takes plain JSON."""
    if isinstance(value, OrderResult):
        value = _to_plain(vars(value))
    if not isinstance(value, dict):
        value = {"status": "unknown", "acknowledgement": str(value)[:200]}
    return json.loads(json.dumps(value, default=str))


def _decimal(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _reduces(side: str, size: Decimal) -> bool:
    """True when selling a long or buying back a short: the only directions allowed."""
    return (size > 0 and side == "sell") or (size < 0 and side == "buy")


class WindDownExecutor:
    """Cancel, reduce, close, reconcile — restart-safe, and unable to do anything else."""

    def __init__(self, exchange, ledger, *, launch_nonce: str | None = None,
                 dust_micro: int = 1_000_000, reader=None) -> None:
        self.exchange = exchange
        self.ledger = ledger
        self.launch_nonce = launch_nonce
        self.dust_micro = dust_micro
        # How this diary's earlier operations are read back. A writable disk ledger
        # refuses to be iterated (its boundary moves under the reader), so the caller
        # that reopens a diary to kill it again — ``factorylab kill`` — supplies a
        # read-only pass over the same file. Without one, only what this object can
        # see itself is known, which is the whole truth inside a single process.
        self.reader = reader
        self.report: dict = {
            "attempted": True, "orders": 0, "cancelled": 0, "closed": 0, "sold": 0,
            "failed": 0, "reconciled": 0, "refused": 0, "ledger_failures": 0,
            "operations": 0, "operation_log": [], "errors": [],
            "production_state": KILLED, "exposure_state": UNKNOWN,
            # The earlier name for the same fact, kept so readers written against
            # the first kill contract (and GPT-6's converted regressions) still read.
            "exposure_status": UNKNOWN,
        }
        self._known, self._submitted = self._replay()
        # Targets whose attempt in this kill was definitively refused or only partly
        # done; the next round re-reads the venue and sends their next attempt.
        self._owed: set[tuple] = set()
        self._minimums: dict[tuple[str, str], int] | None = None

    # ---- the diary

    def _append(self, row: dict) -> None:
        """Write one record, or count and announce that the store refused it.

        A ledger failure during a wind-down is never fatal and never stops the
        kill: the count reaches the witness line outside the diary and stderr
        reaches the operator, and the executor keeps going.
        """
        try:
            self.ledger.append(row)
        except Exception as exc:  # noqa: BLE001 - a store may never block a kill
            self.report["ledger_failures"] += 1
            self.report.setdefault("error", type(exc).__name__)
            self.report["errors"].append({"step": "ledger", "kind": row.get("kind"),
                                          "error": type(exc).__name__})
            print(f"factorylab wind-down: the diary refused one record "
                  f"({type(exc).__name__}); the kill proceeds", file=sys.stderr)

    def _replay(self) -> tuple[dict[str, dict], set[str]]:
        """What this diary already knows: results by operation id, and ids still in flight."""
        try:
            items = list(self.reader() if self.reader is not None else self.ledger.items())
        except Exception:  # noqa: BLE001 - a diary that cannot be read knows nothing
            items = [row for row in getattr(self.ledger, "rows", []) or []
                     if isinstance(row, dict)]
        known: dict[str, dict] = {}
        submitted: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            op_id = item.get("op_id")
            if not isinstance(op_id, str):
                continue
            if item.get("kind") == OP:
                submitted.add(op_id)
            elif item.get("kind") == OP_RESULT:
                known[op_id] = item.get("result") or {}
        return known, submitted

    # ---- one operation

    def _complete(self, op: str, result: dict, detail: dict) -> bool:
        """The venue's answer says the operation did all it names: nothing is owed."""
        status = result.get("status")
        if status not in _SUCCESS[op]:
            return False
        if op == "cancel":
            return True
        filled, wanted = _decimal(result.get("filled_size")), _decimal(detail.get("size"))
        if filled is None or wanted is None:
            return True  # an acknowledgement that carries no size is taken at its word
        return filled >= abs(wanted)

    @staticmethod
    def _retryable(op: str, result: dict) -> bool:
        """A definitive answer that left the target in place: another attempt is safe.

        A rejection, an immediate-or-cancel that cancelled (with or without a partial
        fill) and a fill for less than the size are the venue saying what it did.
        A timeout, an exception, a resting order or silence is not: those are read
        again, never resent.
        """
        return result.get("status") in (("rejected",) if op == "cancel"
                                        else ("rejected", "cancelled", "filled"))

    def _operate(self, op: str, coin: str, market: str, side: str, target, detail: dict,
                 call, *, retry: bool = True) -> None:
        """Ledger the identity, submit once, ledger the answer. Or reconcile, or retry.

        Every attempt on a target has its own identity (``sequence_of``). The latest
        one the diary holds decides what happens now: a complete answer is known and
        nothing is sent; an attempt submitted without an answer is read, never
        resent; a definitive answer that left the target in place (rejected,
        cancelled, partly filled) is followed by the next attempt.
        """
        attempt, latest = 0, None
        while True:
            op_id = operation_id(self.launch_nonce, coin, market, side,
                                 sequence_of(target, attempt))
            if op_id not in self._known and op_id not in self._submitted:
                break
            latest, attempt = op_id, attempt + 1

        def record_for(n: int) -> dict:
            return {"op": op, "op_id": operation_id(self.launch_nonce, coin, market, side,
                                                    sequence_of(target, n)),
                    "coin": coin, "market": market, "side": side,
                    "sequence": sequence_of(target, n), "attempt": n, **detail}

        if latest is not None:
            record = record_for(attempt - 1)
            if latest not in self._known:
                # Submitted, with no result recorded: a dropped acknowledgement or a
                # process that died between the two records. Read, never resend.
                result = self._lookup(latest)
                self._known[latest] = result
                self._note(record, "reconciled")
                self._append({"kind": OP_RESULT, "disposition": "reconciled", **record,
                              "result": result})
                self._count(op, result, record)
                return
            result = self._known[latest]
            if self._complete(op, result, detail) or not retry:
                # The result is in the diary. This attempt happened once and is
                # never sent a second time.
                self._note(record, "known")
                self._append({"kind": OP_RESULT, "disposition": "known", **record,
                              "result": result})
                return
            if not self._retryable(op, result):
                # Ambiguous (a timeout, an exception, a resting order): read it again.
                result = self._lookup(latest)
                self._known[latest] = result
                self._note(record, "reconciled")
                self._append({"kind": OP_RESULT, "disposition": "reconciled", **record,
                              "result": result})
                self._count(op, result, record)
                return
        record = record_for(attempt)
        op_id = record["op_id"]
        self._append({"kind": OP, **record})
        self._note(record, "submitted")
        try:
            result = _plain(call(op_id))
        except Exception as exc:  # noqa: BLE001 - a venue may never block a kill
            result = {"status": "failed", "error": type(exc).__name__}
        self._submitted.add(op_id)
        self._known[op_id] = result
        self._append({"kind": OP_RESULT, "disposition": "submitted", **record,
                      "result": result})
        self._count(op, result, record)
        if retry and self._retryable(op, result) and not self._complete(op, result, detail):
            self._owed.add((op, coin, market, side, str(target)))

    def _note(self, record: dict, disposition: str) -> None:
        """Count one operation under its disposition and keep it in the report's log."""
        self.report["operations"] += 1
        self.report["orders" if disposition == "submitted" else "reconciled"] += 1
        self.report["operation_log"].append({**record, "disposition": disposition})

    def _count(self, op: str, result: dict, record: dict) -> None:
        """One operation's arithmetic. An acknowledgement that is not the deed is a failure."""
        if result.get("status") in _SUCCESS[op]:
            self.report[_COUNTER[op]] += 1
        else:
            self.report["failed"] += 1
            self.report["errors"].append({
                "step": _COUNTER[op], "op_id": record["op_id"], "coin": record["coin"],
                "status": result.get("status"), "error": str(result.get("error") or "")[:200]})

    def _refuse(self, op: str, coin: str, market: str, side: str, sequence, reason: str) -> None:
        """An operation outside the authority is refused here, before the venue sees it."""
        op_id = operation_id(self.launch_nonce, coin, market, side, sequence)
        self.report["refused"] += 1
        self.report["operations"] += 1
        self.report["failed"] += 1
        self.report["errors"].append({"step": "refused", "op_id": op_id, "coin": coin,
                                      "error": reason})
        self._append({"kind": OP_RESULT, "disposition": "refused", "op": op, "op_id": op_id,
                      "coin": coin, "market": market, "side": side,
                      "sequence": str(sequence),
                      "result": {"status": "refused", "error": reason}})

    def _lookup(self, op_id: str) -> dict:
        """What the venue says about an operation this diary submitted and never heard about."""
        lookup = getattr(self.exchange, "lookup", None)
        if lookup is None:
            return {"status": "unknown", "error": "the venue cannot be asked"}
        try:
            return _plain(lookup(op_id))
        except Exception as exc:  # noqa: BLE001
            return {"status": "unknown", "error": type(exc).__name__}

    # ---- reads

    def _read(self, name: str, call):
        """One venue read, or a ledgered failure and no invented facts in its place."""
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            self.report["failed"] += 1
            self.report["errors"].append({"step": name, "error": type(exc).__name__})
            self._append({"kind": STEP, "step": "read_failed", "read": name,
                          "error": type(exc).__name__})
            return None

    def _value_micro(self, pair: str, base: str, size, mids: dict):
        mid = mids.get(pair) if mids else None
        if mid is None and mids:
            mid = mids.get(base)
        amount, price = _decimal(size), _decimal(mid)
        if amount is None or price is None:
            return None
        return usd_to_micro(amount * price, rounding="nearest")

    # ---- the three passes and the reconciliation

    def run(self) -> dict:
        """Empty the account as far as the venue allows, then say what is actually left.

        Up to ``ROUNDS_PER_KILL`` rounds: each re-reads the venue, and every round
        after the first touches only the targets whose attempt the venue
        definitively refused or only partly did, under that target's next identity.
        """
        for round_ in range(ROUNDS_PER_KILL):
            only = None if round_ == 0 else set(self._owed)
            if only is not None and not only:
                break
            self._owed = set()
            self._pass(only)

        self._reconcile()
        self._append({"kind": STEP, "step": "summary",
                      **{k: v for k, v in self.report.items()
                         if k not in ("errors", "operation_log", "residual")}})
        return self.report

    def _pass(self, only: set | None) -> None:
        """One read of the venue and one attempt on each target it shows (or each owed one)."""
        def wanted(op, coin, market, side, target) -> bool:
            return only is None or (op, coin, market, side, str(target)) in only

        resting = self._read("open_orders", lambda: self.exchange.open_orders()) or []
        for order in resting:
            coin = str(order.get("coin"))
            oid = str(order.get("order_id"))
            market = str(order.get("market") or ("spot" if "/" in coin else "perp"))
            side = str(order.get("side") or "cancel")
            if not wanted("cancel", coin, market, side, oid):
                continue
            self._operate("cancel", coin, market, side, oid, {"order_id": oid},
                          lambda op_id, oid=oid, coin=coin: self.exchange.cancel(
                              oid, coin=coin, client_id=op_id))

        account = self._read("account", lambda: self.exchange.account())
        mids = self._read("mids", lambda: self.exchange.mids()) or {}
        if account is not None:
            self._close_positions(account, mids, wanted)
            self._sell_balances(account, mids, wanted)

    def _minimum_micro(self, coin: str, market: str) -> int:
        """The venue's smallest order value for ``coin``, in micro-USD; 0 when it states none.

        Hyperliquid refuses any order worth less than $10, so a residual below that
        cannot be sold at all: it is dust by the venue's rule whatever ``dust_micro``
        says, and is reported as such rather than left pending forever.
        """
        if self._minimums is None:
            self._minimums = {}
            try:
                listing = self.exchange.instruments()
                for kind in ("perp", "spot"):
                    for row in listing.get(kind, ()) or ():
                        value = _decimal(row.get("min_order_value_usd"))
                        if value is not None and value > 0:
                            self._minimums[(kind, str(row.get("coin")))] = usd_to_micro(
                                value, rounding="nearest")
            except Exception:  # noqa: BLE001 - a venue that states no minimum has none here
                pass
        return self._minimums.get((market, coin), 0)

    def _close_positions(self, account, mids: dict | None = None, wanted=None) -> None:
        for position in getattr(account, "positions", ()) or ():
            size = _decimal(getattr(position, "size", None))
            coin = str(getattr(position, "coin", ""))
            if size is None or not size:
                continue
            side = "sell" if size > 0 else "buy"
            if wanted is not None and not wanted("close", coin, "perp", side, 0):
                continue
            if not _reduces(side, size):  # pragma: no cover - the side is derived from the sign
                self._refuse("close", coin, "perp", side, 0, "would not reduce the position")
                continue
            # A position below the venue's order minimum is tried once (a reduce-only
            # close may still be admitted) and never retried: a refusal is final.
            value = self._value_micro(coin, coin, abs(size), mids or {})
            minimum = self._minimum_micro(coin, "perp")
            self._operate("close", coin, "perp", side, 0, {"size": str(size)},
                          lambda op_id, coin=coin: self.exchange.close(coin, client_id=op_id),
                          retry=value is None or value >= minimum)

    def _dust_reason(self, pair: str, value: int | None, market: str = "spot") -> str | None:
        """Why a residual is dust -- the precommitted bound or the venue's minimum -- or None."""
        if value is None:
            return None  # an unpriceable balance is exposure of unknown size, never dust
        if value <= self.dust_micro:
            return "within_precommitted_bound"
        if value < self._minimum_micro(pair, market):
            return "below_venue_minimum"
        return None

    def _sell_balances(self, account, mids: dict, wanted=None) -> None:
        for balance in getattr(account, "spot_balances", ()) or ():
            base = str(getattr(balance, "coin", ""))
            total = _decimal(getattr(balance, "total", None))
            if base == "USDC" or total is None or total <= 0:
                continue
            pair = f"{base}/USDC"
            if wanted is not None and not wanted("sell", pair, "spot", "sell", 0):
                continue
            value = self._value_micro(pair, base, total, mids)
            reason = self._dust_reason(pair, value)
            if reason is not None:
                # Dust is left where it is: selling it costs more than it is worth, or
                # the venue would refuse an order that small. Either way it is a
                # state the report names, not a leak and not a pending exposure.
                self._append({"kind": STEP, "step": "dust", "coin": pair,
                              "size": str(total), "value_micro": value, "reason": reason})
                continue
            self._operate("sell", pair, "spot", "sell", 0,
                          {"size": str(total), "value_micro": value},
                          lambda op_id, pair=pair, total=total: self.exchange.close(
                              pair, total, client_id=op_id, market="spot"))

    def _reconcile(self) -> None:
        """Read the account once more and write what is left. An acknowledgement is not flat."""
        residual: dict = {"resting": [], "positions": [], "balances": [], "dust": []}
        unreadable = []
        try:
            resting = list(self.exchange.open_orders())
        except Exception as exc:  # noqa: BLE001
            resting, unreadable = [], [*unreadable, ("open_orders", type(exc).__name__)]
        try:
            account = self.exchange.account()
            if getattr(account, "stale", False):
                # An older snapshot cannot prove the account flat now.
                account, unreadable = None, [*unreadable, ("account", "StaleAccount")]
        except Exception as exc:  # noqa: BLE001
            account, unreadable = None, [*unreadable, ("account", type(exc).__name__)]
        try:
            mids = dict(self.exchange.mids())
        except Exception as exc:  # noqa: BLE001
            mids, unreadable = {}, [*unreadable, ("mids", type(exc).__name__)]

        residual["resting"] = [{"order_id": str(o.get("order_id")), "coin": str(o.get("coin"))}
                               for o in resting]
        if account is not None:
            for position in getattr(account, "positions", ()) or ():
                size = _decimal(getattr(position, "size", None))
                if size:
                    coin = str(getattr(position, "coin", ""))
                    value = self._value_micro(coin, coin, abs(size), mids)
                    if (value is not None
                            and value < self._minimum_micro(coin, "perp")):
                        # The venue will take no order this small: reported, not pending.
                        residual["dust"].append({"coin": coin, "size": str(size),
                                                 "value_micro": value, "market": "perp",
                                                 "reason": "below_venue_minimum"})
                        continue
                    residual["positions"].append({"coin": coin, "size": str(size)})
            for balance in getattr(account, "spot_balances", ()) or ():
                base = str(getattr(balance, "coin", ""))
                total = _decimal(getattr(balance, "total", None))
                if base == "USDC" or total is None or total <= 0:
                    continue
                pair = f"{base}/USDC"
                value = self._value_micro(pair, base, total, mids)
                row = {"coin": pair, "size": str(total), "value_micro": value}
                reason = self._dust_reason(pair, value)
                if reason is not None:
                    residual["dust"].append({**row, "reason": reason})
                else:
                    # A balance whose value cannot be priced is not dust: it is
                    # exposure of an unknown size, and it is reported as such.
                    residual["balances"].append(row)

        if unreadable or (account is None):
            state = UNKNOWN
        elif residual["resting"] or residual["positions"] or residual["balances"]:
            state = PENDING
        elif residual["dust"]:
            state = DUST
        else:
            state = FLAT
        self.report["exposure_state"] = self.report["exposure_status"] = state
        self.report["residual"] = residual
        self._append({"kind": RECONCILIATION, "production_state": KILLED,
                      "exposure_state": state, "residual": residual,
                      "operations": self.report["operations"],
                      "unreadable": [{"read": read, "error": error}
                                     for read, error in unreadable]})


def execute(exchange, ledger, *, launch_nonce: str | None = None,
            dust_micro: int = 1_000_000, reader=None) -> dict:
    """Run one wind-down against ``exchange``, recording it in ``ledger``. Never raises."""
    return WindDownExecutor(exchange, ledger, launch_nonce=launch_nonce,
                            dust_micro=dust_micro, reader=reader).run()
