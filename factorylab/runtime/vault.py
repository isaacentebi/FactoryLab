"""Runtime vault method group: the venue's vaults as a surface (``[venue] vault_tools``).

A vault moves money between custodians the factory already has names for. Money
deposited leaves the perps account's free collateral and becomes equity in a vault;
a withdrawal brings it back less any commission the vault's leader takes, and the
difference from the deposit's basis is venue P&L. None of that is new money.

A vault this factory leads can also be paid from outside: a depositor who
withdraws at a profit pays the leader a commission (factorylab/world/vaults.py
cites the venue's terms). That is income -- money entering from outside the
factory -- in the sense of the essay's charter and loop (II.IV) and the
distinction the treasury already keeps: it is booked through ``Treasury.earn`` and
the runtime's income path, never as financing (principal converted into thinking
money) and never as P&L (the factory's own trading). The commission a leader pays
itself on its own withdrawal, and is repaid in the same transaction, is neither.

Writes carry the same discipline as ``_venue_write``: a durable intent under a
stable client id before submission, a lost acknowledgement resolved by reading the
venue (here, the transfer's own ledger row) and never by sending it again, a
bounded poll schedule, and every refusal ledgered and told to its author.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from factorylab.kernel.money import usd_to_micro
from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS
from factorylab.world.vaults import (
    CREATE_FEE_USD,
    LEADER_MIN_FRACTION,
    UNPARSED,
    check_create,
    exact_micro,
    leader_share_after,
    own_withdraw_hashes,
)
from factorylab.world.venue_tools import _json_value

#: The service name a vault leader's commission is booked under as income.
COMMISSION_SERVICE = "vault.leader_commission"
#: A lost vault write is looked for in the venue ledger from this long before its
#: intent, so a clock difference between this host and the venue cannot hide its row.
LOOKUP_SKEW_NS = 60 * 10**9
#: The fields a settled write must carry: the venue transaction it is bound to, and
#: the amounts its fee or P&L is booked from.
_SETTLEMENT_FIELDS = {"venue.vault_create": ("fee_usd", "hash"),
                      "venue.vault_deposit": ("hash",),
                      "venue.vault_withdraw": ("net", "basis", "hash")}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _null_hash(tx: str) -> bool:
    try:
        return int(tx, 16) == 0
    except (TypeError, ValueError):
        return not tx


class VaultMixin:
    """Preserve runtime state and behavior for vault operations."""

    def _vault_account(self) -> str | None:
        """The venue account this world's vault rows are addressed to."""
        address = getattr(self.exchange, "_address", None)
        return address.lower() if isinstance(address, str) else None

    def _vault_read(self, tool_id: str, args: dict) -> dict:
        """Guarantees the venue's own vault record, JSON-ready, or the reason it is unread."""
        try:
            if tool_id == "venue.vault_details":
                result = self.exchange.vault_details(args["vault"])
            else:
                result = self.exchange.vault_equities()
        except Exception as exc:  # noqa: BLE001 - an unread record is reported, never guessed
            return {"error": f"vault record unavailable: {type(exc).__name__}"}
        return _json_value(result)

    def _vault_refusal(self, operation: str, args: dict, *,
                       committed: Decimal = Decimal(0)) -> tuple[str | None, Decimal]:
        """Why the venue would refuse this vault write, or None; and what it takes from perps.

        Guarantees the tests are the venue's own terms read from the venue now: the
        creation minimum and fee, a closed vault or one shut to deposits, a
        withdrawal above the equity held, inside its lockup, above the venue's
        withdrawable figure, or taking a leader below 5%. Money leaving perps is
        weighed against free perps collateral -- the pot ``_order_collateral``
        weighs orders against -- less what earlier writes in the same batch
        (``committed``) already take. The venue's own refusal remains final.
        """
        try:
            usd = _dec(args.get("usd"))
        except ArithmeticError:
            return "usd is not a number", Decimal(0)
        if not usd.is_finite() or usd <= 0:
            return "usd must be positive", Decimal(0)
        if exact_micro(usd) is None:
            return "usd is finer than one micro-USD (at most 6 decimals)", Decimal(0)
        if operation == "venue.vault_create":
            reason = check_create(args.get("name"), args.get("description"), usd)
            if reason:
                return reason, Decimal(0)
            need = usd + CREATE_FEE_USD
        else:
            try:
                detail = self.exchange.vault_details(str(args.get("vault")))
            except Exception as exc:  # noqa: BLE001 - an unread vault is not an open one
                return f"vault record unavailable: {type(exc).__name__}", Decimal(0)
            if detail.get("error"):
                return f"vault record unavailable: {detail['error']}", Decimal(0)
            if detail.get("is_closed"):
                return "vault is closed", Decimal(0)
            if operation == "venue.vault_deposit":
                if not detail.get("allow_deposits", True) and not detail.get("is_leader"):
                    return "vault does not accept deposits", Decimal(0)
                need = usd
            else:
                return self._withdraw_refusal(detail, usd), Decimal(0)
        reason = self._vault_collateral(need + committed)
        return reason, (Decimal(0) if reason else need)

    @staticmethod
    def _withdraw_refusal(detail: dict, usd: Decimal) -> str | None:
        held = _dec(detail.get("own_equity_usd") or 0)
        if usd > held:
            return f"withdrawal of {usd} exceeds this account's equity in the vault ({held})"
        lockup, seen = detail.get("own_lockup_until_ns"), detail.get("observed_at_ns")
        if lockup is not None and seen is not None and seen < lockup:
            return f"deposit locked until {lockup} ns; the venue read was at {seen} ns"
        if detail.get("is_leader"):
            after = leader_share_after(held, _dec(detail.get("equity_usd") or 0), usd)
            if after is not None and after < LEADER_MIN_FRACTION:
                return "leader share would fall below 5% of the vault"
        cap = detail.get("max_withdrawable_usd")
        if cap is not None and usd > _dec(cap):
            return f"withdrawal exceeds the venue's withdrawable amount ({cap})"
        return None

    def _vault_collateral(self, need: Decimal) -> str | None:
        """Free perps collateral covers ``need`` USDC leaving perps, or the reason it does not.

        The same view and the same staleness rule as ``_order_collateral``: margin
        used and unreflected resting-order holds are not free, the manifest's
        headroom is precommitted, and a view the venue did not just give -- or a
        hold it would not state -- cannot justify moving collateral away.
        """
        try:
            view = self._collateral_view(self.m.exchange.coins[0], "perp")
            stale = self._collateral_stale(view)
            if stale is not None:
                return f"vault transfer refused: {stale}"
            raw = view.get("open_order_holds_usd")
            if raw is None and not view["holds_included_in_margin_used"]:
                return ("vault transfer collateral unknown: the venue has not said what "
                        "resting orders hold")
            holds = Decimal(0) if view["holds_included_in_margin_used"] else _dec(raw)
            free = (_dec(view["eligible_equity_usd"]) - _dec(view["margin_used_usd"])
                    - holds)
        except (AttributeError, KeyError, ValueError, ArithmeticError, RuntimeError,
                TypeError, IndexError) as exc:
            return f"vault transfer collateral unavailable: {type(exc).__name__}"
        if need <= free:
            return None
        return (f"insufficient collateral: {need} USDC would leave perps and "
                f"{max(free, Decimal(0))} USDC of perps collateral is free")

    def _vault_write(self, handle: str, operation: str, args: dict, *, slot: str) -> dict:
        """Guarantees a durable intent before submission and one submission per client id.

        A retry with the same identity reads back what it received, or resolves an
        uncertain answer from the venue's ledger; it is never sent again. A refusal
        happens before any intent and is published to its author.
        """
        if self._class_transfer_pending():
            return self._refuse_order(handle, "class transfer awaiting receipt",
                                      kind="vault.refused", operation=operation)
        client_id = f"{handle}:{slot}"
        previous = self.vault_intents.get(client_id)
        if previous is not None:
            if previous["operation"] != operation or previous["args"] != args:
                return self._refuse_order(handle, "client id already binds another intent",
                                          kind="vault.refused", operation=operation)
            if previous["result"]["status"] == "uncertain":
                if self._vault_polls_exhausted(client_id):
                    self._give_up_on_vault(client_id)
                    return dict(previous["result"])
                return self._recover_vault(client_id)
            return dict(previous["result"])
        reason, _moved = self._vault_refusal(operation, args)
        if reason:
            return self._refuse_order(handle, reason, kind="vault.refused", operation=operation)
        intent = {"handle": handle, "client_id": client_id, "operation": operation,
                  "args": dict(args), "result": {"status": "uncertain"},
                  "since_ns": self.clock.now_ns}
        self.ledger.append({"kind": "vault.intent", **intent})
        self.vault_intents[client_id] = intent
        # Submitted or lost, the write may have moved the account's collateral.
        self._venue_moved()
        try:
            usd = _dec(args["usd"])
            if operation == "venue.vault_create":
                result = self.exchange.vault_create(args["name"], args["description"], usd,
                                                    client_id=client_id)
            else:
                result = self.exchange.vault_transfer(
                    args["vault"], operation == "venue.vault_deposit", usd, client_id=client_id)
        except Exception as exc:  # noqa: BLE001 - a lost write is uncertain, never failed
            result = {"status": "uncertain", "error": f"write exception: {type(exc).__name__}"}
        if not isinstance(result, dict) or result.get("status") not in ("ok", "rejected"):
            self._record_vault_result(client_id, result if isinstance(result, dict) else {
                "status": "uncertain", "error": "write returned a non-object acknowledgement"})
            return self._recover_vault(client_id)
        return self._record_vault_result(client_id, result)

    def _vault_key(self, intent: dict) -> tuple:
        args = intent["args"]
        return (intent["operation"], str(args.get("vault") or "").lower(), _dec(args["usd"]))

    def _vault_claimed(self, client_id: str) -> frozenset[str]:
        """The venue transactions other vault writes are already bound to."""
        return frozenset(i["result"]["hash"] for cid, i in self.vault_intents.items()
                         if cid != client_id and i["result"].get("hash")) | frozenset(
            # Transactions of released decisions' writes stay bound (wave 17b).
            getattr(self, "vault_released_hashes", ()))

    def _vault_lookup(self, client_id: str) -> dict:
        """Ask the venue's ledger which row is this write's; never resubmit anything.

        Guarantees a row another write is bound to is never offered, and that writes
        alike in operation, vault and amount and still unbound are resolved together,
        in submission order, so two real writes of one amount each find their own row
        and a lost one never borrows an acknowledged one's.
        """
        intent = self.vault_intents[client_id]
        key = self._vault_key(intent)
        peers = sorted(
            ((int(i["since_ns"]), cid) for cid, i in self.vault_intents.items()
             if not i.get("unresolved") and not i["result"].get("hash")
             and i["result"]["status"] in ("uncertain", "ok") and self._vault_key(i) == key),
        )
        order = [cid for _since, cid in peers]
        since = min((s for s, _ in peers), default=int(intent["since_ns"]))
        try:
            return self.exchange.vault_lookup(
                client_id, operation=intent["operation"], args=intent["args"],
                since_ns=max(0, since - LOOKUP_SKEW_NS), claimed=self._vault_claimed(client_id),
                position=order.index(client_id) if client_id in order else 0,
                peers=max(1, len(order)))
        except Exception as exc:  # noqa: BLE001
            return {"status": "uncertain", "error": f"lookup exception: {type(exc).__name__}"}

    def _recover_vault(self, client_id: str) -> dict:
        """Ask the venue what became of an uncertain write; never resubmit it."""
        self._venue_moved()
        return self._record_vault_result(client_id, self._vault_lookup(client_id))

    def _record_vault_result(self, client_id: str, result: dict) -> dict:
        """Guarantees an acknowledgement binds its venue transaction to this write alone.

        The bound hash is part of the intent, so it is checkpointed with it and a
        resumed world still knows which row is whose.
        """
        result = json.loads(json.dumps(result, default=str))
        intent = self.vault_intents[client_id]
        if result.get("status") not in ("ok", "rejected"):
            result = {"status": "uncertain", "error": str(
                result.get("error") or "venue acknowledgement unavailable")[:300]}
        elif result.get("hash") and result["hash"] in self._vault_claimed(client_id):
            result = {"status": "uncertain",
                      "error": "venue row already bound to another vault write"}
        uncertain = result["status"] == "uncertain"
        polls = int(intent.get("polls", 0)) + int(uncertain)
        self.ledger.append({"kind": "vault.uncertain" if uncertain else "vault.acknowledged",
                            "client_id": client_id, "handle": intent["handle"],
                            "operation": intent["operation"], "result": result,
                            **({"poll": polls} if uncertain else {})})
        self.vault_intents[client_id] = {**intent, "result": dict(result), "polls": polls}
        if result["status"] == "ok":
            self._vault_settle(client_id)
        return dict(result)

    def _vault_polls_exhausted(self, client_id: str) -> bool:
        intent = self.vault_intents.get(client_id)
        return intent is not None and int(intent.get("polls", 0)) >= UNCERTAIN_ORDER_POLLS

    def _give_up_on_vault(self, client_id: str) -> None:
        """Say once that this write's outcome, or its amounts, were never confirmed.

        An uncertain write is ``vault.unresolved``. An acknowledged one whose venue
        row never arrived is ``vault.unbooked``: its custody move is ledgered, and
        the fee or P&L its row would have stated is named as not booked, rather than
        silently absent.
        """
        intent = self.vault_intents[client_id]
        if intent.get("unresolved"):
            return
        kind = "vault.unresolved" if intent["result"]["status"] == "uncertain" else (
            "vault.unbooked")
        self.ledger.append({"kind": kind, "client_id": client_id,
                            "handle": intent["handle"], "operation": intent["operation"],
                            "polls": int(intent.get("polls", 0)),
                            "result": dict(intent["result"])})
        self.vault_intents[client_id] = {**intent, "unresolved": True}

    def _vault_settle(self, client_id: str) -> None:
        """Book one acknowledged vault write's custody move and venue effect, each once.

        Guarantees money leaving perps is ledgered as moved on acknowledgement, at the
        amount requested; money arriving from a vault is ledgered at the amount the
        venue says arrived, once its row states it. The venue effect -- the creation
        fee, or a withdrawal's realised P&L (net received plus any commission repaid
        to this leader, less the basis withdrawn) -- is booked when the venue has
        stated the amounts and its row is bound: a live acknowledgement carries
        neither, and the transfer's own ledger row supplies them later.
        """
        intent = self.vault_intents[client_id]
        result, operation, handle = intent["result"], intent["operation"], intent["handle"]
        vault = result.get("vault") or str(intent["args"].get("vault", "")).lower() or None
        usd = _dec(intent["args"]["usd"])
        withdraw = operation == "venue.vault_withdraw"
        if not intent.get("custody_booked"):
            seat = self.handle_to_assembly.get(handle) or self.outcomes.seat_of(handle)
            if vault is not None and vault not in self.vault_book:
                self.vault_book[vault] = {"seat": seat, "handle": handle,
                                          "leader": operation == "venue.vault_create",
                                          "since_ns": self.clock.now_ns}
            if not withdraw:
                self._vault_custody(intent, vault, "venue_perps", "venue_vaults",
                                    usd_to_micro(usd, rounding="nearest"))
            intent = {**intent, "custody_booked": True}
        needed = _SETTLEMENT_FIELDS.get(operation, ())
        if not intent.get("settled") and all(result.get(k) is not None for k in needed):
            if operation == "venue.vault_create":
                self._book_vault_effect(handle, -usd_to_micro(_dec(result["fee_usd"]),
                                                              rounding="nearest"),
                                        f"vault_fee:{client_id}", "venue_perps")
            elif withdraw:
                arrived = _dec(result["net"]) + _dec(result.get("commission_rebate") or 0)
                self._vault_custody(intent, vault, "venue_vaults", "venue_perps",
                                    usd_to_micro(arrived, rounding="nearest"))
                self._book_vault_effect(handle, usd_to_micro(arrived - _dec(result["basis"]),
                                                             rounding="nearest"),
                                        f"vault_withdraw:{client_id}", "venue_vaults")
            intent = {**intent, "settled": True}
        self.vault_intents[client_id] = intent

    def _vault_custody(self, intent: dict, vault: str | None, source: str, dest: str,
                       micro: int) -> None:
        self.ledger.append({"kind": "vault.custody", "client_id": intent["client_id"],
                            "handle": intent["handle"], "vault": vault,
                            "operation": intent["operation"], "from": source, "to": dest,
                            "micro": micro, "ts": self.clock.now_ns})

    def _book_vault_effect(self, handle: str, delta: int, reference: str, custody: str) -> None:
        """A vault's venue effect is booked where ``_settle_venue`` books a fill's."""
        if not delta:
            return
        self.budget.book_venue(delta, f"exchange_pnl:{reference}")
        self.ledger.append({"kind": "venue.settled", "custody": custody, "amount": delta,
                            "reference": reference, "reason": "exchange_pnl",
                            "handle": handle, "event": self.n, "ts": self.clock.now_ns})
        by_custody = self.venue_deltas.setdefault(handle, {})
        by_custody[custody] = by_custody.get(custody, 0) + delta

    def _reconcile_vault_intents(self, *, final: bool = False) -> None:
        """Resolve uncertain vault writes, and bind and settle acknowledged ones.

        Both are asked about on the bounded schedule orders are: at most
        ``UNCERTAIN_ORDER_POLLS`` reads, then one ``vault.unresolved`` (or
        ``vault.unbooked``) and no more, except the terminal reconciliation of a
        dying runtime, which asks once more.
        """
        for client_id in sorted(self.vault_intents,
                                key=lambda c: (int(self.vault_intents[c]["since_ns"]), c)):
            intent = self.vault_intents[client_id]
            if intent.get("unresolved"):
                continue
            status = intent["result"]["status"]
            settling = status == "ok" and not intent.get("settled")
            if status != "uncertain" and not settling:
                continue
            if self._vault_polls_exhausted(client_id) and not final:
                self._give_up_on_vault(client_id)
                continue
            if status == "uncertain":
                self._recover_vault(client_id)
                continue
            found = self._vault_lookup(client_id)
            polls = int(intent.get("polls", 0)) + 1
            if found.get("status") == "ok":
                merged = {**intent["result"],
                          **{k: v for k, v in found.items() if k != "status"}}
                self.vault_intents[client_id] = {**intent, "result": merged, "polls": polls}
                self.ledger.append({"kind": "vault.settlement_read", "client_id": client_id,
                                    "handle": intent["handle"], "result": _json_value(found)})
                self._vault_settle(client_id)
            else:
                self.vault_intents[client_id] = {**intent, "polls": polls}

    def _collect_vault_income(self) -> None:
        """Book each leader commission the venue paid this account from outside, once.

        A ``vaultLeaderCommission`` row names no vault, so a commission is this
        world's income only when every vault the account leads is one this world's
        seats created; otherwise it could be an operator's vault or one that predates
        the world, and it is ledgered as skipped. A commission in the transaction of
        one of this account's own withdrawals is its own money repaid, whatever the
        amount, and is booked as nothing. A page carrying a vault row that could not
        be read books no commission at all: the unread row could be that withdrawal.
        Income is booked through ``Treasury.earn``, idempotent on the row's identity,
        and then the runtime's income path; the seat credited is the one that created
        the vaults this account leads, when they all share one, and otherwise the pool.
        """
        if not getattr(self.m.exchange, "vault_tools", False):
            return
        try:
            rows = self.exchange.vault_ledger(self.vault_ledger_cursor_ns)
            leading = ({str(v["vault"]).lower() for v in self.exchange.vault_equities()["leading"]}
                       if any(r["type"] == "vaultLeaderCommission" for r in rows) else set())
        except Exception as exc:  # noqa: BLE001 - an unread ledger books nothing
            self.ledger.append({"kind": "vault.ledger_unavailable",
                                "reason": type(exc).__name__, "ts": self.clock.now_ns})
            return
        if not rows:
            return
        account = self._vault_account()
        own = own_withdraw_hashes(rows, account)
        unparsed = [r["hash"] for r in rows if r["type"] == UNPARSED]
        led = {vault for vault, entry in self.vault_book.items() if entry.get("leader")}
        foreign = sorted(leading - led)
        seats = {self.vault_book[vault].get("seat") for vault in led}
        seat = next(iter(seats)) if len(seats) == 1 else None
        seen = set(self.vault_ledger_seen)
        for row in rows:
            if row["type"] != "vaultLeaderCommission" or not row.get("usd"):
                continue
            if row["ts_ns"] == self.vault_ledger_cursor_ns and row["hash"] in seen:
                continue  # processed on an earlier poll at the inclusive cursor
            micro = usd_to_micro(row["usd"], rounding="nearest")
            if micro <= 0:
                continue
            if row["hash"] in own:
                self.ledger.append({"kind": "vault.commission_returned", "tx": row["hash"],
                                    "micro": micro, "ts": self.clock.now_ns})
                continue
            reason = ("the ledger page carries vault rows that could not be read"
                      if unparsed else
                      "the account leads vaults this world did not create" if foreign
                      else "the account leads no vault this world created" if not led
                      else None)
            if reason is not None:
                self.ledger.append({"kind": "vault.commission_skipped", "tx": row["hash"],
                                    "micro": micro, "reason": reason, "unparsed": unparsed,
                                    "foreign_vaults": foreign, "ts": self.clock.now_ns})
                continue
            tx = row["hash"] if not _null_hash(row["hash"]) else f"{row['hash']}@{row['ts_ns']}"
            try:
                item = self.treasury.earn(COMMISSION_SERVICE, micro, tx, chain="hypercore",
                                          asset="USDC", recipient=row.get("user") or account,
                                          custody="venue_perps", seat=seat,
                                          observed_ns=row["ts_ns"])
            except ValueError:
                continue  # the treasury ledgered the conflict; nothing is booked
            if item is not None:
                self._book_income(item)
        # The cursor stays inclusive: the venue reports milliseconds, so a row indexed
        # late in the newest row's millisecond must still be read on the next poll.
        # Rows already processed at that millisecond are remembered by identity.
        newest = max(r["ts_ns"] for r in rows)
        self.vault_ledger_seen = sorted(
            {r["hash"] for r in rows if r["ts_ns"] == newest}
            | (set(self.vault_ledger_seen) if newest == self.vault_ledger_cursor_ns else set()))
        self.vault_ledger_cursor_ns = newest
