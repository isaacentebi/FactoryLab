"""Ledger-first integration of pure lot accounting: every return's consequence account."""

from collections import Counter
from dataclasses import asdict
from fractions import Fraction

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.lots import (
    FEE_UNKNOWN,
    NO_MARK,
    RELEASED_ORDER,
    LotTable,
    Payoff,
    fill_stream,
    instrument_market,
    instrument_streams,
)
from factorylab.settlement.receipts import ExecutionReceipt, ReceiptBook
from factorylab.settlement.vocabulary import _require_event_index


class ReturnConsequences:
    """Every change to attribution, costs, inventory and outcomes has preceding ledger evidence."""

    def __init__(self, ledger: Ledger, backstop: int, *, horizon_ns: int | None = None) -> None:
        _require_event_index(backstop, "backstop", positive=True)
        if horizon_ns is not None:
            _require_event_index(horizon_ns, "horizon_ns", positive=True)
        self.ledger = ledger
        self.backstop = backstop
        # The consequence horizon on the venue's clock (wave 16, D2): when set, a
        # return's backstop is counted in nanoseconds from its opening, not in ticks.
        self.horizon_ns = horizon_ns
        self.table = LotTable()
        self.mids: dict[str, str] = {}
        # Wave 16, D2 (Codex on #152): each open return's mark per instrument, the first
        # venue mid timestamped at or after its horizon, fixed when that mid arrives:
        # never the latest mid cached from an earlier event.
        self.horizon_marks: dict[str, dict[str, str]] = {}
        # Each mark's own fact time (Codex on #152): streams arrive out of fact order
        # (a resolution polled late, a book read on time), so the mark is the EARLIEST
        # price at or after H any stream states, and an outcome is fixed only once
        # every stream it reads is delivered through that instant.
        self.horizon_mark_ns: dict[str, dict[str, int]] = {}
        # Codex on #152 (eaf23e0): the venue time through which every world fact has
        # been delivered, the latest fact this book has seen (its own, and the runtime's
        # previous tick, ``tick_through_ns``). A horizon or a patience passes on it,
        # never on when ``resolve`` runs.
        self.facts_ns: int | None = None
        self.tick_through_ns: int | None = None
        # Codex on #152 (7677eaa): each open return's position-changing facts with their
        # fact times (fills it opened or closed, funding on its lots, redemptions,
        # service income): its lots per instrument after each, and what each realised
        # and earned. Its state at H is derived from these at fix time, applying every
        # fact at or before H, so what arrived when, on which stream, cannot matter.
        self.history: dict[str, list[dict]] = {}
        self.pending_orders: dict[str, dict] = {}
        # R4-C: intents the venue never answered and never will. The hold on
        # consequence resolution is released for them, but the exposure is not
        # forgotten: an order the venue may still be holding can still own a
        # fill, so it keeps its return's attribution alive.
        self.unresolved_orders: dict[str, dict] = {}
        # Outcomes fixed outside ``resolve``, waiting to be handed to the runtime
        # with everything else it fixed this event. A released hold no longer fixes
        # one early (its return resolves in ``resolve``); a checkpoint may carry some.
        self.censored_payoffs: list[Payoff] = []
        self.deferred_events: list[tuple[str, dict, int]] = []
        # §6.A: an execution receipt is a fact about the world — a fill, a
        # refusal — addressable on its own and never confused with an
        # assessment of the decision that caused it.
        self.receipts = ReceiptBook(ledger)

    def _execution(self, kind: str, handle: str, event: int, facts: dict) -> str:
        """Write one execution receipt for a fact this accounting just admitted."""
        return self.receipts.record(
            ExecutionReceipt(kind=kind, handle=handle, owner=None, at_event=event, facts=facts))

    def order_intent(self, client_id: str, handle: str, coin: str) -> None:
        """An unacknowledged order keeps attribution and dependent economic outcomes pending."""
        item = {"handle": handle, "coin": coin}
        self.ledger.append({"kind": "consequence.intent", "client_id": client_id, **item})
        self.pending_orders[client_id] = item

    def order_acknowledged(self, client_id: str) -> list[tuple[str, dict, int]]:
        """Release deferred economic events in original order only after identity is resolved."""
        self.ledger.append({"kind": "consequence.acknowledged", "client_id": client_id})
        # An answer, however late, ends the exposure this intent was carrying.
        self.unresolved_orders.pop(client_id, None)
        return self._release(client_id)

    def release_unresolved(self, client_id: str, event: int,
                           reason: str = "external_unobservable") -> list[tuple[str, dict, int]]:
        """Release a hold no answer will ever lift; the order's portion becomes unknown.

        Rehearsal 5 (PR #105): one order timed out on submit and the venue never
        reported it, so the intent stayed pending and every later return's
        outcome stayed unfixed behind it. The polling is bounded; this is what
        the bound means. The hold is lifted so every later return resolves
        normally, and the intent is kept as unresolved exposure, so an order the
        venue was holding all along can still own its fill and the money that
        fill realises reaches the owner late rather than never.

        Only the unresolved order's portion is unknown (defect 10). The return
        that sent it is not closed here: it resolves on its own schedule, its
        observed fills and open lots accounted like any other's, and its money
        is what those observed orders produced. What cannot be known is whether
        the return paid off, since the missing order could have changed that, so
        its ``return_paid_off`` settles censored with the documented reason -- an
        excluded sample, no standing, an ``unknown`` outcome for its owner --
        unless the venue answers before the return resolves.
        """
        item = self.pending_orders.get(client_id)
        if item is None:
            return []
        self.ledger.append({"kind": "order.unresolved_released", "handle": item["handle"],
                            "coin": item["coin"], "client_id": client_id, "reason": reason})
        self.unresolved_orders[client_id] = {**item, "reason": reason}
        return self._release(client_id)

    def _unknown_portions(self) -> dict[str, str]:
        """Returns with an order nobody observed, and the documented reason for each."""
        return {item["handle"]: item.get("reason", "external_unobservable")
                for item in self.unresolved_orders.values()}

    def _release(self, client_id: str) -> list[tuple[str, dict, int]]:
        """Drop one hold and, if it was the last, replay what it was holding back."""
        self.pending_orders.pop(client_id, None)
        if not self.pending_orders and self.deferred_events:
            events = self.deferred_events
            self.ledger.append({"kind": "consequence.replay", "count": len(events)})
            self.deferred_events = []
            for kind, payload, event in events:
                self.observe(kind, payload, event)
            return events
        return []

    def _tick(self, event: int) -> int:
        """The clock the backstop counts. Here the caller's event index; a runtime that
        keeps world ticks overrides it, so the backstop is counted in ticks."""
        return event

    def _now_ns(self) -> int | None:
        """The venue clock the horizon counts, or None. A runtime overrides it."""
        return None

    def _patience_ns(self) -> int | None:
        """How long after its opening a return waits for its horizon marks, or None
        (it waits for them however long). A runtime overrides it with the named
        trades' patience, ``H`` plus the verdict window (Codex on #152)."""
        return None

    def _exit_rates(self) -> dict[str, str | None] | None:
        """The venue's taker rate per market a mark deducts as the exit fee, or None.

        None marks open lots at the mid alone. A runtime overrides it with the rates
        the venue stated (wave 16, D7)."""
        return None

    def _apply(self, kind: str, evidence: dict, table: LotTable) -> None:
        self.ledger.append({"kind": f"consequence.{kind}", **evidence})
        self.table = table

    def start(self, handle: str, event: int) -> None:
        """Admit the return before any tool can create exposure on its behalf."""
        tick = self._tick(event)
        ns = self._now_ns()
        self._apply("return", {"handle": handle, "event": event, "tick": tick,
                               **({"ns": ns} if ns is not None else {})},
                    self.table.start(handle, event, tick, ns))

    def finish(self, handle: str, cost_micro: int) -> None:
        """Persist the full metered cost before it becomes the payoff threshold."""
        self._apply(
            "cost",
            {"handle": handle, "cost_micro": cost_micro},
            self.table.finish(handle, cost_micro),
        )

    def void(self, handle: str, event: int) -> None:
        """Close an admitted return that authored nothing without opening a consequence.

        A router draw that chose nobody is not a producer return: it owes no
        ``return_paid_off``, so none is resolved, none is sealed against it and
        no seat is ever addressed with the outcome of a decision it never made.
        """
        self._apply("void", {"handle": handle, "event": event}, self.table.void(handle))

    def bind_service(self, service: str, handle: str, event: int) -> bool:
        """Bind a registered service to the return that registered it (C11); report
        whether the return has an account to bind to."""
        try:
            table = self.table.bind_service(service, handle)
        except ValueError:
            return False
        self._apply("service", {"service": service, "handle": handle, "event": event}, table)
        return True

    def income(self, service: str, micro: int, event: int) -> str | None:
        """Credit a settled service receipt to the registering return's open outcome.

        Returns the handle credited, or None when the service is unbound or its
        return's outcome is already fixed: the receipt is still the seller's
        money, it just no longer changes a score that was published.
        """
        handle = self.table.service_return(service)
        if handle is None or not self.account_open(handle):
            return None
        table = self.table.income(service, micro)
        # Income is a fact at the runtime's instant: after a return's H, late money.
        self._record_effects(self._now_ns(), None, self.table, table)
        self._apply("income", {"service": service, "handle": handle, "micro": micro,
                               "event": event}, table)
        return handle

    def settle_late(self, event: int) -> dict[str, int]:
        """Ledger and hand back realised P&L that arrived after an outcome was fixed.

        The score of a fixed outcome never changes; the money does. Each handle's
        signed amount is ledgered as ``consequence.late`` before the table moves on.
        """
        table, late = self.table.late_realizations()
        if not late:
            return {}
        for handle, micro in late.items():
            self.ledger.append({"kind": "consequence.late", "handle": handle, "micro": micro,
                                "event": event})
        self.table = table
        return late

    def account_open(self, handle: str) -> bool:
        """Only a return admitted here and not yet resolved may create venue exposure."""
        try:
            account = self.table.account(handle)
        except KeyError:
            return False
        return account.payoff is None and not account.voided

    def order_result(self, handle: str, result: dict, args: dict, event: int, *,
                     coin: str | None = None) -> None:
        """Attribute accepted market, limit and close orders before processing their fills.

        ``coin`` (else the order's ``args["coin"]``) is the instrument it was placed on,
        kept with the order: a resting order waits on its own venue's fill stream only.
        """
        if result.get("status") not in ("filled", "resting") or result.get("order_id") is None:
            return
        size = args.get("size") if result["status"] == "resting" else result.get("filled_size")
        oid = str(result["order_id"])
        existing = self.table.order_owner(oid)
        if existing is not None:
            if existing != handle:
                raise ValueError("order already belongs to another decision")
            return
        if not self.account_open(handle) and handle not in self._unresolved_handles():
            reason = "no open consequence account"
            self.ledger.append({"kind": "consequence.refused", "handle": handle,
                                "order_id": oid, "reason": reason})
            self._execution("refusal", handle, event, {"order_id": oid, "reason": reason})
            return
        coin = coin if coin is not None else args.get("coin")
        self._apply(
            "order",
            {"handle": handle, "order_id": oid, "size": str(size), "event": event,
             **({"coin": str(coin)} if coin is not None else {})},
            self.table.order(oid, handle, str(size),
                             coin=None if coin is None else str(coin)),
        )

    def _unresolved_handles(self) -> set[str]:
        """Returns whose censored outcome still has an intent the venue may answer.

        Their accounts are closed, so nothing resolves against them again, but an
        order the venue finally admits to holding is still theirs: it may be
        attributed, it may fill, and what it realises is theirs, late.
        """
        return {item["handle"] for item in self.unresolved_orders.values()}

    def cancel(self, order_id: str, event: int) -> None:
        """Release only the unfilled liability of an acknowledged cancellation or rejection."""
        self._apply("cancel", {"order_id": order_id, "event": event}, self.table.cancel(order_id))

    def confirm_terminal(self, order_id: str, status: str, filled: str, event: int) -> None:
        """Record, with its evidence first, the venue's own word that an order is terminal.

        Wave 17b: only an order its venue confirmed filled, cancelled or rejected, by
        reading back its own order status, can no longer fill; until then its
        account is never released (``LotTable.closed``). An order this book does not
        hold, or holds confirmed already, writes nothing.
        """
        order = next((o for o in self.table.orders if o.order_id == order_id), None)
        if order is None or order.confirmed is not None:
            return
        self._apply("terminal", {"order_id": order_id, "handle": order.handle,
                                 "status": status, "filled": str(filled), "event": event},
                    self.table.confirm(order_id, str(filled)))

    def observe(self, kind: str, payload: dict, event: int) -> None:
        """Only observed fills and signed funding payments change lot economics."""
        if self.pending_orders and kind in ("Fill", "Funding"):
            self.ledger.append({"kind": "consequence.deferred", "event_kind": kind,
                                "payload": dict(payload), "event": event})
            self.deferred_events.append((kind, dict(payload), event))
            return
        if kind == "MarketMid":
            self.ledger.append({"kind": "consequence.mid", "event": event, **payload})
            self.mids[payload["coin"]] = str(payload["mid"])
            ts = payload.get("ts_ns", self._now_ns())
            if ts is not None:
                self._mark_horizons(str(payload["coin"]), int(ts), str(payload["mid"]))
                self._saw_fact(int(ts))
        elif kind == "Fill":
            at = payload.get("ts_ns", self._now_ns())
            if at is not None:
                self._saw_fact(int(at))
            try:
                table = self.table.fill(
                    order_id=str(payload["order_id"]),
                    coin=payload["coin"],
                    is_buy=payload["is_buy"],
                    size=str(payload.get("inventory_size", payload["size"])),
                    px=str(payload["px"]),
                    fee_usd=str(payload["fee_usd"]),
                    liquidation=payload.get("liquidation", False),
                    market=payload.get("market", "perp"),
                    order_size=str(payload["size"]),
                )
            except ValueError as exc:
                if "exceeds the order" in str(exc):
                    # An execution beyond what the order ordered is an inconsistency
                    # between the venue's report and the order it answers. It is
                    # quarantined with its evidence, attributed to nobody, and moves
                    # no lot; the order's consistent fills remain its owner's.
                    self.ledger.append({"kind": "consequence.quarantined", "event": event,
                                        "order_id": str(payload["order_id"]),
                                        "reason": str(exc), "payload": dict(payload)})
                    return
                if "open consequence account" not in str(exc):
                    raise
                # A fill nobody with an account ordered never enters the shared FIFO.
                self.ledger.append({"kind": "consequence.refused", "event": event,
                                    "order_id": str(payload["order_id"]), "reason": str(exc)})
                return
            released = next((row[1] for row in self.table.released_orders
                             if row[0] == str(payload["order_id"])), None)
            if released is not None:
                # A fill on an order the venue had confirmed terminal, whose account was
                # then released (wave 17b): a venue error, and real money. It is its
                # owner's late realization (``LotTable.fill``), booked, never graded.
                self.ledger.append({"kind": "consequence.released_fill", "event": event,
                                    "order_id": str(payload["order_id"]), "handle": released,
                                    "reason": RELEASED_ORDER})
            # What the fill did to every open return, at its own fact time.
            self._record_effects(at, str(payload["coin"]), self.table, table)
            self._apply("fill", {"event": event, "payload": dict(payload)}, table)
            order = next((o for o in table.orders if o.order_id == str(payload["order_id"])), None)
            handle = order.handle if order is not None else self.table.service_return(
                str(payload["order_id"]))
            if handle is not None:
                self._execution("fill", handle, event, {
                    "order_id": str(payload["order_id"]), "coin": payload["coin"],
                    "is_buy": payload["is_buy"], "size": str(payload["size"]),
                    "px": str(payload["px"]), "fee_usd": str(payload["fee_usd"]),
                    "market": payload.get("market", "perp"),
                    "liquidation": bool(payload.get("liquidation", False)),
                })
        elif kind == "Funding" and payload.get("paid_usd") is not None:
            at = payload.get("ts_ns", self._now_ns())
            if at is not None:
                self._saw_fact(int(at))
            table = self.table.funding(payload["coin"], str(payload["paid_usd"]))
            # Funding at its funding time: charged to the lots of the returns holding
            # them then; a funding time after a return's H is late money (R10-m).
            self._record_effects(at, str(payload["coin"]), self.table, table)
            self._apply("funding", {"event": event, "payload": dict(payload)}, table)
        elif kind == "OrderRejected" and payload.get("order_id") is not None:
            self.cancel(str(payload["order_id"]), event)

    def redeem(self, coin: str, payout: str, event: int, facts: dict, *,
               at_ns: int | None = None) -> dict[str, int]:
        """Close every event lot of ``coin`` at its market's resolution, with evidence first.

        Guarantees the resolution is ledgered before any lot moves, and that each
        decision that held the token is given one ``resolution`` execution receipt
        naming what it held, the payout and what that realised: a resolution is a
        fact about the world, addressed to the decisions it settled. Returns the
        signed micro-USD realised per handle, floored once. ``at_ns`` is the
        resolution's own fact time: what it did to each return is recorded at it
        (``_record_effects``), so a resolution after a return's H is late money for it,
        never its grade (Codex on #152).
        """
        if at_ns is not None:
            # The resolution is the token's price at its instant: the first price at
            # or after a horizon when no book read came before it.
            self._mark_horizons(coin, int(at_ns), str(payout))
            self._saw_fact(int(at_ns))
        table, credited = self.table.redeem(coin, payout)
        if table is self.table:
            return {}
        self._record_effects(at_ns if at_ns is not None else self._now_ns(), coin,
                             self.table, table)
        self._apply("resolution", {"coin": coin, "payout": str(payout), "event": event,
                                   **facts}, table)
        realized = {}
        for handle, net in credited.items():
            realized[handle] = net.numerator // net.denominator
            self._execution("resolution", handle, event, {
                **facts, "coin": coin, "payout": str(payout),
                "realized_micro": realized[handle]})
        return realized

    def _saw_fact(self, at_ns: int) -> None:
        """Raise the venue time this book has seen facts through to ``at_ns``."""
        self.facts_ns = at_ns if self.facts_ns is None else max(self.facts_ns, at_ns)

    def _through_ns(self) -> int | float | None:
        """The venue time every world fact has been delivered through, inclusive.

        Guarantees a value ``C`` such that no fact with fact-time at or before ``C``
        is still to come: the instant before the latest fact seen (facts at that very
        instant may still be in flight), or the runtime's previous tick, whichever is
        later (the clock only when neither is known), and never after the venue's own
        delivered-through instant of the streams an outcome reads (mids, fills,
        funding; ``_stream_through_ns``, ruling R10-o): a polled venue can report a
        fact after a later tick, so the tick is only an upper bound. A horizon has
        passed once ``C`` reaches it.
        """
        known = [v for v in (None if self.facts_ns is None else self.facts_ns - 1,
                             self.tick_through_ns) if v is not None]
        through = max(known) if known else self._now_ns()
        venue = self._stream_through_ns()
        if venue is not None and through is not None:
            through = min(through, venue)
        return through

    def _stream_through_ns(self) -> int | float | None:
        """The earliest delivered-through instant of the venue streams an outcome reads,
        or None when the venue states none (a runtime overrides it; ruling R10-o)."""
        return None

    def _stream_watermark(self, stream: str) -> int | float | None:
        """The instant one fact stream (``lots.FACT_STREAMS``) is delivered through,
        or None when the runtime states none for it (a runtime overrides it)."""
        return None

    def _through_for(self, handle: str, base: int | float | None) -> int | float | None:
        """The instant every fact the return ``handle`` reads has been delivered
        through: ``base`` (the facts seen and the ticks), never after the watermark of
        any stream it still needs (``_stream_needs``; Codex on #152).

        Guarantees each open consequence waits on exactly its own instruments'
        streams, each only as far as it needs it: a Polymarket book that fails to read
        holds the event lots on it, never an unrelated perp's outcome; and a stream a
        return needs only up to the instant it became flat in that instrument, once
        delivered through that instant, holds nothing, so a funding poll that stops
        after a position was closed never holds the return that closed it.
        """
        stated, unstated = [], False
        for stream, until in sorted(self._stream_needs(handle).items()):
            mark = self._stream_watermark(stream)
            if mark is None:
                unstated = True
            elif until is None or mark < until:
                stated.append(mark)
            # else: delivered through all this return needs of it; it holds nothing.
        if stated and not unstated:
            # Every stream it still waits on states its own watermark: they alone say
            # how far its facts are delivered (the ticks and facts seen are a proxy).
            return min(stated)
        if base is None or not stated:
            return base
        return min(base, *stated)

    def _stream_needs(self, handle: str) -> dict[str, int | None]:
        """Each fact stream the return ``handle`` reads, and the fact time through which
        it needs it: None for through its horizon H.

        Guarantees, per instrument (Codex on #152), derived from the return's recorded
        facts as its state at H is (``_states_at_horizon``), never from a snapshot:
        through H for an instrument it holds at H or has an order resting in (its
        mids mark it at H, its funding accrues to H, and its fills may still come);
        through ``t_flat``, the fact time at which it became flat in it, for one it
        held before H and not at H with no order resting in it (nothing after that
        instant changes what H grades); and nothing for one it first held after H
        (late money). A resting order's own fill stream is needed through H. A return
        with no venue clock needs every stream of everything it holds or held through
        its horizon, as the table stands.
        """
        account = next((a for a in self.table.returns if a.handle == handle), None)
        horizon = (account.opened_at_ns + self.horizon_ns
                   if account is not None and account.opened_at_ns is not None
                   and self.horizon_ns is not None else None)
        resting = [order for order in self.table.orders
                   if order.handle == handle and order.remaining]
        rested = {order.coin for order in resting if order.coin is not None}
        needs: dict[str, int | None] = {}

        def need(stream: str, until: int | None) -> None:
            if stream in needs and (needs[stream] is None or until is None):
                needs[stream] = None
            else:
                needs[stream] = until if stream not in needs else max(needs[stream], until)

        held = self._instruments_of(handle)
        for coin, market in held:
            until = None
            if horizon is not None and coin not in rested:
                until = self._flat_at_horizon(handle, coin, horizon)
                if until is False:
                    continue  # first held after H: late money, nothing to wait on
            for stream in instrument_streams(coin, market):
                need(stream, until)
        for order in resting:
            if order.coin is not None:
                # A resting order fills on its own venue's feed only (Codex on #152),
                # and may yet open a position graded at H.
                market = instrument_market(order.coin)
                for stream in (*instrument_streams(order.coin, market), fill_stream(market)):
                    need(stream, None)
            else:
                # An order bound before instruments were recorded: the venues of the
                # instruments its return holds, else every venue's feed.
                held_markets = {market for _coin, market in held}
                for stream in ({fill_stream(m) for m in held_markets} if held_markets
                               else {"hl:fills", "pm:events"}):
                    need(stream, None)
        return needs

    def _flat_at_horizon(self, handle: str, coin: str, horizon: int) -> int | None | bool:
        """When the return ``handle`` became flat in ``coin`` for good before its
        horizon: the fact time of its last recorded change in ``coin`` at or before H
        when that change left it holding nothing; None when it holds ``coin`` at H;
        False when it held ``coin`` only after H."""
        changes = [entry for entry in self.history.get(handle, ())
                   if entry.get("coin") == coin and "lots" in entry]
        if not changes:
            return None  # held with no recorded fact: as the table stands, through H
        before = [entry for entry in changes if entry["ns"] <= horizon]
        if not before:
            return False
        # The latest in fact time (arrival order among equal times): the state at H.
        last = sorted(before, key=lambda entry: entry["ns"])[-1]
        return None if last["lots"] else int(last["ns"])

    def _record_effects(self, at_ns: int | None, coin: str | None, before: LotTable,
                        after: LotTable) -> None:
        """Record, at ``at_ns``, what one fact did to each open return.

        Guarantees every open return the fact changed gets one history entry: its lots
        on ``coin`` after the fact (when they changed), and the money it realised and
        earned by it. A return's facts on one instrument arrive in the venue's order,
        which is their fact-time order; across instruments their order is irrelevant.
        Nothing is recorded for a return with no venue clock (its outcome is fixed on
        ticks, from the table as it stands).
        """
        if at_ns is None or self.horizon_ns is None:
            return
        previous = {account.handle: account for account in before.returns}
        for account in after.returns:
            if (account.payoff is not None or account.voided
                    or account.opened_at_ns is None):
                continue
            prior = previous.get(account.handle)
            entry: dict = {}
            if coin is not None:
                was = [lot for lot in before.lots if lot.handle == account.handle
                       and lot.coin == coin]
                now = [lot for lot in after.lots if lot.handle == account.handle
                       and lot.coin == coin]
                if was != now:
                    entry["lots"] = now
            realized = account.realized_micro - (prior.realized_micro if prior else 0)
            earned = account.earned_micro - (prior.earned_micro if prior else 0)
            if realized:
                entry["realized"] = realized
            if earned:
                entry["earned"] = earned
            if entry:
                self.history.setdefault(account.handle, []).append(
                    {"ns": int(at_ns), "coin": coin, **entry})

    def _states_at_horizon(self) -> dict[str, dict]:
        """Every open return's economics at its horizon, derived from its history.

        Guarantees each is what applying every fact of the return with fact-time at or
        before H, in fact-time order, gives: per instrument, its lots after the last
        such fact; the money those facts realised and earned. A fact after H is late
        money by definition, whenever and on whatever stream it arrived (Codex on
        #152). A return with no venue clock is absent (graded from the table).
        """
        states: dict[str, dict] = {}
        if self.horizon_ns is None:
            return states
        for account in self.table.returns:
            if (account.payoff is not None or account.voided
                    or account.opened_at_ns is None):
                continue
            horizon = account.opened_at_ns + self.horizon_ns
            lots: dict[str, list] = {}
            realized, earned = Fraction(0), 0
            for entry in self.history.get(account.handle, ()):
                if entry["ns"] > horizon:
                    continue
                if "lots" in entry:
                    lots[entry["coin"]] = entry["lots"]
                realized += entry.get("realized", 0)
                earned += entry.get("earned", 0)
            states[account.handle] = {"lots": [lot for held in lots.values() for lot in held],
                                      "realized": realized, "set_aside": Fraction(0),
                                      "earned": earned}
        return states

    def _instruments_of(self, handle: str) -> set[tuple[str, str]]:
        """Every (instrument, market) a return holds now or held at any recorded fact."""
        held = {(lot.coin, lot.market) for lot in self.table.lots if lot.handle == handle}
        for entry in self.history.get(handle, ()):
            held |= {(lot.coin, lot.market) for lot in entry.get("lots", ())}
        return held

    def graded_instruments(self) -> set[tuple[str, str]]:
        """Every (instrument, market) an open return holds or held: each needs its mark."""
        held = {(lot.coin, lot.market) for lot in self.table.lots}
        for entries in self.history.values():
            for entry in entries:
                held |= {(lot.coin, lot.market) for lot in entry.get("lots", ())}
        return held

    def _mark_horizons(self, coin: str, ts_ns: int, mid: str) -> None:
        """Fix ``mid`` as the horizon mark of ``coin`` for every open return whose
        horizon it reaches (``ts_ns`` at or after its opening plus ``horizon_ns``) and
        that has no mark of ``coin`` yet: the first venue mid at or after its horizon.

        Guarantees the mark does not depend on the order of events within a batch
        (Codex on #152): a return is marked whether or not it holds ``coin`` yet, so
        a resting order filled at H, whose Fill a venue emits after MarketMid(H) in
        the same batch, inherits MarketMid(H). A fill whose fact-time is after H is
        late money and never enters the graded outcome (``_states_at_horizon``).
        """
        if self.horizon_ns is None:
            return
        for account in self.table.returns:
            if (account.payoff is None and not account.voided
                    and account.opened_at_ns is not None
                    and ts_ns >= account.opened_at_ns + self.horizon_ns):
                seen = self.horizon_mark_ns.get(account.handle, {}).get(coin)
                if seen is None or ts_ns < seen:
                    self.horizon_marks.setdefault(account.handle, {})[coin] = mid
                    self.horizon_mark_ns.setdefault(account.handle, {})[coin] = int(ts_ns)

    def resolve(self, event: int) -> list[Payoff]:
        """Persist all newly fixed outcomes before publishing the successor accounting state."""
        # An outcome censored for documented unobservability was fixed the moment
        # its hold was released; it is handed over here with everything else.
        fixed, self.censored_payoffs = self.censored_payoffs, []
        if self.pending_orders:
            for payoff in fixed:
                for kept in (self.horizon_marks, self.horizon_mark_ns, self.history):
                    kept.pop(payoff.handle, None)
            return fixed  # Unknown inventory ownership cannot manufacture a no-fill outcome.
        through = self._through_ns()
        by_handle = {account.handle: self._through_for(account.handle, through)
                     for account in self.table.returns
                     if account.payoff is None and not account.voided}
        table = self.table.resolve(event, self.backstop, self.mids,
                                   censored=self._unknown_portions(), tick=self._tick(event),
                                   now_ns=self._now_ns(), through_ns=through,
                                   through_by_handle=by_handle,
                                   horizon_ns=self.horizon_ns,
                                   horizon_state=self._states_at_horizon(),
                                   exit_rates=self._exit_rates(),
                                   horizon_marks=self.horizon_marks,
                                   horizon_mark_ns=self.horizon_mark_ns,
                                   patience_ns=self._patience_ns())
        for before, after in zip(self.table.returns, table.returns, strict=True):
            if before.payoff is None and after.payoff is not None:
                self.ledger.append({"kind": "consequence.outcome", **asdict(after.payoff)})
                if after.payoff.censored == FEE_UNKNOWN:
                    # Ruling R10-i: fixed at its horizon, and uninformative: the venue
                    # never stated the rate its lots exit at by then.
                    self.ledger.append({"kind": "consequence.uninformative",
                                        "handle": after.payoff.handle,
                                        "reason": FEE_UNKNOWN})
                elif after.payoff.censored == NO_MARK:
                    # Fixed a patience past its opening, uninformative: the venue never
                    # priced these instruments at or after its horizon by then.
                    marked = self.horizon_marks.get(after.payoff.handle, {})
                    graded = self._states_at_horizon().get(after.payoff.handle)
                    lots = (graded["lots"] if graded is not None else
                            [lot for lot in self.table.lots
                             if lot.handle == after.payoff.handle])
                    held = sorted({lot.coin for lot in lots if lot.coin not in marked})
                    self.ledger.append({"kind": "consequence.uninformative",
                                        "handle": after.payoff.handle,
                                        "reason": NO_MARK, "instruments": held})
                fixed.append(after.payoff)
        self.table = table
        # A horizon mark is pinned by its return's open outcome: fixed or voided, no
        # reader remains.
        for kept in (self.horizon_marks, self.horizon_mark_ns, self.history):
            for handle in [h for h in kept if not self.account_open(h)]:
                del kept[handle]
        return fixed

    def payoff(self, handle: str) -> Payoff | None:
        """Return the fixed economic outcome, or None while a known return remains open."""
        return self.table.account(handle).payoff

    def counts(self) -> dict[str, int]:
        """Count returns once, independent of how many evaluators judged each return.

        Released accounts (wave 17b) are counted from the table's own counts, so a
        release never changes an answer here.
        """
        every = [r.payoff for r in self.table.returns if r.payoff is not None]
        # A censored outcome answers nothing, so it is neither a payoff nor a
        # failure to pay off; it is counted only as what it is.
        outcomes = [p for p in every if p.censored is None]
        released = self.table.released_counts()
        return {
            "paid_off": sum(p.y for p in outcomes) + released["paid_off"],
            "not_paid_off": sum(1 - p.y for p in outcomes) + released["not_paid_off"],
            "marked": sum(p.marked for p in outcomes) + released["marked"],
            "censored_outcomes": (sum(p.censored is not None for p in every)
                                  + released["censored_outcomes"]),
            # A voided return is not pending: it owes no outcome at all. Nor is a
            # released one: only a closed account is ever released.
            "consequences_pending": sum(
                r.payoff is None and not r.voided for r in self.table.returns),
            "lots_opened": sum(r.opened_lots for r in self.table.returns)
            + released["lots_opened"],
            "lots_closed": sum(r.closed_lots for r in self.table.returns)
            + released["lots_closed"],
            "closes_credited": sum(r.closes for r in self.table.returns)
            + released["closes_credited"],
        }

    def releasable(self, handle: str) -> bool:
        """Whether ``handle``'s account is closed and nothing held here still names it.

        Guarantees False while its account is open, owns a lot, or has an order its
        venue has not confirmed terminal (``LotTable.closed``), while an intent it
        sent is unacknowledged (``pending_orders``) or unresolved
        (``unresolved_orders``: the venue may still admit to an order that is its),
        and while any fill or funding payment waits in ``deferred_events`` (whose
        order's owner is not known yet). A handle with no account at all is
        releasable here: nothing here owes it.
        """
        if self.deferred_events:
            return False
        if any(item.get("handle") == handle for item in self.pending_orders.values()):
            return False
        if any(item.get("handle") == handle for item in self.unresolved_orders.values()):
            return False
        try:
            self.table.account(handle)
        except KeyError:
            return True
        return self.table.closed(handle)

    def release(self, handles, mark: int, *, authors=None) -> None:
        """Release closed accounts into the table's counts.

        Guarantees every handle is ``releasable`` (otherwise ``ValueError`` and
        nothing changes). Every fact a released account holds is already ledgered
        (its admission, cost, orders, fills and outcome), and a release changes no
        attribution: its orders keep their owner (``LotTable.order_owner``) and the
        seat that authored it (``authors``), so no new evidence precedes it. Handles
        with no account are skipped.
        """
        owned = [h for h in dict.fromkeys(handles) if self._has_account(h)]
        if not owned:
            return
        for handle in owned:
            if not self.releasable(handle):
                raise ValueError(f"account {handle} is not closed and cannot be released")
        self.table = self.table.release(owned, mark, authors=authors)

    def _has_account(self, handle: str) -> bool:
        try:
            self.table.account(handle)
        except KeyError:
            return False
        return True

    def forget_released_orders(self, before: int) -> None:
        """Forget released orders marked before ``before`` (``LotTable.forget_released_orders``)."""
        self.table = self.table.forget_released_orders(before)


