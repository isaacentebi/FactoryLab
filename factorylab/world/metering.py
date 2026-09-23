"""Debit-before-return for any priced capability.

``Meter.run`` reserves the cost ceiling on the wallet, executes the capability,
commits the actual cost, and only then returns the result. Unknown billing
provisionally consumes the ceiling and remains explicit in the wallet. The wallet is
reached through a narrow protocol so this module does not import the kernel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, TypeVar

from factorylab.world.models import ModelProvider, ModelRequest, ModelResponse, PriceTable
from factorylab.world.openrouter import OpenRouterError as ProviderOpenRouterError
from factorylab.world.venice import VeniceError as ProviderVeniceError

T = TypeVar("T")


class WalletLike(Protocol):
    """Wallet operations that reserve ceilings and book completed work in full."""

    def reserve(self, amount: int, handle: str, reason: str) -> Any: ...
    def commit(self, reservation: Any, actual: int) -> None: ...
    def release(self, reservation: Any) -> None: ...
    def commit_reported(self, reservation: Any, actual: int) -> int: ...
    def commit_uncertain(self, reservation: Any) -> None: ...


class Infeasible(Exception):
    """The wallet could not cover the reservation. The action did not run."""


class UnbilledFailure(RuntimeError):
    """A trusted adapter establishes that no bill or paid execution occurred."""


class OpenRouterError(ProviderOpenRouterError, UnbilledFailure):
    """An unbilled OpenRouter failure retains its provider class name in invocations."""


class VeniceError(ProviderVeniceError, UnbilledFailure):
    """An unbilled Venice failure retains its provider class name in invocations."""


def classify_provider_failure(exc: Exception) -> Exception:
    """Only definitive pre-dispatch evidence turns a provider error into an unbilled one."""
    if isinstance(exc, UnbilledFailure):
        return exc
    if not isinstance(exc, (ProviderOpenRouterError, ProviderVeniceError)):
        return exc
    # The adapter clears ``sent`` only when the request body never reached the provider
    # (unresolved host, refused connection, rejected handshake, missing key). A dropped
    # connection can follow a POST the provider accepted, generated and billed, so it
    # stays billing-uncertain. A 4xx is a rejection the provider made before generating.
    unsent = exc.sent is False
    rejected = type(exc.status) is int and 400 <= exc.status < 500
    if not (unsent or rejected):
        return exc
    cls = OpenRouterError if isinstance(exc, ProviderOpenRouterError) else VeniceError
    return cls(exc.status, "Request failed before generation", sent=exc.sent)


class BillingUncertain(RuntimeError):
    """A failed completion retains its provisional debit and safe failure classification.

    ``cost`` is what the wallet booked for it: the ceiling, or the true cost once a
    settlement from the provider's own balance has released the difference.
    """

    def __init__(self, cost: int, cause: Exception):
        self.cost = cost
        # The provider failure behind it, for a caller that tells an expired call from
        # a failed one (time audit T8). Never rendered: it may name the provider.
        self.cause = cause
        super().__init__(f"billing uncertain ({type(cause).__name__})")


def provider_namespace(model_id: str) -> str:
    """The provider whose balance pays for a model id: ``venice``, ``x402`` or ``openrouter``."""
    for prefix in ("venice", "x402"):
        if model_id.startswith(prefix + ":"):
            return prefix
    return "openrouter"


@dataclass
class BillSettlement:
    """Settles uncertain bills from the provider's own balance; model calls are serial.

    A call that ends with no bill is booked at its ceiling. Its true cost is the
    provider's balance before the call minus the balance after. Reads are lazy:
    only the uncertain path reads the balance after, and the reference before is the
    last read of that provider (at launch, or after the last settled bill) less
    every cost booked through it since, which ``charged`` accumulates. With no
    trustworthy reference the read taken now becomes the reference for the next
    bill and this one keeps its ceiling. A read that fails, a provider without a
    balance, or a difference outside ``[0, ceiling]`` leaves the bill uncertain at
    the ceiling: a cost is measured or it is not, never guessed.

    ``read(model_id)`` returns the provider's balance in micro-USD, ``None`` when
    the provider has no bounded balance to read; it is expected to be journaled by
    the caller so replay reproduces every read. ``record`` receives one ledger
    item per attempt so the outcome is auditable whether or not money moved.
    """

    read: Callable[[str], int | None]
    record: Callable[[dict], Any] | None = None
    reference: dict[str, dict[str, int]] = field(default_factory=dict)

    def _note(self, item: dict) -> None:
        if self.record is not None:
            self.record({"kind": "metering.settlement", **item})

    def refresh(self, model_id: str) -> int | None:
        """Take a fresh reference for the model's provider; a failed read clears it."""
        namespace = provider_namespace(model_id)
        try:
            balance = self.read(model_id)
        except Exception:
            self.reference.pop(namespace, None)
            return None
        if type(balance) is not int:
            self.reference.pop(namespace, None)
            return None
        self.reference[namespace] = {"balance": balance, "spent": 0}
        return balance

    def charged(self, model_id: str, actual: int) -> None:
        """Book a settled cost against the reference so the next settlement is exact."""
        reference = self.reference.get(provider_namespace(model_id))
        if reference is not None and type(actual) is int and actual > 0:
            reference["spent"] += actual

    def settle(self, wallet: Any, reservation: Any, model_id: str) -> int | None:
        """Settle one bill just booked at its ceiling; return the true cost, or None."""
        namespace = provider_namespace(model_id)
        reference = self.reference.get(namespace)
        base = {"reservation_id": reservation.id, "handle": reservation.handle,
                "model_id": model_id, "provisional_micro": reservation.amount}
        try:
            after = self.read(model_id)
        except Exception as exc:
            self.reference.pop(namespace, None)
            self._note({**base, "status": "balance_unavailable", "error": type(exc).__name__})
            return None
        if type(after) is not int:
            self.reference.pop(namespace, None)
            self._note({**base, "status": "no_balance"})
            return None
        self.reference[namespace] = {"balance": after, "spent": 0}
        if reference is None:
            self._note({**base, "status": "reference_taken", "provider_balance_after": after})
            return None
        before = reference["balance"] - reference["spent"]
        actual = before - after
        reads = {"provider_balance_before": reference["balance"],
                 "spent_since_before": reference["spent"], "provider_balance_after": after}
        if actual < 0 or actual > reservation.amount:
            self._note({**base, "status": "outside_ceiling", "measured_micro": actual, **reads})
            return None
        try:
            released = wallet.settle_uncertain(
                reservation.id, actual, balance_before=reference["balance"],
                balance_after=after, spent_since_before=reference["spent"])
        except Exception as exc:
            self._note({**base, "status": "refused", "error": type(exc).__name__, **reads})
            return None
        self._note({**base, "status": "settled", "actual_micro": actual,
                    "released_micro": released, **reads})
        return actual


