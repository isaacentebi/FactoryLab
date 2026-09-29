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
from copy import deepcopy
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


#: The name the contract travels under in ``response_format.json_schema``.
SCHEMA_NAME = "outcome"


#: The JSON-schema keywords a strict route's decoder is handed (``strict_schema``):
#: the ones strict constrained decoders commonly compile. Hosts refused the full
#: contract with HTTP 400 on ``propertyNames`` (Alibaba's for Qwen, 2026-09-27;
#: llguidance for MiniMax, 2026-09-28), and Meta's on a recursive definition
#: (2026-09-29).
STRICT_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum", "anyOf",
    "$ref", "$defs", "description", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minItems", "maxItems",
})


def strict_schema(schema: Any) -> Any:
    """``schema`` as a strict decoder compiles it: ``STRICT_KEYWORDS`` alone, no recursion.

    Guarantees:

    - the result admits every value ``schema`` admits. A keyword outside
      ``STRICT_KEYWORDS`` is removed, and removing one of JSON Schema's assertions
      never narrows what a schema admits (a removed ``items`` or
      ``additionalProperties`` reads as the default, which admits anything, and
      ``additionalProperties`` is removed with the ``patternProperties`` it
      complements, since alone it would bind the fields the patterns matched). A
      schema inside a ``$defs`` entry that is a reference becomes ``{}``, which
      admits anything, so no definition refers to one: none is recursive. Nothing
      is added, and no other value is changed;
    - no schema position in the result, a ``$defs`` entry included, carries a
      keyword outside ``STRICT_KEYWORDS``, while the names a ``properties``,
      ``$defs`` or ``required`` lists (data, not keywords: a field may be called
      ``pattern``) and the values of an ``enum`` are kept as they are;
    - ``schema`` is never mutated, and the result shares nothing with it.

    A value that is not a JSON object (a boolean schema) is returned as it is.
    """
    # Chapter II §II.b: physics is enforced, not announced. The decoder is handed as
    # much of the contract as it can compile; what it cannot (a field-name pattern,
    # dependent fields, property counts, a child schema's nesting below its first
    # level) the kernel still enforces on every reply, against the full published
    # schema, exactly as before.
    return _strict(schema, in_definition=False)


def _strict(schema: Any, *, in_definition: bool) -> Any:
    if not isinstance(schema, dict):
        return deepcopy(schema)
    if in_definition and "$ref" in schema:
        return {}
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in STRICT_KEYWORDS:
            continue
        if key in ("properties", "$defs"):
            if isinstance(value, dict):
                inner = in_definition or key == "$defs"
                out[key] = {name: _strict(item, in_definition=inner)
                            for name, item in value.items()}
        elif key == "anyOf":
            if isinstance(value, list):
                out[key] = [_strict(item, in_definition=in_definition) for item in value]
        elif key in ("items", "additionalProperties"):
            # Only the single-schema form is kept; the tuple form of ``items`` goes.
            # ``additionalProperties`` binds the fields ``patternProperties`` does not
            # match, so without the patterns it would bind more: it goes with them.
            if isinstance(value, dict | bool) and not (
                    key == "additionalProperties" and "patternProperties" in schema):
                out[key] = _strict(value, in_definition=in_definition)
        else:
            out[key] = deepcopy(value)
    return out


def response_format(req: Any, *, schema_route: bool = False,
                    strict_route: bool = False) -> dict | None:
    """The ``response_format`` a request asks for on the OpenAI wire, or None for none.

    Guarantees that on a route whose manifest ``contract`` is ``json_schema_strict``
    (``strict_route``) a request carrying ``response_schema`` sends
    ``strict_schema`` of it as ``json_schema`` with ``strict: true``; that on a route
    whose contract is ``json_schema`` (``schema_route``) such a request sends that
    schema, unaltered and not shared with the request, as ``json_schema`` with
    ``strict: false``; that any other request asking for JSON, or carrying a
    schema, sends ``json_object``; and that a request asking for neither sends
    nothing. The request itself, and its ``response_schema``, are never changed.
    """
    # Chapter II §II.b: on a route that can carry it, the contract is enforced by the
    # decoder that samples the reply. Which routes can is a load-time fact of the
    # manifest, never a fallback chosen mid-run. On a json_schema route strict stays
    # false because OpenAI's strict mode is a different contract (every property
    # required, every object closed): the schema is sent as the kernel reads it. A
    # json_schema_strict route asks the host to constrain decoding to the part of the
    # contract strict decoders compile (``strict_schema``). Either way the kernel's
    # validation of the reply, against the full contract, remains the authority.
    schema = getattr(req, "response_schema", None)
    if schema is not None and strict_route:
        return {"type": "json_schema", "json_schema": {
            "name": SCHEMA_NAME, "strict": True, "schema": strict_schema(schema)}}
    if schema is not None and schema_route:
        return {"type": "json_schema", "json_schema": {
            "name": SCHEMA_NAME, "strict": False, "schema": deepcopy(schema)}}
    if schema is not None or getattr(req, "json_object", False):
        return {"type": "json_object"}
    return None


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
