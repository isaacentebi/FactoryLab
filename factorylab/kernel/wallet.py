"""A single conserved wallet with irrevocable death and prepaid reservations."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
        self.__exhausted = initial <= 0
        self.__schedule = drip_schedule
        self.__clock = clock_ns
        self.__reservations: dict[str, Reservation] = {}
        self.__next_reservation = 0
        self.__drip_count = 0
        self.__drips = self.__settlements = self.__commits = 0
        self.__pots_view: Callable[[], dict] | None = None
        self.__novelty = None
        self.__unhistoried: Callable[[str, str], bool] = lambda _h, _r: False
        self.__novelty_holds: dict[str, tuple[int | None, Money]] = {}
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
        """Historied spending excludes outstanding holds and the unused novelty share."""
        protected = self.__novelty.remaining() if self.__novelty is not None else 0
        return self.unhistoried_available - protected

    @property
    def unhistoried_available(self) -> Money:
        """Unhistoried work can use either ordinary money or the protected share."""
        return self.__balance - sum(item.amount for item in self.__reservations.values())

    def bind_novelty(self, reserve, unhistoried: Callable[[str, str], bool]) -> None:
        """Bind one kernel reserve and a trusted action classifier for this wallet's lifetime."""
        from factorylab.kernel.reserve import NoveltyReserve

        if self.__novelty is not None or not isinstance(reserve, NoveltyReserve):
            raise ValueError("novelty reserve can only be bound once")
        if not callable(unhistoried) or not reserve._belongs_to(self.__ledger):
            raise ValueError("novelty classifier and same-ledger reserve are required")
        self.__novelty, self.__unhistoried = reserve, unhistoried

    def available_for(self, handle: str, reason: str) -> Money:
        """Only trusted unhistoried actions may include the protected share in their ceiling."""
        return self.unhistoried_available if self.__unhistoried(handle, reason) else self.available

    def bind_pots(self, view: Callable[[], dict]) -> None:
        """Bind one observational view; it cannot mutate the conserved wallet balance."""
        if self.__pots_view is not None or not callable(view):
            raise ValueError("pots view must be callable and can only be bound once")
        self.__pots_view = view

    def pots(self) -> dict:
        """Return detached pot observations, with unavailable data explicitly marked unknown."""
        from copy import deepcopy

        if self.__pots_view is None:
            return {"venue": None, "reserve": None, "seed": None, "sellers": {},
                    "complete": False, "pending": False, "total_micro": None}
        return deepcopy(self.__pots_view())

    @property
    def drip_schedule(self) -> DripSchedule | None:
        """The construction-time schedule is immutable for this wallet's lifetime."""
        return self.__schedule

    @property
    def dead(self) -> bool:
        """Crossing zero is final, even when later observed settlements recover cash."""
        return self.__exhausted or self.__balance <= 0

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
        if amount > self.available_for(handle, reason):
            self._log("infeasible", amount, self.balance, handle, reason)
            raise Infeasible("reservation exceeds available balance")
        reservation = Reservation(
            f"wallet-{self.__next_reservation}", amount, handle, reason, _issuer=self
        )
        self._log("reserve", amount, self.balance, handle, reason, reservation_id=reservation.id)
        if self.__novelty is not None and self.__unhistoried(handle, reason):
            self.__novelty_holds[reservation.id] = self.__novelty._allocate_compute(amount)
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
        self._commit(reservation, actual)

    def commit_reported(self, reservation: Reservation, actual: Money) -> None:
        """Debit a completed vendor bill in full, including debt beyond its held ceiling.

        Only a genuine, still-open reservation can carry a reported overrun.
        Ordinary commit remains ceiling-bounded. Both evidence items precede
        the balance/hold mutation, so interrupted accounting replays once.
        """
        require_money(actual, nonnegative=True)
        self._live()
        self._held(reservation)
        if actual > reservation.amount:
            self.__ledger.append({
                "kind": "metering.overrun", "handle": reservation.handle,
                "reason": reservation.reason, "reservation_id": reservation.id,
                "reserved": reservation.amount, "actual": actual,
                "overrun": actual - reservation.amount, "ts": self.__clock(),
            })
        self._commit(reservation, actual)

    def _commit(self, reservation: Reservation, actual: Money) -> None:
        balance = self.balance - actual
        self._log(
            "commit",
            actual,
            balance,
            reservation.handle,
            reservation.reason,
            reservation_id=reservation.id,
            released=max(0, reservation.amount - actual),
        )
        self.__balance = balance
        self.__exhausted |= balance <= 0
        self.__commits += actual
        self._refund_novelty(reservation, actual)
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
        self._refund_novelty(reservation, 0)
        del self.__reservations[reservation.id]

    def _refund_novelty(self, reservation: Reservation, spent: Money) -> None:
        allocation = self.__novelty_holds.pop(reservation.id, None)
        if allocation is not None:
            self.__novelty._refund_compute(allocation, spent)

    def settle(self, delta: Money, handle: str, reason: str) -> None:
        """Book signed exchange P&L or funding only while the world remains alive."""
        self.settle_batch([(delta, handle, reason)])

    def settle_batch(self, settlements: list[tuple[Money, str, str]]) -> None:
        """Book every observed exchange effect; crossing zero is final even if cash recovers.

        Validate the whole batch before writing. Preserve one ordinary settlement
        item per effect, then publish the new state only after all appends succeed.
        A retry belongs to authenticated recovery, never to a second live submission.
        """
        self._live()
        balance = self.balance
        entries = []
        exhausted = self.__exhausted
        for delta, handle, reason in settlements:
            require_money(delta)
            if reason not in ("exchange_pnl", "funding"):
                raise ValueError("settlement source must be exchange_pnl or funding")
            if not isinstance(handle, str) or not handle:
                raise ValueError("settlement handle is required")
            balance += delta
            exhausted |= balance <= 0
            entries.append((delta, balance, handle, reason))
        for delta, after, handle, reason in entries:
            self._log("settle", delta, after, handle, reason)
        self.__balance = balance
        self.__exhausted = exhausted
        self.__settlements += sum(delta for delta, _, _, _ in entries)

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

    def state(self) -> dict:
        """Return accounting and outstanding holds, excluding ledger, clock and issuer objects."""
        return {
            "initial": self.__initial, "balance": self.__balance, "schedule": self.__schedule,
            "exhausted": self.__exhausted,
            "reservations": [replace(r, _issuer=None) for r in self.__reservations.values()],
            "next_reservation": self.__next_reservation, "drip_count": self.__drip_count,
            "drips": self.__drips, "settlements": self.__settlements, "commits": self.__commits,
            "novelty_holds": dict(self.__novelty_holds),
        }

    def _restore_state(self, state: dict) -> None:
        """Restore authenticated kernel checkpoint data without creating a new money entry."""
        if self.__ledger.final or self.dead:
            raise Infeasible("cannot restore a dead wallet")
        if state["initial"] != self.__initial or state["schedule"] != self.__schedule:
            raise ValueError("wallet launch configuration differs")
        for name in ("balance", "drips", "settlements", "commits"):
            require_money(state[name])
        if state["balance"] != (
            state["initial"] + state["drips"] + state["settlements"] - state["commits"]
        ):
            raise ValueError("checkpoint violates conservation")
        exhausted = state.get("exhausted", state["balance"] <= 0)
        if type(exhausted) is not bool:
            raise ValueError("checkpoint exhaustion must be boolean")
        holds = {r.id: replace(r, _issuer=self) for r in state["reservations"]}
        for name in (
            "balance", "next_reservation", "drip_count", "drips", "settlements", "commits",
        ):
            setattr(self, f"_Wallet__{name}", state[name])
        self.__reservations = holds
        self.__exhausted = exhausted or self.__balance <= 0
        self.__novelty_holds = dict(state.get("novelty_holds", {}))

    def _reservation_for_resume(self, reservation_id: str) -> Reservation:
        """Rebind an authenticated owner's saved hold to this wallet's actual reservation."""
        return self.__reservations[reservation_id]
