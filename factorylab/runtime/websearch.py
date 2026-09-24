"""One bounded web search, bought at cost from the world's search-capable route.

A seat that needs to know something the venue cannot tell it calls ``web.search``
on purpose and pays for it: the metered cost of exactly one model call on a
route the provider searches with (OpenRouter's ``:online`` web plugin, whose
per-request charge is in that cost, or Venice's ``enable_web_search``). Nothing
is added on top: the provider is the only one paid, and the wallet moves only
when money moves. It is a kernel call, not
a wake — no propensity, no judgement, no return — so its cost lands on the
calling seat's consequence account the way a connector read's does.

The fixed system prompt is the whole instruction the route is given; the seat's
query is the only thing it carries. The answer is parsed, bounded and returned as
a list; anything else is an error with the cost the wallet actually paid.
"""

from __future__ import annotations

import json
from typing import Any

from factorylab.cortex.tools import _validate_args, web_search_spec
from factorylab.world.metering import BillingUncertain, Infeasible
from factorylab.world.models import ModelRequest, ModelResponse, TokenPrice

#: A query is a sentence, not a payload; a page of results is ten at the outside.
MAX_QUERY_CHARS = 400
MIN_QUERY_CHARS = 1
MAX_RESULTS = 10
DEFAULT_RESULTS = 5
#: What one result may say, and what the whole answer may weigh in a seat's prompt.
MAX_SNIPPET_CHARS = 600
MAX_RESULT_BYTES = 16 * 1024
#: The completion budget for the search call, and the input slack MeteredModel uses.
MAX_TOKENS = 1200
INPUT_SLACK = 1.5

#: The only instruction the route is given. It is fixed here, not composed from a
#: seat's text, so a search cannot be turned into an unpriced general model call.
SYSTEM_PROMPT = (
    "You are a web search tool inside an automated system. Search the web for the "
    "query you are given and answer with JSON and nothing else: an object with one key, "
    '"results", whose value is a list of objects with the string keys "title", "url", '
    '"snippet" and, when the page states one, "published" (an ISO-8601 date). Order the '
    "results best first and return no more than the number asked for. Write no prose, no "
    "explanation, no markdown fence and no key other than these."
)

REFUSALS = {
    "cap": "web search ceiling exceeds the world's max_call_usd",
    "unaffordable": "web search call unaffordable",
    "malformed": "web search route did not answer with a result list",
    "failed": "web search route failed",
}


def _clean(value: Any, limit: int) -> str | None:
    """A published field is a bounded string or it is not there."""
    if not isinstance(value, str):
        return None
    text = value.strip()[:limit]
    return text or None


def parse_results(text: str, max_results: int) -> list[dict[str, str]] | None:
    """Return the bounded result list the route answered with, or None if it did not.

    Guarantees the list the seat sees is at most ``max_results`` long, that every
    snippet is at most ``MAX_SNIPPET_CHARS`` characters and that the whole list
    serialises within ``MAX_RESULT_BYTES`` — a route that answers with a megabyte
    of prose cannot put a megabyte into the calling seat's next prompt. Only
    ``title``, ``url``, ``snippet`` and ``published`` survive; a row missing a
    title or a url is dropped rather than published half-formed.
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    rows = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return None
    bounded: list[dict[str, str]] = []
    size = 2  # the brackets of the serialised list
    for row in rows:
        if len(bounded) >= max_results or not isinstance(row, dict):
            continue
        title = _clean(row.get("title"), 200)
        url = _clean(row.get("url"), 400)
        if title is None or url is None:
            continue
        item = {"title": title, "url": url,
                "snippet": _clean(row.get("snippet"), MAX_SNIPPET_CHARS) or ""}
        published = _clean(row.get("published"), 40)
        if published is not None:
            item["published"] = published
        weight = len(json.dumps(item).encode()) + 1
        if size + weight > MAX_RESULT_BYTES:
            break
        size += weight
        bounded.append(item)
    return bounded


def _model_cost(prices, price: TokenPrice, response: ModelResponse) -> int:
    """The vendor's reported cost when it gave one, otherwise the registered price."""
    if response.cost_micro is not None:
        return response.cost_micro
    if any(type(n) is not int or n < 0
           for n in (response.input_tokens, response.output_tokens)):
        raise ValueError("invalid vendor token usage")
    serving = prices.prices.get(response.model_id, price)
    return serving.cost(response.input_tokens, response.output_tokens)


def protect(rt, results: list[dict[str, str]]) -> None:
    """Keep searched text off every durable surface, exactly as a fetched body is.

    The posture is the connector's (``runtime/compute.py``, ``MIN_PROTECTED_BODY_CHARS``):
    what a seat read outside the venue is data it may reason from, never text the
    population re-publishes into its own institutions, so a title or snippet long
    enough to be prose is redacted from the ledger and refuses a final return that
    carries it verbatim. URLs and anything shorter are repeatable facts — a date, a
    ticker, a headline of four words — and protecting them would refuse every later
    return that merely mentions what was searched for.
    """
    from factorylab.runtime.compute import MIN_PROTECTED_BODY_CHARS

    for row in results:
        for name in ("title", "snippet"):
            text = row.get(name) or ""
            if len(text) >= MIN_PROTECTED_BODY_CHARS:
                rt.ledger.protect_connector_body(text)


