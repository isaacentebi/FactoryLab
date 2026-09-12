"""Debit-before-return for any priced capability.

``Meter.run`` reserves the cost ceiling on the wallet, executes the capability,
commits the actual cost, and only then returns the result. Unknown billing
provisionally consumes the ceiling and remains explicit in the wallet. The wallet is
reached through a narrow protocol so this module does not import the kernel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, TypeVar

from factorylab.world.models import ModelProvider, ModelRequest, ModelResponse, PriceTable

T = TypeVar("T")


class WalletLike(Protocol):
    """Wallet operations that reserve ceilings and book completed work in full."""

    def reserve(self, amount: int, handle: str, reason: str) -> Any: ...
    def commit(self, reservation: Any, actual: int) -> None: ...
    def release(self, reservation: Any) -> None: ...
    def commit_reported(self, reservation: Any, actual: int) -> None: ...
    def commit_uncertain(self, reservation: Any) -> None: ...


class Infeasible(Exception):
    """The wallet could not cover the reservation. The action did not run."""


class UnbilledFailure(RuntimeError):
    """A trusted adapter establishes that no bill or paid execution occurred."""


class BillingUncertain(RuntimeError):
    """A failed completion retains its provisional debit and safe failure classification."""

    def __init__(self, cost: int, cause: Exception):
        self.cost = cost
        super().__init__(f"billing uncertain ({type(cause).__name__})")


@dataclass(frozen=True)
class Metered[T]:
    """A result and its full debited cost, including any reported overrun."""

    result: T
    cost: int
    reserved: int
    handle: str
    overrun: int = 0  # portion beyond the ceiling, already included in the debit
    cost_source: Literal["reported", "table"] = "table"


@dataclass
class Meter:
    """Runs priced work against a wallet with reserve → execute → commit semantics.

    Guarantees: no result is returned before its cost is committed; a failed
    execution releases funds only when known unbilled. A reported vendor
    overrun is debited in full before return, even when it exhausts the wallet.
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
            actual = cost_of(result)
            if type(actual) is not int or actual < 0:
                raise ValueError("cost must be non-negative integer micro-USD")
        except Exception as exc:
            if isinstance(exc, UnbilledFailure):
                self.wallet.release(reservation)
            else:
                self.wallet.commit_uncertain(reservation)
            if on_failure is not None:
                on_failure(exc)
            if isinstance(exc, UnbilledFailure):
                raise
            raise BillingUncertain(ceiling, exc) from None
        if actual > ceiling:
            self.wallet.commit_reported(reservation, actual)
            return Metered(result, actual, ceiling, handle, overrun=actual - ceiling)
        self.wallet.commit(reservation, actual)
        return Metered(result, actual, ceiling, handle)


@dataclass
class MeteredModel:
    """Every completion uses reported cost when available, otherwise registered prices.

    The reservation prices ``max_tokens`` of output plus estimated input.
    Any actual cost beyond that ceiling is debited and remains explicit in ``overrun``.
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
        """Return a committed completion with its cost source and any already-debited overrun."""
        price = self.prices.price(req.model_id)

        def cost_of(response: ModelResponse) -> int:
            if response.cost_micro is not None:
                return response.cost_micro
            if any(type(n) is not int or n < 0
                   for n in (response.input_tokens, response.output_tokens)):
                raise ValueError("invalid vendor token usage")
            serving_price = self.prices.prices.get(response.model_id, price)
            return serving_price.cost(response.input_tokens, response.output_tokens)

        metered = self.meter.run(
            handle=handle,
            reason=f"model:{req.model_id}",
            ceiling=self.ceiling(req),
            execute=lambda: self.provider.complete(req),
            cost_of=cost_of,
        )
        return replace(
            metered, cost_source="reported" if metered.result.cost_micro is not None else "table"
        )
