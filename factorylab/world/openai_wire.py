"""One reader for OpenAI-shaped chat completions, shared by every rail that speaks it.

Providers differ in how they price a call and in the error they raise; they do
not differ in how a completion is laid out. Everything that is common lives
here, so a change to the wire shape is made once, and a fault in one provider's
pricing can never surface as another provider's exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
        )
    except error:
        raise
    except Exception:
        raise error(None, "Invalid completion") from None
