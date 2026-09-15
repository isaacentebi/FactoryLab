"""Final world termination and the sole public release path for the ledger seal."""

from collections.abc import Callable, Iterable
from time import time_ns

from factorylab.kernel.events import Bus, Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.money import Money, require_money
from factorylab.kernel.wallet import Wallet

#: The non-terminal budget trigger: unaffordable now, backed by a scheduled release.
DORMANT = "budget_dormant"


class Termination:
    """World finality and seal release remain irreversible, even if event callbacks fail."""

    def __init__(
        self,
        conditions: Iterable[str] = ("balance_zero", "explicit_kill", "ledger_failure"),
        *,
        ledger: Ledger,
        bus: Bus,
        clock_ns: Callable[[], int] = time_ns,
    ) -> None:
        required = frozenset(("balance_zero", "explicit_kill", "ledger_failure"))
        self.__conditions = frozenset(conditions)
        if self.__conditions != required:
            raise ValueError(
                "termination requires exactly balance_zero, explicit_kill and ledger_failure"
            )
        if bus.ledger is not ledger:
            raise ValueError("termination bus and ledger must belong to the same world")
        self.__ledger = ledger
        self.__bus = bus
        self.__clock = clock_ns
        self.__reason: str | None = None
        ledger._bind_termination(self)

    @property
    def conditions(self) -> frozenset[str]:
        """The three mandatory termination conditions cannot be removed."""
        return self.__conditions

    @property
    def final(self) -> bool:
        """Once killed, a world remains final forever."""
        return self.__reason is not None

    @property
    def reason(self) -> str | None:
        """Return the first final reason, never a later overwrite."""
        return self.__reason

    @property
    def seal_key(self) -> bytes:
        """Return the ledger key only after this world's termination."""
        return self.__ledger.key_store.key

    def check(self, wallet: Wallet, now_ns: int, *,
              cheapest_seat_micro: Money | None = None) -> str | None:
        """Return the final reason or a mandatory trigger without reviving or mutating money.

        ``budget_dormant`` is not a terminal reason: the wallet cannot afford
        the cheapest feasible seat (``cheapest_seat_micro``, or any spending at
        all when the runtime names no seat) but locked backing remains and a
        release is still scheduled, so the world pauses paid cognition and
        waits. Terminal death by budget requires ``locked == 0``.
        """
        if wallet.ledger is not self.__ledger:
            raise ValueError("wallet must belong to this world")
        if type(now_ns) is not int or now_ns < 0:
            raise ValueError("now_ns must be nonnegative integer nanoseconds")
        if cheapest_seat_micro is not None:
            require_money(cheapest_seat_micro, nonnegative=True)
        if self.final:
            return self.reason
        if not self.__ledger.healthy():
            return "ledger_failure"
        if wallet.dead:
            return "balance_floor" if wallet.balance_floor_micro else "balance_zero"
        if self.dormant(wallet, cheapest_seat_micro=cheapest_seat_micro):
            return DORMANT
        return None

    @staticmethod
    def dormant(wallet: Wallet, *, cheapest_seat_micro: Money | None = None) -> bool:
        """True while the unlocked money cannot buy the cheapest seat and a release is due later."""
        needed = max(1, cheapest_seat_micro or 0)
        return (wallet.locked > 0 and wallet.next_release_ns is not None
                and wallet.unhistoried_available < needed)

    def kill(self, reason: str) -> None:
        """Publish one final event and release the key; repeated kills preserve the first reason."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("termination reason is required")
        if reason == DORMANT:
            raise ValueError("dormancy is a pause, never a termination reason")
        if self.final:
            return
        event = Event(
            "termination", EventKind.TERMINATED, self.__clock(), {"reason": reason}, "kernel"
        )
        self.__reason = reason
        self.__bus._publish_terminal(event, self)
