"""Debit-before-return for any priced capability.

``Meter.run`` reserves the cost ceiling on the wallet, executes the capability,
commits the actual cost, and only then returns the result. If execution fails
the reservation is released and the failure is recorded. The wallet is
reached through a narrow protocol so this module does not import the kernel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypeVar

from factorylab.world.models import ModelProvider, ModelRequest, ModelResponse, PriceTable

T = TypeVar("T")


class WalletLike(Protocol):
    """The three wallet operations metering needs. Satisfied by the kernel Wallet."""

    def reserve(self, amount: int, handle: str, reason: str) -> Any: ...
    def commit(self, reservation: Any, actual: int) -> None: ...
    def release(self, reservation: Any) -> None: ...


class Infeasible(Exception):
    """The wallet could not cover the reservation. The action did not run."""


@dataclass(frozen=True)
class Metered[T]:
    """A result together with what it cost. ``cost`` is the committed amount."""

    result: T
    cost: int
    reserved: int
    handle: str
    overrun: int = 0  # vendor billed beyond the ceiling; owed, not yet debited
    cost_source: Literal["reported", "table"] = "table"


@dataclass
class Meter:
    """Runs priced work against a wallet with reserve → execute → commit semantics.

    Guarantees: no result is returned before its cost is committed; a failed
    execution commits nothing and releases the reservation; ``actual`` can never
    exceed the reservation because the ceiling is enforced by the wallet.
    """

    wallet: WalletLike

    def run(
        self,
        *,
        handle: str,
        reason: str,
        ceiling: int,
        execute: Callable[[], T],
        cost_of: Callable[[T], int],
        on_failure: Callable[[BaseException], None] | None = None,
    ) -> Metered[T]:
        if ceiling < 0:
            raise ValueError("ceiling must be non-negative")
        try:
            reservation = self.wallet.reserve(ceiling, handle, reason)
        except Exception as exc:  # the kernel raises its own Infeasible; normalise
            raise Infeasible(str(exc)) from exc
        try:
            result = execute()
        except BaseException as exc:
            self.wallet.release(reservation)
            if on_failure is not None:
                on_failure(exc)
            raise
        actual = cost_of(result)
        if actual < 0:
            self.wallet.release(reservation)
            raise ValueError("cost must be non-negative")
        if actual > ceiling:
            # The vendor billed more than the ceiling. The wallet must still be
            # charged the truth; commit the ceiling and record the overrun so the
            # runtime settles the remainder as a debt and lowers future ceilings.
            self.wallet.commit(reservation, ceiling)
            return Metered(result, ceiling, ceiling, handle, overrun=actual - ceiling)
        self.wallet.commit(reservation, actual)
        return Metered(result, actual, ceiling, handle)


@dataclass
class MeteredModel:
    """Every completion uses reported cost when available, otherwise registered prices.

    The reservation prices ``max_tokens`` of output plus estimated input.
    Any actual cost beyond that ceiling remains explicit in ``overrun``.
    """

    provider: ModelProvider
    prices: PriceTable
    meter: Meter
    input_slack: float = 1.5

    def ceiling(self, req: ModelRequest) -> int:
        price = self.prices.price(req.model_id)
        prompt_chars = len(req.system) + sum(len(str(m.get("content", ""))) for m in req.messages)
        est_input = int(prompt_chars * self.input_slack) + 64
        return price.cost(est_input, req.max_tokens)

    def complete(self, req: ModelRequest, *, handle: str) -> Metered[ModelResponse]:
        """Return a committed completion with its cost source and any outstanding overrun."""
        price = self.prices.price(req.model_id)
        metered = self.meter.run(
            handle=handle,
            reason=f"model:{req.model_id}",
            ceiling=self.ceiling(req),
            execute=lambda: self.provider.complete(req),
            cost_of=lambda r: (
                r.cost_micro
                if r.cost_micro is not None
                else self.prices.price(r.model_id).cost(r.input_tokens, r.output_tokens)
                if r.model_id in self.prices.prices
                else price.cost(r.input_tokens, r.output_tokens)
            ),
        )
        return replace(
            metered, cost_source="reported" if metered.result.cost_micro is not None else "table"
        )
