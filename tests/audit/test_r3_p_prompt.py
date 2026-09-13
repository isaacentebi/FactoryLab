"""R3-P: the prompt is small at the front and stable at the front.

Two claims are proved here. The venue's listing is no longer copied into every
prompt: the world block carries the trading markets' instrument records and a
pointer to ``venue.instruments`` for the rest, so its size does not follow the
venue's listing. And everything static within a charter edition and registration
state is rendered first, in one contiguous block that is byte-identical across
consecutive calls to any assembly, so a provider's automatic prefix cache can
hit; the identity stamp, the account, the prices and the event come after it.

The prefix claims are proved on the message list a provider actually posts —
``[system, *messages]`` — and not on the user text alone: the assemblies of one
world hold different system prompts, and a block that led only the user message
would sit behind bytes that differ per assembly.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import STABLE_WORLD_KEYS, Request
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.openai_wire import parse_completion
from factorylab.world.openrouter import OpenRouterProvider
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items
from tests.world.test_openrouter import FakeTransport

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


def wire(rt: Runtime, assembly_id: str, description: str, payload: dict) -> list[dict[str, Any]]:
    """The message list a provider posts for this request, taken from the provider.

    ``OpenRouterProvider`` and ``VeniceProvider`` both build
    ``[{"role": "system", ...}, *req.messages]``; this drives the OpenRouter one
    over a fake transport so the sequence asserted on is the one that is sent,
    not a reconstruction of it.
    """
    mreq = rt.assemblies[assembly_id].build_model_request(request_for(rt, description, payload))
    transport = FakeTransport([{
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }])
    OpenRouterProvider(transport=transport).complete(mreq)
    return transport.calls[-1][2]["messages"]


def wire_text(messages: list[dict[str, Any]]) -> str:
    """The wire's bytes in order: what a provider's prefix cache tokenises."""
    return "".join(f"{m['role']}\n{m['content']}\n" for m in messages)


def prompt_for(rt: Runtime, assembly_id: str, description: str, payload: dict) -> str:
    return wire_text(wire(rt, assembly_id, description, payload))


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


def test_two_assemblies_on_two_events_share_one_byte_identical_wire_prefix():
    """Different system prompts, one shared prefix — asserted on the posted messages."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        id="own-prompt", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="A WHOLLY DIFFERENT SYSTEM PROMPT", max_tokens=128, effort="low"))
    decider, other = rt.assemblies["seed-decider"], rt.assemblies["own-prompt"]
    # A registered assembly brings its own system prompt: without one there is nothing
    # here to prove, because identical system text would share a prefix wherever it sat.
    assert decider.spec.system_prompt != other.spec.system_prompt
    first_wire = wire(rt, "seed-decider", "Respond to event Tick on scripted.", {"index": 1})
    second_wire = wire(rt, "own-prompt", "Respond to event MarketMid on scripted.",
                       {"index": 99, "coin": "ETH"})
    first, second = wire_text(first_wire), wire_text(second_wire)
    prefix = request_for(rt, "any", {}).stable_prefix()
    # The system message leads the wire and the block leads the system message, so the
    # two wires agree byte for byte for at least the whole of it.
    assert [m["role"] for m in first_wire][0] == [m["role"] for m in second_wire][0] == "system"
    assert first_wire[0]["content"].startswith(prefix)
    assert second_wire[0]["content"].startswith(prefix)
    shared = os.path.commonprefix([first, second])
    assert prefix in shared and shared.index(prefix) == len("system\n")
    # What differs between the assemblies begins only after the block.
    assert decider.spec.system_prompt not in shared
    assert other.spec.system_prompt not in shared
    assert first.startswith(prefix, len("system\n")) and second.startswith(prefix, len("system\n"))
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
    mreq = rt.assemblies["seed-decider"].build_model_request(request)
    prefix = request.stable_prefix()
    user = mreq.messages[-1]["content"]
    # The block is the system message's head; everything about this call is the user's.
    assert mreq.system == prefix + rt.assemblies["seed-decider"].spec.system_prompt
    assert '"you": "seed-decider"' in user and '"you"' not in prefix
    assert "EVENT-MARKER" in user and "EVENT-MARKER" not in prefix
    assert "Respond to event Tick" in user and prefix not in user
    # Controller prices move every closed window, so they are named, not inlined.
    assert '"card_prices"' in user


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


def diverge(rt: Runtime) -> float:
    """Run the sampling actuator over a diverging window history; return the new mix."""
    rt.sampling_history = [{"window": 0, "verdict": 0.1, "consequence": 0.9},
                           {"window": 1, "verdict": 0.2, "consequence": 0.8}][:rt.m.immune.k - 1]
    rt.stats.last_window_values = {"verdict_mean": 0.3, "forecast_skill": 0.7}
    rt._sampling_actuator()
    return rt.consequence_mix


def test_a_live_adaptation_moves_the_values_and_leaves_the_prefix_byte_identical():
    """The two values this runtime adapts live are outside the prefix it must not break.

    The sampling actuator raises the consequence mix when verdicts rise while payoff
    skill falls, and the immune controller borrows extra decay on thrash. Both are
    weights the population reads, and neither may cost a cached prefix.
    """
    rt = make_runtime()
    before_wire = wire(rt, "seed-decider", "Respond to event Tick on scripted.", {"index": 1})
    prefix = request_for(rt, "a", {}).stable_prefix()
    committed_mix, committed_decay = rt.ev.consequence_share, rt.m.prices.decay

    mix = diverge(rt)
    # Exactly the immune controller's own intervention (runtime/immune.py, on thrash).
    decay = committed_decay + rt.m.immune.decay_step
    rt.controller.set_decay(decay, ledger=rt.ledger, window=1)
    assert mix != committed_mix and decay != committed_decay  # something really changed

    after_wire = wire(rt, "seed-decider", "Respond to event Tick on scripted.", {"index": 1})
    # The system message, byte for byte: prefix and all.
    assert after_wire[0]["content"] == before_wire[0]["content"]
    assert request_for(rt, "a", {}).stable_prefix() == prefix
    # The prefix carries the committed parameters and names where the live ones are.
    assert f'"consequence_mix": {committed_mix}' in prefix
    assert f'"decay": {committed_decay}' in prefix
    assert f'"consequence_mix": {mix}' not in prefix and f'"decay": {decay}' not in prefix
    assert "world.adaptive_scoring" in prefix
    # The moving part carries what is actually in force.
    user = after_wire[-1]["content"]
    adaptive = json.loads(user.split("INPUTS\n")[1].split("\n\nOUTCOME")[0])["world"][
        "adaptive_scoring"]
    assert adaptive["consequence_mix"] == mix and adaptive["controller_decay"] == decay
    assert "adaptive_scoring" not in STABLE_WORLD_KEYS


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
