"""One reader for OpenAI-shaped chat completions, shared by every rail that speaks it.

Providers differ in how they price a call and in the error they raise; they do
not differ in how a completion is laid out, nor in what a transport fault says
about whether the request was dispatched. Everything that is common lives here,
so a change to the wire shape is made once, and a fault in one provider's
pricing can never surface as another provider's exception.
"""

from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from typing import Any

# Faults that prove the request body never reached the provider: the host never
# resolved, the connection was refused, or the TLS handshake was rejected. Every
# other transport fault (read timeout, reset, truncated body) can follow a POST
# the provider already accepted, generated and billed.
PREDISPATCH = (socket.gaierror, socket.herror, ConnectionRefusedError,
               ssl.SSLCertVerificationError)


#: The reason a provider gives when a completion outlived the deadline its caller
#: stated (``ModelRequest.timeout_s``): the call may have been billed, and no answer
#: arrived within the time it was allowed.
CALL_EXPIRED = "Call deadline expired"


def expired(exc: BaseException) -> bool:
    """True when a transport fault is the socket timing out, not the peer failing."""
    return any(isinstance(cause, TimeoutError) for cause in (exc, getattr(exc, "reason", None)))


def call_timeout(req_timeout: float | None, ceiling: float) -> float:
    """The deadline one completion is given: its caller's, never above the adapter's."""
    return ceiling if req_timeout is None else max(1.0, min(float(req_timeout), ceiling))


def dispatched(exc: BaseException) -> bool:
    """True unless the failure is definitive evidence the request was never sent."""
    # ``URLError`` carries the underlying socket failure as its ``reason``.
    return not any(isinstance(cause, PREDISPATCH) for cause in (exc, getattr(exc, "reason", None)))


@dataclass(frozen=True)
class WireCompletion:
    """One validated completion: its text, its token counts and its identity."""

    model: str | None
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str
    request_id: str | None
    reasoning_tokens: int | None
    message: dict
    usage: dict
    # Input tokens the provider served from its own prompt cache, when it says so
    # (``usage.prompt_tokens_details.cached_tokens``). None means unreported, which
    # is not the same as a miss: a provider that never reports it never says zero.
    cached_tokens: int | None = None


def parse_completion(response: Any, *, error: type[Exception]) -> WireCompletion:
    """Return the completion a response carries, raising ``error`` on anything malformed.

    Token counts are nonnegative integers or the response is refused. List
    content is flattened over its text parts, and absent content reads as the
    empty string. Cost is deliberately not read here: each rail prices its own
    call and raises its own error when a price is unusable.
    """
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(p["text"] for p in content if p.get("type") == "text")
        usage = response["usage"]
        input_tokens, output_tokens = usage["prompt_tokens"], usage["completion_tokens"]
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise ValueError("token counts must be nonnegative integers")
        details = usage.get("completion_tokens_details") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached = prompt_details.get("cached_tokens")
        return WireCompletion(
            model=response.get("model"),
            text=content or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            stop_reason=choice.get("finish_reason") or "",
            request_id=response.get("id"),
            reasoning_tokens=details.get("reasoning_tokens")
            if "reasoning_tokens" in details else None,
            message=message,
            usage=usage,
            cached_tokens=cached if type(cached) is int and cached >= 0 else None,
        )
    except error:
        raise
    except Exception:
        raise error(None, "Invalid completion") from None