class FillCursor:
    """Inclusive fill polls preserve partial fills and repeated identical executions once each.

    The exchange protocol has no execution id. Timestamp plus all Fill fields and
    their multiplicity distinguish observations; only the latest timestamp's
    counts need retaining because the next poll includes that timestamp.
    """

    def __init__(self, ledger: Ledger, *, start_ns: int) -> None:
        """Exclude pre-launch executions, persisting the initial inclusive boundary."""
        if type(start_ns) is not int or start_ns < 0:
            raise ValueError("start_ns must be nonnegative integer nanoseconds")
        ledger.append({"kind": "consequence.fill_cursor", "since_ns": start_ns, "seen": []})
        self.ledger = ledger
        self.since_ns = start_ns
        self.seen: dict[tuple, int] = {}
        # Ruling R10-o: the request time of the latest successful fills read, the
        # instant every execution at or before it has been delivered through.
        self.through_ns: int | None = None

    def poll(self, exchange, *, strict: bool = False,
             now_ns: int | None = None) -> list[tuple[int, dict]]:
        """Return unseen executions in timestamp order, persisting the cursor before advance.

        Guarantees each execution's payload states its own venue time (``fill_ns``),
        and that a successful read made at ``now_ns`` advances ``through_ns`` to it; a
        failed read advances nothing (ruling R10-o).
        """
        try:
            fills = exchange.fills(self.since_ns)
        except RuntimeError:  # read-only venue without an account
            if strict:
                raise
            return []
        if now_ns is not None:
            self.through_ns = now_ns if self.through_ns is None else max(self.through_ns,
                                                                          now_ns)
        counts = Counter()
        result = []
        for fill in sorted(fills, key=lambda f: f.ts_ns):
            if fill.ts_ns < self.since_ns:
                continue
            payload = {
                "order_id": fill.order_id,
                "coin": fill.coin,
                "is_buy": fill.is_buy,
                "size": str(fill.size),
                "px": str(fill.px),
                "fee_usd": str(fill.fee),
                "realized_usd": str(fill.realized),
                "liquidation": fill.liquidation,
                "market": getattr(fill, "market", "perp"),
                "inventory_size": str(getattr(fill, "inventory_size", None) or fill.size),
            }
            key = (fill.ts_ns, *payload.values())
            counts[key] += 1
            if counts[key] > self.seen.get(key, 0):
                # Its own venue time, outside the cursor's identity key (R10-o).
                result.append((fill.ts_ns, {**payload, "fill_ns": fill.ts_ns}))
        if result:
            latest = max(ts for ts, _ in result)
            seen = {key: count for key, count in counts.items() if key[0] == latest}
            self.ledger.append(
                {
                    "kind": "consequence.fill_cursor",
                    "since_ns": latest,
                    "seen": [[list(key), count] for key, count in seen.items()],
                }
            )
            self.since_ns, self.seen = latest, seen
        return result