@dataclass(frozen=True)
class Metered[T]:
    """A result and its full debited cost, including any reported overrun."""

    result: T
    cost: int
    reserved: int
    handle: str
    overrun: int = 0  # portion beyond the ceiling, already included in the debit
    cost_source: str = "table"  # "table", "reported", or a rail's own provenance


@dataclass
class Meter:
    """Runs priced work against a wallet with reserve → execute → commit semantics.

    Guarantees: no result is returned before its cost is committed; a failed
    execution releases funds only when known unbilled. A reported vendor
    overrun is bounded by the wallet's immutable reported-cost policy.
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
        on_uncertain: Callable[[Any], int | None] | None = None,
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
            exc = classify_provider_failure(exc)
            booked = ceiling
            if isinstance(exc, UnbilledFailure):
                self.wallet.release(reservation)
            else:
                self.wallet.commit_uncertain(reservation)
                if on_uncertain is not None:
                    # The bill is booked at its ceiling; a settlement may now measure
                    # its true cost from the provider's balance and release the rest.
                    settled = on_uncertain(reservation)
                    if settled is not None:
                        booked = settled
            if on_failure is not None:
                on_failure(exc)
            if isinstance(exc, UnbilledFailure):
                raise exc from None
            raise BillingUncertain(booked, exc) from None
        if actual > ceiling:
            booked = self.wallet.commit_reported(reservation, actual)
            # Older injected WalletLike implementations return no booked amount.
            booked = actual if booked is None else booked
            return Metered(result, booked, ceiling, handle, overrun=max(0, booked - ceiling))
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
    settlement: BillSettlement | None = None

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

        settlement = self.settlement
        metered = self.meter.run(
            handle=handle,
            reason=f"model:{req.model_id}",
            ceiling=self.ceiling(req),
            execute=lambda: self.provider.complete(req),
            cost_of=cost_of,
            on_uncertain=(
                None if settlement is None
                else lambda reservation: settlement.settle(
                    self.meter.wallet, reservation, req.model_id)
            ),
        )
        if settlement is not None:
            settlement.charged(req.model_id, metered.cost)
        return replace(
            metered, cost_source="reported" if metered.result.cost_micro is not None else "table"
        )
