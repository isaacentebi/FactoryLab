"""Protected, expiring novelty entitlements independent of wallet creation."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
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
        window_ns: int,
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
        if type(window_ns) is not int or window_ns <= 0:
            raise ValueError("window_ns must be a positive integer")
        if not callable(has_history):
            raise TypeError("history predicate is required")
        self.__fraction = fraction
        self.__window_ns = window_ns
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

    @property
    def window_ns(self) -> int:
        """The window duration is immutable for this reserve's lifetime."""
        return self.__window_ns

    def open_window(self, now_ns: int, window_spend_budget: Money) -> None:
        """Start a nonoverlapping window, discarding unused entitlements from its predecessor."""
        require_money(window_spend_budget, nonnegative=True)
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        if self.__start is not None and now_ns < self.__start + self.__window_ns:
            raise Infeasible("cannot reopen or overlap a reserve window")
        numerator, denominator = self.__fraction.as_integer_ratio()
        amount = window_spend_budget * numerator // denominator
        self.__ledger.append(
            {
                "kind": "novelty.window",
                "ts": now_ns,
                "amount": amount,
                "expired": self.__remaining,
                "budget": window_spend_budget,
            }
        )
        self.__start = now_ns
        self.__remaining = amount
        self.__receipts.clear()

    def _active(self) -> bool:
        now = self.__clock()
        return (
            self.__start is not None
            and self.__start <= now < self.__start + self.__window_ns
            and not self.__ledger.final
        )

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

    def remaining(self) -> Money:
        """Return the active window's unallocated entitlement, or zero after expiry."""
        return self.__remaining if self._active() else 0

    def _belongs_to(self, ledger: Ledger) -> bool:
        return self.__ledger is ledger

    def _allocate_compute(self, amount: Money) -> tuple[int | None, Money]:
        """A preceding wallet.reserve item entitles one hold to protected compute.

        The wallet classifies the action before this call. Its existing reservation
        id, handle and reason are the audit evidence; no second money item is needed.
        """
        protected = min(amount, self.remaining())
        self.__remaining -= protected
        return self.__start, protected

    def _refund_compute(self, allocation: tuple[int | None, Money], spent: Money) -> None:
        """A preceding wallet.commit/release returns unused protection only to its own window."""
        start, amount = allocation
        if start == self.__start and self._active():
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
