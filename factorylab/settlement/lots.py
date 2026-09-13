"""Pure FIFO accounting; exact fractions prevent fill splitting from creating micro-USD.

Prices and sizes enter as decimal strings, never binary floats. Fractional micro-USD
fees, funding and P&L remain exact until each return's final total is floored once.
A backstop fixes the outcome, not the inventory: subsequent closes still consume
the marked opener's lots. A close credits the realised P&L of the closed quantity
to distinct sides: the opener, net of its opening fee and funding, and the closer,
net of its closing fee. A handle closing its own lot receives the profit once.
Only a decision with an open account can own an order or a lot.
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
    """One immutable outcome binds its net proceeds, full compute cost and mark status."""

    handle: str
    y: int
    net_micro: int
    cost_micro: int
    at_event: int
    marked: bool = False
    liquidated: bool = False


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

    ``cost_micro`` is the return's own metered compute and tools, fixed once. A
    liability the return keeps carrying afterwards — retained public storage
    renewing each window — accrues separately in ``carried_micro`` while the
    outcome is open, and the outcome's threshold is their sum.
    """

    handle: str
    opened_at_event: int
    cost_micro: int | None = None
    carried_micro: int = 0
    realized_micro: Fraction = Fraction(0)
    opened_lots: int = 0
    closed_lots: int = 0
    liquidated: bool = False
    payoff: Payoff | None = None
    closes: int = 0  # lots this return closed, in whole or in part, as the closer


@dataclass(frozen=True)
class LotOrder:
    """Even a fully filled order retains its owner for subsequent venue observations."""

    order_id: str
    handle: str
    remaining: Fraction


