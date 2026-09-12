"""Ledger-first transfers with explicit pots and quarantined uncertain outcomes."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from factorylab.kernel.money import money_to_usd, usd_to_money
from factorylab.world.evm import Pending, RailError


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
    """

    def __init__(
        self,
        ledger,
        wallet,
        rail,
        *,
        provider=None,
        fee_ceiling_micro=2_000_000,
    ):
        self.ledger, self.wallet, self.rail, self.provider = ledger, wallet, rail, provider
        if type(fee_ceiling_micro) is not int or fee_ceiling_micro < 0:
            raise ValueError("fee ceiling must be nonnegative integer micro-USD")
        self.fee_ceiling_micro = fee_ceiling_micro
        self.state: dict | None = None
        self.next_id = 0
        self.last_nonce = 0
        self.gas_spent: dict[str, int] = {}
        self.principal_hold = self.fee_hold = None
        self._pots = {"venue": None, "reserve": None, "seed": None, "sellers": {}}

    def _write(self, kind: str, **fields) -> None:
        self.ledger.append({"kind": "treasury." + kind, **fields})

    def pots(self) -> dict:
        """Return a detached observed view; unknown balances are never converted to zero."""
        pending = bool(self.state and self.state["status"] in {"submitted", "stranded"})
        result = deepcopy(self._pots)
        result["pending"] = pending
        values = [result[k] for k in ("venue", "reserve", "seed")]
        values.extend(result["sellers"].values())
        result["complete"] = not pending and all(type(v) is int for v in values)
        result["total_micro"] = sum(values) if result["complete"] else None
        return result

    def refresh_pots(self) -> dict:
        """Persist a complete or explicitly unavailable observation before replacing the view."""
        if self.state and self.state["status"] in {"submitted", "stranded"}:
            return self.pots()
        if hasattr(self.ledger, "call"):
            seed, sellers = self.ledger.call(
                "treasury.provider_pots", lambda: provider_pots(self.provider), (), {}
            )
        else:
            seed, sellers = provider_pots(self.provider)
        try:
            observed = self.rail.balances()
            venue, reserve = observed["venue"], observed["reserve"]
        except Exception:
            venue = reserve = None
        pots = {"venue": venue, "reserve": reserve, "seed": seed, "sellers": sellers}
        self._write("pots", pots=pots)
        self._pots = pots
        return self.pots()

    def transfer(
        self, direction: str, usd: str | Decimal | int, *, handle: str, now_ns: int
    ) -> dict:
        """A refusal has a reason; a submitted result has references but never promises arrival."""
        direction = "to_reserve" if direction == "to_compute" else direction
        try:
            if direction not in {"to_reserve", "to_venue"}:
                raise RailError("direction must be to_reserve or to_venue")
            if isinstance(usd, bool) or not isinstance(usd, str | Decimal | int):
                raise RailError("usd must be an exact decimal string or integer, not a float")
            amount = usd_to_money(str(usd))
            if amount <= 0:
                raise RailError("usd must be positive")
            if self.state and self.state["status"] in {"submitted", "stranded"}:
                raise RailError("a previous transfer is still pending or stranded")
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
            reference = self.rail.prepare(steps[0], state, self.gas_spent)
            self._check_fee(reference, state)
            if amount + self.fee_ceiling_micro > self.wallet.available:
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
        self.principal_hold = self.wallet.reserve(amount, handle, "treasury:principal")
        try:
            self.fee_hold = self.wallet.reserve(self.fee_ceiling_micro, handle, "treasury:fees")
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
        if ceiling + state["fees_micro"] > self.fee_ceiling_micro:
            raise RailError("step fee ceiling exceeds remaining transfer fee budget")

    def _send(self) -> None:
        state = self.state
        first_attempt = state["attempts"] == 0
        state = {**state, "attempts": state["attempts"] + 1}
        self._write("broadcast", state=state)
        self.state = state
        try:
            self.rail.send(state["steps"][state["index"]], state["reference"])
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
        except (Pending, ConnectionError, TimeoutError):
            return []
        except Exception:
            self._write(
                "pending", transfer_id=state["id"], reason="receipt verification unavailable"
            )
            return []
        if outcome is None:
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
        updated = deepcopy(state)
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
            return [self._fail(outcome.get("reason", "confirmed chain failure"))]
        if updated["index"] + 1 == len(updated["steps"]):
            finished = {**updated, "status": "confirmed"}
            self._write("confirmed", state=finished, tx_refs=finished["receipts"], ts=now_ns)
            self.state = finished
            self.wallet.release(self.principal_hold)
            if self.fee_hold is not None:
                self.wallet.release(self.fee_hold)
            self.principal_hold = self.fee_hold = None
            if hasattr(self.rail, "confirm"):
                self.rail.confirm(finished)
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
        self._prepare_next()
        return []

    def _prepare_next(self) -> None:
        state = self.state
        try:
            ref = self.rail.prepare(state["steps"][state["index"]], deepcopy(state), self.gas_spent)
            self._check_fee(ref, state)
            if ref.get("fee_ceiling_micro", 0) > (
                self.fee_hold.amount if self.fee_hold is not None else 0
            ):
                self._write("fee_unfunded", transfer_id=state["id"], handle=state["handle"],
                            required_micro=ref["fee_ceiling_micro"],
                            reserved_micro=self.fee_hold.amount if self.fee_hold else 0)
                return
        except Exception:
            return  # existing transfer and principal hold remain pending
        updated = {**state, "reference": ref}
        self._write("step_submitted", state=updated, tx_refs=[ref])
        self.state = updated
        self._send()

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
        if self.state and self.state["status"] == "submitted" and self.state["reference"] is None:
            if self.fee_hold is not None and self.wallet.available > 0 and (
                self.fee_hold.amount < self.fee_ceiling_micro - self.state["fees_micro"]
            ):
                self.wallet.release(self.fee_hold)
                self.fee_hold = None
                self._reserve_fees()
            self._prepare_next()
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

    def _fail(self, reason: str) -> dict:
        state = self.state
        stranded = state["principal_moved"]
        result = {**state, "status": "stranded" if stranded else "failed", "reason": reason}
        self._write(
            "failed",
            state=result,
            tx_refs=state["receipts"],
            reason=reason,
            stranded_micro=state["received_micro"] if stranded else 0,
        )
        self.state = result
        if not stranded:
            self.wallet.release(self.principal_hold)
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
                "rail_name": self.rail.name,
                "fake_reserve": self.rail.reserve if self.rail.name == "scripted" else None,
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
        for hold, reason in ((self.principal_hold, "treasury:principal"),
                             (self.fee_hold, "treasury:fees")):
            if hold is not None and hold.reason != reason:
                raise RailError("saved treasury hold belongs to another purpose")
        self.state = saved["state"]
        self.next_id, self.last_nonce = saved["next_id"], saved["last_nonce"]
        self.gas_spent, self._pots = saved["gas_spent"], saved["pots"]
        if saved["fake_reserve"] is not None:
            self.rail.reserve = saved["fake_reserve"]


