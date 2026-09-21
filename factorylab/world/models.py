"""Model providers and price tables.

A provider turns a ``ModelRequest`` into a ``ModelResponse`` carrying the token
usage the vendor reported. Pricing lives in a ``PriceTable`` keyed by the
registered model id, in exact micro-USD per token; total costs round up to
integer micro-USD. Providers never touch the wallet; ``metering`` does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from math import ceil
from typing import Any, Protocol

MICRO = Decimal(1_000_000)


@dataclass(frozen=True)
class TokenPrice:
    """Per-token prices in micro-USD. ``from_per_mtok`` converts $/MTok exactly."""

    input_micro: int | Fraction
    output_micro: int | Fraction
    per_request_micro: int = 0  # e.g. a web-search plugin charged per request

    @classmethod
    def from_per_token(cls, prompt_usd_per_token: str, completion_usd_per_token: str) -> TokenPrice:
        """Return exact fractional micro-USD prices from decimal USD-per-token quotes."""
        return cls(
            Fraction(Decimal(prompt_usd_per_token)) * 1_000_000,
            Fraction(Decimal(completion_usd_per_token)) * 1_000_000,
        )

    @classmethod
    def from_per_mtok(cls, input_usd: str | Decimal, output_usd: str | Decimal) -> TokenPrice:
        """Return the exact per-token micro-USD price for a $/MTok quote.

        $5.00 per million tokens is 5 micro-USD per token. Quotes that do not
        divide evenly are rejected rather than rounded.
        """
        i = Decimal(str(input_usd))
        o = Decimal(str(output_usd))
        if i != i.to_integral_value() or o != o.to_integral_value():
            raise ValueError("per-MTok prices must be whole dollars to map to integer micro-USD")
        return cls(int(i), int(o))

    def cost(self, input_tokens: int, output_tokens: int) -> int:
        """Return the exact usage total rounded up once to whole micro-USD."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        total = (
            input_tokens * self.input_micro
            + output_tokens * self.output_micro
            + self.per_request_micro
        )
        return int(ceil(total))


@dataclass(frozen=True)
class CatalogueEntry:
    """A catalogue model retains its original decimal price quotes."""

    id: str
    name: str
    prompt_usd_per_token: str
    completion_usd_per_token: str
    context_length: int | None
    # The provider's advertised physical completion window.  This is distinct
    # from context length: callers must not invent an output limit from the
    # combined input/output window when the provider leaves it unreported.
    max_completion_tokens: int | None = None

    def price(self) -> TokenPrice:
        """Return exact per-token micro-USD prices without rounding the quotes."""
        return TokenPrice.from_per_token(self.prompt_usd_per_token, self.completion_usd_per_token)


@dataclass
class PriceTable:
    """Registered model ids and their per-token prices.

    Guarantees a lookup for an unregistered id raises, so no call can run
    unpriced.
    """

    prices: dict[str, TokenPrice] = field(default_factory=dict)

    def register(self, model_id: str, price: TokenPrice) -> None:
        self.prices[model_id] = price

    def price(self, model_id: str) -> TokenPrice:
        try:
            return self.prices[model_id]
        except KeyError as exc:
            raise KeyError(f"model {model_id!r} has no registered price") from exc

    def cost(self, model_id: str, input_tokens: int, output_tokens: int) -> int:
        return self.price(model_id).cost(input_tokens, output_tokens)


def anthropic_first_party_prices() -> PriceTable:
    """First-party Anthropic prices as of the cached table (2026-06-24).

    Opus 5: $5 / $25 per MTok. Sonnet 5: $2 / $10. Haiku 4.5: $1 / $5.
    """
    t = PriceTable()
    t.register("claude-opus-5", TokenPrice.from_per_mtok("5", "25"))
    t.register("claude-sonnet-5", TokenPrice.from_per_mtok("2", "10"))
    t.register("claude-haiku-4-5", TokenPrice.from_per_mtok("1", "5"))
    return t


@dataclass(frozen=True)
class ModelRequest:
    model_id: str
    system: str
    messages: tuple[dict[str, Any], ...]
    max_tokens: int = 4096
    effort: str = "medium"
    json_object: bool = False


@dataclass(frozen=True)
class ModelResponse:
    model_id: str  # the model that actually served
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    refused: bool = False
    raw: dict[str, Any] = field(default_factory=dict)
    cost_micro: int | None = None


class ModelProvider(Protocol):
    """Completes a request and reports the vendor's own usage numbers."""

    name: str

    def complete(self, req: ModelRequest) -> ModelResponse: ...


@dataclass
class FakeModel:
    """Scripted provider for tests and the ``scripted`` world.

    ``script`` maps a substring of the last user message to a reply; unmatched
    requests get ``default``. Token usage is declared, not measured, so tests
    can assert exact costs. Guarantees the same request always yields the same
    response and usage.
    """

    name: str = "fake-model"
    script: dict[str, str] = field(default_factory=dict)
    default: str = "NOOP"
    input_tokens_per_char: Decimal = Decimal("0.25")
    output_tokens_per_char: Decimal = Decimal("0.25")
    fixed_input_tokens: int | None = None
    fixed_output_tokens: int | None = None

    def complete(self, req: ModelRequest) -> ModelResponse:
        last = ""
        for m in reversed(req.messages):
            if m.get("role") == "user":
                c = m.get("content", "")
                last = c if isinstance(c, str) else str(c)
                break
        reply = self.default
        for key, val in self.script.items():
            if key in last:
                reply = val
                break
        prompt_chars = len(req.system) + sum(len(str(m.get("content", ""))) for m in req.messages)
        itok = (
            self.fixed_input_tokens
            if self.fixed_input_tokens is not None
            else int(Decimal(prompt_chars) * self.input_tokens_per_char) + 1
        )
        otok = (
            self.fixed_output_tokens
            if self.fixed_output_tokens is not None
            else int(Decimal(len(reply)) * self.output_tokens_per_char) + 1
        )
        return ModelResponse(req.model_id, reply, itok, otok, "end_turn")


class AnthropicProvider:
    """First-party Anthropic models through the official SDK.

    Adaptive thinking is on; effort comes from the request. Refusal fallbacks
    are enabled by default through the server-side ``fallbacks: "default"``
    beta so a policy decline reroutes inside the same call; the response
    reports the model that actually served, which is what gets priced. Set
    ``fallbacks=False`` to disable.
    """

    name = "anthropic"

    def __init__(self, *, fallbacks: bool = True, client: Any | None = None) -> None:
        import anthropic

        self._client = client or anthropic.Anthropic()
        self._fallbacks = fallbacks

    def complete(self, req: ModelRequest) -> ModelResponse:
        kwargs: dict[str, Any] = dict(
            model=req.model_id,
            max_tokens=req.max_tokens,
            system=req.system,
            messages=list(req.messages),
            thinking={"type": "adaptive"},
            output_config={"effort": req.effort},
        )
        if self._fallbacks:
            resp = self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return ModelResponse(
            model_id=resp.model,
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            stop_reason=resp.stop_reason or "",
            refused=resp.stop_reason == "refusal",
            raw={"request_id": getattr(resp, "_request_id", None)},
        )
