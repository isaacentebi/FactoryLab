"""Runtime venue method group."""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from factorylab.cortex.request import Return
from factorylab.kernel.money import money_to_usd, usd_to_micro
from factorylab.runtime.shared import _to_plain
from factorylab.settlement.lots import LotTable
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import AccountState, Order, OrderKind, OrderResult


def wind_down(exchange, ledger, *, dust_micro: int = 1_000_000,
              launch_nonce: str | None = None, reader=None) -> dict:
    """Leave the venue flat before the world dies, through the wind-down executor.

    The three passes are unchanged in what they attempt — cancel every resting
    order, close every open perp position, sell every spot balance worth more than
    ``dust_micro`` — and everything else about them is now the executor's
    (``factorylab/runtime/winddown.py``, edition 3 R3-C): each operation carries a
    durable identity ledgered before submission and after it, a repeated kill or a
    kill after a restart reconciles by that identity rather than acting twice, and
    the report ends with a final account reconciliation rather than with a count of
    acknowledgements. ``exposure_state`` is what the account says afterwards;
    ``exposure_status`` is the same value under its first name.

    Nothing here may raise. A kill that a venue or a diary can block is not a kill.
    """
    from factorylab.runtime.winddown import execute

    return execute(exchange, ledger, launch_nonce=launch_nonce, dust_micro=dust_micro,
                   reader=reader)


def dead_report() -> dict:
    """What a kill that wound nothing down knows: it is dead, and it reconciled nothing."""
    from factorylab.runtime.winddown import KILLED, UNKNOWN

    return {"attempted": False, "orders": 0, "operations": 0,
            "production_state": KILLED, "exposure_state": UNKNOWN,
            "exposure_status": UNKNOWN}


