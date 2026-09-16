"""Ledger-first transfers with explicit pots and quarantined uncertain outcomes."""

from __future__ import annotations

import os
from copy import deepcopy
from decimal import Decimal
from typing import Any

from factorylab.kernel.money import money_to_usd, usd_to_money
from factorylab.world.evm import Pending, RailError
from factorylab.world.x402 import TOP_UP_MICRO

# A stalled step is ledgered on its first failed attempt, whenever its reason changes,
# and then once every this many attempts, so a wait of any length stays public without
# writing one item per tick. Attempts are counted per step and never reset by the limit.
PENDING_JOURNAL_EVERY = 10
# The reason a forwarded exit strands when Circle's mint stays unobserved past the bound.
FORWARD_WAIT_EXCEEDED = "forwarded mint not delivered within treasury.forward_wait_windows"
TRANSFER_BLOCKED = "a previous transfer is still pending or stranded"
# Money that entered the wallet's pots, by class: the architect's initial compute credit
# (subsidy), Venice credit bought from trading capital (conversion), and x402 income.
INCOME_CLASSES = ("earned_micro", "subsidy_micro", "converted_from_principal_micro")


def _fresh_income() -> dict:
    return {"earned_micro": 0, "subsidy_micro": None, "converted_from_principal_micro": 0,
            "spool_offset": 0, "receipts": {}}


def _seed_credits(provider: Any) -> int | None:
    """Account credits are independent of an API key's optional spending allowance."""
    if getattr(provider, "name", "") != "openrouter":
        return provider.balance_micro()
    try:
        data = provider._request("GET", "/credits")["data"]
        remaining = Decimal(str(data["total_credits"])) - Decimal(str(data["total_usage"]))
    except Exception:
        # A scoped key may not read account credits. A finite limit minus usage
        # is still a valid available allowance; an unlimited key remains unknown.
        data = provider._request("GET", "/key")["data"]
        if data.get("limit") is None:
            return None
        remaining = Decimal(str(data["limit"])) - Decimal(str(data["usage"]))
    if not remaining.is_finite():
        return None
    return int(remaining * 1_000_000)


def provider_pots(provider: Any) -> tuple[int | None, dict[str, int | None]]:
    """OpenRouter credits and wallet-bound seller credits are counted once in separate pots."""
    seed, sellers = 0, {}
    openrouter = getattr(provider, "openrouter", None)
    venice = getattr(provider, "venice", None)
    if getattr(provider, "name", "") == "venice":
        venice = provider
    elif openrouter is None and hasattr(provider, "balance_micro"):
        openrouter = provider
    if openrouter is not None:
        try:
            seed = _seed_credits(openrouter)
        except Exception:
            seed = None
    if venice is not None:
        try:
            sellers["venice"] = venice.balance_micro()
        except Exception:
            sellers["venice"] = None
    return seed, sellers