def _refused(rt, action_id: str, handle: str, reason: str, **fields) -> tuple[dict, int]:
    """Refusals are ledgered before their public reason is returned."""
    rt.ledger.append({"kind": "web.refused", "handle": handle, "assembly_id": action_id,
                      "reason": reason, **fields, "ts": rt.clock.now_ns})
    return {"error": reason}, 0


def run(rt, action_id: str, handle: str, args: dict) -> tuple[dict, int]:
    """Reserve first, search once, return the bounded list only after the debit.

    Guarantees the seat's entitlement covers the whole completion ceiling before
    the route is called; that a search whose ceiling
    exceeds the manifest's ``max_call_usd`` is refused before any call; and that a
    failure is charged what the wallet was actually charged, never the ceiling it
    was refused at.
    """
    from factorylab.runtime.worlds import online_id

    spec = rt.m.web
    error = _validate_args(web_search_spec("0")["args_schema"], args)
    if error is None:
        query = args["query"].strip()
        limit = args.get("max_results", DEFAULT_RESULTS)
        if not MIN_QUERY_CHARS <= len(query) <= MAX_QUERY_CHARS:
            error = f"invalid args: query must be {MIN_QUERY_CHARS}-{MAX_QUERY_CHARS} characters"
        elif not 1 <= limit <= MAX_RESULTS:
            error = f"invalid args: max_results must be 1-{MAX_RESULTS}"
    if error is not None:
        return _refused(rt, action_id, handle, error)

    model_id = online_id(spec.search_model)
    fields = {"query": query[:MAX_QUERY_CHARS], "model_id": model_id}
    req = ModelRequest(
        model_id=model_id, system=SYSTEM_PROMPT,
        messages=({"role": "user", "content": f"{query}\n\nReturn at most {limit} results."},),
        max_tokens=MAX_TOKENS, json_object=True,
    )
    price = rt.prices.price(model_id)
    prompt_chars = len(req.system) + sum(len(str(m["content"])) for m in req.messages)
    ceiling = price.cost(int(prompt_chars * INPUT_SLACK) + 64, MAX_TOKENS)
    if ceiling > spec.max_call_micro:
        return _refused(rt, action_id, handle, REFUSALS["cap"], **fields)
    if ceiling > rt.wallet.available_for(handle, "tool:web.search"):
        return _refused(rt, action_id, handle, REFUSALS["unaffordable"], **fields)

    def execute() -> tuple[ModelResponse, list[dict[str, str]] | None]:
        # The provider is the recorded-I/O layer's own journal proxy, so a resumed
        # diary replays this completion from its io.call/io.result pair instead of
        # searching again: the seat reads the same results it read the first time.
        response = rt.provider.complete(req)
        return response, parse_results(response.text, limit)

    def cost_of(answered: tuple[ModelResponse, list | None]) -> int:
        # What the provider billed, whether or not the route answered with a list.
        response, _results = answered
        return _model_cost(rt.prices, price, response)

    settlement = rt.bill_settlement
    try:
        metered = rt._seat_meter(action_id).run(
            handle=handle, reason="tool:web.search", ceiling=ceiling,
            execute=execute, cost_of=cost_of,
            on_uncertain=(None if settlement is None else lambda reservation:
                          settlement.settle(rt._seat_meter(action_id).wallet,
                                            reservation, model_id)),
        )
    except BillingUncertain as exc:
        # The provider may already have billed it, so the wallet keeps what it booked.
        result, cost, results = {"error": REFUSALS["failed"]}, exc.cost, None
    except Infeasible:
        return _refused(rt, action_id, handle, REFUSALS["unaffordable"], **fields)
    except Exception:
        # A failure the adapter proved unbilled: the reservation was released in full.
        return _refused(rt, action_id, handle, REFUSALS["failed"], **fields)
    else:
        if settlement is not None:
            settlement.charged(model_id, metered.cost)
        _, results = metered.result
        cost = metered.cost
        if results is None:
            result = {"error": REFUSALS["malformed"]}
        else:
            protect(rt, results)
            result = {"results": results, "as_of_ns": rt.clock.now_ns}
    if "error" not in result:
        result["cost_micro"] = cost
    rt.ledger.append({"kind": "web.call", "handle": handle, "assembly_id": action_id,
                      **fields, "results": 0 if results is None else len(results),
                      "cost": cost, "ok": "error" not in result, "ts": rt.clock.now_ns})
    if "error" in result:
        rt.ledger.append({"kind": "web.refused", "handle": handle, "assembly_id": action_id,
                          "reason": result["error"], **fields, "ts": rt.clock.now_ns})
    return result, cost
