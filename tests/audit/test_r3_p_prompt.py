"""R3-P: the prompt is small at the front and stable at the front.

Two claims are proved here. The venue's listing is no longer copied into every
prompt: the world block carries the trading markets' instrument records and a
pointer to ``venue.instruments`` for the rest, so its size does not follow the
venue's listing. And everything static within a charter edition and registration
state is rendered first, in one contiguous block that is byte-identical across
consecutive calls to any assembly, so a provider's automatic prefix cache can
hit; the identity stamp, the account, the prices and the event come after it.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import STABLE_WORLD_KEYS, Request
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.openai_wire import parse_completion
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

TRADING = {"perp": ["BTC", "ETH"], "spot": ["BTC/USDC"]}


def padded_runtime(perps: int, pairs: int) -> Runtime:
    """A scripted runtime whose venue lists far more than the world may trade."""
    exchange = FakeExchange(
        coins=("BTC", "ETH"), spot_pairs=("BTC/USDC",),
        listed_coins=tuple(f"C{i:04d}" for i in range(perps)),
        listed_spot_pairs=tuple(f"S{i:04d}/USDC" for i in range(pairs)),
    )
    return Runtime(load_manifest("scripted"), events=0, seed=1,
                   initial_balance_micro=100_000_000, ledger_path=None, drip=False,
                   router_gamma=.1, exchange=exchange, provider=ScriptedProvider())


def rendered(block: dict) -> str:
    return json.dumps(block, sort_keys=True, indent=2)


def request_for(rt: Runtime, description: str, payload: dict,
                handle: str = "fixture") -> Request:
    return rt._request(
        handle, description,
        {"kind": "Tick", "payload": payload, "world": rt._world_block()},
        {"type": "object", "properties": {"action": {"type": "string"}}}, 10**15, "verdict")


def prompt_for(rt: Runtime, assembly_id: str, description: str, payload: dict) -> str:
    request = request_for(rt, description, payload)
    return rt.assemblies[assembly_id].build_model_request(request).messages[-1]["content"]


# --------------------------------------------------------------- the listing leaves


def test_the_listing_leaves_the_prompt_and_the_block_size_stops_following_it():
    """A venue listing of 1,500 instruments costs the world block nothing."""
    small, large = padded_runtime(2, 1), padded_runtime(212, 1321)
    listing = rendered(large.exchange.instruments())
    assert len(listing) > 100_000  # the listing itself is what used to be copied in
    blocks = [rendered(rt._world_block()) for rt in (small, large)]
    assert len(blocks[1]) - len(blocks[0]) < 1_000
    assert len(blocks[1]) < 60_000
    venue = large._world_block()["venue"]
    assert {market: [row["coin"] for row in rows] for market, rows in venue.items()} == TRADING
    assert all(row["lot_size"] and row["min_order_value_usd"] is not None
               for rows in venue.values() for row in rows)


def test_the_world_block_says_where_the_full_listing_is():
    rt = padded_runtime(212, 1321)
    block = rt._world_block()
    assert "venue.instruments" in block["venue_listing"]
    spec = next(s for s in block["tools"] if s["id"] == "venue.instruments")
    assert "list" in spec["description"].lower()
    # Any listed coin is still readable through the venue tools.
    assert "error" not in rt.venue_tools.call("venue.order_book", {"coin": "C0100", "depth": 2})
    assert {r["coin"] for r in rt.venue_tools.call("venue.instruments", {})["perp"]} >= {"C0100"}


# ------------------------------------------------------------------ a stable prefix


def test_two_assemblies_on_two_events_share_one_byte_identical_prefix():
    rt = make_runtime()
    first = prompt_for(rt, "seed-decider", "Respond to event Tick on scripted.", {"index": 1})
    second = prompt_for(rt, "seed-observer", "Respond to event MarketMid on scripted.",
                        {"index": 99, "coin": "ETH"})
    prefix = request_for(rt, "any", {}).stable_prefix()
    assert first.startswith(prefix) and second.startswith(prefix)
    block = rt._world_block()
    assert json.dumps(block["charter"]) in prefix  # the charter text, escaped as JSON
    for card in rt.charter.cards:
        assert card.id in prefix
    assert '"recurrence"' in prefix and '"consequence_mix"' in prefix  # mechanics
    assert '"venue.place_market"' in prefix and '"venue.instruments"' in prefix
    stable = {k: v for k, v in block.items() if k in STABLE_WORLD_KEYS}
    assert len(rendered(stable)) > 0.85 * len(rendered(block))
    assert len(prefix) >= len(rendered(stable))
    assert len(prefix) > 0.5 * len(first)


def test_the_prefix_changes_on_a_charter_edition_and_on_a_registration():
    rt = make_runtime()
    before = request_for(rt, "a", {}).stable_prefix()
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        id="new-public", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="PRIVATE PROMPT", max_tokens=128, effort="low"))
    after_registration = request_for(rt, "a", {}).stable_prefix()
    assert after_registration != before
    rt.charter = replace(rt.charter, edition=rt.charter.edition + 1)
    assert request_for(rt, "a", {}).stable_prefix() != after_registration


def test_the_identity_stamp_and_the_event_are_outside_the_prefix():
    rt = make_runtime()
    request = request_for(rt, "Respond to event Tick on scripted.", {"marker": "EVENT-MARKER"})
    prompt = rt.assemblies["seed-decider"].build_model_request(request).messages[-1]["content"]
    prefix = request.stable_prefix()
    assert '"you": "seed-decider"' in prompt and '"you"' not in prefix
    assert "EVENT-MARKER" in prompt and "EVENT-MARKER" not in prefix
    assert "Respond to event Tick" in prompt[len(prefix):]
    # Controller prices move every closed window, so they are named, not inlined.
    assert '"card_prices"' in prompt[len(prefix):]


def test_the_prefix_holds_still_while_the_account_and_the_prices_move():
    rt = make_runtime()
    before = request_for(rt, "a", {}).stable_prefix()
    rt._derive_regions()
    rt.controller.set_price("well_formed_rate", 0.7, amendment_id="fixture")
    rt.registration_feedback.append({"reason": "MOVED"})
    after = request_for(rt, "a", {}).stable_prefix()
    assert after == before
    prompt = request_for(rt, "a", {}).prompt_text()
    assert "0.7" in prompt[len(after):] and "MOVED" in prompt[len(after):]


# ------------------------------------------------------------- the cache hit lands


def test_the_wire_reads_cached_prompt_tokens_when_the_provider_reports_them():
    def response(usage: dict) -> dict:
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": usage}

    with_cache = parse_completion(response({
        "prompt_tokens": 100, "completion_tokens": 2,
        "prompt_tokens_details": {"cached_tokens": 96}}), error=ValueError)
    without = parse_completion(response({"prompt_tokens": 100, "completion_tokens": 2}),
                               error=ValueError)
    assert with_cache.cached_tokens == 96
    assert without.cached_tokens is None


@pytest.mark.parametrize("cached", [1234, None])
def test_the_invocation_entry_records_the_provider_cached_token_count(cached):
    class Provider(ScriptedProvider):
        def complete(self, req):
            resp = super().complete(req)
            raw = dict(resp.raw)
            if cached is not None:
                raw["cached_tokens"] = cached
            return replace(resp, raw=raw)

    manifest = load_manifest("scripted")
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                 ledger_path=None, drip=False, router_gamma=.1,
                 exchange=FakeExchange(), provider=Provider())
    rt.ledger.active = True
    rt._manage_reserve_window()
    handle = decision(rt)
    rt.consequences.start(handle, rt.n)
    rt._invoke("seed-decider", request_for(rt, "Respond to event Tick on scripted.",
                                           {"index": 1}, handle), "producer")
    usage = ledger_items(rt, "invocation")[-1]["usage"]
    assert usage.get("cached_tokens") == cached or not usage.get("cached_tokens")
    if cached is not None:
        assert usage["cached_tokens"] == cached
    else:
        assert not usage.get("cached_tokens")
