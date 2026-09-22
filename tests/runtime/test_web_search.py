"""`web.search`: one priced, bounded, replayable look outside the venue.

No network and no paid model call: the search route is a scripted provider that answers
with a fixed result list, so what is tested is the price, the bound, the refusals and the
recorded-I/O replay, never a live search.
"""

import json
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import pytest

from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.runtime.websearch import (
    REFUSALS,
    SYSTEM_PROMPT,
    parse_results,
)
from factorylab.runtime.worlds import WebSpec, load_manifest, online_id
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.scripted import ScriptedProvider

RESULTS = [
    {"title": "Hyperliquid funding", "url": "https://example.org/funding",
     "snippet": "Funding printed 0.0034% on the hour.", "published": "2026-09-15"},
    {"title": "HYPE open interest", "url": "https://example.org/oi",
     "snippet": "Open interest rose through the session."},
]
#: fake-haiku is $1/$5 per MTok with a $0.007 web plugin, so a 100/200-token search
#: costs 100 + 1000 + 7000 micro-USD before the tool's own flat price.
MODEL_COST = 100 * 1 + 200 * 5 + 7000
CALL_PRICE = 2000


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")

    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


@dataclass
class SearchProvider(ScriptedProvider):
    """The scripted world, plus a fixed answer on whatever route asks for a search."""

    text: str = json.dumps({"results": RESULTS})
    searches: list = field(default_factory=list)

    def complete(self, req: ModelRequest) -> ModelResponse:
        if req.model_id.endswith(":online"):
            self.searches.append(req)
            return ModelResponse(req.model_id, self.text, 100, 200, "end_turn")
        return super().complete(req)


