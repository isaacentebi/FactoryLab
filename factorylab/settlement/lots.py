"""Pure FIFO accounting; exact fractions prevent fill splitting from creating micro-USD.

Prices and sizes enter as decimal strings, never binary floats. Fractional micro-USD
fees, funding and P&L remain exact until each return's final total is floored once.
A backstop fixes the outcome, not the inventory: subsequent closes still consume
the marked opener's lots. A close credits the realised P&L of the closed quantity
once, split between its distinct sides by the notional each contributed (the
opener's entry price and the closer's exit price on the closed quantity): the
opener's part is net of its opening fee and funding, the closer's net of its
closing fee. A handle closing its own lot receives the whole profit once.
Only a decision with an open account can own an order or a lot.

An ``event`` lot is an outcome token of a binary event market (Polymarket). It
is held long only and is marked like a spot lot, at the market's own midpoint:
the price is the market's anticipatory settlement of the belief (essay II.IV.b),
so the decision is scored at the backstop rather than waiting on a resolution
that may come after its learner has moved on. The resolution itself closes the
lot later (``redeem``) and its money reaches the owner as a late realization.

A backstop mark values an open lot at what closing it would realise (wave 16, D7):
the mid less the exit fee at the venue's taker rate for its market, so the acting
road is charged the same round trip as the road not taken. The mark is never money:
the real close is booked once, late, with the fee the venue actually charged.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

from factorylab.kernel.money import require_money
from factorylab.settlement.scoring import _require_id
from factorylab.settlement.vocabulary import _require_event_index


def exact(value: str | int | Decimal | Fraction) -> Fraction:
    """Reject floats and nonfinite numbers; preserve every supplied decimal digit."""
    if type(value) not in (str, int, Decimal, Fraction):
        raise ValueError("an exact decimal or fraction is required")
    return Fraction(value)


@dataclass(frozen=True)
class Payoff:
    """One immutable outcome binds its net proceeds, full compute cost and mark status.

    ``net_micro`` is the return's trading result; ``earned_micro`` is what the
    service it registered was paid before the outcome was fixed (edition 2, C11).
    A return pays off when the two together exceed its cost.
    """

    handle: str
    y: int
    net_micro: int
    cost_micro: int
    at_event: int
    marked: bool = False
    liquidated: bool = False
    earned_micro: int = 0
    # The exit fee a marked outcome deducted for its open quantity (wave 16, D7): an
    # estimate at the venue's taker rate, never money.
    exit_fee_micro: int = 0
    # The documented reason this outcome carries no fact at all (R4-C). A
    # censored outcome closes the account so later returns resolve, but its
    # ``y`` is not an observation: nothing is scored from it and no money moves
    # on it. ``external_unobservable`` is the one reason the runtime writes.
    censored: str | None = None


@dataclass(frozen=True)
class Lot:
    """An open quantity retains its original owner and unallocated carrying charges."""

    handle: str | None
    coin: str
    is_buy: bool
    size: Fraction
    px: Fraction
    charges_micro: Fraction
    market: str = "perp"


@dataclass(frozen=True)
class ReturnAccount:
    """Cost is unknown until all invocation rounds and tools have returned.

    ``cost_micro`` is the return's own metered compute and tools, fixed once, and
    is the outcome's threshold. It holds only debits with a real counterparty.
    """

    handle: str
    opened_at_event: int
    cost_micro: int | None = None
    realized_micro: Fraction = Fraction(0)
    opened_lots: int = 0
    closed_lots: int = 0
    liquidated: bool = False
    payoff: Payoff | None = None
    closes: int = 0  # lots this return closed, in whole or in part, as the closer
    earned_micro: int = 0  # paid calls of the service this return registered, while open
    earnings: int = 0  # how many such receipts
    late_micro: int = 0  # realised P&L already booked to the owner after the outcome was fixed
    # A return no seat authored: a router abstention. It is kept so its handle can
    # never be admitted twice, but it owes no outcome — nothing resolves against it
    # and no payoff forecast may be sealed on it.
    voided: bool = False
    # The world tick the return opened at, when the caller keeps a tick clock: the
    # consequence backstop is then counted in ticks, never in internal events.
    opened_at_tick: int | None = None
    # The venue-clock nanosecond the return opened at, when the caller keeps one: the
    # consequence horizon is then counted on the venue's clock (wave 16, D2).
    opened_at_ns: int | None = None


@dataclass(frozen=True)
class LotOrder:
    """Even a fully filled order retains its owner for subsequent venue observations.

    ``ordered`` is the quantity the venue accepted and ``executed`` what has
    filled against it since. ``remaining`` is the unfilled liability, cleared by
    a cancel; ``ordered`` is not, so a fill that executed before a cancel took
    effect is still accepted, up to the original size and never beyond it.
    """

    order_id: str
    handle: str
    remaining: Fraction
    ordered: Fraction | None = None  # None: an order bound before sizes were tracked
    executed: Fraction = Fraction(0)
    # Wave 17b: the quantity the venue's own order status reported filled when it said
    # the order is terminal (filled, cancelled or rejected), or None while the venue
    # has not said so. Only a venue-confirmed terminal order can no longer fill.
    confirmed: Fraction | None = None


#: The per-account counts ``released`` keeps for accounts no longer in ``returns``.
RELEASED_COUNTS = ("accounts", "paid_off", "not_paid_off", "marked", "censored_outcomes",
                   "voided", "lots_opened", "lots_closed", "closes_credited")
#: What a fill naming a released account's order is: a late realization of its owner
#: (``LotTable.fill``), booked and never graded.
RELEASED_ORDER = "the order's return was settled and released"
#: Why a return's outcome is censored at its horizon when the venue never stated the
#: taker rate its open lots exit at (wave 16, ruling R10-i): uninformative, not pending.
FEE_UNKNOWN = "fee_unknown"
#: Why a return's outcome is censored when an instrument it holds was never priced by
#: a venue mid at or after its horizon within its patience (Codex on #152): the same
#: rule a named trade the venue never priced follows; uninformative, never pending.
NO_MARK = "no_mark"


#: The markets whose exit a venue's taker schedule prices (an event token's is not).
VENUE_FEE_MARKETS = ("perp", "spot")


def _exit_rates_for(exit_rates, lots, account, now_ns, horizon_ns) -> dict[str, str | None]:
    """The exit rate of each instrument ``lots`` hold, at the return's horizon.

    A mapping is read per market as stated (a market it does not list carries no fee:
    absent here). A callable ``(instrument, at_ns)`` is asked for each perp or spot
    instrument's own rate at the account's opening plus ``horizon_ns`` on the venue's
    clock, or at ``now_ns`` when either is unknown; an event token carries no venue
    exit fee. Keyed by instrument (a lot's coin), never pooled across instruments.
    """
    if exit_rates is None:
        return {}
    if callable(exit_rates):
        at = (account.opened_at_ns + horizon_ns
              if account.opened_at_ns is not None and horizon_ns is not None else now_ns)
        if at is None:
            return {}
        return {lot.coin: exit_rates(lot.coin, at) for lot in lots
                if lot.market in VENUE_FEE_MARKETS}
    return {lot.coin: exit_rates[lot.market] for lot in lots if lot.market in exit_rates}


@dataclass(frozen=True)
class LotTable:
    """Every operation returns a detached successor; the input table never changes."""

    lots: tuple[Lot, ...] = ()
    returns: tuple[ReturnAccount, ...] = ()
    orders: tuple[LotOrder, ...] = ()
    services: tuple[tuple[str, str], ...] = ()  # (service id, registering return)
    # Wave 17b: accounts released once closed and fully settled survive only as these
    # counts (``RELEASED_COUNTS`` order); their orders as (order id, owning handle,
    # the caller's release mark, the seat that authored the handle) until the caller
    # forgets them; and what fills on those orders, or on lots they opened, realised
    # after the release, as (handle, exact total, booked) until booked.
    released: tuple[int, ...] = ()
    released_orders: tuple[tuple[str, str, int, str | None], ...] = ()
    released_late: tuple[tuple[str, Fraction, int], ...] = ()

    def seed_spot(self, coin: str, size: str, px: str) -> "LotTable":
        """Launch inventory has an exact basis and no decision receives opening credit."""
        _require_id(coin)
        quantity, price = exact(size), exact(px)
        if quantity <= 0 or price <= 0 or not coin.endswith("/USDC"):
            raise ValueError("invalid launch spot inventory")
        if self.returns or self.orders or any(lot.coin == coin for lot in self.lots):
            raise ValueError("spot inventory may only be seeded once before decisions")
        return replace(self, lots=(*self.lots, Lot(
            None, coin, True, quantity, price, Fraction(0), "spot",
        )))

    def start(self, handle: str, event: int, tick: int | None = None,
              ns: int | None = None) -> "LotTable":
        """Admit a unique return before its orders can produce fills."""
        _require_id(handle)
        _require_event_index(event, "event")
        if tick is not None:
            _require_event_index(tick, "tick")
        if ns is not None:
            _require_event_index(ns, "ns")
        if any(r.handle == handle for r in self.returns):
            raise ValueError("return already admitted")
        return replace(self, returns=(*self.returns,
                                      ReturnAccount(handle, event, opened_at_tick=tick,
                                                    opened_at_ns=ns)))

    def finish(self, handle: str, cost_micro: int) -> "LotTable":
        """Fix a return's nonnegative total compute cost exactly once."""
        require_money(cost_micro, nonnegative=True)
        account = self.account(handle)
        if account.cost_micro is not None:
            raise ValueError("return cost already final")
        return self._accounts({handle: replace(account, cost_micro=cost_micro)})

    def void(self, handle: str) -> "LotTable":
        """Void an admitted return that authored nothing, so it owes no outcome.

        A router abstention opens no consequence: nothing resolves against a
        voided account, so no payoff is ever fixed for it and no seat can be
        addressed with one. A return that already traded, cost something or was
        resolved is not an abstention and may not be voided.
        """
        account = self.account(handle)
        if (account.payoff is not None or account.cost_micro is not None
                or account.opened_lots or account.closes or account.earnings
                or any(lot.handle == handle for lot in self.lots)
                or any(order.handle == handle for order in self.orders)):
            raise ValueError("only a return that authored nothing may be voided")
        return self._accounts({handle: replace(account, voided=True)})

    def bind_service(self, service: str, handle: str) -> "LotTable":
        """Bind a registered service to the return that registered it, so the service's
        paid calls are that return's economic consequence. A later version of the
        same service rebinds it to the return that registered the version."""
        _require_id(service)
        _require_id(handle)
        if not any(r.handle == handle for r in self.returns):
            raise ValueError("service requires an open consequence account")
        others = tuple((s, h) for s, h in self.services if s != service)
        return replace(self, services=(*others, (service, handle)))

    def service_return(self, service: str) -> str | None:
        """Return the handle a service is bound to, or None for an unbound service."""
        return next((h for s, h in self.services if s == service), None)

    def income(self, service: str, micro: int) -> "LotTable":
        """Credit one settled service receipt to the registering return while its
        outcome is open. A fixed outcome is never reopened: the money is the
        seller's either way (credited at receipt), only the score stays as it was."""
        require_money(micro, nonnegative=True)
        handle = self.service_return(service)
        if handle is None:
            return self
        try:
            account = self.account(handle)
        except KeyError:
            return self  # released: its outcome was fixed long ago, and stays as it was
        if account.payoff is not None:
            return self
        return self._accounts({handle: replace(
            account, earned_micro=account.earned_micro + micro, earnings=account.earnings + 1)})

    def late_realizations(self) -> tuple["LotTable", dict[str, int]]:
        """Book realised P&L that arrived after a return's outcome was fixed.

        A marked outcome estimated open lots at the mid; the lots closed later,
        and the wallet booked the real result. Each fixed account's realised
        total, less what was booked late before, is the owner's to bear or keep.
        Returns the successor table and the signed amount per handle.
        """
        updates, late = {}, {}
        for account in self.returns:
            if account.payoff is None:
                continue
            realized = account.realized_micro.numerator // account.realized_micro.denominator
            delta = realized - account.late_micro
            if delta:
                late[account.handle] = delta
                updates[account.handle] = replace(account, late_micro=realized)
        table = self._accounts(updates)
        if not self.released_late:
            return table, late
        # What a released account's orders or lots realised after its release: its
        # owner's money, booked late like any other and never graded (wave 17b).
        rows = []
        for handle, total, booked in self.released_late:
            realized = total.numerator // total.denominator
            if realized != booked:
                late[handle] = late.get(handle, 0) + realized - booked
            if any(lot.handle == handle for lot in self.lots):
                rows.append((handle, total, realized))
        return replace(table, released_late=tuple(rows)), late

    def account(self, handle: str) -> ReturnAccount:
        """Return the original account or fail for an unknown return."""
        # The table is immutable, so its handle index is built once, on first read,
        # and never goes stale: every change is a new table with no index yet. The
        # first account with a handle wins, exactly as the linear scan found it. It
        # is not a field, so equality, ``fields()`` and checkpoints never see it.
        index = self.__dict__.get("_accounts_by_handle")
        if index is None:
            index = {}
            for account in self.returns:
                index.setdefault(account.handle, account)
            object.__setattr__(self, "_accounts_by_handle", index)
        try:
            return index[handle]
        except KeyError:
            raise KeyError(handle) from None

    def order(self, order_id: str, handle: str, size: str) -> "LotTable":
        """Bind an accepted order to its calling return; ownership cannot be replaced."""
        _require_id(order_id)
        _require_id(handle)
        quantity = exact(size)
        if quantity <= 0:
            raise ValueError("order size must be positive")
        if any(o.order_id == order_id for o in self.orders) or any(
                row[0] == order_id for row in self.released_orders):
            raise ValueError("order already attributed")
        if not any(r.handle == handle for r in self.returns):
            raise ValueError("order requires an open consequence account")
        return replace(self, orders=(*self.orders, LotOrder(order_id, handle, quantity, quantity)))

    def cancel(self, order_id: str) -> "LotTable":
        """Clear unfilled liability without deleting the order's historical ownership."""
        return replace(
            self,
            orders=tuple(
                replace(o, remaining=Fraction(0)) if o.order_id == order_id else o
                for o in self.orders
            ),
        )

    def confirm(self, order_id: str, filled: str) -> "LotTable":
        """Record that the venue's own order status says the order is terminal.

        ``filled`` is the quantity that status reports filled. Guarantees the first
        confirmation stays, an unknown order changes nothing, and nothing else moves:
        a confirmation is the venue's word that the order can fill no more, and only
        a confirmed order lets its account be released (``closed``).
        """
        quantity = exact(filled)
        if quantity < 0:
            raise ValueError("a filled quantity is nonnegative")
        return replace(self, orders=tuple(
            replace(o, confirmed=quantity)
            if o.order_id == order_id and o.confirmed is None else o
            for o in self.orders))

    def fill(
        self,
        *,
        order_id: str,
        coin: str,
        is_buy: bool,
        size: str,
        px: str,
        fee_usd: str,
        liquidation: bool = False,
        market: str = "perp",
        order_size: str | None = None,
    ) -> "LotTable":
        """Close opposite lots FIFO, crediting each closed lot's realised P&L once.

        The P&L of a closed quantity is conserved: an opener and a distinct
        closer split it by the notional each contributed, the entry price and
        the exit price on that quantity, so the two credits sum to the lot's
        P&L and never both hold it in full. The opener's part is net of its
        opening fee and accrued funding; the closer's is net of its closing
        fee. A handle closing its own lot takes the whole P&L once. A reversal
        opens only its residual quantity for the caller. Liquidation never
        opens a new position and credits no closer: the liquidated opener
        keeps the whole P&L and pays the fee. Venue average-entry realized P&L
        is not an allocation key: FIFO P&L is computed from actual
        opening/closing prices. A fill whose order belongs to no open account
        is refused rather than pooled, and so is one that would execute more
        against its order than the order's original quantity.
        """
        _require_id(order_id)
        if "/" in coin:
            market = "spot"
        if market not in ("perp", "spot", "event"):
            raise ValueError("unknown market")
        if market in ("spot", "event") and liquidation:
            raise ValueError(f"{market} lots cannot be liquidated")
        _require_id(coin)
        if type(is_buy) is not bool or type(liquidation) is not bool:
            raise ValueError("fill side and liquidation must be booleans")
        quantity, price, fee = exact(size), exact(px), exact(fee_usd) * 1_000_000
        if quantity <= 0 or price <= 0:
            raise ValueError("fill size and price must be positive")
        order = next((o for o in self.orders if o.order_id == order_id), None)
        owner = order.handle if order else None
        accounts = {r.handle: r for r in self.returns}
        released_owner = None
        if order is None:
            released_owner = next((row[1] for row in self.released_orders
                                   if row[0] == order_id), None)
            if not liquidation and released_owner is not None:
                # The venue confirmed this order terminal and its account was released,
                # yet it filled: a venue error, and still real money. The fill moves
                # the lots as the venue's position did, and what it realises is its
                # owner's, booked late (``late_realizations``) and never graded.
                owner = released_owner
        # Accounts held only for this fill: a released owner's, and the owners of lots
        # its released orders opened. Their credits go to ``released_late``.
        transient = {row[1] for row in self.released_orders} | {
            handle for handle, _total, _booked in self.released_late}
        transient = {h for h in transient if h not in accounts
                     and (h == owner or any(lot.handle == h for lot in self.lots))}
        for handle in transient:
            accounts[handle] = ReturnAccount(handle, 0)
        if not liquidation and owner not in accounts:
            raise ValueError("fill without an open consequence account")
        if market in ("spot", "event") and not is_buy and quantity > sum(
            (lot.size for lot in self.lots if lot.coin == coin and lot.market == market),
            Fraction(0),
        ):
            raise ValueError("spot sell exceeds long inventory")
        executed = quantity if order_size is None else exact(order_size)
        if executed <= 0:
            raise ValueError("executed order size must be positive")
        if (not liquidation and order is not None and order.ordered is not None
                and order.executed + executed > order.ordered):
            # More has executed against the order than it ever ordered: the fill is
            # an inconsistency, not a consequence, and is refused before any lot moves.
            raise ValueError("fill exceeds the order's ordered quantity")
        remainder = quantity
        lots = []
        closer_net = Fraction(0)
        closes = 0
        for lot in self.lots:
            if remainder <= 0 or lot.coin != coin or lot.market != market or lot.is_buy == is_buy:
                lots.append(lot)
                continue
            closed = min(remainder, lot.size)
            share = closed / lot.size
            pnl = (price - lot.px) * closed * (1 if lot.is_buy else -1) * 1_000_000
            closing_fee = fee * closed / quantity
            # One P&L, credited once. A distinct closer takes the part its exit
            # notional contributed; the opener keeps the part its entry notional
            # did. A self-close, or a liquidation (which has no closer, so its
            # fee is the liquidated opener's own cost), leaves it all with the opener.
            closer_pnl = (pnl * price / (lot.px + price)
                          if owner != lot.handle and not liquidation else Fraction(0))
            net = pnl - closer_pnl - lot.charges_micro * share - (closing_fee if liquidation
                                                                  else 0)
            closer_net += closer_pnl - closing_fee
            closes += 1
            if lot.handle in accounts:
                account = accounts[lot.handle]
                accounts[lot.handle] = replace(
                    account,
                    realized_micro=account.realized_micro + net,
                    closed_lots=account.closed_lots + int(closed == lot.size),
                    liquidated=account.liquidated or liquidation,
                )
            if closed < lot.size:
                lots.append(
                    replace(
                        lot, size=lot.size - closed, charges_micro=lot.charges_micro * (1 - share)
                    )
                )
            remainder -= closed
        if closes and owner in accounts:
            accounts[owner] = replace(
                accounts[owner],
                realized_micro=accounts[owner].realized_micro + closer_net,
                closes=accounts[owner].closes + closes,
            )
        if remainder and not liquidation:
            lots.append(Lot(owner, coin, is_buy, remainder, price,
                            fee * remainder / quantity, market))
            if owner in accounts:
                accounts[owner] = replace(
                    accounts[owner], opened_lots=accounts[owner].opened_lots + 1
                )
        orders = tuple(
            replace(o, remaining=max(Fraction(0), o.remaining - executed),
                    executed=o.executed + executed)
            if o.order_id == order_id
            else o
            for o in self.orders
        )
        table = replace(self._accounts(accounts), lots=tuple(lots), orders=orders)
        return table._credit_released({h: accounts[h].realized_micro for h in transient})

    def _credit_released(self, credits: Mapping[str, Fraction]) -> "LotTable":
        """Add released handles' realised credits to ``released_late``, exactly."""
        credits = {h: c for h, c in credits.items() if c}
        if not credits:
            return self
        rows = {handle: [total, booked] for handle, total, booked in self.released_late}
        for handle, credit in credits.items():
            rows.setdefault(handle, [Fraction(0), 0])[0] += credit
        return replace(self, released_late=tuple(
            (handle, total, booked) for handle, (total, booked) in rows.items()))

    def funding(self, coin: str, paid_usd: str) -> "LotTable":
        """Allocate a signed observed funding payment by open quantity, without rounding."""
        paid = exact(paid_usd) * 1_000_000
        total = sum((lot.size for lot in self.lots if lot.coin == coin), Fraction(0))
        if not total or not paid:
            return self
        return replace(
            self,
            lots=tuple(
                replace(lot, charges_micro=lot.charges_micro + paid * lot.size / total)
                if lot.coin == coin
                else lot
                for lot in self.lots
            ),
        )

    def redeem(self, coin: str, payout: str) -> tuple["LotTable", dict[str, Fraction]]:
        """Close every event lot of ``coin`` at the price its market resolved to.

        Guarantees each lot's owner is credited once with exactly what the
        resolution paid for it, ``(payout - entry) * size`` net of the lot's
        opening fee, and that no closer is credited: the market's resolution
        closes the position, not another decision. ``payout`` is the price one
        outcome token redeemed at, 0 to 1 inclusive (0.5 each on a 50-50
        resolution). Only ``event`` lots move; a coin with none is unchanged.
        Returns the successor table and the signed micro-USD credited per
        handle, exact, for the caller's receipts.
        """
        _require_id(coin)
        price = exact(payout)
        if not 0 <= price <= 1:
            raise ValueError("an event market pays between 0 and 1 per token")
        accounts = {r.handle: r for r in self.returns}
        lots, credited, late = [], {}, {}
        for lot in self.lots:
            if lot.coin != coin or lot.market != "event":
                lots.append(lot)
                continue
            net = (price - lot.px) * lot.size * 1_000_000 - lot.charges_micro
            if lot.handle in accounts:
                account = accounts[lot.handle]
                accounts[lot.handle] = replace(
                    account, realized_micro=account.realized_micro + net,
                    closed_lots=account.closed_lots + 1)
                credited[lot.handle] = credited.get(lot.handle, Fraction(0)) + net
            elif lot.handle is not None:
                # A lot a released account's order opened after its release (wave 17b).
                late[lot.handle] = late.get(lot.handle, Fraction(0)) + net
        if len(lots) == len(self.lots):
            return self, {}
        table = replace(self._accounts(accounts), lots=tuple(lots))
        return table._credit_released(late), credited

    def resolve(self, event: int, backstop: int, mids: Mapping[str, str], *,
                censored: Mapping[str, str] | None = None,
                tick: int | None = None, now_ns: int | None = None,
                horizon_ns: int | None = None,
                exit_rates: Mapping[str, str | None] | None = None,
                horizon_marks: Mapping[str, Mapping[str, str]] | None = None,
                after_horizon: Mapping[str, Fraction] | None = None,
                patience_ns: int | None = None
                ) -> "LotTable":
        """Fix ready outcomes once; marks require a valid mid for every remaining coin.

        The backstop counts from the return's opening, including any time awaiting
        a fill: on the venue's clock when the caller passes ``now_ns`` and
        ``horizon_ns`` and the account recorded the nanosecond it opened at (wave 16,
        D2), else in world ticks when the caller passes ``tick`` and the account
        recorded the tick it opened at, else in the caller's events. Accepted
        unfilled orders defer early settlement. A return pays off when the realised
        result credited to it, as opener or closer, exceeds its own cost; a no-fill
        return cannot inherit anyone's P&L.

        ``exit_rates`` marks every open lot to its liquidation value, the mid less
        ``mid * size * rate`` (wave 16, D7): either a mapping (market -> the venue's
        taker rate, a decimal fraction of notional; a market it does not list carries
        no exit fee) or a callable ``(market, at_ns) -> rate``, asked for the rate at
        the return's horizon (its opening plus ``horizon_ns``; ruling R10-i). The mid
        is fixed when the horizon has passed whatever the rate read: a lot whose rate
        is unknown (None) fixes the outcome censored, ``FEE_UNKNOWN``, never pending.
        The deduction is an estimate at the mark, never booked as money.

        ``horizon_marks`` (handle -> instrument -> mid): on the venue's clock a
        return's open lots are marked at the first venue mid of each instrument
        timestamped at or after its horizon (wave 16, D2), and it waits until every
        instrument it holds has one; the latest cached ``mids`` never stand in for it.
        With ``patience_ns``, it waits no longer than its opening plus that patience
        on the venue's clock (a named trade's own rule): past it, an instrument still
        without its mark fixes the outcome censored, ``NO_MARK``, uninformative, with
        the lots that were marked still valued.

        ``after_horizon`` (handle -> micro-USD): funding the venue charged a return's
        lots for funding times after its horizon. It is added back, so an outcome
        accrues funding only for funding times at or before its horizon, however late
        the mark that fixes it arrives (wave 16, ruling R10-m). The money itself is
        booked as charged.

        ``censored`` names returns that also sent an order nobody could observe
        (handle -> documented reason). Such a return resolves on its own schedule
        like any other, and its outcome carries the money its observed orders
        produced; only the answer to whether it paid off is unknown, because the
        unobserved order could have changed it, so the outcome is censored.
        """
        _require_event_index(event, "event")
        _require_event_index(backstop, "backstop", positive=True)
        updates = {}
        for account in self.returns:
            if account.voided or account.cost_micro is None or account.payoff is not None:
                continue
            lots = [lot for lot in self.lots if lot.handle == account.handle]
            waiting = any(o.handle == account.handle and o.remaining for o in self.orders)
            if (now_ns is not None and horizon_ns is not None
                    and account.opened_at_ns is not None):
                young = now_ns - account.opened_at_ns < horizon_ns
            else:
                age = (tick - account.opened_at_tick
                       if tick is not None and account.opened_at_tick is not None
                       else event - account.opened_at_event)
                young = age < backstop
            if (lots or waiting) and young:
                continue
            net = account.realized_micro + (after_horizon or {}).get(account.handle, 0)
            exit_fee = Fraction(0)
            unknown = False
            unmarked = False
            if lots:
                marked = mids
                if (horizon_marks is not None and horizon_ns is not None
                        and account.opened_at_ns is not None):
                    marked = horizon_marks.get(account.handle, {})
                missing = any(lot.coin not in marked for lot in lots)
                if missing and not (patience_ns is not None and now_ns is not None
                                    and account.opened_at_ns is not None
                                    and now_ns > account.opened_at_ns + patience_ns):
                    continue
                unmarked = missing
                valued = [lot for lot in lots if lot.coin in marked]
                rates = _exit_rates_for(exit_rates, valued, account, now_ns, horizon_ns)
                if any(rate is None for rate in rates.values()):
                    unknown = True
                for lot in valued:
                    mid = exact(marked[lot.coin])
                    if mid <= 0:
                        raise ValueError("mark must be positive")
                    net += (mid - lot.px) * lot.size * (
                        1 if lot.is_buy else -1
                    ) * 1_000_000 - lot.charges_micro
                    if rates.get(lot.coin) is not None:
                        exit_fee += mid * lot.size * exact(rates[lot.coin]) * 1_000_000
                net -= exit_fee
            micro = net.numerator // net.denominator
            # Everything the return cost: its own compute and tools.
            cost = account.cost_micro
            acted = account.opened_lots > 0 or account.closes > 0 or account.earnings > 0
            reason = ((censored or {}).get(account.handle)
                      or (NO_MARK if unmarked else FEE_UNKNOWN if unknown else None))
            outcome = Payoff(
                account.handle,
                0 if reason else int(acted and micro + account.earned_micro > cost),
                micro,
                cost,
                event,
                bool(lots),
                account.liquidated,
                account.earned_micro,
                exit_fee_micro=-((-exit_fee.numerator) // exit_fee.denominator),
                censored=reason,
            )
            # An unmarked outcome is settled money, booked to the owner when it is
            # fixed: the late baseline starts there. A marked outcome books nothing
            # at the mark, so everything its account realises, before or after the
            # mark, is booked late once it is real.
            updates[account.handle] = replace(account, payoff=outcome,
                                              late_micro=0 if lots else micro)
        return self._accounts(updates)

    def closed(self, handle: str) -> bool:
        """Whether ``handle``'s account can never change again: nothing is owed to it.

        Guarantees True only for an account whose outcome is fixed (or that was
        voided), whose realised money is all booked to its owner
        (``late_realizations`` owes it nothing), that owns no open lot (a marked
        outcome's lots still realise late money for it), and every order of which
        the venue's own order status confirmed terminal (``confirm``) with no more
        filled than has been accounted: a cancel acknowledgement, a wall clock or a
        reward-chain horizon is never the venue's word that an order can fill no
        more. A released or unknown handle is False.
        """
        try:
            account = self.account(handle)
        except KeyError:
            return False
        if account.payoff is None and not account.voided:
            return False
        realized = account.realized_micro.numerator // account.realized_micro.denominator
        if account.payoff is not None and realized != account.late_micro:
            return False  # realised money not yet booked to its owner (``late_realizations``)
        if any(lot.handle == handle for lot in self.lots):
            return False
        return all(order.remaining == 0 and order.confirmed is not None
                   and order.executed >= order.confirmed
                   for order in self.orders if order.handle == handle)

    def release(self, handles, mark: int, *,
                authors: Mapping[str, str | None] | None = None) -> "LotTable":
        """Release closed accounts into counts; their orders keep only their owner.

        Essay II.IV.c: a consequence is "consumed ... and then discarded"; what
        persists is aggregates. Guarantees every handle is ``closed`` (otherwise
        ``ValueError`` and the table is unchanged), that ``released_counts`` plus
        the retained accounts give exactly the counts the unreleased table gave,
        and that each released account's orders survive as (order id, owner,
        ``mark``, the owner's author in ``authors``), so a fill the venue still
        reports on one is booked to its owner (``RELEASED_ORDER``) until
        ``forget_released_orders`` passes ``mark``. Nothing else changes.
        """
        handles = list(dict.fromkeys(handles))
        if not handles:
            return self
        for handle in handles:
            if not self.closed(handle):
                raise ValueError(f"account {handle} is open or unknown and cannot be released")
        authors = authors or {}
        gone = set(handles)
        counts = dict(self.released_counts())
        for account in self.returns:
            if account.handle not in gone:
                continue
            payoff = account.payoff
            observed = payoff is not None and payoff.censored is None
            counts["accounts"] += 1
            counts["paid_off"] += int(observed and payoff.y == 1)
            counts["not_paid_off"] += int(observed and payoff.y != 1)
            counts["marked"] += int(observed and payoff.marked)
            counts["censored_outcomes"] += int(payoff is not None and payoff.censored is not None)
            counts["voided"] += int(account.voided)
            counts["lots_opened"] += account.opened_lots
            counts["lots_closed"] += account.closed_lots
            counts["closes_credited"] += account.closes
        return replace(
            self,
            returns=tuple(r for r in self.returns if r.handle not in gone),
            orders=tuple(o for o in self.orders if o.handle not in gone),
            released=tuple(counts[name] for name in RELEASED_COUNTS),
            released_orders=(*self.released_orders,
                             *((o.order_id, o.handle, mark, authors.get(o.handle))
                               for o in self.orders if o.handle in gone)),
        )

    def realized_by_handle(self) -> dict[str, Fraction]:
        """What each handle has realised so far, exactly: a retained account's total,
        and a released handle's since its release (``released_late``). A caller that
        attributes realised money by venue diffs this around one operation, so money a
        released decision realises keeps its venue as a retained one's does."""
        realized = {r.handle: r.realized_micro for r in self.returns}
        for handle, total, _booked in self.released_late:
            realized[handle] = realized.get(handle, Fraction(0)) + total
        return realized

    def released_counts(self) -> dict[str, int]:
        """The counts of every released account, by ``RELEASED_COUNTS`` name."""
        values = self.released or (0,) * len(RELEASED_COUNTS)
        return dict(zip(RELEASED_COUNTS, values, strict=True))

    def order_owner(self, order_id: str) -> str | None:
        """The handle that owns ``order_id``: a retained order's, or a released one's."""
        order = next((o for o in self.orders if o.order_id == order_id), None)
        if order is not None:
            return order.handle
        return next((row[1] for row in self.released_orders if row[0] == order_id), None)

    def released_author(self, handle: str) -> str | None:
        """The seat that authored a released handle, as its release named it."""
        return next((row[3] for row in self.released_orders if row[1] == handle), None)

    def forget_released_orders(self, before: int) -> "LotTable":
        """Forget released orders marked before ``before``, except the orders of a handle
        still owed late money or holding a lot; a later fill naming a forgotten order
        is an order no account owns, refused as such and never pooled."""
        owed = {handle for handle, _total, _booked in self.released_late} | {
            lot.handle for lot in self.lots}
        kept = tuple(row for row in self.released_orders
                     if row[2] >= before or row[1] in owed)
        if len(kept) == len(self.released_orders):
            return self
        return replace(self, released_orders=kept)

    def _accounts(self, updates: dict[str, ReturnAccount]) -> "LotTable":
        if not updates:
            # Nothing changes: the table is immutable, so it is its own successor
            # (and keeps the handle index it has already built).
            return self
        return replace(self, returns=tuple(updates.get(r.handle, r) for r in self.returns))
