"""Runtime venue method group."""

from __future__ import annotations

import json
from decimal import ROUND_CEILING, Decimal

from factorylab.cortex.request import Return
from factorylab.kernel.money import money_to_usd, usd_to_micro
from factorylab.runtime.shared import _to_plain
from factorylab.settlement.lots import LotTable
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import Order, OrderKind, OrderResult


class VenueMixin:
    """Preserve runtime state and behavior for venue operations."""

    def _equity_micro(self) -> int:
        try:
            return usd_to_micro(self.exchange.account().equity_usd, rounding="nearest")
        except RuntimeError:  # read-only live venue: the wallet is the only equity there is
            return self.wallet.balance

    def _observe_positions(self) -> None:
        """A new peak position notional is recorded before it enters the window."""
        try:
            positions = self.exchange.account().positions
            mids = self.exchange.mids() if positions else {}
        except RuntimeError:
            return
        notionals: dict[str, Decimal] = {}
        if any(position.size and position.coin not in mids for position in positions):
            return
        for position in positions:
            if position.size:
                notionals[position.coin] = notionals.get(position.coin, Decimal(0)) + (
                    position.size * Decimal(str(mids[position.coin]))
                )
        if not notionals:
            return
        peak = max(usd_to_micro(abs(value), rounding="nearest") for value in notionals.values())
        previous = self.window.max_position_notional_micro
        if previous is None or peak > previous:
            self.ledger.append(
                {
                    "kind": "observation.position_peak",
                    "window": self.window.index,
                    "notional_micro": peak,
                    "ts": self.clock.now_ns,
                }
            )
            self.window.max_position_notional_micro = peak

    def _settle_exchange_effects(self, evs: list[WorldEvent]) -> None:
        settlements = []
        spot_table = self.consequences.table
        refused = set()
        for we in evs:
            if we.kind is WorldEventKind.FILL and we.payload.get("market") == "spot":
                if self.consequences.pending_orders:
                    # The consequence book defers these until ownership is known.
                    continue
                try:
                    spot_table = self._spot_fill_table(spot_table, we.payload)
                except ValueError as exc:
                    self.ledger.append({"kind": "consequence.refused", "event": self.n,
                                        "order_id": str(we.payload["order_id"]),
                                        "reason": str(exc)})
                    refused.add(id(we))
                    continue
                we.payload["realized_usd"] = str(self._account_spot_fill(we.payload))
        for we in evs:
            if we.kind is WorldEventKind.FILL:
                delta = usd_to_micro(we.payload["realized_usd"], rounding="nearest") - usd_to_micro(
                    we.payload["fee_usd"]
                , rounding="nearest")
                if delta:
                    settlements.append((delta, f"fill:{we.payload['order_id']}", "exchange_pnl"))
            elif we.kind is WorldEventKind.FUNDING:
                paid = usd_to_micro(we.payload["paid_usd"], rounding="nearest")
                if paid:
                    settlements.append((-paid, f"funding:{we.payload['coin']}:{we.ts_ns}",
                                        "funding"))
        if settlements:
            self.wallet.settle_batch(settlements)
        for we in evs:
            if id(we) not in refused:
                self.consequences.observe(str(we.kind), dict(we.payload), self.n)
        for we in evs:
            if we.kind is WorldEventKind.FILL:
                self.stats.fills += 1
                self.window.fills += 1
                self.window.notional_micro += usd_to_micro(
                    Decimal(str(we.payload["size"])) * Decimal(str(we.payload["px"]))
                , rounding="nearest")
                realized = usd_to_micro(we.payload["realized_usd"], rounding="nearest")
                self.window.realized_pnl_micro += realized
                fee = usd_to_micro(we.payload["fee_usd"], rounding="nearest")
                self.realized_to_date += realized
                self.fees_to_date += fee

            elif we.kind is WorldEventKind.FUNDING:
                paid = usd_to_micro(we.payload["paid_usd"], rounding="nearest")
                self.funding_to_date -= paid

            self.internal.append(self._kernel_event(we))
        if hasattr(self.exchange, "sync_cash"):
            self.exchange.sync_cash(
                getattr(self.treasury, "venue_balance_usd", money_to_usd(self.wallet.balance))
            )
        self._observe_positions()

    def _execute_outputs(self, ret: Return) -> None:
        out = ret.outputs
        if self.wallet.dead or ret.status != "ok" or out.get("action") != "order":
            return
        try:
            order = Order(
                str(out["coin"]),
                str(out.get("side", "buy")).lower() == "buy",
                Decimal(str(out["size"])),
                client_id=ret.handle,
                market=out.get("market", "perp"),
            )
        except (KeyError, ValueError, ArithmeticError):
            return
        reason = self._order_exclusion(ret.handle, order.coin, order.size, order.is_buy)
        result = ({"status": "rejected", "error": reason} if reason else self._venue_write(
            ret.handle, "venue.place_market", {"coin": order.coin,
                "side": "buy" if order.is_buy else "sell", "size": str(order.size),
                "market": order.market}, slot="output"
        ))
        self.stats.orders_placed += 1
        if result["status"] == "rejected":
            self.stats.orders_rejected += 1
        if hasattr(self.exchange, "drain_events"):  # fake venue fills synchronously
            self._settle_exchange_effects(self.exchange.drain_events())

    def _venue_write(self, handle: str, operation: str, args: dict, *, slot: str) -> dict:
        """Every venue write has a durable intent and a stable identity before submission."""
        pending = getattr(self.treasury, "state", None)
        if (pending and pending["status"] == "submitted"
                and pending["direction"] in ("spot_to_perps", "perps_to_spot")
                and operation != "venue.cancel"):
            return {"status": "rejected", "error": "class transfer awaiting receipt"}
        client_id = handle if slot == "output" else f"{handle}:{slot}"
        previous = self.order_intents.get(client_id)
        if previous is not None:
            if previous["operation"] != operation or previous["args"] != args:
                self.ledger.append({"kind": "order.refused", "handle": handle,
                                    "reason": "client id already binds another intent"})
                return {"status": "rejected", "error": "client id already binds another intent"}
            if previous["result"]["status"] == "uncertain":
                return self._recover_order(client_id)
            return dict(previous["result"])
        if args.get("market") == "spot" and (
            operation == "venue.close" or (
                operation in ("venue.place_market", "venue.place_limit")
                and args.get("side") == "sell"
            )
        ):
            held = self.spot_inventory.get(args["coin"], (Decimal(0), Decimal(0)))[0]
            quantity = held if args.get("size") is None else Decimal(str(args["size"]))
            lots = sum((lot.size for lot in self.consequences.table.lots
                        if lot.coin == args["coin"] and lot.market == "spot"), 0)
            if quantity <= 0 or quantity > min(held, lots):
                reason = "spot sell exceeds accounted inventory"
                self.ledger.append({"kind": "order.refused", "handle": handle, "reason": reason})
                return {"status": "rejected", "error": reason}
        if any(i["result"]["status"] == "uncertain" and i["args"]["coin"] == args["coin"]
               for i in self.order_intents.values()):
            self.ledger.append({"kind": "order.refused", "handle": handle,
                                "reason": "prior order on this coin is still uncertain"})
            return {"status": "rejected", "error": "prior order on this coin is still uncertain"}
        intent = {"handle": handle, "client_id": client_id, "operation": operation,
                  "args": dict(args), "result": {"status": "uncertain"}}
        self.ledger.append({"kind": "order.intent", **intent})
        self.order_intents[client_id] = intent
        self.consequences.order_intent(client_id, handle, args["coin"])
        try:
            if operation == "venue.cancel":
                result = self.exchange.cancel(args["order_id"], coin=args["coin"],
                                              client_id=client_id)
            elif operation == "venue.close":
                size = None if args.get("size") is None else Decimal(str(args["size"]))
                if size is None and args.get("market") == "spot":
                    size = self.spot_inventory[args["coin"]][0]
                result = self.exchange.close(args["coin"], size, client_id=client_id,
                                             **({"market": "spot"} if args.get("market") == "spot"
                                                else {}))
            else:
                limit = operation == "venue.place_limit"
                result = self.exchange.place(Order(
                    args["coin"], args["side"] == "buy", Decimal(str(args["size"])),
                    OrderKind.LIMIT if limit else OrderKind.MARKET,
                    Decimal(str(args["price"])) if limit else None, client_id,
                    reduce_only=args.get("reduce_only", False),
                    market=args.get("market", "perp"),
                ))
            result = _to_plain(vars(result)) if isinstance(result, OrderResult) else result
        except Exception:
            result = {"status": "uncertain"}
        if not isinstance(result, dict) or result.get("status") == "uncertain":
            return self._recover_order(client_id)
        return self._record_order_result(client_id, result)

    @staticmethod
    def _spot_fill_table(table: LotTable, payload: dict) -> LotTable:
        """Inventory changes require the same exact acceptance as the consequence book."""
        return table.fill(
            order_id=str(payload["order_id"]), coin=payload["coin"],
            is_buy=payload["is_buy"], size=str(payload.get("inventory_size", payload["size"])),
            px=str(payload["px"]), fee_usd=str(payload["fee_usd"]),
            liquidation=payload.get("liquidation", False), market="spot",
            order_size=str(payload["size"]),
        )

    def _account_spot_fill(self, payload: dict) -> Decimal:
        """An accepted spot fill changes inventory only after its evidence is durable."""
        coin = payload["coin"]
        quantity = Decimal(str(payload.get("inventory_size", payload["size"])))
        px = Decimal(str(payload["px"]))
        held, entry = self.spot_inventory.get(coin, (Decimal(0), Decimal(0)))
        buy = payload["is_buy"]
        if not buy and quantity > held:
            raise ValueError("spot fill exceeds accounted inventory")
        realized = Decimal(0) if buy else (px - entry) * quantity
        remaining = held + (quantity if buy else -quantity)
        cost = (held * entry + quantity * px) / remaining if buy else entry
        self.ledger.append({"kind": "spot.inventory", "coin": coin,
                            "size": str(remaining), "entry_px": str(cost),
                            "order_id": payload["order_id"]})
        self.spot_inventory[coin] = (remaining, cost)
        return realized

    def _recover_order(self, client_id: str) -> dict:
        """Query an ambiguous intent; never resubmit it or replace its originating handle."""
        intent = self.order_intents[client_id]
        try:
            cancel = intent["operation"] == "venue.cancel"
            result = self.exchange.lookup(client_id, **(
                {"order_id": intent["args"]["order_id"]} if cancel else {}
            ))
            result = _to_plain(vars(result))
            if cancel:
                if result["status"] in ("filled", "rejected"):
                    result = {"status": "rejected", "error": "target order already terminal"}
                elif result["status"] != "cancelled":
                    result = {"status": "uncertain"}
        except Exception:
            result = {"status": "uncertain"}
        return self._record_order_result(client_id, result)

    def _record_order_result(self, client_id: str, result: dict) -> dict:
        result = json.loads(json.dumps(result, default=str))
        intent = self.order_intents[client_id]
        if result.get("status") not in ("filled", "resting", "cancelled", "rejected"):
            result = {"status": "uncertain", "error": "venue acknowledgement unavailable"}
        self.ledger.append({"kind": "order.uncertain" if result["status"] == "uncertain"
                            else "order.acknowledged", "client_id": client_id,
                            "handle": intent["handle"], "result": result})
        self.order_intents[client_id] = {**intent, "result": dict(result)}
        if result["status"] != "uncertain":
            if intent["operation"] == "venue.cancel":
                if result["status"] == "cancelled":
                    self.consequences.cancel(intent["args"]["order_id"], self.n)
            else:
                attributed = result
                if result["status"] == "cancelled" and Decimal(str(result["filled_size"])) > 0:
                    attributed = {**result, "status": "filled"}
                self.consequences.order_result(intent["handle"], attributed, intent["args"], self.n)
            spot_table = self.consequences.table
            corrections = []
            for kind, payload, _event in self.consequences.order_acknowledged(client_id):
                if kind == "Fill":
                    if payload.get("market") == "spot":
                        try:
                            spot_table = self._spot_fill_table(spot_table, payload)
                        except ValueError:
                            continue  # The replay already recorded its refusal.
                        realized = usd_to_micro(
                            self._account_spot_fill(payload), rounding="nearest",
                        )
                        delta = realized - usd_to_micro(payload["realized_usd"], rounding="nearest")
                        if delta:
                            corrections.append((delta, f"fill:{payload['order_id']}",
                                                "exchange_pnl"))
                    self._record_fill_notional(payload)
            if corrections:
                self.wallet.settle_batch(corrections)
                delta = sum(change for change, _handle, _reason in corrections)
                self.realized_to_date += delta
                self.window.realized_pnl_micro += delta
        return dict(result)

    def _reconcile_orders(self) -> None:
        """Pending identities are reconciled before consuming newly observed venue fills."""
        for client_id, intent in list(self.order_intents.items()):
            if intent["result"]["status"] == "uncertain":
                self._recover_order(client_id)

    def _order_exclusion(
        self, handle: str, coin: str, size: Decimal, is_buy: bool,
        price: Decimal | None = None, *, reduce_only: bool = False,
    ) -> str | None:
        """Protected compute is excluded from collateral available for new order exposure.

        Existing margin and resting orders count before new exposure. Reductions
        remain available to unwind risk; world-priced losses still settle in full.
        """
        if "/" in coin and not is_buy:
            return None
        if reduce_only or not self.reserve.remaining():
            return None
        try:
            account = self.exchange.account()
            mids = self.exchange.mids()
            mark = max(mids[coin], price or mids[coin])
            current = next((p.size for p in account.positions if p.coin == coin), Decimal(0))
            target = current + (size if is_buy else -size)
            increase = max(Decimal(0), abs(target) - abs(current)) * mark
            if increase == 0:
                return None
            resting = sum((Decimal(str(o["size"])) * Decimal(str(o["price"]))
                           / self._order_leverage(o["coin"])
                           for o in self.exchange.open_orders()), Decimal(0))
            required = account.margin_used_usd + increase / self._order_leverage(coin) + resting
            ceiling = int((required * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
            if ceiling <= self.wallet.available:
                return None
            reason = "order collateral exceeds available wallet balance"
        except (AttributeError, KeyError, ValueError, ArithmeticError, RuntimeError) as exc:
            reason = f"order collateral unavailable: {type(exc).__name__}"
        self.ledger.append({"kind": "order.infeasible", "handle": handle, "reason": reason,
                            "available": self.wallet.available, "ts": self.clock.now_ns})
        return reason

    def _order_leverage(self, coin: str) -> Decimal:
        """Use acknowledged leverage; unknown live leverage receives no collateral discount."""
        if "/" in coin:
            return Decimal(1)
        if self.venue_tools is not None:
            for tool, args, ok in reversed(self.venue_tools.log):
                if tool == "venue.set_leverage" and ok and args["coin"] == coin:
                    return Decimal(args["leverage"])
        if self.exchange.deterministic:
            return Decimal(self.exchange.target.max_leverage)
        return Decimal(1)
