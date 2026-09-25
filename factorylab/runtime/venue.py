"""Runtime venue method group."""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from factorylab.cortex.request import Return
from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.shared import _to_plain
from factorylab.settlement.lots import LotTable
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import (
    AccountState,
    Order,
    OrderKind,
    OrderResult,
    VenueUnavailable,
)

#: How many times an uncertain order is polled before the runtime says so and stops.
#: The manifest declares no order-lifecycle schedule, so the schedule is this bound:
#: an uncertain intent is asked about at most this many times in all, each answer
#: ledgered once, and then one ``order.unresolved`` closes the question. An order
#: whose identity the venue will not confirm is a fact to record, not a poll to
#: repeat forever. The kill wind-down's terminal reconciliation reads past it: a
#: dying runtime asks once more, whatever the schedule already spent.
UNCERTAIN_ORDER_POLLS = 5


def _custody_of(market: str) -> str:
    """Which venue account a fill settles in; a venue keeps spot and perps apart."""
    return "venue_spot" if market == "spot" else "venue_perps"


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
            if owed and getattr(getattr(self, "polymarket", None), "writes", False):
                self._wind_down_polymarket(report)
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
            self._reconcile_orders(final=True)
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

    def _tick_account_observation(self) -> tuple[AccountState | None, str | None, int]:
        """One account read a tick, its failure included, as ``(account, reason, at_ns)``.

        A failed read is a fact about the tick, not an invitation to ask again:
        the memo holds the refusal as well as the answer, so a hundred prompts
        built in one tick ask an unreachable venue once. The caller renders the
        reason as ``unavailable``; nothing here substitutes a number for it.
        """
        tick = self.ticks_consumed
        memo = getattr(self, "_account_memo", None)
        if memo is None or memo[0] != tick:
            try:
                account = self.exchange.account()
                # A fallback to an older snapshot is not this tick's account.
                memo = ((tick, None, "StaleAccount", self.clock.now_ns)
                        if getattr(account, "stale", False)
                        else (tick, account, None, self.clock.now_ns))
            except Exception as exc:  # noqa: BLE001 - every read failure is reportable
                memo = (tick, None, type(exc).__name__, self.clock.now_ns)
            self._account_memo = memo
        return memo[1], memo[2], memo[3]

    def _tick_account(self) -> AccountState:
        """The venue's account state, read once for the tick that reads it, for prompts only.

        Rehearsal 3 read the account 4,198 times in five hours, 3.5 MB of diary, for
        the world block and the tick payload of every prompt in the tick. One read a
        tick answers all of them, under the same memo discipline as ``_tick_mids``.

        Prompt building only. Order placement and every settlement path read the
        venue directly: a memoised equity or position set is a description of the
        tick, and a consequence must be weighed against the account as it is.
        """
        account, reason, _at_ns = self._tick_account_observation()
        if account is None:
            raise VenueUnavailable(f"venue account unavailable: {reason}")
        return account

    def _refuse_order(self, handle: str, reason: str, *, kind: str = "order.refused",
                      **extra) -> dict:
        """Ledger one refusal that happened before any intent, and tell its author why.

        The reason goes to the diary as ``kind`` and to the author's own inbox
        under the order's handle, and to no other seat. Returns the rejection the
        caller hands back.
        """
        self.ledger.append({"kind": kind, "handle": handle, "reason": reason,
                            **extra, "ts": self.clock.now_ns})
        owner = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
        if owner is not None:
            self.outcomes.append(owner, handle=handle,
                outcome={"kind": "order_refused", "status": "rejected", "reason": reason,
                         **extra}, delta_micro=0,
                evidence={"kind": kind, "handle": handle, "ts": self.clock.now_ns})
        return {"status": "rejected", "error": reason}

    def _equity_micro(self) -> int | None:
        """The venue's equity, or ``None`` when the venue would not say (edition 3, C5).

        This used to answer an unreadable venue with the compute wallet's balance.
        The wallet is authority, not equity held at a venue, and a window that
        opens on a fabricated figure measures turnover and exposure against a
        number no custodian ever held. An unknown equity leaves every ratio
        derived from it unmeasured, which is what it is.
        """
        try:
            account = self.exchange.account()
        except RuntimeError:
            return None
        if getattr(account, "stale", False):
            return None  # an old snapshot is not the equity a window opens on
        return usd_to_micro(account.equity_usd, rounding="nearest")

    def _venue_moved(self) -> None:
        """Drop what was observed of the venue: its books may have moved since.

        The one invalidation for same-tick observation reuse. Called before every
        venue write, after every recovered acknowledgement, and by every settlement
        that carries a fill, a funding payment or a liquidation; a treasury class
        transfer moves the key itself. It is deliberately not called for a mid: a
        price the venue published in the batch being settled is not a change the
        runtime made, and it is the reason there was anything to collapse.
        """
        self._peak_observed = None

    def _observe_positions(self) -> None:
        """Record one marked position sample per window/tick and after known venue moves.

        Repeated MarketMid events from the same batch reuse the observation. Orders,
        recovered acknowledgements, fills, funding, liquidations and class transfers
        invalidate it. Failed or incomplete reads remain eligible for retry.

        This is a sampled peak, not a continuous one: a price spike between reads
        may be missed. Collateral checks, equity reads and paid population reads
        still query the venue independently.
        """
        transfer = getattr(self.treasury, "state", None)
        key = (self.window.index, self.ticks_consumed,
               (transfer.get("status"), transfer.get("direction"),
                transfer.get("amount_micro")) if isinstance(transfer, dict) else None)
        if getattr(self, "_peak_observed", None) == key:
            return
        try:
            account = self.exchange.account()
            if getattr(account, "stale", False):
                return  # a peak is observed on a live account or not at all
            positions = account.positions
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
        self._peak_observed = key
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

    def _settle_venue(self, settlements: list[tuple]) -> None:
        """Book venue P&L, fees and funding where they happen: on the venue accounts.

        Edition 3 C5, GPT-6 Pro's third reading §2: "venue P&L, fees and funding
        settle on the venue accounts only". These effects used to run through
        ``wallet.settle_batch``, so a trading loss consumed compute authority the
        venue never touched and could kill a world holding a full OpenRouter
        balance. The venue's own account state is the record of them; what this
        writes is the diary evidence -- one item per effect, with the custody it
        landed in and the decision that owns it -- so the wake, the consequence
        line and an operator can all read what the venue did without reading it
        as money leaving the compute wallet.

        The compute wallet moves only when money moves: provider bills for model
        calls, a seller's price for a paid read or a paid call, treasury fees,
        releases and confirmed conversions into provider credit. Nothing here is
        any of those.
        """
        for delta, reference, reason, custody, order_id in settlements:
            handle = self._order_owner(order_id)
            # The venue's own settled P&L: what every seat's venue claim is backed by.
            self.budget.book_venue(delta, f"{reason}:{reference}")
            self.ledger.append({
                "kind": "venue.settled", "custody": custody, "amount": delta,
                "reference": reference, "reason": reason, "handle": handle,
                "event": self.n, "ts": self.clock.now_ns,
            })
            if handle is not None:
                by_custody = self.venue_deltas.setdefault(handle, {})
                by_custody[custody] = by_custody.get(custody, 0) + delta

    def _order_owner(self, order_id: str | None) -> str | None:
        """The decision that placed an order, from the consequence book's own record."""
        if order_id is None:
            return None
        orders = getattr(getattr(self.consequences, "table", None), "orders", None) or ()
        return next((o.handle for o in orders if o.order_id == str(order_id)), None)

    def _settle_exchange_effects(self, evs: list[WorldEvent], *,
                                 observe_positions: bool = True) -> None:
        if any(we.kind is not WorldEventKind.MARKET_MID for we in evs):
            # A fill, a funding payment or a liquidation is the venue's books moving.
            self._venue_moved()
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
            if id(we) in refused:
                # A fill the lot book refused has no accounted owner and no accounted
                # inventory: the venue's own realized figure for it is not evidence
                # of anyone's P&L, so it is neither settled nor counted below.
                continue
            if we.kind is WorldEventKind.FILL:
                delta = usd_to_micro(we.payload["realized_usd"], rounding="nearest") - usd_to_micro(
                    we.payload["fee_usd"]
                , rounding="nearest")
                if delta:
                    settlements.append((delta, f"fill:{we.payload['order_id']}", "exchange_pnl",
                                        _custody_of(we.payload.get("market", "perp")),
                                        str(we.payload["order_id"])))
            elif we.kind is WorldEventKind.FUNDING:
                paid = usd_to_micro(we.payload["paid_usd"], rounding="nearest")
                if paid:
                    settlements.append((-paid, f"funding:{we.payload['coin']}:{we.ts_ns}",
                                        "funding", "venue_perps", None))
        if settlements:
            self._settle_venue(settlements)
        for we in evs:
            if id(we) not in refused:
                self.consequences.observe(str(we.kind), dict(we.payload), self.n)
        for we in evs:
            if id(we) in refused:
                self.internal.append(self._kernel_event(we))
                continue
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
        # The venue's cash is no longer pushed here from the compute wallet. That
        # sync is what made a venue loss spend thinking money and a model call
        # shrink the trading account: two custodians, one balance. Each keeps its
        # own now, and ``sync_cash`` survives only as a deliberate funding call.
        if observe_positions:
            self._observe_positions()

    def _wind_down_polymarket(self, report: dict) -> None:
        """Wind the polymarket pot down beside the venue, and let its exposure count.

        A kill owes every custody its wind-down; a Polymarket position the book
        would not take is exposure the dead world still holds, so the report's
        ``exposure_state`` is the worse of the two venues'.
        """
        from factorylab.runtime import winddown
        from factorylab.runtime.polymarket import wind_down as polymarket_wind_down

        pm = polymarket_wind_down(self)
        report["polymarket"] = pm
        rank = (winddown.FLAT, winddown.DUST, winddown.PENDING, winddown.UNKNOWN)
        states = [report.get("exposure_state", winddown.UNKNOWN), pm["exposure_state"]]
        report["exposure_state"] = max(
            states, key=lambda state: rank.index(state) if state in rank else len(rank))

    def tool_writes(self, handle: str) -> list[dict]:
        """The venue writes this decision made through tools, in submission order.

        Guarantees only intents durably written under this handle's tool slots
        are returned; the answer's own market order (client id == handle) is not.
        A vault write is a venue write like an order and is returned beside them,
        and a Polymarket write is one of them too: a decision acts once, on any venue.
        """
        polymarket = getattr(self, "polymarket", None)
        return [intent for client_id, intent in (
                    *self.order_intents.items(), *getattr(self, "vault_intents", {}).items())
                if intent["handle"] == handle and client_id != handle] + (
            polymarket.writes_of(handle) if polymarket is not None else [])

    def executed_operations(self, handle: str) -> list[dict]:
        """What this decision executed at the venue, as its evaluators may see it.

        Guarantees each row is a durable intent and the venue's latest answer to
        it — never the producer's own narrative — so a judge can weigh a claim
        against the operations the decision actually took.
        """
        intents = list(self.tool_writes(handle))
        if handle in self.order_intents:
            intents.append(self.order_intents[handle])
        rows = []
        for intent in intents:
            result = intent.get("result") or {}
            rows.append({
                "operation": intent["operation"], "client_id": intent["client_id"],
                "args": dict(intent["args"]), "status": result.get("status"),
                **{key: result[key] for key in ("order_id", "filled_size", "avg_px", "error")
                   if result.get(key) is not None},
            })
        return rows

    def _execute_outputs(self, ret: Return, kind: str | None = None) -> None:
        """Place the market order an answer names, at most once, and only a producer kind's.

        Invariant (the one gate an answer's order passes): a venue write happens
        only from an answer that is a valid, non-declining order of its contract,
        or from a venue write tool call the seat made and the kernel admitted
        (``_run_tool``, never here). So nothing is placed for a return that is not
        ``ok`` (a malformed or failed reply, or a decline the invocation rewrote to
        ``refused``), nor for one whose outputs decline (``declines``: ``status``
        reads ``cannot``, whatever order fields sit beside it), nor for one that
        is not a dict. Only the
        contract's fields are read; the kernel never reads a reply's prose to
        decide what the seat meant (§I.a: it never chooses a seat's action).

        Guarantees nothing is placed for a return whose kind does not own the answer
        order (``ANSWER_ORDER_KINDS``; primitive audit F7): a population kind's
        ``action`` is its own word. The kind is ``kind`` when the caller knows it,
        else the one the return bound (``return_kinds``), else its author's only
        kind; an unknown kind places nothing. For a producer kind every earlier
        rule holds: a decision acts once, and an answer never trades in place of a
        refused or dropped write.
        """
        from factorylab.cortex.assembly import ANSWER_ORDER_KINDS, declines

        out = ret.outputs
        attempted = self.venue_attempts.pop(ret.handle, None)
        if (self.wallet.dead or ret.status != "ok" or not isinstance(out, dict)
                or declines(out)):
            return
        kind = kind or self.return_kinds.get(ret.handle)
        if kind is None:
            owner = self.assemblies.get(self.handle_to_assembly.get(ret.handle, ""))
            emits = owner.spec.emits if owner is not None else ()
            kind = emits[0] if len(emits) == 1 else None
        if kind not in ANSWER_ORDER_KINDS:
            return
        if out.get("action") != "order":
            if str(out.get("action", "")).lower().startswith(("buy:", "sell:")):
                self._refuse_order(ret.handle, 'action labels are not orders; use action="order" '
                                   'with explicit coin, side and size')
            return
        # A decision acts once. When it already wrote to the venue through a tool,
        # "order" in its answer names that trade; executing it would trade twice.
        written = self.tool_writes(ret.handle)
        if written:
            self.ledger.append({"kind": "order.reported", "handle": ret.handle,
                                "client_ids": [w["client_id"] for w in written],
                                "reason": "the decision already wrote to the venue "
                                          "through tools; the answer reports it"})
            return
        # A decision whose venue write was refused, or whose writing batch was
        # dropped whole, has not acted -- and its answer must not act in the write's
        # place: the answer names the trade the refused write meant (or reports it),
        # and a market order is not the resting limit, the hedge or the close it was.
        batch_dropped = any(d.get("section") == "tool_calls" and "index" not in d
                            for d in ret.dropped)
        if attempted or batch_dropped:
            # The refusal states the fact and no remedy (smuggling audit D6).
            self._refuse_order(
                ret.handle, "nothing was submitted: this decision's venue write was refused "
                f"({attempted or 'its tool batch was dropped'}), and its answer's order does "
                "not execute in the write's place")
            return
        side = out.get("side")
        if not isinstance(side, str) or side.lower() not in ("buy", "sell"):
            self._refuse_order(
                ret.handle, 'nothing was submitted: action "order" named no coin, side and '
                "size, and this decision placed nothing through a venue tool. A market "
                'order is the answer {"action": "order", "coin", "side", "size"}; a limit '
                "order is the tool venue.place_limit {coin, side, size, price}")
            return
        try:
            order = Order(
                str(out["coin"]),
                side.lower() == "buy",
                Decimal(str(out["size"])),
                client_id=ret.handle,
                market=out.get("market", "perp"),
            )
        except KeyError:
            self._refuse_order(ret.handle, 'nothing was submitted: an answer order names its '
                               'coin, side and size')
            return
        except (ValueError, ArithmeticError) as exc:
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

    def _class_transfer_pending(self) -> bool:
        pending = getattr(self.treasury, "state", None)
        return bool(pending and pending["status"] == "submitted"
                    and pending["direction"] in ("spot_to_perps", "perps_to_spot"))

    def _spot_shortfall(self, operation: str, args: dict) -> str | None:
        """Why a spot sell or close exceeds the inventory this world accounts, or None."""
        if args.get("market") != "spot" or not (
            operation == "venue.close" or (
                operation in ("venue.place_market", "venue.place_limit")
                and args.get("side") == "sell"
            )
        ):
            return None
        held = self.spot_inventory.get(args["coin"], (Decimal(0), Decimal(0)))[0]
        quantity = held if args.get("size") is None else Decimal(str(args["size"]))
        lots = sum((lot.size for lot in self.consequences.table.lots
                    if lot.coin == args["coin"] and lot.market == "spot"), 0)
        if quantity <= 0 or quantity > min(held, lots):
            return "spot sell exceeds accounted inventory"
        return None

    def venue_batch_refusal(self, seat: str, handle: str,
                            writes: list[tuple[str, str, dict]]) -> tuple[int, str] | None:
        """The first write of a batch that would be refused, and why, or None.

        Guarantees a batch of venue writes is weighed whole before any of it is
        submitted: each write meets the same collateral, class-transfer and
        spot-inventory tests it would meet alone, and two identical placements in
        one batch are one order written twice (a decision acts once). An order
        identical to one an earlier decision left resting is not refused: the venue
        allows it and fees price it (Chapter II rulings, R6). ``writes`` is (slot, tool, args)
        in batch order. A hedge whose second leg would be refused therefore never
        leaves its first leg standing alone. Collateral is weighed per write against
        the account as it is now, less what the batch's earlier writes take from the
        same pool: the margin an earlier perp order needs and the USDC a vault write
        moves out of perps are not free for a later perp order or vault write, and
        an earlier spot buy's cost is not free for a later spot buy. Without that, a
        deposit and an order that each fit alone passed together and left one leg
        standing when the venue refused the other.
        """
        from factorylab.world.venue_tools import VAULT_WRITES

        placed: set[tuple] = set()
        committed = Decimal(0)  # taken from free perps collateral by earlier writes
        spot_committed = Decimal(0)  # taken from spot USDC by earlier spot buys
        for index, (slot, tool, args) in enumerate(writes):
            client_id = f"{handle}:{slot}"
            if client_id in self.order_intents or client_id in getattr(
                    self, "vault_intents", {}):
                continue  # a retry reconciles; it is not a new write
            if self._class_transfer_pending() and tool != "venue.cancel":
                return index, "class transfer awaiting receipt"
            if tool in VAULT_WRITES:
                reason, moved = self._vault_refusal(tool, args, committed=committed)
                if reason:
                    return index, reason
                committed += moved
                continue
            shortfall = self._spot_shortfall(tool, args)
            if shortfall:
                return index, shortfall
            if tool not in ("venue.place_market", "venue.place_limit"):
                continue
            key = (tool, args.get("coin"), args.get("side"), str(args.get("size")),
                   str(args.get("price")), args.get("market", "perp"))
            if key in placed:
                return index, "the same order is placed twice in one batch"
            placed.add(key)
            try:
                size = Decimal(str(args.get("size")))
                price = Decimal(str(args["price"])) if "price" in args else None
            except ArithmeticError:
                return index, "size or price is not a number"
            coin, is_buy = str(args.get("coin")), args.get("side") == "buy"
            reduce_only = args.get("reduce_only") is True
            reason = self._order_collateral(
                handle, coin, size, is_buy, price, reduce_only=reduce_only,
                committed=spot_committed if "/" in coin else committed)
            if reason:
                return index, reason
            if not reduce_only:
                taken = self._order_requirement(coin, size, is_buy, price)
                if "/" in coin:
                    spot_committed += taken
                else:
                    committed += taken
        return None

    def _order_requirement(self, coin: str, size: Decimal, is_buy: bool,
                           price: Decimal | None = None) -> Decimal:
        """What an accepted order takes from its pool's free balance, as the collateral
        check weighs it: a perp order's initial margin at the venue's leverage, a spot
        buy's cost. Zero where the venue has not said enough to know."""
        try:
            mids = self._tick_mids()
            mark = max(mids[coin], price or mids[coin])
            if "/" in coin:
                return size * mark if is_buy else Decimal(0)
            view = self._collateral_view(coin, "perp")
            current = Decimal(str(view.get("position_size", 0)))
            target = current + (size if is_buy else -size)
            increase = max(Decimal(0), abs(target) - abs(current)) * mark
            leverage = self._order_leverage(coin, view)
        except (AttributeError, KeyError, ValueError, ArithmeticError, RuntimeError,
                TypeError):
            return Decimal(0)
        return increase / leverage if leverage else Decimal(0)

    def _venue_write(self, handle: str, operation: str, args: dict, *, slot: str) -> dict:
        """Every venue write has a durable intent and a stable identity before submission."""
        if self._class_transfer_pending() and operation != "venue.cancel":
            return self._refuse_order(handle, "class transfer awaiting receipt")
        client_id = handle if slot == "output" else f"{handle}:{slot}"
        if client_id not in self.order_intents:
            blocked = self._spot_shortfall(operation, args)
            if blocked:
                return self._refuse_order(handle, blocked)
        previous = self.order_intents.get(client_id)
        if previous is not None:
            if previous["operation"] != operation or previous["args"] != args:
                return self._refuse_order(handle, "client id already binds another intent")
            if previous["result"]["status"] == "uncertain":
                if self._polls_exhausted(client_id):
                    # The schedule is spent: repeating the identical write asks the
                    # venue nothing new, and it may never resubmit. It reads back
                    # the unresolved answer it already has.
                    self._give_up_on_order(client_id)
                    return dict(previous["result"])
                return self._recover_order(client_id)
            return dict(previous["result"])
        # An uncertain intent blocks only its own identity: repeating it reconciles
        # (above) and never resubmits. It never shuts the coin: another write on the
        # same coin -- another seat's, or a close or cancel -- carries its own identity,
        # and one lost acknowledgement used to refuse every one of them forever.
        intent ={"handle": handle, "client_id": client_id, "operation": operation,
                  "args": dict(args), "result": {"status": "uncertain"}}
        self.ledger.append({"kind": "order.intent", **intent})
        self.order_intents[client_id] = intent
        self.consequences.order_intent(client_id, handle, args["coin"])
        # Submitted or lost, a write is the venue possibly moving: nothing observed
        # before it describes the account an order is weighed against afterwards.
        self._venue_moved()
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
        # What the venue says about this identity can be a fill nobody had observed.
        self._venue_moved()
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
        uncertain = result["status"] == "uncertain"
        polls = int(intent.get("polls", 0)) + int(uncertain)
        self.ledger.append({"kind": "order.uncertain" if uncertain
                            else "order.acknowledged", "client_id": client_id,
                            "handle": intent["handle"], "result": result,
                            **({"poll": polls} if uncertain else {})})
        self.order_intents[client_id] = {**intent, "result": dict(result), "polls": polls}
        if result["status"] != "uncertain":
            if intent["operation"] == "venue.cancel":
                if result["status"] == "cancelled":
                    self.consequences.cancel(intent["args"]["order_id"], self.n)
            else:
                attributed = result
                if result["status"] == "cancelled" and Decimal(str(result["filled_size"])) > 0:
                    attributed = {**result, "status": "filled"}
                self.consequences.order_result(intent["handle"], attributed, intent["args"], self.n)
            before = self.consequences.table
            self._replay_deferred(self.consequences.order_acknowledged(client_id), before)
        return dict(result)

    def _replay_deferred(self, events: list[tuple[str, dict, int]], before=None) -> None:
        """Account the economic events a released hold was deferring, in their own order.

        A hold is released by an answer (``order_acknowledged``) or, when no
        answer will ever come, by ``release_unresolved``; either way the events
        it held back are accounted the same way here. The consequence book has
        already replayed them by the time this runs, so each spot fill is checked
        against the table as it stood before the release (``before``): checking it
        against the table that already holds it would execute it twice.
        """
        spot_table = before if before is not None else self.consequences.table
        corrections = []
        for kind, payload, _event in events:
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
                                            "exchange_pnl", "venue_spot",
                                            str(payload["order_id"])))
                self._record_fill_notional(payload)
        if corrections:
            self._settle_venue(corrections)
            delta = sum(change for change, *_rest in corrections)
            self.realized_to_date += delta
            self.window.realized_pnl_micro += delta

    def _polls_exhausted(self, client_id: str) -> bool:
        """Whether this intent has already spent its bounded poll schedule."""
        intent = self.order_intents.get(client_id)
        return intent is not None and int(intent.get("polls", 0)) >= UNCERTAIN_ORDER_POLLS

    def _give_up_on_order(self, client_id: str) -> None:
        """Say once, in the diary, that this order's identity was never confirmed, and
        release the hold that saying so leaves behind (R4-C).

        PR #105 bounded the asking and stopped there, because releasing the hold
        meant deciding what an order of unknown fill status means for the return
        that sent it. It means the return's consequence is unknown: it settles
        censored for documented external unobservability, and every later
        return's outcome resolves again. The intent is then terminal
        (``unresolved``): it is polled no more and blocks nothing -- not its coin,
        not another seat, not a close or a cancel. What stays is the exposure: the
        kill wind-down still reads the venue for it, and a fill the venue
        eventually admits still belongs to this return.
        """
        intent = self.order_intents[client_id]
        if intent.get("unresolved"):
            return
        self.ledger.append({"kind": "order.unresolved", "client_id": client_id,
                            "handle": intent["handle"], "coin": intent["args"].get("coin"),
                            "operation": intent["operation"], "polls": int(intent.get("polls", 0)),
                            "result": dict(intent["result"])})
        self.order_intents[client_id] = {**intent, "unresolved": True}
        before = self.consequences.table
        self._replay_deferred(self.consequences.release_unresolved(client_id, self.n), before)

    def _reconcile_orders(self, *, final: bool = False) -> None:
        """Pending identities are reconciled before consuming newly observed venue fills.

        An uncertain intent is polled on a bounded schedule: at most
        ``UNCERTAIN_ORDER_POLLS`` answers in all, each ledgered once, then one
        ``order.unresolved`` and no further polling. The terminal reconciliation
        of a kill wind-down (``final``) still reads the venue for it: the last
        read of a dying runtime is owed to the order whatever the schedule spent.
        """
        for client_id, intent in list(self.order_intents.items()):
            if intent["result"]["status"] != "uncertain":
                continue
            if self._polls_exhausted(client_id) and not final:
                self._give_up_on_order(client_id)
                continue
            self._recover_order(client_id)
        if getattr(self, "vault_intents", None):
            self._reconcile_vault_intents(final=final)

    def _order_collateral(
        self, handle: str, coin: str, size: Decimal, is_buy: bool,
        price: Decimal | None = None, *, reduce_only: bool = False,
        committed: Decimal = Decimal(0),
    ) -> str | None:
        """New exposure is collateralised by the pot the venue actually charges.

        Edition 3 C5 keeps three quantities apart: the learning score, the seat's
        spending entitlement, and the assets a custodian holds. Margin is charged
        by the venue against money already at the venue, so that is the pot the
        requirement is weighed against -- never the compute wallet, which is
        authority to buy thoughts and collateral for nothing.

        The pot comes from the venue's own ``collateral_view``, not from a
        summary figure: GPT-6 Pro's third reading §2 found ``equity_usd`` too
        broad, because it includes spot marks that are not eligible collateral
        for a perp. The view states the account mode, the collateral asset, the
        eligible equity, the margin already used, the margin resting orders hold
        and whether that is already inside margin used, the leverage the venue has in effect
        for this instrument, and when it was observed.

        The check is then exactly the reviewer's: incremental margin, plus holds
        not already reflected in margin used, plus what earlier writes of the same
        batch already take, against eligible equity minus margin used.

        Spot and perps are checked separately and against their own balances: a
        spot buy needs the USDC to pay for it, a spot sell needs the base coin to
        deliver. Neither borrows the perps account's equity.

        Unknown or stale collateral blocks new risk: a venue that would not say,
        or that answered from a snapshot older than one tick, cannot be used to
        justify opening exposure. It never blocks a cancellation or a reduction --
        ``reduce_only`` returns before any of this -- and the venue's own refusal
        remains the final word.
        """
        if reduce_only:
            return None
        spot = "/" in coin
        available: Decimal | None = None
        try:
            view = self._collateral_view(coin, "spot" if spot else "perp")
            mids = self._tick_mids()
            mark = max(mids[coin], price or mids[coin])
            # What earlier writes of the same batch already take from this pool is
            # not free for this one: it rides as headroom (``venue_batch_refusal``).
            headroom = committed
            stale = self._collateral_stale(view)
            if stale is not None:
                reason = stale
            elif spot:
                reason = self._spot_collateral(view, coin, size, is_buy, mark, headroom)
            else:
                available = view["eligible_equity_usd"] - view["margin_used_usd"]
                reason = self._perp_collateral(view, coin, size, is_buy, mark, headroom)
            if reason is None:
                return None
        except (AttributeError, KeyError, ValueError, ArithmeticError, RuntimeError,
                TypeError) as exc:
            reason = f"order collateral unavailable: {type(exc).__name__}"
        self._refuse_order(handle, reason, kind="order.infeasible",
                           venue_available_usd=None if available is None else str(available))
        return reason

    def _collateral_view(self, coin: str, market: str = "perp") -> dict:
        """The venue's own collateral view, unchanged.

        There is no declared principal between the venue and the population any
        more (architect decision D1): a cap on how much of the venue's money the
        population may lean on is an objective supplied from outside, a Class-2
        imposition. ``[venue] principal_usd`` is still read so the manifests that
        declare it load with their historical hashes, and it is inert.
        """
        return dict(self.exchange.collateral_view(coin, market))

    def _collateral_stale(self, view: dict) -> str | None:
        """An observation the venue did not just make cannot authorise new risk.

        A deterministic venue computes the view from its own books at the moment
        it is asked, so there is nothing for it to be stale about. A live venue
        stamps the account read the view is built from, and Hyperliquid's adapter
        keeps that stamp when it falls back to its last complete snapshot: the
        fallback is exactly the case worth refusing.

        A timestamp alone does not catch it. A read that succeeded and then an
        endpoint failure inside the same tick falls back to a snapshot whose
        observation time is this tick's, so the view's own ``stale`` marker is
        the fact, and it is refused whatever the age says.
        """
        observed_at = view.get("observed_at_ns")
        if view.get("stale"):
            return "order collateral is stale: the venue did not refresh the account"
        if observed_at is None:
            return "order collateral unavailable: venue reported no observation time"
        if getattr(self.exchange, "deterministic", False):
            return None
        age = self.clock.now_ns - observed_at
        if age > self.m.tick_interval_ns:
            return "order collateral is stale: venue account older than one tick"
        return None

    def _perp_collateral(self, view: dict, coin: str, size: Decimal, is_buy: bool,
                         mark: Decimal, headroom: Decimal) -> str | None:
        """Incremental margin plus unreflected holds plus headroom, against free collateral.

        The margin is charged at the leverage the venue says is in effect for this
        instrument (``leverage_for_instrument``), never at a manifest ceiling and
        never at an assumed 1x. When the venue has not said -- a live account with
        no position and no acknowledged ``set_leverage`` on the coin, or a resting
        order on such a coin -- the requirement is not knowable here, and the
        venue's own acceptance or rejection is the answer.
        """
        current = Decimal(str(view.get("position_size", 0)))
        target = current + (size if is_buy else -size)
        increase = max(Decimal(0), abs(target) - abs(current)) * mark
        if increase == 0:
            return None  # a pure reduction releases collateral rather than needing it
        leverage = self._order_leverage(coin, view)
        raw_holds = view.get("open_order_holds_usd")
        if leverage is None or raw_holds is None:
            return None  # the venue is the authority on what it has not told us
        holds = (Decimal(0) if view["holds_included_in_margin_used"]
                 else Decimal(str(raw_holds)))
        required = increase / leverage + holds + headroom
        available = view["eligible_equity_usd"] - view["margin_used_usd"]
        if required <= available:
            return None
        return "order collateral exceeds venue free collateral"

    def _spot_collateral(self, view: dict, coin: str, size: Decimal, is_buy: bool,
                         mark: Decimal, headroom: Decimal) -> str | None:
        """Spot is delivery, not margin: a buy needs the quote, a sell needs the base."""
        balances = view.get("spot_available") or {}
        if is_buy:
            cost = size * mark + headroom
            usdc = Decimal(str(balances.get("USDC", 0)))
            if cost <= usdc:
                return None
            return "spot buy exceeds venue USDC balance"
        held = Decimal(str(balances.get(coin.split("/")[0], 0)))
        if size <= held:
            return None
        return "spot sell exceeds venue base balance"

    def _order_leverage(self, coin: str, view: dict | None = None) -> Decimal | None:
        """The leverage the venue has in effect for ``coin``, or ``None`` when unknown.

        Read from the venue's own collateral view, which reads it from the account
        (Hyperliquid reports it per position in ``clearinghouseState``) or from the
        venue's acknowledgement of ``set_leverage``. Spot is delivery, never margin.
        """
        if "/" in coin:
            return Decimal(1)
        if view is None:
            view = self._collateral_view(coin, "perp")
        leverage = view.get("leverage_for_instrument")
        if leverage is None:
            return None
        leverage = Decimal(str(leverage))
        return leverage if leverage.is_finite() and leverage > 0 else None