@dataclass(frozen=True)
class LotTable:
    """Every operation returns a detached successor; the input table never changes."""

    lots: tuple[Lot, ...] = ()
    returns: tuple[ReturnAccount, ...] = ()
    orders: tuple[LotOrder, ...] = ()

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

    def start(self, handle: str, event: int) -> "LotTable":
        """Admit a unique return before its orders can produce fills."""
        _require_id(handle)
        _require_event_index(event, "event")
        if any(r.handle == handle for r in self.returns):
            raise ValueError("return already admitted")
        return replace(self, returns=(*self.returns, ReturnAccount(handle, event)))

    def finish(self, handle: str, cost_micro: int) -> "LotTable":
        """Fix a return's nonnegative total compute cost exactly once."""
        require_money(cost_micro, nonnegative=True)
        account = self.account(handle)
        if account.cost_micro is not None:
            raise ValueError("return cost already final")
        return self._accounts({handle: replace(account, cost_micro=cost_micro)})

    def carry(self, handle: str, cost_micro: int) -> "LotTable":
        """Add a nonnegative retained liability to a return whose outcome is still open.

        Guarantees: a fixed outcome is never reopened, the charge is money, and
        the return's own final compute cost is left exactly as it was recorded.
        """
        require_money(cost_micro, nonnegative=True)
        account = self.account(handle)
        if account.payoff is not None:
            raise ValueError("return outcome already final")
        return self._accounts({handle: replace(
            account, carried_micro=account.carried_micro + cost_micro)})

    def account(self, handle: str) -> ReturnAccount:
        """Return the original account or fail for an unknown return."""
        for account in self.returns:
            if account.handle == handle:
                return account
        raise KeyError(handle)

    def order(self, order_id: str, handle: str, size: str) -> "LotTable":
        """Bind an accepted order to its calling return; ownership cannot be replaced."""
        _require_id(order_id)
        _require_id(handle)
        quantity = exact(size)
        if quantity <= 0:
            raise ValueError("order size must be positive")
        if any(o.order_id == order_id for o in self.orders):
            raise ValueError("order already attributed")
        if not any(r.handle == handle for r in self.returns):
            raise ValueError("order requires an open consequence account")
        return replace(self, orders=(*self.orders, LotOrder(order_id, handle, quantity)))

    def cancel(self, order_id: str) -> "LotTable":
        """Clear unfilled liability without deleting the order's historical ownership."""
        return replace(
            self,
            orders=tuple(
                replace(o, remaining=Fraction(0)) if o.order_id == order_id else o
                for o in self.orders
            ),
        )

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
        """Close opposite lots FIFO, crediting realised P&L once per distinct handle.

        The opener's credit is net of its opening fee and accrued funding; the
        closer's is net of its closing fee. A reversal opens only its residual
        quantity for the caller. Liquidation never opens a new position and
        credits no closer. Venue average-entry realized P&L is not an allocation
        key: FIFO P&L is computed from actual opening/closing prices. A fill
        whose order belongs to no open account is refused rather than pooled.
        """
        _require_id(order_id)
        if "/" in coin:
            market = "spot"
        if market not in ("perp", "spot"):
            raise ValueError("unknown market")
        if market == "spot" and liquidation:
            raise ValueError("spot lots cannot be liquidated")
        _require_id(coin)
        if type(is_buy) is not bool or type(liquidation) is not bool:
            raise ValueError("fill side and liquidation must be booleans")
        quantity, price, fee = exact(size), exact(px), exact(fee_usd) * 1_000_000
        if quantity <= 0 or price <= 0:
            raise ValueError("fill size and price must be positive")
        order = next((o for o in self.orders if o.order_id == order_id), None)
        owner = order.handle if order else None
        accounts = {r.handle: r for r in self.returns}
        if not liquidation and owner not in accounts:
            raise ValueError("fill without an open consequence account")
        if market == "spot" and not is_buy and quantity > sum(
            (lot.size for lot in self.lots if lot.coin == coin and lot.market == market),
            Fraction(0),
        ):
            raise ValueError("spot sell exceeds long inventory")
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
            # A liquidation has no closer: its fee is the liquidated opener's own cost.
            net = pnl - lot.charges_micro * share - (closing_fee if liquidation else 0)
            closer_net += (pnl if owner != lot.handle else 0) - closing_fee
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
        executed = quantity if order_size is None else exact(order_size)
        if executed <= 0:
            raise ValueError("executed order size must be positive")
        orders = tuple(
            replace(o, remaining=max(Fraction(0), o.remaining - executed))
            if o.order_id == order_id
            else o
            for o in self.orders
        )
        return replace(self._accounts(accounts), lots=tuple(lots), orders=orders)

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

    def resolve(self, event: int, backstop: int, mids: Mapping[str, str]) -> "LotTable":
        """Fix ready outcomes once; marks require a valid mid for every remaining coin.

        The backstop counts runtime events from the return, including any time
        awaiting a fill. Accepted unfilled orders defer early settlement. A return
        pays off when the realised result credited to it, as opener or closer,
        exceeds its own cost, carried liabilities included; a no-fill return
        cannot inherit anyone's P&L.
        """
        _require_event_index(event, "event")
        _require_event_index(backstop, "backstop", positive=True)
        updates = {}
        for account in self.returns:
            if account.cost_micro is None or account.payoff is not None:
                continue
            lots = [lot for lot in self.lots if lot.handle == account.handle]
            waiting = any(o.handle == account.handle and o.remaining for o in self.orders)
            if (lots or waiting) and event < account.opened_at_event + backstop:
                continue
            net = account.realized_micro
            if lots:
                if any(lot.coin not in mids for lot in lots):
                    continue
                for lot in lots:
                    mid = exact(mids[lot.coin])
                    if mid <= 0:
                        raise ValueError("mark must be positive")
                    net += (mid - lot.px) * lot.size * (
                        1 if lot.is_buy else -1
                    ) * 1_000_000 - lot.charges_micro
            micro = net.numerator // net.denominator
            # Everything the return cost: its own compute and tools, plus every
            # liability it was still carrying when the outcome was fixed.
            cost = account.cost_micro + account.carried_micro
            outcome = Payoff(
                account.handle,
                int((account.opened_lots > 0 or account.closes > 0) and micro > cost),
                micro,
                cost,
                event,
                bool(lots),
                account.liquidated,
            )
            updates[account.handle] = replace(account, payoff=outcome)
        return self._accounts(updates)

    def _accounts(self, updates: dict[str, ReturnAccount]) -> "LotTable":
        return replace(self, returns=tuple(updates.get(r.handle, r) for r in self.returns))