class Treasury:
    """Principal is held until settlement; a pending or stranded transfer cannot be spent twice.

    Pot observations are cached while a transfer is in flight and labelled incomplete.
    Reconciliation never silently writes an observed balance into the kernel wallet.
    One transfer at a time serializes each signer's nonce and simplifies recovery.

    A forwarded exit whose burn confirmed but whose mint Circle never delivered is
    stranded after ``forward_wait_windows`` reserve windows and parked in ``stranded``
    with its principal hold: it no longer occupies the transfer slot, and whenever the
    slot is free a tick re-checks it (the forwarder's delivery, or the reserve's own
    self-mint of the still-unclaimed message) and completes it through the same steps.
    """

    def __init__(
        self,
        ledger,
        wallet,
        rail,
        *,
        provider=None,
        fee_ceiling_micro=2_000_000,
        max_venice_per_window=10_000_000,
        max_forward_fees_per_window=1_000_000,
        forward_wait_windows=2,
    ):
        self.ledger, self.wallet, self.rail, self.provider = ledger, wallet, rail, provider
        if type(forward_wait_windows) is not int or forward_wait_windows < 1:
            raise ValueError("forward_wait_windows must be a positive integer")
        self.forward_wait_windows = forward_wait_windows
        if type(fee_ceiling_micro) is not int or fee_ceiling_micro < 0:
            raise ValueError("fee ceiling must be nonnegative integer micro-USD")
        self.fee_ceiling_micro = fee_ceiling_micro
        if type(max_venice_per_window) is not int or max_venice_per_window < 0:
            raise ValueError("Venice window budget must be nonnegative integer micro-USD")
        self.max_venice_per_window = max_venice_per_window
        if type(max_forward_fees_per_window) is not int or max_forward_fees_per_window < 0:
            raise ValueError("forwarding fee window budget must be nonnegative integer micro-USD")
        self.max_forward_fees_per_window = max_forward_fees_per_window
        self.venice_window = 0
        self.venice_spent = 0
        # Forwarding fees quoted for submitted exits this window; a failure still counts.
        self.forward_spent = 0
        self.state: dict | None = None
        self.next_id = 0
        self.last_nonce = 0
        self.gas_spent: dict[str, int] = {}
        self.principal_hold = self.fee_hold = None
        # Recoverable forwarded strands, oldest first, each with its own principal hold.
        self.stranded: list[dict] = []
        self._pots = {"venue": None, "reserve": None, "seed": None, "sellers": {}}
        self.income = _fresh_income()
        # Receipts the wake host's seller wrote for paid calls it served; the runtime,
        # the only writer of this ledger, turns each into an ``income.earned`` item.
        self.income_spool = os.environ.get("FACTORYLAB_INCOME_SPOOL") or None

    def _write(self, kind: str, **fields) -> None:
        self.ledger.append({"kind": "treasury." + kind, **fields})

    def open_window(self, index: int) -> None:
        """A forward reserve-window boundary renews the Venice submission budget once."""
        if type(index) is not int or index < self.venice_window:
            raise ValueError("treasury window must advance monotonically")
        if index != self.venice_window:
            self._write("venice_window", window=index, spent_micro=0, forward_fees_micro=0)
            self.venice_window, self.venice_spent = index, 0
            self.forward_spent = 0

    def _blocking(self) -> bool:
        """The slot is taken: a transfer in flight, or a strand nothing can recover."""
        state = self.state
        return bool(state and (state["status"] == "submitted" or (
            state["status"] == "stranded" and not state.get("recoverable"))))

    def pots(self) -> dict:
        """Return a detached observed view; unknown balances are never converted to zero."""
        pending = self._blocking()
        result = deepcopy(self._pots)
        result["pending"] = pending
        # A stall is public: the last reason a poll or preparation could not complete,
        # and when that wait began on the current step.
        stall = (self.state or {}).get("pending") if pending else None
        result["pending_reason"] = stall["reason"] if stall else None
        result["pending_since"] = stall["since_ns"] if stall else None
        # Parked forwarded strands: burned principal still held, claimable and re-checked.
        result["stranded"] = [
            {"transfer_id": entry["state"]["id"],
             "stranded_micro": entry["state"]["received_micro"],
             "reason": entry["state"]["reason"], "since_ns": entry["state"]["stranded_ns"]}
            for entry in self.stranded]
        values = [result[k] for k in ("venue", "reserve", "seed")]
        values.extend(result["sellers"].values())
        result["complete"] = not pending and all(type(v) is int for v in values)
        result["total_micro"] = sum(values) if result["complete"] else None
        result.update({k: self.income[k] for k in INCOME_CLASSES})
        return result

    def earn(self, service: str, micro: int, tx: str, **detail) -> dict | None:
        """Book one paid service call: ledgered first, then counted as earned income."""
        if not isinstance(service, str) or not service:
            raise ValueError("service id is required")
        if type(micro) is not int or micro <= 0:
            raise ValueError("earned amount must be positive integer micro-USD")
        if not isinstance(tx, str) or not tx:
            raise ValueError("a settlement reference is required")
        receipt_id = tx.lower() if tx.startswith("0x") else tx
        signature = {"service": service, "micro": micro,
                     **{k: detail.get(k) for k in ("payer", "program", "version")}}
        receipts = self.income.get("receipts", {})
        if receipt_id in receipts:
            if receipts[receipt_id] != signature:
                raise ValueError("settlement reference reused with conflicting payment")
            return None
        item = {**detail, "kind": "income.earned", "service": service, "micro": micro, "tx": tx}
        self.ledger.append(item)
        self.income = {**self.income, "earned_micro": self.income["earned_micro"] + micro,
                       "receipts": {**receipts, receipt_id: signature}}
        return item

    def collect_income(self) -> list[dict]:
        """Read the seller's receipt spool through the journal and ledger each new receipt.

        The read is an external observation like a rail balance, so it is recorded
        by the recovery journal and replays byte-for-byte; the consumed offset is
        part of the treasury snapshot, so a receipt is never booked twice.
        """
        if self.income_spool is None:
            return []
        from factorylab.world.income import read_income_spool

        offset = self.income["spool_offset"]
        args = (str(self.income_spool), offset)
        if hasattr(self.ledger, "call"):
            # The name's ``lookup`` suffix classifies the call read-only for replay.
            observed = self.ledger.call("treasury.income.lookup", read_income_spool, args, {})
        else:
            observed = read_income_spool(*args)
        booked = []
        for receipt in observed["receipts"]:
            item = self.earn(
                receipt["service"], receipt["micro"], receipt["tx"],
                payer=receipt.get("payer"), program=receipt.get("program"),
                version=receipt.get("version"), served_ns=receipt.get("ts"),
            )
            if item is not None:
                booked.append(item)
        self.income = {**self.income, "spool_offset": observed["offset"]}
        return booked

    def _gas_view(self) -> dict:
        """The exit route's gas position: never money, so it cannot change completeness."""
        try:
            gas = self.rail.gas_view(dict(self.gas_spent))
        except Exception:
            return {"refill_ready": False, "blocked_by": "gas position unavailable"}
        if self._blocking():
            gas = {**gas, "refill_ready": False, "blocked_by": gas.get("blocked_by")
                   or TRANSFER_BLOCKED}
        return gas

    def refresh_pots(self) -> dict:
        """Persist a complete or explicitly unavailable observation before replacing the view."""
        if self._blocking():
            # Money pots stay the cached observation, labelled incomplete; the gas block
            # is re-read so the population sees the stall and what blocks the next exit.
            if hasattr(self.rail, "gas_view"):
                pots = {**self._pots, "gas": self._gas_view()}
                self._write("pots", pots=pots, pending=True)
                self._pots = pots
            return self.pots()
        if hasattr(self.ledger, "call"):
            seed, sellers = self.ledger.call(
                "treasury.provider_pots", lambda: provider_pots(self.provider), (), {}
            )
        else:
            seed, sellers = provider_pots(self.provider)
        observed = {}
        try:
            observed = self.rail.balances()
            venue, reserve = observed["venue"], observed["reserve"]
            if "venice" in observed:
                sellers["venice"] = observed["venice"]
        except Exception:
            venue = reserve = None
        pots = {"venue": venue, "reserve": reserve, "seed": seed, "sellers": sellers}
        pots.update({k: observed[k] for k in ("perps", "spot") if k in observed})
        if hasattr(self.rail, "gas_view"):
            pots["gas"] = self._gas_view()
        credits = [seed, *sellers.values()]
        if self.income["subsidy_micro"] is None and all(type(v) is int for v in credits):
            # The first complete observation of compute credit is the architect's
            # subsidy: nothing has been converted from principal or earned before it.
            subsidy = sum(credits)
            self._write("subsidy", micro=subsidy, seed_micro=seed, sellers=dict(sellers))
            self.income = {**self.income, "subsidy_micro": subsidy}
        self._write("pots", pots=pots)
        self._pots = pots
        return self.pots()

    def transfer(
        self, direction: str, usd: str | Decimal | int, *, handle: str, now_ns: int
    ) -> dict:
        """A refusal has a reason; a submitted result has references but never promises arrival."""
        direction = "to_reserve" if direction == "to_compute" else direction
        try:
            if direction not in {"to_reserve", "to_venue", "to_venice",
                                 "spot_to_perps", "perps_to_spot"}:
                raise RailError("unsupported treasury direction")
            if isinstance(usd, bool) or not isinstance(usd, str | Decimal | int):
                raise RailError("usd must be an exact decimal string or integer, not a float")
            amount = usd_to_money(str(usd))
            if amount <= 0:
                raise RailError("usd must be positive")
            if direction == "to_venice":
                if amount != TOP_UP_MICRO:
                    raise RailError("to_venice requires the fixed $5 tranche")
                if self.venice_spent + amount > self.max_venice_per_window:
                    raise RailError("treasury.max_venice_per_window exhausted")
            if self._blocking():
                raise RailError(TRANSFER_BLOCKED)
            self.rail.preflight(direction, amount, self.gas_spent)
            steps = self.rail.plan(direction)
            nonce = max(now_ns // 1_000_000, self.last_nonce + 1)
            state = {
                "id": f"treasury-{self.next_id}",
                "direction": direction,
                "amount_micro": amount,
                "received_micro": amount,
                "handle": handle,
                "nonce": nonce,
                "steps": list(steps),
                "index": 0,
                "status": "submitted",
                "fees_micro": 0,
                "receipts": [],
                "principal_moved": False,
                "started_ns": now_ns,
                "route_data": {},
                "attempts": 0,
                "last_send_ns": now_ns,
            }
            if direction == "to_venice":
                state.update(venice_window=self.venice_window,
                             venice_spent_after=self.venice_spent + amount)
            reference = self.rail.prepare(steps[0], state, self.gas_spent)
            self._check_fee(reference, state)
            route = reference.get("gas_route")
            if route and route.get("forward"):
                quoted = route["forward_fee_micro"]
                if self.forward_spent + quoted > self.max_forward_fees_per_window:
                    raise RailError("treasury.max_forward_fees_per_window exhausted")
                state["forward_spent_after"] = self.forward_spent + quoted
            fee_budget = (0 if direction in ("to_venice", "spot_to_perps", "perps_to_spot")
                          else self.fee_ceiling_micro)
            if amount + fee_budget > self.wallet.available:
                raise RailError("wallet cannot reserve principal plus transfer fee ceiling")
        except (RailError, ValueError, TypeError, ArithmeticError) as exc:
            # All RailError messages are generated locally; never propagate vendor bodies.
            reason = str(exc) if isinstance(exc, RailError) else "invalid exact USD amount"
            self._write("refused", direction=direction, reason=reason, handle=handle)
            return {"status": "refused", "error": reason}
        except Exception:
            self._write(
                "refused", direction=direction, reason="rail preflight unavailable", handle=handle
            )
            return {"status": "refused", "error": "rail preflight unavailable"}
        if route:
            # The branch the route chose from its own observed position, public before signing.
            self._write("gas_route", transfer_id=state["id"], direction=direction, **route)
        self.principal_hold = self.wallet.reserve(amount, handle, "treasury:principal")
        try:
            self.fee_hold = self.wallet.reserve(fee_budget, handle, "treasury:fees")
            state["reference"] = reference
            self._write("submitted", state=state, tx_refs=[reference])
        except Exception:
            self.wallet.release(self.principal_hold)
            if self.fee_hold is not None:
                self.wallet.release(self.fee_hold)
            self.principal_hold = self.fee_hold = None
            raise
        self.state = deepcopy(state)
        self.next_id += 1
        self.last_nonce = nonce
        if direction == "to_venice":
            self.venice_spent = state["venice_spent_after"]
        if "forward_spent_after" in state:
            self.forward_spent = state["forward_spent_after"]
        self._send()
        return {
            "status": self.state["status"],
            "transfer_id": state["id"],
            "tx_refs": [deepcopy(reference)],
        }

    def _check_fee(self, reference: dict, state: dict) -> None:
        ceiling = reference.get("fee_ceiling_micro", 0)
        if type(ceiling) is not int or ceiling < 0:
            raise RailError("route fee ceiling must be nonnegative integer micro-USD")
        if state["direction"] == "to_venice" and ceiling:
            raise RailError("the fixed Venice tranche has no separate transfer fee")
        if ceiling + state["fees_micro"] > self.fee_ceiling_micro:
            raise RailError("step fee ceiling exceeds remaining transfer fee budget")

    def _send(self) -> None:
        state = self.state
        first_attempt = state["attempts"] == 0
        state = {**state, "attempts": state["attempts"] + 1}
        self._write("broadcast", state=state)
        self.state = state
        try:
            result = self.rail.send(state["steps"][state["index"]], state["reference"])
            if result is not None:
                updated = {**state, "route_data": {**state["route_data"], "submission": result}}
                self._write("acknowledged", state=updated)
                self.state = updated
        except Pending:
            self._write("pending", transfer_id=state["id"], reason="submission outcome unknown")
        except RailError as exc:
            # Explicit venue rejection is definitive before a withdrawal leaves the source.
            if first_attempt and str(exc) == "venue rejected withdrawal":
                self._fail(str(exc))
            else:
                self._write(
                    "pending", transfer_id=state["id"], reason="submission requires reconciliation"
                )
        except Exception:
            self._write("pending", transfer_id=state["id"], reason="submission outcome unknown")

    def reconcile(self, now_ns: int) -> list[dict]:
        """Advance at most one receipt-confirmed step per tick; never replace an ambiguous nonce."""
        state = self.state
        if not state or state["status"] != "submitted":
            return []
        step = state["steps"][state["index"]]
        try:
            outcome = self.rail.poll(step, {**deepcopy(state), "gas_spent": dict(self.gas_spent)})
        except Exception as exc:
            self._stall(step, "poll", exc, now_ns)
            return []
        if outcome is None:
            self.state = self._settled(state)  # a clean poll with no evidence yet: no stall
            return []
        fee = outcome["fee_micro"]
        if type(fee) is not int or not 0 <= fee <= self.fee_ceiling_micro - state["fees_micro"]:
            self._write(
                "pending", transfer_id=state["id"], reason="fee evidence exceeds reservation"
            )
            return []
        wallet_fee = outcome.get("wallet_fee_micro", fee)
        if type(wallet_fee) is not int or not 0 <= wallet_fee <= fee:
            self._write("pending", transfer_id=state["id"], reason="invalid wallet fee evidence")
            return []
        updated = self._settled(deepcopy(state))
        updated["fees_micro"] += fee
        updated["receipts"].append(outcome["evidence"])
        updated["received_micro"] = outcome["received_micro"]
        if "route_data" in outcome:
            updated["route_data"] = deepcopy(outcome["route_data"])
        if outcome["confirmed"] and outcome.get("principal_moved", False):
            updated["principal_moved"] = True
        self._write(
            "step_confirmed" if outcome["confirmed"] else "step_failed",
            transfer_id=state["id"],
            step=step,
            outcome=outcome,
            state=updated,
            ts=now_ns,
        )
        self.state = updated
        if outcome.get("gas_fee_wei"):
            key = outcome["chain_key"]
            spent = {**self.gas_spent, key: self.gas_spent.get(key, 0) + outcome["gas_fee_wei"]}
            self._write("gas", spent_wei=spent)
            self.gas_spent = spent
        if fee:
            # The wallet's unit is USDC/provider credits. Prefunded native gas
            # has its own budget and fee ledger; spending HYPE/ETH cannot burn
            # unrelated USDC a second time. The total economic fee remains capped.
            if wallet_fee:
                self.wallet.commit_reported(self.fee_hold, wallet_fee)
            else:
                self.wallet.release(self.fee_hold)
            self.fee_hold = None
            self._reserve_fees()
        if not outcome["confirmed"]:
            return [self._fail(outcome.get("reason", "confirmed chain failure"), now_ns)]
        if updated["index"] + 1 == len(updated["steps"]):
            if hasattr(self.rail, "confirm"):
                try:
                    self.rail.confirm(updated)
                except (RailError, ValueError, ArithmeticError) as exc:
                    # A scripted class move is applied at settlement and the venue
                    # re-checks availability: a source that lost value since submission
                    # settles nothing, so no principal left and the transfer fails.
                    self.state = {**updated, "principal_moved": False}
                    return [self._fail(f"venue refused the settlement: {exc}")]
            finished = {**self._settled(updated), "status": "confirmed"}
            self._write("confirmed", state=finished, tx_refs=finished["receipts"], ts=now_ns)
            self.state = finished
            if finished["direction"] == "to_venice":
                self.income = {**self.income, "converted_from_principal_micro":
                               self.income["converted_from_principal_micro"]
                               + finished["received_micro"]}
            self.wallet.release(self.principal_hold)
            if self.fee_hold is not None:
                self.wallet.release(self.fee_hold)
            self.principal_hold = self.fee_hold = None
            self.refresh_pots()
            return [
                {
                    "status": "confirmed",
                    "transfer_id": finished["id"],
                    "received_micro": finished["received_micro"],
                    "fees_micro": finished["fees_micro"],
                    "tx_refs": deepcopy(finished["receipts"]),
                }
            ]
        # Record completion independently from preparing the next step. This lets
        # attestation/gas outages retry preparation without charging this receipt twice.
        next_state = {
            **updated,
            "index": updated["index"] + 1,
            "reference": None,
            "attempts": 0,
            "last_send_ns": now_ns,
        }
        self._write("advance", state=next_state)
        self.state = next_state
        self._prepare_next(now_ns)
        return []

    def _prepare_next(self, now_ns: int, ref: dict | None = None) -> None:
        state = self.state
        step = state["steps"][state["index"]]
        try:
            if ref is None:
                ref = self.rail.prepare(step, deepcopy(state), self.gas_spent)
            self._check_fee(ref, state)
            if ref.get("fee_ceiling_micro", 0) > (
                self.fee_hold.amount if self.fee_hold is not None else 0
            ):
                self._write("fee_unfunded", transfer_id=state["id"], handle=state["handle"],
                            required_micro=ref["fee_ceiling_micro"],
                            reserved_micro=self.fee_hold.amount if self.fee_hold else 0)
                return
        except Exception as exc:
            # The existing transfer and principal hold remain pending, and the wait is public.
            self._stall(step, "prepare", exc, now_ns)
            if self._forward_wait_exceeded():
                # Circle has not delivered for the bounded number of reserve windows and
                # the reserve could not (or need not) self-mint: strand, recoverably.
                self._fail(FORWARD_WAIT_EXCEEDED, now_ns, waited=self.state["pending"])
            return
        updated = {**self._settled(state), "reference": ref}
        self._write("step_submitted", state=updated, tx_refs=[ref])
        self.state = updated
        self._send()

    @staticmethod
    def _settled(state: dict) -> dict:
        """The step made progress: its stall record, if any, is over."""
        return {k: v for k, v in state.items() if k != "pending"}

    def _stall(self, step: str, phase: str, exc: BaseException, now_ns: int) -> None:
        """Record why the current step could not advance, bounded and without RPC text.

        The reason is a rail's own locally authored message (RailError and Pending
        carry no response bodies) or, for any other exception, its class name alone.
        A Pending ``carry`` replaces the record's reference; a failure without one
        keeps the reference already carried, so a transient outage never loses a cursor.
        """
        state = self.state
        reason = str(exc) if isinstance(exc, RailError) else type(exc).__name__
        carry = getattr(exc, "carry", None)
        previous = state.get("pending")
        if previous is None:
            record = {"step": step, "phase": phase, "reason": reason, "attempts": 1,
                      "since_ns": now_ns, "since_window": self.venice_window,
                      "reference": deepcopy(carry)}
        else:
            record = {**previous, "phase": phase, "reason": reason,
                      "attempts": previous["attempts"] + 1}
            if carry is not None:
                record["reference"] = deepcopy(carry)
        if (previous is None or reason != previous["reason"]
                or record["attempts"] % PENDING_JOURNAL_EVERY == 0):
            self._write("pending", transfer_id=state["id"], **record)
        self.state = {**state, "pending": record}

    @staticmethod
    def _forwarded(state: dict) -> bool:
        """The principal left HyperCore in a burn whose Base mint is Circle's to deliver."""
        return bool(state["principal_moved"]
                    and (state["route_data"].get("burn") or {}).get("forwarded"))

    def _forward_wait_exceeded(self) -> bool:
        """A forwarded mint still unobserved after the manifest's windows is stranded."""
        state = self.state
        record = state.get("pending")
        if record is None or not self._forwarded(state):
            return False
        # Checkpoints predating the bound carry no window: the wait is measured from now.
        since = record.get("since_window", self.venice_window)
        return self.venice_window - since >= self.forward_wait_windows

    def _recover(self, now_ns: int) -> None:
        """Re-check the oldest parked strand on a free slot; a reference re-enters the plan.

        The rail's mint preparation is the same read as during the wait: Circle's
        finalized delivery, or the reserve's own delivery of an unclaimed message when it
        can pay. Nothing is written while the check still waits; the strand is public in
        the pots view and in its ``treasury.failed`` item until it moves.
        """
        entry = self.stranded[0]
        state = entry["state"]
        step = state["steps"][state["index"]]
        try:
            ref = self.rail.prepare(step, deepcopy(state), self.gas_spent)
            self._check_fee(ref, state)
        except Exception:
            return
        self.stranded.pop(0)
        self.principal_hold = entry["principal_hold"]
        recovered = {k: v for k, v in self._settled(state).items()
                     if k not in ("reason", "recoverable", "stranded_ns")}
        recovered.update(status="submitted", reference=None, attempts=0, last_send_ns=now_ns,
                         recovered_ns=now_ns)
        self.state = recovered
        self._write("recovered", state=recovered, ts=now_ns)
        self._reserve_fees()
        self._prepare_next(now_ns, ref)

    def _reserve_fees(self) -> None:
        """A trading loss reduces the remaining fee hold without losing receipt reconciliation."""
        remaining = self.fee_ceiling_micro - self.state["fees_micro"]
        affordable = min(remaining, max(0, self.wallet.available))
        if affordable < remaining:
            self._write("fee_unfunded", transfer_id=self.state["id"],
                        handle=self.state["handle"], required_micro=remaining,
                        reserved_micro=affordable)
        if not self.wallet.dead:
            self.fee_hold = self.wallet.reserve(affordable, self.state["handle"], "treasury:fees")

    def tick(self, now_ns: int) -> list[dict]:
        self.collect_income()
        if self.stranded and not self._blocking():
            self._recover(now_ns)
            return []
        if self.state and self.state["status"] == "submitted" and self.state["reference"] is None:
            if self.fee_hold is not None and self.wallet.available > 0 and (
                self.fee_hold.amount < self.fee_ceiling_micro - self.state["fees_micro"]
            ):
                self.wallet.release(self.fee_hold)
                self.fee_hold = None
                self._reserve_fees()
            self._prepare_next(now_ns)
            return []
        result = self.reconcile(now_ns)
        if (
            self.state
            and self.state["status"] == "submitted"
            and self.state["reference"]
            and now_ns - self.state["last_send_ns"] >= 60_000_000_000
        ):
            updated = {**self.state, "last_send_ns": now_ns}
            self._write("retry", state=updated)
            self.state = updated
            self._send()  # exactly the same nonce and transaction, never a replacement
        return result

    def _fail(self, reason: str, now_ns: int | None = None, **detail) -> dict:
        state = self.state
        stranded = state["principal_moved"]
        result = {**self._settled(state), "status": "stranded" if stranded else "failed",
                  "reason": reason}
        recoverable = stranded and self._forwarded(state)
        if recoverable:
            # The message is Circle's to deliver or anyone's to submit: the strand keeps
            # its principal hold, leaves the slot and is re-checked whenever it is free.
            result.update(recoverable=True, stranded_ns=now_ns)
        self._write(
            "failed",
            state=result,
            tx_refs=state["receipts"],
            reason=reason,
            stranded_micro=state["received_micro"] if stranded else 0,
            **detail,
        )
        self.state = result
        if not stranded:
            self.wallet.release(self.principal_hold)
            self.principal_hold = None
        elif recoverable:
            self.stranded.append({"state": deepcopy(result), "principal_hold": self.principal_hold})
            self.principal_hold = None
        if self.fee_hold is not None:
            self.wallet.release(self.fee_hold)
        self.fee_hold = None
        return {"status": result["status"], "error": reason, "transfer_id": state["id"]}

    def snapshot(self) -> dict:
        """Only public replay references and reservation IDs leave the treasury; never keys."""
        return deepcopy(
            {
                "state": self.state,
                "next_id": self.next_id,
                "last_nonce": self.last_nonce,
                "gas_spent": self.gas_spent,
                "pots": self._pots,
                "principal_hold_id": self.principal_hold.id if self.principal_hold else None,
                "fee_hold_id": self.fee_hold.id if self.fee_hold else None,
                "stranded": [{"state": entry["state"],
                              "principal_hold_id": entry["principal_hold"].id
                              if entry["principal_hold"] else None}
                             for entry in self.stranded],
                "rail_name": self.rail.name,
                "fake_reserve": self.rail.reserve if self.rail.name == "scripted" else None,
                "fake_venice": self.rail.venice if self.rail.name == "scripted" else None,
                "venice_window": self.venice_window,
                "venice_spent": self.venice_spent,
                "forward_spent": self.forward_spent,
                "income": self.income,
            }
        )

    def restore(self, snapshot: dict) -> None:
        """Restore authenticated references and bind holds; restoring itself never broadcasts."""
        if snapshot["rail_name"] != self.rail.name:
            raise RailError("treasury route differs from the saved world")
        saved = deepcopy(snapshot)
        principal = saved["principal_hold_id"]
        fee = saved["fee_hold_id"]
        self.principal_hold = self.wallet._reservation_for_resume(principal) if principal else None
        self.fee_hold = self.wallet._reservation_for_resume(fee) if fee else None
        self.stranded = []
        for entry in saved.get("stranded", []):  # checkpoints predate parked strands
            hold_id = entry["principal_hold_id"]
            hold = self.wallet._reservation_for_resume(hold_id) if hold_id else None
            self.stranded.append({"state": entry["state"], "principal_hold": hold})
        holds = [(self.principal_hold, "treasury:principal"), (self.fee_hold, "treasury:fees")]
        holds.extend((entry["principal_hold"], "treasury:principal") for entry in self.stranded)
        for hold, reason in holds:
            if hold is not None and hold.reason != reason:
                raise RailError("saved treasury hold belongs to another purpose")
        self.state = saved["state"]
        self.next_id, self.last_nonce = saved["next_id"], saved["last_nonce"]
        self.gas_spent, self._pots = saved["gas_spent"], saved["pots"]
        self.venice_window, self.venice_spent = saved["venice_window"], saved["venice_spent"]
        self.forward_spent = saved.get("forward_spent", 0)  # checkpoints predate forwarding
        self.income = {**_fresh_income(), **saved.get("income", {})}  # and income classes
        if saved["fake_reserve"] is not None:
            self.rail.reserve = saved["fake_reserve"]
            self.rail.venice = saved["fake_venice"]


class FakeRail:
    """Scripted principal moves only when the next tick confirms, with one fixed declared fee."""

    name = "scripted"

    def __init__(self, wallet, *, fee_micro: int = 10_000, exchange=None):
        self.wallet, self.fee_micro = wallet, fee_micro
        self.exchange = exchange
        self.reserve = 0
        self.venice = 0

    def balances(self) -> dict:
        venue = self.wallet.balance - self.reserve - self.venice
        result = {"venue": venue, "reserve": self.reserve, "venice": self.venice}
        if self.exchange is not None:
            acct = self.exchange.account()
            spot = int((acct.equity_usd - self.exchange._perp_equity()) * 1_000_000)
            book = self.exchange._cash + self.exchange._spot_cash + sum(
                (p.size * p.entry_px for p in self.exchange._spot_positions.values()), Decimal(0))
            adjustment = money_to_usd(venue) - book
            perps = int((self.exchange._perp_equity() + adjustment) * 1_000_000)
            result.update(venue=perps + spot, perps=perps, spot=spot)
        return result

    def plan(self, direction: str) -> tuple[str, ...]:
        return (direction,)

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        if direction not in {"to_reserve", "to_venue", "to_venice",
                                 "spot_to_perps", "perps_to_spot"}:
            raise RailError("unsupported scripted direction")
        if direction in ("spot_to_perps", "perps_to_spot"):
            if self.exchange is None:
                raise RailError("spot exchange unavailable")
            self.exchange.sync_cash(money_to_usd(
                self.wallet.balance - self.reserve - self.venice))
            available = (self.exchange._spot_available("USDC") if direction == "spot_to_perps"
                         else self.exchange._perp_withdrawable())
            if amount > int(available * 1_000_000):
                raise RailError("amount exceeds available source class")
            return
        pot = self.balances()["venue" if direction == "to_reserve" else "reserve"]
        if direction == "to_reserve" and self.exchange is not None:
            self.exchange.sync_cash(money_to_usd(
                self.wallet.balance - self.reserve - self.venice))
            pot = int(self.exchange._perp_withdrawable() * 1_000_000)
        if amount < 5_000_000:
            raise RailError("amount is below venue minimum")
        fee = 0 if direction == "to_venice" else self.fee_micro
        if amount > pot or amount <= fee:
            raise RailError("amount exceeds source pot or does not cover the fixed fee")

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        return {"network": "scripted", "tx_hash": state["id"] + ":fake"}

    def send(self, step: str, reference: dict) -> None:
        pass

    def poll(self, step: str, state: dict) -> dict:
        fee = 0 if step in ("to_venice", "spot_to_perps", "perps_to_spot") else self.fee_micro
        return {
            "confirmed": True,
            "received_micro": state["amount_micro"] - fee,
            "fee_micro": fee,
            "principal_moved": True,
            "evidence": state["reference"],
        }

    def confirm(self, state: dict) -> None:
        if state["direction"] in ("spot_to_perps", "perps_to_spot"):
            self.exchange.class_transfer(money_to_usd(state["amount_micro"]),
                                         state["direction"] == "spot_to_perps")
        elif state["direction"] == "to_reserve":
            self.reserve += state["received_micro"]
        else:
            self.reserve -= state["amount_micro"]
            if state["direction"] == "to_venice":
                self.venice += state["received_micro"]


class FakeTreasury(Treasury):
    def __init__(self, ledger, wallet, *, fee_micro=10_000, max_venice_per_window=10_000_000,
                 exchange=None):
        super().__init__(
            ledger, wallet, FakeRail(wallet, fee_micro=fee_micro, exchange=exchange),
            fee_ceiling_micro=fee_micro,
            max_venice_per_window=max_venice_per_window,
        )
        self.refresh_pots()

    def pots(self) -> dict:
        result = super().pots()
        if not result["pending"]:
            result.update(
                {k: v for k, v in self.rail.balances().items() if k != "venice"},
                seed=0,
                sellers={"venice": self.rail.venice},
                complete=True,
                total_micro=self.rail.balances()["venue"] + self.rail.reserve + self.rail.venice,
            )
        return result

    @property
    def venue_balance_usd(self) -> Decimal:
        return money_to_usd(self.wallet.balance - self.rail.reserve - self.rail.venice)


# The venue stamps an accountClassTransfer row with its own execution time, which is
# always later than the millisecond nonce signed at prepare. One retry interval bounds
# the gap the venue was measured to take; a row outside it belongs to another action.
CLASS_EXECUTION_TOLERANCE_MS = 60_000


class ClassTransferRail:
    """USDC class transfers retain the signed nonce until matching ledger evidence arrives."""

    def class_preflight(self, direction: str, amount: int) -> None:
        sdk = self.exchange._exchange
        if (sdk is None or sdk.wallet.address.lower() != self.exchange._address.lower()
                or sdk.vault_address):
            raise RailError("class transfers require the main account signing key")
        acct = self.exchange.account()
        available = (acct.cash_usd if direction == "perps_to_spot" else next(
            (b.available for b in acct.spot_balances if b.coin == "USDC"), Decimal(0)))
        if amount > int(available * 1_000_000):
            raise RailError("amount exceeds available source class")

    def class_prepare(self, step: str, state: dict) -> dict:
        return {"network": self.exchange.name, "sender": self.exchange._address,
                "action": {"type": "usdClassTransfer",
                           "amount": str(money_to_usd(state["amount_micro"])),
                           "toPerp": step == "spot_to_perps", "nonce": state["nonce"]},
                "nonce": state["nonce"], "fee_ceiling_micro": 0}

    def class_send(self, reference: dict) -> None:
        from hyperliquid.utils.constants import MAINNET_API_URL
        from hyperliquid.utils.signing import sign_usd_class_transfer_action

        if (reference["sender"] != self.exchange._address
                or reference["network"] != self.exchange.name):
            raise RailError("class transfer identity mismatch")
        action = deepcopy(reference["action"])
        sdk = self.exchange._exchange
        signature = sign_usd_class_transfer_action(
            sdk.wallet, action, self.exchange.base_url == MAINNET_API_URL)
        response = sdk._post_action(action, signature, reference["nonce"])
        if response.get("status") != "ok":
            raise RailError("venue rejected withdrawal")

    def class_poll(self, state: dict) -> dict | None:
        """Confirm one hashed row of the signed direction and amount executed in window.

        The nonce is this client's prepare time and ``time`` is the venue's execution
        time, so they are never equal; the evidence is the unique row whose execution
        falls in ``[nonce, nonce + CLASS_EXECUTION_TOLERANCE_MS]``, pinned by its hash.
        Two candidate rows are ambiguous and confirm nothing.
        """
        ref = state["reference"]
        start, end = ref["nonce"], ref["nonce"] + CLASS_EXECUTION_TOLERANCE_MS
        rows = self.exchange._info.user_non_funding_ledger_updates(
            self.exchange._address, start)
        matches = []
        for row in rows:
            delta = row.get("delta", {})
            executed = row.get("time")
            if (type(executed) is int and start <= executed <= end and row.get("hash")
                    and delta.get("type") == "accountClassTransfer"
                    and delta.get("toPerp") is ref["action"]["toPerp"]
                    and Decimal(str(delta.get("usdc", "0"))) * 1_000_000
                    == state["amount_micro"]):
                matches.append(row)
        if len(matches) != 1:
            return None
        return {"confirmed": True, "received_micro": state["amount_micro"],
                "fee_micro": 0, "principal_moved": True, "evidence": matches[0]}


class UnconfiguredRail(ClassTransferRail):
    """A world with no reserve address can observe its venue but cannot move treasury money."""

    name = "unconfigured"

    def __init__(self, exchange):
        self.exchange = exchange

    def balances(self) -> dict:
        acct = self.exchange.account()
        venue = int(acct.equity_usd * 1_000_000)
        result = {"venue": venue, "reserve": 0}
        if getattr(self.exchange, "spot_pairs", ()):
            state = self.exchange._info.user_state(self.exchange._address)
            perps = int(Decimal(str(state["marginSummary"]["accountValue"])) * 1_000_000)
            result.update(perps=perps, spot=venue - perps)
        return result

    def plan(self, direction: str) -> tuple[str, ...]:
        if direction not in ("spot_to_perps", "perps_to_spot"):
            raise RailError("reserve route is not configured")
        return (direction,)

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        self.plan(direction)
        self.class_preflight(direction, amount)

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        return self.class_prepare(step, state)

    def send(self, step: str, reference: dict) -> None:
        self.class_send(reference)

    def poll(self, step: str, state: dict) -> dict | None:
        return self.class_poll(state)