class FakeRail:
    """Scripted principal moves only when the next tick confirms, with one fixed declared fee."""

    name = "scripted"

    def __init__(self, wallet, *, fee_micro: int = 10_000):
        self.wallet, self.fee_micro = wallet, fee_micro
        self.reserve = 0

    def balances(self) -> dict:
        return {"venue": self.wallet.balance - self.reserve, "reserve": self.reserve}

    def plan(self, direction: str) -> tuple[str, ...]:
        return (direction,)

    def preflight(self, direction: str, amount: int, gas_spent: dict) -> None:
        if direction not in {"to_reserve", "to_venue"}:
            raise RailError("unsupported scripted direction")
        pot = self.balances()["venue" if direction == "to_reserve" else "reserve"]
        if amount < 5_000_000:
            raise RailError("amount is below venue minimum")
        if amount > pot or amount <= self.fee_micro:
            raise RailError("amount exceeds source pot or does not cover the fixed fee")

    def prepare(self, step: str, state: dict, gas_spent: dict) -> dict:
        return {"network": "scripted", "tx_hash": state["id"] + ":fake"}

    def send(self, step: str, reference: dict) -> None:
        pass

    def poll(self, step: str, state: dict) -> dict:
        return {
            "confirmed": True,
            "received_micro": state["amount_micro"] - self.fee_micro,
            "fee_micro": self.fee_micro,
            "principal_moved": True,
            "evidence": state["reference"],
        }

    def confirm(self, state: dict) -> None:
        if state["direction"] == "to_reserve":
            self.reserve += state["received_micro"]
        else:
            self.reserve -= state["amount_micro"]


class FakeTreasury(Treasury):
    def __init__(self, ledger, wallet, *, fee_micro=10_000):
        super().__init__(
            ledger, wallet, FakeRail(wallet, fee_micro=fee_micro), fee_ceiling_micro=fee_micro
        )
        self.refresh_pots()

    def pots(self) -> dict:
        result = super().pots()
        if not result["pending"]:
            result.update(
                self.rail.balances(),
                seed=0,
                sellers={},
                complete=True,
                total_micro=self.wallet.balance,
            )
        return result

    @property
    def venue_balance_usd(self) -> Decimal:
        return money_to_usd(self.wallet.balance - self.rail.reserve)


class UnconfiguredRail:
    """A world with no reserve address can observe its venue but cannot move treasury money."""

    name = "unconfigured"

    def __init__(self, exchange):
        self.exchange = exchange

    def balances(self) -> dict:
        return {"venue": int(self.exchange.account().equity_usd * 1_000_000), "reserve": 0}

    def preflight(self, *_args) -> None:
        raise RailError("treasury.reserve_address and gas budgets are not configured")
