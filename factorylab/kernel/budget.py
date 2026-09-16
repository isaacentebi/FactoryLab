"""Per-seat entitlements: classifications of one conserved wallet's unlocked micro-USD.

One wallet remains the financial root. A ``BudgetBook`` divides the wallet's
unlocked balance into named entitlements, one per seat, and an unallocated
remainder. Nothing here creates money: every operation moves an integer amount
between those classifications, and the amount a seat may spend is bounded by
both the wallet and its own entitlement.

Invariant, at every step::

    sum(entitlements) + unallocated == unlocked_balance(wallet) - holds

where ``holds`` is the sum of this book's outstanding seat reservations. A seat's
entitlement is reported net of its own holds, so a reservation moves the held
amount out of the entitlement and a commit settles it against the wallet.

Every movement is ledgered first as ``kind: "budget"`` with an ``op`` naming it.

Lineages: each genesis seat is a lineage root; a child registered by a seat belongs
to its proposer's lineage. A released tranche's ``base_share`` is split equally
across the live lineages, each lineage's share going to its head (the root while
it lives, then its oldest live member), so replication never buys a larger share
of the next tranche: the root funds its children through trial transfers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from decimal import Decimal
from time import time_ns
from typing import Any

from factorylab.kernel.money import Money, require_money
from factorylab.kernel.wallet import Infeasible

DEFAULT_BASE_SHARE = "0.8"


def unlocked_balance(wallet: Any) -> Money:
    """Return the wallet's unlocked balance under contract C1, or its balance before C1.

    An endowment wallet exposes ``unlocked`` (balance minus locked backing). A
    wallet without that attribute has no locked backing, so its whole balance is
    unlocked. This is the one place the book reads the wallet's money.
    """
    unlocked = getattr(wallet, "unlocked", None)
    return wallet.balance if unlocked is None else unlocked


class BudgetBook:
    """Named entitlements over one wallet's unlocked balance; the remainder is unallocated."""

    def __init__(
        self,
        wallet: Any,
        ledger: Any | None = None,
        *,
        clock_ns: Callable[[], int] = time_ns,
        base_share: str | float | Decimal = DEFAULT_BASE_SHARE,
    ) -> None:
        share = Decimal(str(base_share))
        if not share.is_finite() or not 0 < share <= 1:
            raise ValueError("base_share must be in (0, 1]")
        self.__wallet = wallet
        self.__ledger = ledger if ledger is not None else wallet.ledger
        self.__clock = clock_ns
        self.__share = share
        self.__gross: dict[str, Money] = {}  # entitlement including the seat's own holds
        self.__holds: dict[str, tuple[str, Money]] = {}  # reservation id -> (seat, amount)
        self.__retired: set[str] = set()
        self.__last_holds: dict[str, Money] = {}  # seat -> ceiling of its last model call
        # reservation id -> (seat, what the seat itself paid) for bills booked at the
        # ceiling with their true cost unknown; settled through ``_refund_uncertain``.
        self.__uncertain: dict[str, tuple[str, Money]] = {}
        # seat -> lineage id (the genesis root's id), in registration order, so the
        # oldest live member of a lineage is its first entry that is still live.
        self.__lineage: dict[str, str] = {}

    # ---- reads

    @property
    def base_share(self) -> Decimal:
        """The fraction of each endowment tranche split equally across live seats."""
        return self.__share

    def entitlement(self, assembly_id: str) -> Money:
        """Return what the seat may still commit: its entitlement net of its open holds."""
        return self.__gross.get(assembly_id, 0) - self.held_by(assembly_id)

    def cover(self, assembly_id: str, protected: Money = 0) -> Money:
        """What one call of the seat may reserve: its entitlement plus the protected share
        it may draw. The one rule routing reads and ``SeatWallet.reserve`` enforces."""
        require_money(protected, nonnegative=True)
        if assembly_id in self.__retired:
            return 0
        return max(0, self.entitlement(assembly_id) + protected)

    def held_by(self, assembly_id: str) -> Money:
        """Return the seat's outstanding reservations."""
        return sum(amount for seat, amount in self.__holds.values() if seat == assembly_id)

    def last_hold(self, assembly_id: str) -> Money:
        """Return the ceiling the seat's last model call reserved: what one call of its
        needs, as evidence rather than an estimate; zero before its first call."""
        return self.__last_holds.get(assembly_id, 0)

    def holds(self) -> Money:
        """Return every outstanding seat reservation this book tracks."""
        return sum(amount for _seat, amount in self.__holds.values())

    def seats(self) -> tuple[str, ...]:
        """Return the live seats, in id order; a retired seat holds nothing."""
        return tuple(sorted(seat for seat in self.__gross if seat not in self.__retired))

    def entitlements(self) -> dict[str, Money]:
        """Return each live seat's net entitlement, in id order."""
        return {seat: self.entitlement(seat) for seat in self.seats()}

    def lineage(self, assembly_id: str) -> str:
        """Return the lineage a seat belongs to; a seat never adopted is its own root."""
        return self.__lineage.get(assembly_id, assembly_id)

    def lineages(self) -> dict[str, dict[str, Any]]:
        """Return every lineage with live seats: its head and its live members, in
        registration order. A lineage whose members have all retired is not live and
        takes no share of a release."""
        members: dict[str, list[str]] = {}
        for seat in self.__lineage:
            if seat not in self.__retired and seat in self.__gross:
                members.setdefault(self.__lineage[seat], []).append(seat)
        for seat in self.seats():
            if seat not in self.__lineage:
                members.setdefault(seat, []).append(seat)
        return {lineage: {"head": seats[0], "seats": seats} for lineage, seats in members.items()}

    def heads(self) -> tuple[str, ...]:
        """Return the seat that receives each live lineage's release share, in lineage order."""
        return tuple(row["head"] for row in self.lineages().values())

    def unallocated(self) -> Money:
        """Return the unlocked money no seat is entitled to: ``unlocked - entitlements - holds``.

        Shared spending that no seat authored (rent, fees, trading losses booked to
        the wallet) is absorbed here, so the value is signed: a negative pool means
        the entitlements overstate the wallet, and the wallet's own check bounds
        every reservation until a credit or release restores it.
        """
        return unlocked_balance(self.__wallet) - sum(self.__gross.values())

    def check_invariant(self) -> bool:
        """Confirm the classification sums to the unlocked balance net of holds, and that every
        hold this book tracks is a live reservation of the wallet for the same amount."""
        booked = sum(self.entitlements().values()) + self.unallocated()
        if booked != unlocked_balance(self.__wallet) - self.holds():
            return False
        live = {r.id: r.amount for r in self.__wallet.state()["reservations"]}
        return all(live.get(rid) == amount for rid, (_seat, amount) in self.__holds.items())

    # ---- movements

    def _log(self, op: str, **fields: Any) -> None:
        self.__ledger.append({"kind": "budget", "op": op, **fields, "ts": self.__clock()})

    def _to_commons(self, seat: str, amount: Money, reason: str, *, source: str) -> None:
        """Ledger where a late credit to a retired seat actually went (edition 3, R3-C).

        Retirement is final at this layer: a consequence that arrives after a seat
        has retired — a settled trade, a late service payment — creates no
        entitlement for it and never returns it to its lineage's headship. The
        money is not lost; it stays in the pool everyone draws from. GPT-6's third
        reading asked for that to be *ledgered as such*, so the fact has an item of
        its own with the seat and the amount, beside the refusal that produced it,
        and the wake shows it.
        """
        self._log("retired_credit_to_commons", assembly_id=seat, amount=amount,
                  source=source, reason=reason, unallocated_after=self.unallocated())

    def _after(self, *seats: str) -> dict[str, Any]:
        return {"entitlement_after": {seat: self.entitlement(seat) for seat in seats},
                "unallocated_after": self.unallocated()}

    @staticmethod
    def _seat(assembly_id: str) -> str:
        if not isinstance(assembly_id, str) or not assembly_id:
            raise ValueError("assembly id is required")
        return assembly_id

    def grant(self, assembly_id: str, amount: Money, reason: str) -> None:
        """Move ``amount`` from the unallocated pool to a seat; refuses beyond the pool."""
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        if amount > self.unallocated():
            self._log("infeasible", assembly_id=seat, amount=amount, reason=reason,
                      unallocated=self.unallocated())
            raise Infeasible("grant exceeds the unallocated pool")
        after = self.__gross.get(seat, 0) + amount
        self._log("grant", assembly_id=seat, amount=amount, reason=reason,
                  entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() - amount)
        self.__gross[seat] = after
        self.__retired.discard(seat)

    def earn(self, assembly_id: str, amount: Money, reason: str) -> None:
        """Classify new money the wallet has just received from outside as the seat's.

        Earned service income (C11) is settled into the wallet before this is
        called, so ``unlocked`` has already risen by ``amount``: the seat's
        entitlement rises by the same amount and the pool is untouched, whatever
        it holds. Unlike ``credit`` this is never bounded by the pool, because it
        classifies money that arrived, not money the pool held.
        """
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        if seat in self.__retired:
            self._log("retired_earn", assembly_id=seat, amount=amount, reason=reason,
                      unallocated_after=self.unallocated())
            self._to_commons(seat, amount, reason, source="income")
            return None
        after = self.__gross.get(seat, 0) + amount
        self._log("income", assembly_id=seat, amount=amount, reason=reason,
                  entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() - amount)
        self.__gross[seat] = after
        self.__retired.discard(seat)

    def adopt(self, assembly_id: str, proposer: str | None, reason: str) -> str:
        """Place a registered seat in its proposer's lineage; without a proposer, or
        with one this book never placed, the seat roots a lineage of its own.
        Returns the lineage id. A seat already placed keeps its lineage: a next
        version of a retired id stays where the id was."""
        seat = self._seat(assembly_id)
        if seat in self.__lineage:
            return self.__lineage[seat]
        lineage = seat if proposer is None else self.lineage(self._seat(proposer))
        self._log("lineage", assembly_id=seat, lineage=lineage, proposer=proposer, reason=reason)
        self.__lineage[seat] = lineage
        return lineage

    def credit(self, assembly_id: str, amount: Money, reason: str) -> Money:
        """Classify money the wallet has already received as the seat's; never new money.

        Returns the amount credited. When the pool cannot back the whole amount
        (shared spending already consumed it), only the backed part is credited
        and the shortfall is ledgered, so entitlements never exceed the wallet.
        """
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        if seat in self.__retired:
            self._log("retired_credit", assembly_id=seat, amount=amount, reason=reason,
                      unallocated_after=self.unallocated())
            self._to_commons(seat, amount, reason, source="credit")
            return 0
        credited = max(0, min(amount, self.unallocated()))
        after = self.__gross.get(seat, 0) + credited
        self._log("credit", assembly_id=seat, amount=credited, requested=amount, reason=reason,
                  entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() - credited)
        self.__gross[seat] = after
        self.__retired.discard(seat)
        return credited

    def debit(self, assembly_id: str, amount: Money, reason: str) -> None:
        """Move ``amount`` from a seat back to the unallocated pool; refuses beyond the seat."""
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        if amount > self.entitlement(seat):
            self._log("infeasible", assembly_id=seat, amount=amount, reason=reason,
                      entitlement=self.entitlement(seat))
            raise Infeasible("debit exceeds the seat's entitlement")
        after = self.__gross.get(seat, 0) - amount
        self._log("debit", assembly_id=seat, amount=amount, reason=reason,
                  entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() + amount)
        self.__gross[seat] = after

    def charge(self, assembly_id: str, amount: Money, reason: str) -> Money:
        """Debit a loss from its maker down to a floor of zero; the rest lands on the pool.

        Returns what the seat paid. The wallet has already booked the loss, so the
        uncovered remainder is not new spending: it is the commons bearing what
        the seat could not, and it is ledgered as ``commons`` on the same item.
        """
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        own = max(0, min(amount, self.entitlement(seat)))
        after = self.__gross.get(seat, 0) - own
        self._log("charge", assembly_id=seat, amount=amount, own=own, commons=amount - own,
                  reason=reason, entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() + own)
        self.__gross[seat] = after
        return own

    def bridge(self, assembly_id: str, handle: str, amount: Money, reason: str) -> Money:
        """Back one call's ceiling beyond a seat's cover from the pool, as far as it goes.

        Routing admitted the seat on the ceiling it could see; the rendered request
        is dearer. The gap is the runtime's estimation error, not the seat's choice,
        so the commons backs it for this one call rather than failing the return.
        Nothing moves here: the seat still pays what it has on commit and the rest
        is ledgered there as ``commons``. Returns the backed amount.
        """
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        backed = max(0, min(amount, self.unallocated()))
        self._log("bridge", assembly_id=seat, handle=handle, amount=amount, backed=backed,
                  reason=reason, entitlement=self.entitlement(seat),
                  unallocated_after=self.unallocated())
        return backed

    def transfer(self, src: str, dst: str, amount: Money, reason: str) -> None:
        """Move ``amount`` from one seat to another; refuses beyond the source's entitlement."""
        source, target = self._seat(src), self._seat(dst)
        require_money(amount, nonnegative=True)
        if source == target:
            raise ValueError("transfer needs two different seats")
        if amount > self.entitlement(source):
            self._log("infeasible", src=source, dst=target, amount=amount, reason=reason,
                      entitlement=self.entitlement(source))
            raise Infeasible("transfer exceeds the source seat's entitlement")
        src_after = self.__gross.get(source, 0) - amount
        dst_after = self.__gross.get(target, 0) + amount
        self._log("transfer", src=source, dst=target, amount=amount, reason=reason,
                  entitlement_after={source: src_after - self.held_by(source),
                                     target: dst_after - self.held_by(target)},
                  unallocated_after=self.unallocated())
        self.__gross[source] = src_after
        self.__gross[target] = dst_after
        self.__retired.discard(target)

    def retire(self, assembly_id: str, reason: str) -> Money:
        """Return a retired seat's entitlement to the pool; the seat stays known but empty."""
        seat = self._seat(assembly_id)
        if self.held_by(seat):
            raise Infeasible("a seat with open holds cannot retire")
        returned = self.__gross.get(seat, 0)
        lineage = self.lineage(seat)
        was_head = self.lineages().get(lineage, {}).get("head") == seat
        self._log("retire", assembly_id=seat, amount=returned, reason=reason,
                  entitlement_after={seat: 0}, unallocated_after=self.unallocated() + returned)
        self.__gross[seat] = 0
        self.__retired.add(seat)
        if was_head:
            # Headship passes to the lineage's oldest live member; with none left the
            # lineage's future release share stays in the pool.
            successor = self.lineages().get(lineage, {}).get("head")
            self._log("lineage_handoff", lineage=lineage, retired=seat, head=successor,
                      reason=reason)
        return returned

    def _split(self, op: str, amount: Money, seats: Iterable[str], reason: str, *,
               lineages: dict[str, str] | None = None) -> dict[str, Money]:
        """Classify ``amount`` of the pool: ``base_share`` of it equally across ``seats``.

        Only what the pool actually holds is split: shared spending can have left
        the pool below the tranche, and that hole is refilled before any seat is
        endowed, so entitlements never exceed the wallet through a release.
        ``lineages`` names, for a release, the lineage each recipient is the head of.
        """
        require_money(amount, nonnegative=True)
        ordered = sorted({self._seat(seat) for seat in seats})
        backed = max(0, min(amount, self.unallocated()))
        numerator, denominator = self.__share.as_integer_ratio()
        shared = backed * numerator // denominator
        per_seat = shared // len(ordered) if ordered else 0
        grants = {seat: per_seat for seat in ordered} if per_seat else {}
        granted = per_seat * len(grants)
        self._log(op, amount=amount, backed=backed, reason=reason, base_share=str(self.__share),
                  grants=grants, to_unallocated=amount - granted,
                  unallocated_after=self.unallocated() - granted,
                  **({"lineages": lineages} if lineages is not None else {}))
        for seat, share in grants.items():
            self.__gross[seat] = self.__gross.get(seat, 0) + share
            self.__retired.discard(seat)
        for seat in ordered:
            self.__gross.setdefault(seat, 0)
        return grants

    def genesis(self, seats: Iterable[str]) -> dict[str, Money]:
        """Split the launch pool once: ``base_share`` equally across the seeded seats,
        each the root of its own lineage."""
        if self.__gross or self.__holds:
            raise Infeasible("genesis runs once, on an empty book")
        seeded = sorted({self._seat(seat) for seat in seats})
        for seat in seeded:
            self.__lineage[seat] = seat
        return self._split("genesis", max(0, self.unallocated()), seeded, "genesis",
                           lineages={seat: seat for seat in seeded})

    def on_release(self, amount: Money, reason: str = "release") -> dict[str, Money]:
        """Classify one released endowment tranche already booked unlocked in the wallet.

        Contract C1's release path calls this with the amount ``Wallet.release`` moved
        from locked to unlocked: ``base_share`` of it is split equally across the live
        lineages, each share to the lineage's head, and the remainder stays
        unallocated. Never raises: a tranche the pool does not fully hold (shared
        spending ran it down) refills the pool first.
        """
        heads = {row["head"]: lineage for lineage, row in self.lineages().items()}
        return self._split("release", amount, heads, reason, lineages=heads)

    def commons_release(self, reason: str) -> dict[str, Money]:
        """Split the whole unallocated pool equally across the live lineages, once.

        The runtime calls this at a reserve-window boundary when no live seat can
        cover a call from its own entitlement while the pool still holds money
        (second reading, P1-04): the commons is released so somebody can act,
        rather than the world idling with money nobody may spend. It goes where a
        tranche goes, one equal share per live lineage to that lineage's head, so
        replication buys no larger share of the commons either; the head funds its
        children through transfers. Unlike a tranche release nothing is held back
        and ``base_share`` does not apply; an integer remainder below one micro per
        lineage stays unallocated. Ledgered as ``budget op="commons_release"`` with
        the grants and the lineage of each head. Returns the grants; an empty pool
        or no live seat moves nothing and ledgers nothing.
        """
        heads = {row["head"]: lineage for lineage, row in self.lineages().items()}
        pool = self.unallocated()
        per_head = pool // len(heads) if heads and pool > 0 else 0
        if per_head <= 0:
            return {}
        grants = {head: per_head for head in heads}
        granted = per_head * len(heads)
        self._log("commons_release", amount=pool, reason=reason, grants=grants,
                  lineages=heads, to_unallocated=pool - granted,
                  unallocated_after=pool - granted)
        for head, share in grants.items():
            self.__gross[head] = self.__gross.get(head, 0) + share
        return grants

    # ---- holds, used only by SeatWallet

    def _cover(self, assembly_id: str, amount: Money, handle: str, reason: str, *,
               extra: Money = 0) -> None:
        seat = self._seat(assembly_id)
        require_money(amount, nonnegative=True)
        require_money(extra, nonnegative=True)
        if seat not in self.__gross:
            # A seat this book never endowed (instantiated past registration, or a
            # child whose trial the pool could not cover) is refused by name: the
            # meter reports it like any other refusal instead of failing inside.
            self._log("infeasible", assembly_id=seat, amount=amount, handle=handle,
                      reason=reason, entitlement=0, protected=extra, unknown_seat=True)
            raise Infeasible(f"seat {seat!r} has no entitlement: this book never endowed it")
        if amount > self.cover(seat, extra):
            self._log("infeasible", assembly_id=seat, amount=amount, handle=handle, reason=reason,
                      entitlement=self.entitlement(seat), protected=extra)
            raise Infeasible("reservation exceeds the seat's entitlement")

    def _hold(self, assembly_id: str, reservation: Any) -> None:
        seat = self._seat(assembly_id)
        if reservation.id in self.__holds:
            raise Infeasible("reservation is already held")
        self._log("hold", assembly_id=seat, amount=reservation.amount, handle=reservation.handle,
                  reason=reservation.reason, reservation_id=reservation.id,
                  entitlement_after={seat: self.entitlement(seat) - reservation.amount},
                  unallocated_after=self.unallocated())
        self.__holds[reservation.id] = (seat, reservation.amount)
        if str(reservation.reason).startswith("model:"):
            self.__last_holds[seat] = reservation.amount

    def _settle_hold(self, reservation: Any, actual: Money) -> Money:
        """Debit the booked cost from the seat as far as its entitlement reaches.

        The wallet has already paid ``actual``. The seat pays first; whatever its
        entitlement cannot cover (protected exploration admitted at reserve time,
        or a reported overrun beyond the hold) stays with the pool and is ledgered
        as ``commons`` so the commons-funded part of every call is visible.
        Returns what the seat itself paid.
        """
        require_money(actual, nonnegative=True)
        seat, held = self._held(reservation)
        own = max(0, min(actual, self.__gross.get(seat, 0)))
        after = self.__gross.get(seat, 0) - own
        self._log("commit", assembly_id=seat, amount=actual, own=own, commons=actual - own,
                  handle=reservation.handle, reason=reservation.reason,
                  reservation_id=reservation.id,
                  released=max(0, held - actual), overrun=max(0, actual - held),
                  entitlement_after={seat: after - (self.held_by(seat) - held)},
                  unallocated_after=self.unallocated() + own)
        del self.__holds[reservation.id]
        self.__gross[seat] = after
        return own

    def _settle_uncertain_hold(self, reservation: Any) -> None:
        """Book an uncertain bill at its ceiling and remember which seat paid what."""
        seat, _held = self._held(reservation)
        own = self._settle_hold(reservation, reservation.amount)
        self.__uncertain[reservation.id] = (seat, own)

    def _refund_uncertain(self, reservation_id: str, released: Money, reason: str) -> Money:
        """Return an over-charged ceiling to the seat that paid it, as far as it paid.

        The wallet has already taken ``released`` back into its balance, so the
        pool holds it; this classifies the seat's own part of it as the seat's
        again. What the commons paid stays with the pool. A bill this book never
        saw (booked through the shared meter) refunds nothing to any seat.
        """
        require_money(released, nonnegative=True)
        seat, own = self.__uncertain.pop(reservation_id, (None, 0))
        if seat is None:
            return 0
        refund = max(0, min(released, own, self.unallocated()))
        after = self.__gross.get(seat, 0) + refund
        self._log("settle_uncertain", assembly_id=seat, amount=refund, released=released,
                  own=own, reason=reason, reservation_id=reservation_id,
                  entitlement_after={seat: after - self.held_by(seat)},
                  unallocated_after=self.unallocated() - refund)
        self.__gross[seat] = after
        return refund

    def _held(self, reservation: Any) -> tuple[str, Money]:
        """Return the seat and amount behind a reservation this book holds."""
        try:
            return self.__holds[reservation.id]
        except KeyError:
            raise Infeasible(
                f"reservation {reservation.id!r} is not held by this budget book") from None

    def _release_hold(self, reservation: Any) -> None:
        seat, held = self._held(reservation)
        self._log("release_hold", assembly_id=seat, amount=held, handle=reservation.handle,
                  reason=reservation.reason, reservation_id=reservation.id,
                  entitlement_after={seat: self.entitlement(seat) + held},
                  unallocated_after=self.unallocated())
        del self.__holds[reservation.id]

    # ---- checkpoint

    def state(self) -> dict:
        """Return the classification exactly; the wallet holds the money itself."""
        return {
            "base_share": str(self.__share),
            "entitlements": dict(self.__gross),
            "holds": {rid: [seat, amount] for rid, (seat, amount) in self.__holds.items()},
            "retired": sorted(self.__retired),
            "last_holds": dict(self.__last_holds),
            "uncertain": {rid: [seat, own] for rid, (seat, own) in self.__uncertain.items()},
            "lineages": dict(self.__lineage),
        }

    def _restore_state(self, state: dict) -> None:
        if Decimal(str(state.get("base_share", str(self.__share)))) != self.__share:
            raise ValueError("budget base share differs")
        gross = {str(seat): require_money(amount) for seat, amount in state["entitlements"].items()}
        holds = {str(rid): (str(seat), require_money(amount, nonnegative=True))
                 for rid, (seat, amount) in state.get("holds", {}).items()}
        self.__gross = gross
        self.__holds = holds
        self.__retired = {str(seat) for seat in state.get("retired", ())}
        self.__last_holds = {str(seat): require_money(amount, nonnegative=True)
                             for seat, amount in state.get("last_holds", {}).items()}
        self.__uncertain = {str(rid): (str(seat), require_money(own, nonnegative=True))
                            for rid, (seat, own) in state.get("uncertain", {}).items()}
        # A checkpoint from before lineages knew only seats: each is its own root.
        self.__lineage = {str(seat): str(lineage)
                          for seat, lineage in state.get("lineages", {}).items()}
        if not self.__lineage:
            self.__lineage = {seat: seat for seat in gross}


