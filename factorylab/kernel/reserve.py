"""Protected novelty entitlements, accrued as a flow, independent of wallet creation.

Essay II.II.b asks for "some share of compute and write access ... usable only in
the context of unhistoried actions". The share is a flow (time audit T6): one
flow period's share of the spendable budget accrues over that period, whatever
the number of windows it is cut into, and the entitlement never exceeds one
period's share. How long a period is, and how much of one a window covers, is the
runtime's clock (``runtime.clockwork``); the reserve guarantees the bound.
"""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from time import time_ns

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money
from factorylab.kernel.registry import Contract
from factorylab.kernel.wallet import Infeasible, Reservation


class NoveltyReserve:
    """Only contracts without inherited settled history can consume this window's share."""

    def __init__(
        self,
        share: float,
        *,
        has_history: Callable[[str], bool],
        ledger: Ledger,
        clock_ns: Callable[[], int] = time_ns,
    ) -> None:
        if isinstance(share, bool) or not isinstance(share, (float, Decimal, str, int)):
            raise TypeError("share must be numeric")
        fraction = Decimal(str(share))
        if not fraction.is_finite() or not 0 < fraction <= 1:
            raise ValueError("novelty share must be in (0, 1]")
        if not callable(has_history):
            raise TypeError("history predicate is required")
        self.__fraction = fraction
        self.__history = has_history
        self.__ledger = ledger
        self.__clock = clock_ns
        self.__start: int | None = None
        self.__remaining = 0
        self.__serial = 0
        self.__receipts: dict[str, tuple[Reservation, Contract]] = {}

    @property
    def share(self) -> Decimal:
        """The protected share is immutable and represented exactly in reserve arithmetic."""
        return self.__fraction

    def open_window(self, now_ns: int, window_spend_budget: Money, *,
                    accrued: Fraction = Fraction(1)) -> None:
        """Start the next window, carrying the unspent flow and adding what accrued.

        Guarantees the new entitlement is ``min(cap, carried + cap × accrued)``,
        where ``cap`` is one flow period's share of ``window_spend_budget`` and
        ``carried`` what the previous window left unspent: however often windows
        open, the reserve never holds more than one period's share, and a window
        that covers ``accrued`` of a period adds only that part of it. A window
        opens strictly after its predecessor and ends when the next one opens.
        Unconsumed registration receipts of the predecessor are voided.
        """
        require_money(window_spend_budget, nonnegative=True)
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        if not isinstance(accrued, Fraction | int) or not 0 <= accrued <= 1:
            raise ValueError("accrued is an exact fraction of one flow period in [0, 1]")
        if self.__start is not None and now_ns <= self.__start:
            raise Infeasible("cannot reopen or overlap a reserve window")
        numerator, denominator = self.__fraction.as_integer_ratio()
        cap = window_spend_budget * numerator // denominator
        accrued = Fraction(accrued)
        carried = self.__remaining if self._active() else 0
        amount = min(cap, carried + cap * accrued.numerator // accrued.denominator)
        self.__ledger.append(
            {
                "kind": "novelty.window",
                "ts": now_ns,
                "amount": amount,
                "carried": carried,
                "accrued": str(accrued),
                "cap": cap,
                "budget": window_spend_budget,
            }
        )
        self.__start = now_ns
        self.__remaining = amount
        self.__receipts.clear()

    def _active(self) -> bool:
        return self.__start is not None and not self.__ledger.final

    def reserve_for(self, contract: Contract, amount: Money) -> Reservation:
        """Issue a one-use entitlement only for a fresh contract in an active window."""
        require_money(amount, nonnegative=True)
        if not isinstance(contract, Contract):
            raise TypeError("Contract required")
        if not self._active():
            raise Infeasible("no active novelty window")
        if self.__history(contract.id):
            raise Infeasible("contract has settled history")
        if amount <= 0 or amount > self.__remaining:
            raise Infeasible("novelty reservation must be positive and within remaining share")
        reservation = Reservation(
            f"novelty-{self.__serial}",
            amount,
            contract.id,
            "novelty",
            contract.id,
            contract.version,
            self.__start,
            self,
        )
        self.__ledger.append(
            {
                "kind": "novelty.reserve",
                "amount": amount,
                "contract_id": contract.id,
                "version": contract.version,
                "reservation_id": reservation.id,
                "ts": self.__clock(),
            }
        )
        self.__remaining -= amount
        self.__serial += 1
        self.__receipts[reservation.id] = (reservation, contract)
        return reservation

    def release(self, receipt: Reservation) -> None:
        """Return an unconsumed receipt's entitlement to its own window; a refused proposal
        does not spend the share it never registered."""
        pair = self.__receipts.get(receipt.id)
        if pair is None or pair[0] is not receipt:
            raise PermissionError("novelty receipt is foreign, consumed or unknown")
        del self.__receipts[receipt.id]
        refunded = receipt.window_start_ns == self.__start and self._active()
        if refunded:
            self.__remaining += receipt.amount
        self.__ledger.append(
            {
                "kind": "novelty.release",
                "reservation_id": receipt.id,
                "contract_id": receipt.contract_id,
                "amount": receipt.amount,
                "refunded": refunded,
                "ts": self.__clock(),
            }
        )

    def remaining(self) -> Money:
        """Return the active window's unallocated entitlement, or zero after expiry."""
        return self.__remaining if self._active() else 0

    def _belongs_to(self, ledger: Ledger) -> bool:
        return self.__ledger is ledger

    def _allocate_compute(self, amount: Money, handle: str = "",
                          reason: str = "") -> tuple[int | None, Money, str, str]:
        """A preceding wallet.reserve item entitles one hold to protected compute.

        The wallet classifies the action before this call. Its existing reservation
        id, handle and reason are the audit evidence; no second money item is needed.
        The handle and reason ride with the allocation so its use can be ledgered.
        """
        protected = min(amount, self.remaining())
        self.__remaining -= protected
        return self.__start, protected, handle, reason

    def _refund_compute(self, allocation: tuple, spent: Money) -> None:
        """A preceding wallet.commit/release returns unused protection only to its own window.

        Guarantees one ``novelty.compute`` entry per allocation that protected
        anything: how much of the niche the hold was entitled to and how much of it
        the booked cost used. It records the entitlement's use; no money moves here.
        """
        start, amount = allocation[0], allocation[1]
        handle, reason = (allocation[2], allocation[3]) if len(allocation) >= 4 else ("", "")
        refunded = start == self.__start and self._active()
        # Ledger first: the entitlement changes only after its record is durable.
        if amount > 0 and not self.__ledger.final:
            self.__ledger.append({"kind": "novelty.compute", "handle": handle,
                                  "reason": reason, "protected": amount,
                                  "used": min(amount, max(0, spent)),
                                  "refunded": refunded, "ts": self.__clock()})
        if refunded:
            self.__remaining += max(0, amount - spent)

    def _validate_registration(
        self, receipt: Reservation, contract: Contract, ledger: Ledger
    ) -> None:
        pair = self.__receipts.get(receipt.id)
        if (
            ledger is not self.__ledger
            or not self._active()
            or pair is None
            or pair[0] is not receipt
            or pair[1] != contract
        ):
            raise PermissionError("novelty receipt is foreign, expired, changed or consumed")
        if self.__history(contract.id):
            raise Infeasible("contract acquired settled history")

    def _consume_registration(self, receipt: Reservation) -> None:
        del self.__receipts[receipt.id]

    def state(self) -> dict:
        """Retain the live window and all unused registration receipts without issuer pointers."""
        return {
            "start": self.__start, "remaining": self.__remaining, "serial": self.__serial,
            "receipts": {k: (replace(r, _issuer=None), c)
                         for k, (r, c) in self.__receipts.items()},
        }

    def _restore_state(self, state: dict) -> None:
        """Authenticated receipts regain this reserve as issuer; spent receipts stay spent."""
        if self.__ledger.final:
            raise RuntimeError("world is final")
        self.__start, self.__remaining, self.__serial = (
            state["start"], state["remaining"], state["serial"],
        )
        self.__receipts = {
            k: (replace(r, _issuer=self), c) for k, (r, c) in state["receipts"].items()
        }
