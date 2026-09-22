"""Ledger-first integration of pure lot accounting: every return's consequence account."""

from collections import Counter
from dataclasses import asdict

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.lots import LotTable, Payoff
from factorylab.settlement.receipts import ExecutionReceipt, ReceiptBook
from factorylab.settlement.vocabulary import _require_event_index


class ReturnConsequences:
    """Every change to attribution, costs, inventory and outcomes has preceding ledger evidence."""

    def __init__(self, ledger: Ledger, backstop: int) -> None:
        _require_event_index(backstop, "backstop", positive=True)
        self.ledger = ledger
        self.backstop = backstop
        self.table = LotTable()
        self.mids: dict[str, str] = {}
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

    def _apply(self, kind: str, evidence: dict, table: LotTable) -> None:
        self.ledger.append({"kind": f"consequence.{kind}", **evidence})
        self.table = table

    def start(self, handle: str, event: int) -> None:
        """Admit the return before any tool can create exposure on its behalf."""
        tick = self._tick(event)
        self._apply("return", {"handle": handle, "event": event, "tick": tick},
                    self.table.start(handle, event, tick))

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

    def carry(self, handle: str, cost_micro: int) -> bool:
        """Add a retained liability to an open return; report whether it could be borne.

        A charge that arrives after the return's outcome is final changes
        nothing here: an outcome is fixed once and never reopened, so the caller
        keeps the liability wherever else it is scored.
        """
        try:
            table = self.table.carry(handle, cost_micro)
        except (KeyError, ValueError):
            return False
        self._apply("carried", {"handle": handle, "cost_micro": cost_micro}, table)
        return True

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
        self._apply("income", {"service": service, "handle": handle, "micro": micro,
                               "event": event}, self.table.income(service, micro))
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

    def order_result(self, handle: str, result: dict, args: dict, event: int) -> None:
        """Attribute accepted market, limit and close orders before processing their fills."""
        if result.get("status") not in ("filled", "resting") or result.get("order_id") is None:
            return
        size = args.get("size") if result["status"] == "resting" else result.get("filled_size")
        oid = str(result["order_id"])
        existing = next((o for o in self.table.orders if o.order_id == oid), None)
        if existing is not None:
            if existing.handle != handle:
                raise ValueError("order already belongs to another decision")
            return
        if not self.account_open(handle) and handle not in self._unresolved_handles():
            reason = "no open consequence account"
            self.ledger.append({"kind": "consequence.refused", "handle": handle,
                                "order_id": oid, "reason": reason})
            self._execution("refusal", handle, event, {"order_id": oid, "reason": reason})
            return
        self._apply(
            "order",
            {"handle": handle, "order_id": oid, "size": str(size), "event": event},
            self.table.order(oid, handle, str(size)),
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
        elif kind == "Fill":
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
            table = self.table.funding(payload["coin"], str(payload["paid_usd"]))
            self._apply("funding", {"event": event, "payload": dict(payload)}, table)
        elif kind == "OrderRejected" and payload.get("order_id") is not None:
            self.cancel(str(payload["order_id"]), event)

    def redeem(self, coin: str, payout: str, event: int, facts: dict) -> dict[str, int]:
        """Close every event lot of ``coin`` at its market's resolution, with evidence first.

        Guarantees the resolution is ledgered before any lot moves, and that each
        decision that held the token is given one ``resolution`` execution receipt
        naming what it held, the payout and what that realised: a resolution is a
        fact about the world, addressed to the decisions it settled. Returns the
        signed micro-USD realised per handle, floored once.
        """
        table, credited = self.table.redeem(coin, payout)
        if table is self.table:
            return {}
        self._apply("resolution", {"coin": coin, "payout": str(payout), "event": event,
                                   **facts}, table)
        realized = {}
        for handle, net in credited.items():
            realized[handle] = net.numerator // net.denominator
            self._execution("resolution", handle, event, {
                **facts, "coin": coin, "payout": str(payout),
                "realized_micro": realized[handle]})
        return realized

    def resolve(self, event: int) -> list[Payoff]:
        """Persist all newly fixed outcomes before publishing the successor accounting state."""
        # An outcome censored for documented unobservability was fixed the moment
        # its hold was released; it is handed over here with everything else.
        fixed, self.censored_payoffs = self.censored_payoffs, []
        if self.pending_orders:
            return fixed  # Unknown inventory ownership cannot manufacture a no-fill outcome.
        table = self.table.resolve(event, self.backstop, self.mids,
                                   censored=self._unknown_portions(), tick=self._tick(event))
        for before, after in zip(self.table.returns, table.returns, strict=True):
            if before.payoff is None and after.payoff is not None:
                self.ledger.append({"kind": "consequence.outcome", **asdict(after.payoff)})
                fixed.append(after.payoff)
        self.table = table
        return fixed

    def payoff(self, handle: str) -> Payoff | None:
        """Return the fixed economic outcome, or None while a known return remains open."""
        return self.table.account(handle).payoff

    def counts(self) -> dict[str, int]:
        """Count returns once, independent of how many evaluators judged each return."""
        every = [r.payoff for r in self.table.returns if r.payoff is not None]
        # A censored outcome answers nothing, so it is neither a payoff nor a
        # failure to pay off; it is counted only as what it is.
        outcomes = [p for p in every if p.censored is None]
        return {
            "paid_off": sum(p.y for p in outcomes),
            "not_paid_off": sum(1 - p.y for p in outcomes),
            "marked": sum(p.marked for p in outcomes),
            "censored_outcomes": sum(p.censored is not None for p in every),
            # A voided return is not pending: it owes no outcome at all.
            "consequences_pending": sum(
                r.payoff is None and not r.voided for r in self.table.returns),
            "lots_opened": sum(r.opened_lots for r in self.table.returns),
            "lots_closed": sum(r.closed_lots for r in self.table.returns),
            "closes_credited": sum(r.closes for r in self.table.returns),
        }


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

    def poll(self, exchange, *, strict: bool = False) -> list[tuple[int, dict]]:
        """Return unseen executions in timestamp order, persisting the cursor before advance."""
        try:
            fills = exchange.fills(self.since_ns)
        except RuntimeError:  # read-only venue without an account
            if strict:
                raise
            return []
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
                result.append((fill.ts_ns, payload))
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
