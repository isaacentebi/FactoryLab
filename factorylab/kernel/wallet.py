"""A single conserved wallet with irrevocable death and prepaid reservations."""

from collections.abc import Callable
from dataclasses import dataclass, field
from time import time_ns

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money


class Infeasible(ValueError):
    """An action cannot proceed within its current wallet or reserve entitlement."""


@dataclass(frozen=True)
class DripSchedule:
    amount: Money
    period_ns: int
    start_ns: int
    end_ns: int

    def __post_init__(self) -> None:
        require_money(self.amount, nonnegative=True)
        for value in (self.period_ns, self.start_ns, self.end_ns):
            if type(value) is not int:
                raise TypeError("schedule times must be integer nanoseconds")
        if self.period_ns <= 0 or self.start_ns < 0 or self.end_ns < self.start_ns:
            raise ValueError("invalid drip schedule")


@dataclass(frozen=True)
class Reservation:
    id: str
    amount: Money
    handle: str
    reason: str
    contract_id: str | None = None
    contract_version: int | None = None
    window_start_ns: int | None = None
    _issuer: object = field(default=None, repr=False, compare=False)


class Wallet:
    """Balance follows conserved integer accounting; holds cannot be spent twice."""

    def __init__(
        self,
        initial: Money,
        ledger: Ledger,
        drip_schedule: DripSchedule | None = None,
        *,
        clock_ns: Callable[[], int] = time_ns,
    ) -> None:
        require_money(initial, nonnegative=True)
        if drip_schedule is not None and not isinstance(drip_schedule, DripSchedule):
            raise TypeError("drip_schedule must be immutable DripSchedule")
        self.__ledger = ledger
        self.__initial = self.__balance = initial
        self.__schedule = drip_schedule
        self.__clock = clock_ns
        self.__reservations: dict[str, Reservation] = {}
        self.__next_reservation = 0
        self.__drip_count = 0
        self.__drips = self.__settlements = self.__commits = 0
        ledger._claim_wallet(self)
        self._log("initial", initial, initial, "", "initial")

    @property
    def ledger(self) -> Ledger:
        """Expose the wallet's single authoritative ledger."""
        return self.__ledger

    @property
    def balance(self) -> Money:
        """Return booked money, including funds currently reserved."""
        return self.__balance

    @property
    def available(self) -> Money:
        """Return booked balance minus all outstanding holds."""
        return self.__balance - sum(item.amount for item in self.__reservations.values())

    @property
    def drip_schedule(self) -> DripSchedule | None:
        """The construction-time schedule is immutable for this wallet's lifetime."""
        return self.__schedule

    @property
    def dead(self) -> bool:
        """Zero or negative booked balance is death, regardless of future drips."""
        return self.__balance <= 0

    def _live(self) -> None:
        if self.dead or self.__ledger.final:
            raise Infeasible("wallet is dead or world is final")

    def _log(self, kind, amount, balance, handle, reason, **extra) -> None:
        self.__ledger.append(
            {
                "kind": f"wallet.{kind}",
                "amount": amount,
                "balance_after": balance,
                "handle": handle,
                "reason": reason,
                "ts": self.__clock(),
                **extra,
            }
        )

    def reserve(self, amount: Money, handle: str, reason: str) -> Reservation:
        """Hold an affordable nonnegative ceiling without changing booked balance."""
        require_money(amount, nonnegative=True)
        if not isinstance(handle, str) or not handle or not isinstance(reason, str) or not reason:
            raise ValueError("handle and reason are required")
        try:
            self._live()
        except Infeasible:
            if not self.__ledger.final:
                self._log("infeasible", amount, self.balance, handle, reason)
            raise
        if amount > self.available:
            self._log("infeasible", amount, self.balance, handle, reason)
            raise Infeasible("reservation exceeds available balance")
        reservation = Reservation(
            f"wallet-{self.__next_reservation}", amount, handle, reason, _issuer=self
        )
        self._log("reserve", amount, self.balance, handle, reason, reservation_id=reservation.id)
        self.__reservations[reservation.id] = reservation
        self.__next_reservation += 1
        return reservation

    def _held(self, reservation: Reservation) -> None:
        if not isinstance(reservation, Reservation):
            raise TypeError("Reservation required")
        if self.__reservations.get(reservation.id) is not reservation:
            raise Infeasible("reservation is foreign, forged or already consumed")

    def commit(self, reservation: Reservation, actual: Money) -> None:
        """Debit at most the original hold exactly once and release its remainder."""
        require_money(actual, nonnegative=True)
        self._live()
        self._held(reservation)
        if actual > reservation.amount:
            raise Infeasible("actual exceeds reservation")
        balance = self.balance - actual
        self._log(
            "commit",
            actual,
            balance,
            reservation.handle,
            reservation.reason,
            reservation_id=reservation.id,
            released=reservation.amount - actual,
        )
        self.__balance = balance
        self.__commits += actual
        del self.__reservations[reservation.id]

    def release(self, reservation: Reservation) -> None:
        """Cancel one hold without moving money, including after balance exhaustion."""
        self._held(reservation)
        self._log(
            "release",
            reservation.amount,
            self.balance,
            reservation.handle,
            reservation.reason,
            reservation_id=reservation.id,
        )
        del self.__reservations[reservation.id]

    def settle(self, delta: Money, handle: str, reason: str) -> None:
        """Book signed exchange P&L or funding only while the world remains alive."""
        require_money(delta)
        if reason not in ("exchange_pnl", "funding"):
            raise ValueError("settlement source must be exchange_pnl or funding")
        if not isinstance(handle, str) or not handle:
            raise ValueError("settlement handle is required")
        self._live()
        balance = self.balance + delta
        self._log("settle", delta, balance, handle, reason)
        self.__balance = balance
        self.__settlements += delta

    def drip(self, now_ns: int) -> Money:
        """Apply each scheduled deposit once in [start, end); never revive a dead wallet."""
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        self._live()
        schedule = self.__schedule
        if schedule is None or now_ns < schedule.start_ns or schedule.end_ns == schedule.start_ns:
            return 0
        last = min(now_ns, schedule.end_ns - 1)
        due_count = (last - schedule.start_ns) // schedule.period_ns + 1
        deposited = 0
        while self.__drip_count < due_count:
            due = schedule.start_ns + self.__drip_count * schedule.period_ns
            balance = self.balance + schedule.amount
            self._log("drip", schedule.amount, balance, "", "scheduled", due_ns=due)
            self.__balance = balance
            self.__drip_count += 1
            self.__drips += schedule.amount
            deposited += schedule.amount
        return deposited

    def check_conservation(self) -> bool:
        """Confirm booked balance equals initial plus drips and settlements minus commits."""
        return self.balance == self.__initial + self.__drips + self.__settlements - self.__commits