def web_runtime(*, provider=None, max_call_micro=50_000, balance=100_000_000):
    """A scripted world whose manifest names fake-haiku as its search route."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, web=WebSpec("fake-haiku", CALL_PRICE, max_call_micro))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=balance,
                   ledger_path=None, router_gamma=.1, exchange=FakeExchange(),
                   provider=provider if provider is not None else SearchProvider())


def decision(rt, owner="seed-decider"):
    handle = rt.queue.open(
        actor=owner, event_id="web-test", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000)
    rt.handle_to_assembly[handle] = owner
    return handle


def ledger_items(rt, kind=None):
    return [i for i in rt.ledger._recovery_items() if kind is None or i["kind"] == kind]


def search(rt, handle, **args):
    return rt._run_tool("seed-decider", handle, {"tool": "web.search", "args": args})


# --- the priced, bounded call ------------------------------------------------------------


def test_search_returns_the_bounded_list_and_charges_call_price_plus_metered_cost():
    """The seat pays the flat price and the one completion, and reads the list only after."""
    rt = web_runtime()
    handle = decision(rt)
    before = rt.wallet.balance
    result, cost = search(rt, handle, query="hyperliquid funding rate")
    assert result["results"] == RESULTS
    assert cost == CALL_PRICE + MODEL_COST and result["cost_micro"] == cost
    assert result["as_of_ns"] == rt.clock.now_ns
    assert rt.wallet.balance == before - cost
    # One call, on the search route, under the fixed prompt and nothing else.
    request = rt.provider.target.searches[0]
    assert len(rt.provider.target.searches) == 1
    assert request.model_id == online_id("fake-haiku") == "fake-haiku:online"
    assert request.system == SYSTEM_PROMPT and request.json_object is True
    assert "hyperliquid funding rate" in request.messages[0]["content"]
    # Debited before the result is published, and ledgered as a tool call would be.
    assert (ledger_items(rt, "wallet.commit")[-1]["seq"] < ledger_items(rt, "web.call")[-1]["seq"])
    row = ledger_items(rt, "web.call")[-1]
    assert row["ok"] is True and row["cost"] == cost and row["results"] == 2
    assert row["query"] == "hyperliquid funding rate" and row["model_id"] == "fake-haiku:online"
    assert rt.wallet.check_conservation()


def test_a_search_whose_ceiling_exceeds_max_call_usd_is_refused_before_the_call():
    """Over cap is a refusal, not an attempt: nothing is reserved and nothing is bought."""
    rt = web_runtime(max_call_micro=3000)
    handle = decision(rt)
    before = rt.wallet.balance
    result, cost = search(rt, handle, query="anything at all")
    assert result == {"error": REFUSALS["cap"]} and cost == 0
    assert not rt.provider.target.searches and rt.wallet.balance == before
    assert ledger_items(rt, "web.refused")[-1]["reason"] == REFUSALS["cap"]


def test_an_unaffordable_search_cannot_dispatch(monkeypatch):
    rt = web_runtime()
    handle = decision(rt)
    monkeypatch.setattr(rt.wallet, "available_for", lambda *a, **k: 10)
    result, cost = search(rt, handle, query="anything at all")
    assert result == {"error": REFUSALS["unaffordable"]} and cost == 0
    assert not rt.provider.target.searches


def test_a_failing_route_is_charged_what_the_wallet_was_charged(monkeypatch):
    """A provider failure is an error with its real debit, never a silent free retry."""
    rt = web_runtime()
    handle = decision(rt)
    before = rt.wallet.balance

    def boom(req):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(rt.provider.target, "complete", boom)
    result, cost = search(rt, handle, query="hyperliquid funding rate")
    assert result == {"error": REFUSALS["failed"]}
    assert cost > 0 and rt.wallet.balance == before - cost
    assert rt.wallet.check_conservation()


def test_a_provider_failure_proved_unbilled_costs_the_seat_nothing(monkeypatch):
    """An adapter that establishes the request never left releases the whole reservation."""
    from factorylab.world.openrouter import OpenRouterError

    rt = web_runtime()
    handle = decision(rt)
    before = rt.wallet.balance

    def unsent(req):
        raise OpenRouterError(None, "API key environment variable is not set", sent=False)

    monkeypatch.setattr(rt.provider.target, "complete", unsent)
    result, cost = search(rt, handle, query="hyperliquid funding rate")
    assert result == {"error": REFUSALS["failed"]} and cost == 0
    assert rt.wallet.balance == before and rt.wallet.check_conservation()


# --- searched text is data, not something to republish -----------------------------------


SENTINEL = [
    {"title": "SENTINEL-TITLE, the whole headline as that page wrote it",
     "url": "https://example.org/SHORT-URL",
     "snippet": "SENTINEL-BODY, the paragraph the page actually printed."},
    {"title": "short title", "url": "https://example.org/b", "snippet": "brief"},
]


def test_searched_text_is_kept_off_durable_surfaces_like_a_fetched_body():
    """Outside text is data a seat reasons from, never text the population republishes.

    Mirrors the connector's posture and its journal test: a title or snippet long
    enough to be prose is protected; a url, a short title and a four-word snippet are
    repeatable facts and stay readable.
    """
    from factorylab.runtime.compute import MIN_PROTECTED_BODY_CHARS

    rt = web_runtime(provider=SearchProvider(text=json.dumps({"results": SENTINEL})))
    handle = decision(rt)
    result, _ = search(rt, handle, query="what did that page say")
    prose, brief = result["results"]
    assert len(prose["snippet"]) >= MIN_PROTECTED_BODY_CHARS > len(brief["snippet"])
    assert rt.ledger.connector_bodies == [prose["title"], prose["snippet"]]
    # Verbatim and JSON-escaped copies both leave a durable surface.
    redacted = json.dumps(rt.ledger.without_connector_bodies(
        {"outputs": {"text": json.dumps(result)}}))
    assert "SENTINEL-TITLE" not in redacted and "SENTINEL-BODY" not in redacted
    assert "SHORT-URL" in redacted and "short title" in redacted and "brief" in redacted
    # The ledger row for the call names the query and the count, never the text.
    assert "SENTINEL" not in json.dumps(ledger_items(rt), default=str)


# --- replay -----------------------------------------------------------------------------


def recording_journal():
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = True
    return journal, recorded


def test_recovery_replays_a_search_from_its_journalled_completion():
    """A search after the last checkpoint replays from its recorded io.call/io.result pair,
    the way a connector read does: no second search, and the same results the seat read."""
    provider = SearchProvider()
    journal, recorded = recording_journal()
    proxy = JournalProxy(provider, journal, "provider")
    req = ModelRequest(model_id="fake-haiku:online", system=SYSTEM_PROMPT,
                       messages=({"role": "user", "content": "funding"},),
                       max_tokens=1200, json_object=True)
    first = proxy.complete(req)
    assert len(provider.searches) == 1
    assert [row["kind"] for row in recorded] == ["io.call", "io.result"]

    def refuse(_req):
        raise AssertionError("replay must not search again")

    replay, _ = recording_journal()
    replay.tail = [{**row, "seq": index, "ts": 0} for index, row in enumerate(recorded)]
    again = JournalProxy(SimpleNamespace(complete=refuse), replay, "provider").complete(req)
    assert again == first and replay.peek() is None
    assert parse_results(again.text, 5) == parse_results(first.text, 5) == RESULTS
