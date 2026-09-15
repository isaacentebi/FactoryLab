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
class ReleaseSchedule:
    """Locked backing leaves escrow in ascending tranches timed from the ledgered Launch.

    Each entry is ``(at_ns, amount_micro)``: ``at_ns`` is an offset from the
    launch timestamp, never an absolute time, so the same manifest defines the
    same schedule whenever the world happens to launch.
    """

    releases: tuple[tuple[int, Money], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.releases, tuple) or not self.releases:
            raise ValueError("a release schedule needs at least one tranche")
        previous = -1
        for item in self.releases:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("each release is an (at_ns, amount_micro) pair")
            at_ns, amount = item
            if type(at_ns) is not int or at_ns < 0:
                raise TypeError("release offsets must be nonnegative integer nanoseconds")
            require_money(amount, nonnegative=True)
            if amount == 0:
                raise ValueError("release tranches must be positive")
            if at_ns < previous:
                raise ValueError("release offsets must be ascending")
            previous = at_ns

    @property
    def total(self) -> Money:
        return sum(amount for _, amount in self.releases)


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
        reported_cost_multiple: int = 10,
        balance_floor_micro: Money = 0,
        locked_micro: Money = 0,
        release_schedule: ReleaseSchedule | None = None,
    ) -> None:
        if type(reported_cost_multiple) is not int or reported_cost_multiple < 1:
            raise ValueError("reported cost multiple must be a positive integer")
        self.__reported_cost_multiple = reported_cost_multiple
        require_money(initial, nonnegative=True)
        require_money(balance_floor_micro, nonnegative=True)
        require_money(locked_micro, nonnegative=True)
        self.__balance_floor_micro = balance_floor_micro
        if drip_schedule is not None and not isinstance(drip_schedule, DripSchedule):
            raise TypeError("drip_schedule must be immutable DripSchedule")
        if release_schedule is not None and not isinstance(release_schedule, ReleaseSchedule):
            raise TypeError("release_schedule must be immutable ReleaseSchedule")
        if locked_micro > initial:
            raise ValueError("locked backing cannot exceed the initial balance")
        if (release_schedule.total if release_schedule is not None else 0) != locked_micro:
            raise ValueError("release tranches must sum to the locked backing")
        self.__ledger = ledger
        self.__initial = self.__balance = initial
        self.__exhausted = initial <= self.__balance_floor_micro
        self.__schedule = drip_schedule
        self.__locked_micro = locked_micro
        self.__locked = locked_micro
        self.__release_schedule = release_schedule
        self.__released = 0
        self.__launch_ns: int | None = None
        self.__clock = clock_ns
        self.__reservations: dict[str, Reservation] = {}
        self.__next_reservation = 0
        self.__drip_count = 0
        self.__drips = self.__settlements = self.__commits = 0
        self.__pots_view: Callable[[], dict] | None = None
        self.__novelty = None
        self.__unhistoried: Callable[[str, str], bool] = lambda _h, _r: False
        self.__novelty_holds: dict[str, tuple[int | None, Money]] = {}
        self.__uncertain_bills: dict[str, dict] = {}
        ledger._claim_wallet(self)
        self._log("initial", initial, initial, "", "initial",
                  **({"locked": locked_micro} if locked_micro else {}))

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
        """Unhistoried work can use either ordinary money or the protected share, never escrow."""
        return self.unlocked - sum(item.amount for item in self.__reservations.values())

    @property
    def locked(self) -> Money:
        """Backing not yet released: booked in the balance, spendable by nobody."""
        return self.__locked

    @property
    def unlocked(self) -> Money:
        """Balance less locked backing; a venue loss can carry it below zero until a release."""
        return self.__balance - self.__locked

    @property
    def release_schedule(self) -> ReleaseSchedule | None:
        """The construction-time release schedule is immutable for this wallet's lifetime."""
        return self.__release_schedule

    @property
    def launch_ns(self) -> int | None:
        """The ledgered Launch timestamp every release offset counts from, once anchored."""
        return self.__launch_ns

    @property
    def released_tranches(self) -> int:
        """How many scheduled tranches have already moved from locked to unlocked."""
        return self.__released

    @property
    def next_release_ns(self) -> int | None:
        """Absolute time of the next unreleased tranche, or None when nothing remains."""
        schedule = self.__release_schedule
        if (schedule is None or self.__launch_ns is None
                or self.__released >= len(schedule.releases)):
            return None
        return self.__launch_ns + schedule.releases[self.__released][0]

    def launch(self, launch_ns: int) -> None:
        """Anchor the release schedule to the ledgered Launch timestamp, exactly once."""
        if type(launch_ns) is not int or launch_ns < 0:
            raise ValueError("launch_ns must be nonnegative integer nanoseconds")
        if self.__launch_ns is not None:
            if self.__launch_ns != launch_ns:
                raise ValueError("wallet is already anchored to a different launch")
            return
        if self.__release_schedule is not None:
            self._log("anchor", 0, self.balance, "", "launch", launch_ns=launch_ns,
                      locked=self.__locked)
        self.__launch_ns = launch_ns

    def release(self, target: "Reservation | int") -> Money | None:
        """Release a hold (``Reservation``) or every due tranche (``now_ns``, C1).

        The two releases share a name because both return something to the
        spendable balance without moving money: a cancelled hold gives back its
        ceiling, a due tranche gives back its backing.
        """
        if type(target) is int:
            return self.release_due(target)
        self.release_hold(target)
        return None

    def release_due(self, now_ns: int) -> Money:
        """Move every due tranche from locked to unlocked, once each, ledgered as ``release``.

        No money is created: the balance is unchanged and only its classification
        moves, so conservation holds before and after. Nothing is due before the
        wallet is anchored to its Launch, and a final ledger releases nothing.
        """
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        schedule = self.__release_schedule
        if schedule is None or self.__launch_ns is None or self.__ledger.final:
            return 0
        released = 0
        while self.__released < len(schedule.releases):
            offset, amount = schedule.releases[self.__released]
            due_ns = self.__launch_ns + offset
            if due_ns > now_ns:
                break
            locked_after = self.__locked - amount
            self.__ledger.append({
                "kind": "release", "tranche": self.__released, "amount": amount,
                "due_ns": due_ns, "locked_after": locked_after,
                "balance_after": self.balance, "ts": self.__clock(),
            })
            self.__locked = locked_after
            self.__released += 1
            released += amount
        return released

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
    def balance_floor_micro(self) -> Money:
        """The death threshold is immutable for this wallet's lifetime."""
        return self.__balance_floor_micro

    @property
    def dead(self) -> bool:
        """Reaching the floor is final, even when later observed settlements recover cash."""
        return self.__exhausted or self.__balance <= self.__balance_floor_micro

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
        """Hold affordable funds; a zero fee hold permits reconciliation after a trading loss."""
        require_money(amount, nonnegative=True)
        if not isinstance(handle, str) or not handle or not isinstance(reason, str) or not reason:
            raise ValueError("handle and reason are required")
        try:
            self._live()
        except Infeasible:
            if not self.__ledger.final:
                self._log("infeasible", amount, self.balance, handle, reason)
            raise
        reconcile_only = amount == 0 and reason == "treasury:fees"
        if amount > self.available_for(handle, reason) and not reconcile_only:
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

    def commit_reported(self, reservation: Reservation, actual: Money) -> Money:
        """Return the booked bill, disputing claims beyond the immutable ceiling multiple.

        Only a genuine, still-open reservation can carry a reported overrun.
        Ordinary commit remains ceiling-bounded. Both evidence items precede
        the balance/hold mutation, so interrupted accounting replays once.
        """
        require_money(actual, nonnegative=True)
        self._live()
        self._held(reservation)
        if actual > reservation.amount * self.__reported_cost_multiple:
            self.__ledger.append({
                "kind": "metering.disputed", "handle": reservation.handle,
                "reason": reservation.reason, "reservation_id": reservation.id,
                "reserved": reservation.amount, "reported": actual,
                "multiple": self.__reported_cost_multiple, "booked": reservation.amount,
                "ts": self.__clock(),
            })
            actual = reservation.amount
        if actual > reservation.amount:
            self.__ledger.append({
                "kind": "metering.overrun", "handle": reservation.handle,
                "reason": reservation.reason, "reservation_id": reservation.id,
                "reserved": reservation.amount, "actual": actual,
                "overrun": actual - reservation.amount, "ts": self.__clock(),
            })
        self._commit(reservation, actual)
        return actual

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
        self.__exhausted |= balance <= self.__balance_floor_micro
        self.__commits += actual
        self._refund_novelty(reservation, actual)
        del self.__reservations[reservation.id]

    def commit_uncertain(self, reservation: Reservation) -> None:
        """Book the held ceiling provisionally; retain billing uncertainty without a live hold."""
        self._held(reservation)
        bill = {"handle": reservation.handle, "reason": reservation.reason,
                "reservation_id": reservation.id, "provisional_micro": reservation.amount}
        self.__ledger.append({"kind": "metering.uncertain", **bill, "ts": self.__clock()})
        self._commit(reservation, reservation.amount)
        self.__uncertain_bills[reservation.id] = bill

    @property
    def uncertain_bills(self) -> dict[str, dict]:
        """Bills booked at their ceiling whose true cost is still unknown, by reservation id."""
        return {k: dict(v) for k, v in self.__uncertain_bills.items()}

    def settle_uncertain(self, reservation_id: str, actual_micro: Money, *,
                         balance_before: Money | None = None,
                         balance_after: Money | None = None,
                         spent_since_before: Money = 0) -> Money:
        """Settle one uncertain bill at its true cost; return what the ceiling over-charged.

        The true cost is what the provider's own balance dropped by across the call:
        ``balance_before`` (the last read before the call, less ``spent_since_before``
        booked through that provider since it) minus ``balance_after``. The caller
        measured it; this method only books it. The released difference returns to
        the balance and ``commits`` so conservation holds, and the bill leaves
        ``uncertain_bills``. A cost above the provisional ceiling is refused: the
        ceiling is the most this bill can ever have been.
        """
        require_money(actual_micro, nonnegative=True)
        require_money(spent_since_before, nonnegative=True)
        for read in (balance_before, balance_after):
            if read is not None:
                require_money(read)
        self._live()
        bill = self.__uncertain_bills.get(reservation_id)
        if bill is None:
            raise Infeasible("no uncertain bill for this reservation")
        provisional = bill["provisional_micro"]
        if actual_micro > provisional:
            raise Infeasible("settled cost exceeds the provisional ceiling")
        released = provisional - actual_micro
        balance = self.balance + released
        self._log(
            "settle_uncertain", released, balance, bill["handle"], bill["reason"],
            reservation_id=reservation_id, provisional_micro=provisional,
            actual_micro=actual_micro, provider_balance_before=balance_before,
            spent_since_before=spent_since_before, provider_balance_after=balance_after,
        )
        self.__balance = balance
        self.__commits -= released
        del self.__uncertain_bills[reservation_id]
        return released

    def release_hold(self, reservation: Reservation) -> None:
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
        """Book signed exchange P&L, funding or earned income only while the world remains alive."""
        self.settle_batch([(delta, handle, reason)])

    def settle_batch(self, settlements: list[tuple[Money, str, str]]) -> None:
        """Book every observed external effect; reaching the floor is final even if cash recovers.

        Validate the whole batch before writing. Preserve one ordinary settlement
        item per effect, then publish the new state only after all appends succeed.
        A retry belongs to authenticated recovery, never to a second live submission.
        ``income`` is a paid service call settled to the reserve (edition 2, C11):
        new money arriving from outside, booked like venue P&L, never negative.
        """
        self._live()
        balance = self.balance
        entries = []
        exhausted = self.__exhausted
        for delta, handle, reason in settlements:
            require_money(delta)
            if reason not in ("exchange_pnl", "funding", "income"):
                raise ValueError("settlement source must be exchange_pnl, funding or income")
            if reason == "income" and delta <= 0:
                raise ValueError("income must be positive")
            if not isinstance(handle, str) or not handle:
                raise ValueError("settlement handle is required")
            balance += delta
            exhausted |= balance <= self.__balance_floor_micro
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
            "exhausted": self.__exhausted, "reported_cost_multiple": self.__reported_cost_multiple,
            "balance_floor_micro": self.__balance_floor_micro,
            "locked_micro": self.__locked_micro, "release_schedule": self.__release_schedule,
            "locked": self.__locked, "released": self.__released, "launch_ns": self.__launch_ns,
            "reservations": [replace(r, _issuer=None) for r in self.__reservations.values()],
            "next_reservation": self.__next_reservation, "drip_count": self.__drip_count,
            "drips": self.__drips, "settlements": self.__settlements, "commits": self.__commits,
            "novelty_holds": dict(self.__novelty_holds),
            "uncertain_bills": {k: dict(v) for k, v in self.__uncertain_bills.items()},
        }

    def _restore_state(self, state: dict) -> None:
        """Restore authenticated kernel checkpoint data without creating a new money entry."""
        if self.__ledger.final or self.dead:
            raise Infeasible("cannot restore a dead wallet")
        if state["initial"] != self.__initial or state["schedule"] != self.__schedule:
            raise ValueError("wallet launch configuration differs")
        if state.get("reported_cost_multiple", 10) != self.__reported_cost_multiple:
            raise ValueError("wallet reported-cost policy differs")
        if state.get("balance_floor_micro", 0) != self.__balance_floor_micro:
            raise ValueError("wallet balance floor differs")
        if (state.get("locked_micro", 0) != self.__locked_micro
                or state.get("release_schedule") != self.__release_schedule):
            raise ValueError("wallet endowment configuration differs")
        released = state.get("released", 0)
        schedule = self.__release_schedule
        tranches = schedule.releases if schedule is not None else ()
        if type(released) is not int or not 0 <= released <= len(tranches):
            raise ValueError("checkpoint released tranche count is invalid")
        locked = self.__locked_micro - sum(amount for _, amount in tranches[:released])
        if state.get("locked", locked) != locked:
            raise ValueError("checkpoint locked backing disagrees with its released tranches")
        launch_ns = state.get("launch_ns")
        if launch_ns is not None and (type(launch_ns) is not int or launch_ns < 0):
            raise ValueError("checkpoint launch anchor is invalid")
        for name in ("balance", "drips", "settlements", "commits"):
            require_money(state[name])
        if state["balance"] != (
            state["initial"] + state["drips"] + state["settlements"] - state["commits"]
        ):
            raise ValueError("checkpoint violates conservation")
        exhausted = state.get("exhausted", state["balance"] <= self.__balance_floor_micro)
        if type(exhausted) is not bool:
            raise ValueError("checkpoint exhaustion must be boolean")
        holds = {r.id: replace(r, _issuer=self) for r in state["reservations"]}
        for name in (
            "balance", "next_reservation", "drip_count", "drips", "settlements", "commits",
        ):
            setattr(self, f"_Wallet__{name}", state[name])
        self.__reservations = holds
        self.__locked, self.__released, self.__launch_ns = locked, released, launch_ns
        self.__exhausted = exhausted or self.__balance <= self.__balance_floor_micro
        self.__novelty_holds = dict(state.get("novelty_holds", {}))
        self.__uncertain_bills = dict(state.get("uncertain_bills", {}))

    def _reservation_for_resume(self, reservation_id: str) -> Reservation:
        """Rebind an authenticated owner's saved hold to this wallet's actual reservation."""
        return self.__reservations[reservation_id]