class SeatWallet:
    """The ``WalletLike`` a seat's meter sees: covered by the wallet and by the seat alike.

    ``reserve`` refuses unless both the wallet and the seat's entitlement cover the
    ceiling; each commit debits both by the booked cost; a release frees both.

    ``protected`` is the runtime's novelty classifier: for a reservation it admits
    (an unhistoried seat's own model call) it returns the protected exploration
    amount the seat may draw beyond its entitlement, the same share the wallet
    itself protects for that call. Without it the seat's entitlement is the bound.

    ``payer`` names the seat liable for a handle when it is not this seat: a child
    request is its parent's subcontracting and spends the parent's entitlement.
    Without it every reservation is this seat's own.
    """

    def __init__(self, wallet: Any, book: BudgetBook, assembly_id: str, *,
                 protected: Callable[[str, str], Money] | None = None,
                 payer: Callable[[str], str | None] | None = None) -> None:
        self.wallet = wallet
        self.book = book
        self.assembly_id = BudgetBook._seat(assembly_id)
        self.protected = protected
        self.payer = payer

    def liable(self, handle: str) -> str:
        """Return the seat whose entitlement covers work under ``handle``."""
        seat = self.payer(handle) if self.payer is not None else None
        return self.assembly_id if seat is None else BudgetBook._seat(seat)

    def reserve(self, amount: Money, handle: str, reason: str) -> Any:
        seat = self.liable(handle)
        extra = self.protected(handle, reason) if self.protected is not None else 0
        self.book._cover(seat, amount, handle, reason, extra=extra)
        reservation = self.wallet.reserve(amount, handle, reason)
        self.book._hold(seat, reservation)
        return reservation

    def commit(self, reservation: Any, actual: Money) -> None:
        self.wallet.commit(reservation, actual)
        self.book._settle_hold(reservation, actual)

    def commit_reported(self, reservation: Any, actual: Money) -> Money:
        booked = self.wallet.commit_reported(reservation, actual)
        booked = actual if booked is None else booked
        self.book._settle_hold(reservation, booked)
        return booked

    def commit_uncertain(self, reservation: Any) -> None:
        self.wallet.commit_uncertain(reservation)
        self.book._settle_uncertain_hold(reservation)

    def settle_uncertain(self, reservation_id: str, actual_micro: Money, **reads: Any) -> Money:
        """Settle the wallet's bill, then hand the seat back what it over-paid (C10)."""
        released = self.wallet.settle_uncertain(reservation_id, actual_micro, **reads)
        self.book._refund_uncertain(reservation_id, released, "settle_uncertain")
        return released

    def release(self, reservation: Any) -> None:
        self.wallet.release(reservation)
        self.book._release_hold(reservation)
