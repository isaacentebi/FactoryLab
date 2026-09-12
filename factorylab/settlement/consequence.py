"""Ledger-first integration of pure lot accounting and kernel verdict commitments."""

from collections import Counter
from dataclasses import asdict

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue
from factorylab.settlement.forecast import Forecast, ForecastBook, open_forecast_decision
from factorylab.settlement.lots import LotTable, Payoff
from factorylab.settlement.vocabulary import RETURN_PAID_OFF, _require_event_index


class ReturnConsequences:
    """Every change to attribution, costs, inventory and outcomes has preceding ledger evidence."""

    def __init__(self, ledger: Ledger, backstop: int) -> None:
        _require_event_index(backstop, "backstop", positive=True)
        self.ledger = ledger
        self.backstop = backstop
        self.table = LotTable()
        self.mids: dict[str, str] = {}
        self.pending_orders: dict[str, dict] = {}
        self.deferred_events: list[tuple[str, dict, int]] = []

    def order_intent(self, client_id: str, handle: str, coin: str) -> None:
        """An unacknowledged order keeps attribution and dependent economic outcomes pending."""
        item = {"handle": handle, "coin": coin}
        self.ledger.append({"kind": "consequence.intent", "client_id": client_id, **item})
        self.pending_orders[client_id] = item

    def order_acknowledged(self, client_id: str) -> None:
        """Release deferred economic events in original order only after identity is resolved."""
        self.ledger.append({"kind": "consequence.acknowledged", "client_id": client_id})
        self.pending_orders.pop(client_id, None)
        if not self.pending_orders and self.deferred_events:
            events = self.deferred_events
            self.ledger.append({"kind": "consequence.replay", "count": len(events)})
            self.deferred_events = []
            for kind, payload, event in events:
                self.observe(kind, payload, event)

    def _apply(self, kind: str, evidence: dict, table: LotTable) -> None:
        self.ledger.append({"kind": f"consequence.{kind}", **evidence})
        self.table = table

    def start(self, handle: str, event: int) -> None:
        """Admit the return before any tool can create exposure on its behalf."""
        self._apply("return", {"handle": handle, "event": event}, self.table.start(handle, event))

    def finish(self, handle: str, cost_micro: int) -> None:
        """Persist the full metered cost before it becomes the payoff threshold."""
        self._apply(
            "cost",
            {"handle": handle, "cost_micro": cost_micro},
            self.table.finish(handle, cost_micro),
        )

    def account_open(self, handle: str) -> bool:
        """Only a return admitted here and not yet resolved may create venue exposure."""
        try:
            return self.table.account(handle).payoff is None
        except KeyError:
            return False

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
        if not self.account_open(handle):
            self.ledger.append({"kind": "consequence.refused", "handle": handle,
                                "order_id": oid, "reason": "no open consequence account"})
            return
        self._apply(
            "order",
            {"handle": handle, "order_id": oid, "size": str(size), "event": event},
            self.table.order(oid, handle, str(size)),
        )

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
                    size=str(payload["size"]),
                    px=str(payload["px"]),
                    fee_usd=str(payload["fee_usd"]),
                    liquidation=payload.get("liquidation", False),
                )
            except ValueError as exc:
                if "open consequence account" not in str(exc):
                    raise
                # A fill nobody with an account ordered never enters the shared FIFO.
                self.ledger.append({"kind": "consequence.refused", "event": event,
                                    "order_id": str(payload["order_id"]), "reason": str(exc)})
                return
            self._apply("fill", {"event": event, "payload": dict(payload)}, table)
        elif kind == "Funding" and payload.get("paid_usd") is not None:
            table = self.table.funding(payload["coin"], str(payload["paid_usd"]))
            self._apply("funding", {"event": event, "payload": dict(payload)}, table)
        elif kind == "OrderRejected" and payload.get("order_id") is not None:
            self.cancel(str(payload["order_id"]), event)

    def resolve(self, event: int) -> list[Payoff]:
        """Persist all newly fixed outcomes before publishing the successor accounting state."""
        if self.pending_orders:
            return []  # Unknown inventory ownership cannot manufacture a no-fill outcome.
        table = self.table.resolve(event, self.backstop, self.mids)
        fixed = []
        for before, after in zip(self.table.returns, table.returns, strict=True):
            if before.payoff is None and after.payoff is not None:
                self.ledger.append({"kind": "consequence.outcome", **asdict(after.payoff)})
                fixed.append(after.payoff)
        self.table = table
        return fixed

    def payoff(self, handle: str) -> Payoff | None:
        """Return the fixed economic outcome, or None while a known return remains open."""
        return self.table.account(handle).payoff

    def seal_verdict(
        self,
        book: ForecastBook,
        queue: DecisionQueue,
        *,
        evaluator_handle: str,
        evaluator_id: str,
        about: str,
        payoff: float,
        event: int,
        now_ns: int,
        tick_ns: int,
    ) -> Forecast:
        """Bind q to the judge's raw payoff probability on a separate original-judge decision."""
        return self._seal_payoff(
            book, queue, forecaster_id=evaluator_id, event_id=f"verdict-{evaluator_handle}",
            parent_handle=evaluator_handle, about=about, q=payoff, event=event,
            now_ns=now_ns, tick_ns=tick_ns,
        )

    def seal_self_forecast(
        self,
        book: ForecastBook,
        queue: DecisionQueue,
        *,
        handle: str,
        assembly_id: str,
        payoff: float,
        event: int,
        now_ns: int,
        tick_ns: int,
    ) -> Forecast:
        """Bind q to a return's own payoff probability, scored like a judge's on the same y."""
        return self._seal_payoff(
            book, queue, forecaster_id=assembly_id, event_id=f"self-{handle}",
            parent_handle=handle, about=handle, q=payoff, event=event,
            now_ns=now_ns, tick_ns=tick_ns,
        )

    def _seal_payoff(
        self, book, queue, *, forecaster_id, event_id, parent_handle, about, q, event, now_ns,
        tick_ns,
    ) -> Forecast:
        account = self.table.account(about)
        horizon = max(1, account.opened_at_event + self.backstop - event)
        handle = open_forecast_decision(
            queue,
            evaluator_id=forecaster_id,
            event_id=event_id,
            q=q,
            deadline_ns=now_ns + (horizon + 2) * tick_ns * 4,
            parent_handle=parent_handle,
            now_event=event,
            horizon=horizon,
        )
        return book.seal(
            Forecast(
                handle,
                forecaster_id,
                about,
                RETURN_PAID_OFF.id,
                {"horizon_events": horizon},
                q,
                event,
                event + horizon,
            )
        )

    def counts(self) -> dict[str, int]:
        """Count returns once, independent of how many evaluators judged each return."""
        outcomes = [r.payoff for r in self.table.returns if r.payoff is not None]
        return {
            "paid_off": sum(p.y for p in outcomes),
            "not_paid_off": sum(1 - p.y for p in outcomes),
            "marked": sum(p.marked for p in outcomes),
            "consequences_pending": sum(r.payoff is None for r in self.table.returns),
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

    def poll(self, exchange) -> list[tuple[int, dict]]:
        """Return unseen executions in timestamp order, persisting the cursor before advance."""
        try:
            fills = exchange.fills(self.since_ns)
        except RuntimeError:  # read-only venue without an account
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