class VenueMixin:
    """Preserve runtime state and behavior for venue operations."""

    def kill(self, reason: str) -> dict:
        """End this world: production dies first, and only then is the venue wound down.

        The single kill path inside a living runtime: the duration kill through
        ``_finish_budget`` and any other runtime death go through here, and the
        operator's ``factorylab kill`` runs the same executor against the same
        manifest. Two states, in this order (edition 3, R3-C):

        1. ``production_state = killed``, before any external operation: the mark
           is in the diary (``kill.production``), on this object, and — when a
           wind-down is owed, so that the window between the mark and the seal can
           contain venue work — in the witness file outside the diary. Nothing
           after this point can make the world alive again: a diary carrying the
           mark without ``Terminated`` never resumes its population; it can only
           be killed again, which reconciles the wind-down and seals it.
        2. The wind-down executor, whose whole authority is to cancel, reduce,
           close and reconcile, and which cannot resume the population or open
           risk. Its operations and its final reconciliation are in the diary
           before ``Terminated``, because a sealed diary takes no further record.

        ``Termination.kill`` then publishes ``Terminated``, writes the witness
        line with ``production_state``, ``exposure_state`` and the operation
        count, and releases the seal. It runs in a ``finally``: no venue, no
        diary and no witness can prevent it.
        """
        from factorylab.runtime import winddown, witness

        if self.termination.final:
            return getattr(self, "wind_down_report", dead_report())
        owed = bool(self.m.kill.wind_down)
        report = dead_report()
        try:
            self.production_state = winddown.KILLED
            try:
                self.ledger.append({"kind": "kill.production",
                                    "production_state": winddown.KILLED, "reason": reason})
            except Exception as exc:  # noqa: BLE001 - a diary may never block a kill
                print(f"factorylab kill: the diary refused the production mark "
                      f"({type(exc).__name__}); the kill proceeds", file=sys.stderr)
            if owed:
                # The window between the mark and the seal is the only time a dead
                # world touches a venue. It is witnessed at both ends.
                witness.note_wind_down(wind_down=True, orders=0,
                                       exposure_state=winddown.UNKNOWN)
                witness.record_production_kill(self.ledger, reason)
            exchange = getattr(self, "exchange", None)
            if owed and exchange is not None:
                report = wind_down(exchange, self.ledger, dust_micro=self.m.kill.dust_micro,
                                   launch_nonce=getattr(self, "launch_nonce", None))
            elif owed:
                report["error"] = "world has no exchange"
        except Exception as exc:  # noqa: BLE001 - nothing may raise into a kill
            report["error"] = type(exc).__name__
        finally:
            # An acknowledgement is not a reconciled flat account.
            report.setdefault("exposure_state", winddown.UNKNOWN)
            report["exposure_status"] = report["exposure_state"]
            report["production_state"] = winddown.KILLED
            self.wind_down_report = report
            self.exposure_state = report["exposure_state"]
            try:
                witness.note_wind_down(
                    wind_down=owed, orders=report.get("orders", 0),
                    exposure_state=report["exposure_state"],
                    operations=report.get("operations", report.get("orders", 0)),
                    ledger_failures=report.get("ledger_failures", 0))
            finally:
                self.termination.kill(reason)
        return report

    def _finish_budget(self) -> None:
        """Reconcile once without invoking the population, then irreversibly end the run.

        The marker makes this phase replayable after interruption. Only lookups and
        fill reads occur; no cancellation, resubmission, or liquidation is implied.
        """
        self.ledger.append({"kind": "runtime.finish_budget"})
        report = {"attempted": self.venue is not None, "fill_read_error": None}
        if self.venue is not None:
            self._reconcile_orders()
            try:
                fills = self.consequence_fills.poll(self.exchange, strict=True)
            except Exception as exc:
                # A failed read cannot turn into evidence of an empty fill set.
                report["fill_read_error"] = type(exc).__name__
                fills = []
            observed = [WorldEvent(WorldEventKind.FILL, max(self.clock.now_ns, ts),
                                  self.exchange.name, payload) for ts, payload in fills]
            self._settle_exchange_effects(observed, observe_positions=False)
        self.ledger.append({"kind": "venue.terminal_reconciliation", **report})
        self.terminal_reconciliation = report
        self.kill("explicit_kill:budget")

    def _trading_markets(self) -> tuple[str, ...]:
        """Return the markets this world trades: the manifest seed plus every registration.

        Read through the venue tools rather than copied, so a registered market
        is in the set from the next tick and a resume that rebuilds the tools
        and replays the ``market:`` contracts restores it. Before the tools
        exist the manifest seed is the whole set.
        """
        tools = getattr(self, "venue_tools", None)
        if tools is None:
            return (*self.m.exchange.coins, *self.m.exchange.spot_pairs)
        return (*tools.coins, *tools.spot_pairs)

    def _tick_mids(self) -> dict[str, Decimal]:
        """The venue's mid prices, read once for the tick that reads them.

        The same memo #89 gave the venue's instrument listing, for the same reason.
        A live venue prices every coin it lists, about 58 KB a read, and every read
        through the recorded-I/O layer is written into the diary in full: rehearsal 3
        read mids 860 times in five hours and wrote 50 MB of the 194 MB diary doing
        it. A tick's prompts are all built from one price, so they read one.

        The memo sits here, above the recorder, so a replayed diary sees exactly the
        calls that were recorded, and ``_snapshot`` drops it at every checkpoint so a
        replayed tail asks the venue where the recorded tail did. It is never saved:
        the first prompt after a resume reads afresh and records that read.

        Only prompt building and the pre-submission collateral check read through
        here. Everything a price has a consequence for — the tick broadcast, fills,
        funding, marking positions at a window boundary, a watcher's trigger, and the
        population's own paid ``venue.mids`` call — reads the venue itself.
        """
        tick = self.ticks_consumed
        memo = getattr(self, "_mids_memo", None)
        if memo is None or memo[0] != tick:
            memo = (tick, self.exchange.mids())
            self._mids_memo = memo
        return dict(memo[1])

    def _tick_account(self) -> AccountState:
        """The venue's account state, read once for the tick that reads it, for prompts only.

        Rehearsal 3 read the account 4,198 times in five hours, 3.5 MB of diary, for
        the world block and the tick payload of every prompt in the tick. One read a
        tick answers all of them, under the same memo discipline as ``_tick_mids``.

        Prompt building only. Order placement and every settlement path read the
        venue directly: a memoised equity or position set is a description of the
        tick, and a consequence must be weighed against the account as it is.
        """
        tick = self.ticks_consumed
        memo = getattr(self, "_account_memo", None)
        if memo is None or memo[0] != tick:
            memo = (tick, self.exchange.account())
            self._account_memo = memo
        return memo[1]

    def _refuse_order(self, handle: str, reason: str, *, kind: str = "order.refused",
                      **extra) -> dict:
        """Publish one refusal that happened before any intent, and tell its author why.

        A refusal the population cannot read is a refusal it will repeat: the
        reason goes to the diary as ``kind`` and to ``registration_feedback``,
        the same surface a refused proposal uses, so the next return sees it in
        its own world block. Returns the rejection the caller hands back.
        """
        self.ledger.append({"kind": kind, "handle": handle, "reason": reason,
                            **extra, "ts": self.clock.now_ns})
        self.registration_feedback.append({"kind": kind,
                                           "reason": f"order: {reason}"})
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle,
                outcome={"kind": "order_refused", "status": "rejected", "reason": reason,
                         **extra}, delta_micro=0,
                evidence={"kind": kind, "handle": handle, "ts": self.clock.now_ns})
        return {"status": "rejected", "error": reason}

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

    def _settle_exchange_effects(self, evs: list[WorldEvent], *,
                                 observe_positions: bool = True) -> None:
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
                notional = usd_to_micro(
                    Decimal(str(we.payload["size"])) * Decimal(str(we.payload["px"]))
                , rounding="nearest")
                self.window.notional_micro += notional
                realized = usd_to_micro(we.payload["realized_usd"], rounding="nearest")
                self.window.realized_pnl_micro += realized
                fee = usd_to_micro(we.payload["fee_usd"], rounding="nearest")
                self.realized_to_date += realized
                self.fees_to_date += fee
                # Counting and publishing are the same moment. The Fill this
                # event becomes is ledgered again as ``event:Fill`` when the
                # population is delivered it, which can be many events later or
                # never; a count with no item is a fill an operator cannot find.
                self.ledger.append({
                    "kind": "fill.counted", "order_id": str(we.payload["order_id"]),
                    "coin": we.payload["coin"], "market": we.payload.get("market", "perp"),
                    "is_buy": we.payload["is_buy"], "size": str(we.payload["size"]),
                    "px": str(we.payload["px"]), "notional_micro": notional,
                    "realized_micro": realized, "fee_micro": fee,
                    "liquidation": we.payload.get("liquidation", False),
                    "window": self.window.index, "event": self.n, "ts": we.ts_ns,
                })

            elif we.kind is WorldEventKind.FUNDING:
                paid = usd_to_micro(we.payload["paid_usd"], rounding="nearest")
                self.funding_to_date -= paid

            self.internal.append(self._kernel_event(we))
        if observe_positions and hasattr(self.exchange, "sync_cash"):
            self.exchange.sync_cash(
                getattr(self.treasury, "venue_balance_usd", money_to_usd(self.wallet.balance))
            )
        if observe_positions:
            self._observe_positions()

    def _execute_outputs(self, ret: Return) -> None:
        out = ret.outputs
        if self.wallet.dead or ret.status != "ok":
            return
        if out.get("action") != "order":
            if str(out.get("action", "")).lower().startswith(("buy:", "sell:")):
                self._refuse_order(ret.handle, 'action labels are not orders; use action="order" '
                                   'with explicit coin, side and size')
            return
        if str(out.get("side", "buy")).lower() not in ("buy", "sell"):
            self._refuse_order(ret.handle, "order side must be buy or sell")
            return
        try:
            order = Order(
                str(out["coin"]),
                str(out.get("side", "buy")).lower() == "buy",
                Decimal(str(out["size"])),
                client_id=ret.handle,
                market=out.get("market", "perp"),
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            self._refuse_order(ret.handle,
                               f"order output is not a readable order: {type(exc).__name__}")
            return
        reason = self._order_collateral(ret.handle, order.coin, order.size, order.is_buy)
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
            return self._refuse_order(handle, "class transfer awaiting receipt")
        client_id = handle if slot == "output" else f"{handle}:{slot}"
        previous = self.order_intents.get(client_id)
        if previous is not None:
            if previous["operation"] != operation or previous["args"] != args:
                return self._refuse_order(handle, "client id already binds another intent")
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
                return self._refuse_order(handle, "spot sell exceeds accounted inventory")
        if any(i["result"]["status"] == "uncertain" and i["args"]["coin"] == args["coin"]
               for i in self.order_intents.values()):
            return self._refuse_order(handle, "prior order on this coin is still uncertain")
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
        except Exception as exc:
            result = {"status": "uncertain", "error": f"write exception: {type(exc).__name__}"}
        if not isinstance(result, dict) or result.get("status") == "uncertain":
            # Preserve the first response before recovery can replace its diagnostic.
            self._record_order_result(client_id, result if isinstance(result, dict) else {
                "status": "uncertain", "error": "write returned a non-object acknowledgement"})
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
        except Exception as exc:
            result = {"status": "uncertain", "error": f"recovery exception: {type(exc).__name__}"}
        return self._record_order_result(client_id, result)

    def _record_order_result(self, client_id: str, result: dict) -> dict:
        result = json.loads(json.dumps(result, default=str))
        intent = self.order_intents[client_id]
        if result.get("status") not in ("filled", "resting", "cancelled", "rejected"):
            result = {"status": "uncertain", "error": str(
                result.get("error") or "venue acknowledgement unavailable")[:300]}
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

    def _order_collateral(
        self, handle: str, coin: str, size: Decimal, is_buy: bool,
        price: Decimal | None = None, *, reduce_only: bool = False,
    ) -> str | None:
        """New exposure is collateralised by the venue's own free collateral, not by thinking money.

        Edition 3 C5 keeps two pots apart: the compute wallet buys thoughts and the
        trading principal sits on the venue. This check compared the venue margin an
        order needs with ``wallet.available`` — the compute wallet net of the
        protected novelty reserve — and so refused a 0.005 BTC short, about $383 of
        notional at 2x, on a venue account carrying $851 of perps cash, because the
        thinking pot had $107 left. Margin is charged by the venue against money that
        is already on the venue, so that is the pot the requirement is weighed
        against.

        The requirement is margin already used, plus the margin resting orders hold,
        plus the increase this order needs; the pot is the venue's free collateral.
        Equivalently, and as it is written here: resting margin plus the increase
        against equity minus margin already used. ``AccountState`` offers
        ``equity_usd``, ``cash_usd`` and ``margin_used_usd``, and ``equity_usd`` is
        the honest base of the three. Both adapters compute it the same way — the
        account marked at mid, unrealised P&L and spot holdings included — whereas
        ``cash_usd`` is the fake's perps cash but Hyperliquid's ``withdrawable``,
        which is already net of margin used and of resting orders, so subtracting
        those from it would count them twice. Equity is read generously across the
        classes a venue keeps: a perp order is collateralised on testnet by perps
        cash alone, so this is a ceiling on that account's collateral rather than a
        promise about it, and the venue's own refusal remains the final word.

        The leverage wall is untouched and is still the hard cast: ``_order_leverage``
        gives no discount for leverage this world never acknowledged. Reductions are
        always allowed, a spot sell needs no collateral, and world-priced losses
        still settle against the wallet in full.
        """
        if "/" in coin and not is_buy:
            return None
        if reduce_only:
            return None
        venue_available: Decimal | None = None
        try:
            account = self.exchange.account()
            mids = self._tick_mids()
            mark = max(mids[coin], price or mids[coin])
            current = next((p.size for p in account.positions if p.coin == coin), Decimal(0))
            target = current + (size if is_buy else -size)
            increase = max(Decimal(0), abs(target) - abs(current)) * mark
            if increase == 0:
                return None
            resting = sum((Decimal(str(o["size"])) * Decimal(str(o["price"]))
                           / self._order_leverage(o["coin"])
                           for o in self.exchange.open_orders()), Decimal(0))
            required = increase / self._order_leverage(coin) + resting
            venue_available = account.equity_usd - account.margin_used_usd
            if required <= venue_available:
                return None
            reason = "order collateral exceeds venue free collateral"
        except (AttributeError, KeyError, ValueError, ArithmeticError, RuntimeError) as exc:
            reason = f"order collateral unavailable: {type(exc).__name__}"
        self._refuse_order(handle, reason, kind="order.infeasible",
                           venue_available_usd=None if venue_available is None
                           else str(venue_available))
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
