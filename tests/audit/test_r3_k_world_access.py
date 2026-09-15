"""Decision six opens public knowledge without granting unregistered trades."""

from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.cortex.registration import parse_proposals
from factorylab.cortex.request import Return
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, resume_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.connector import ConnectorProxy
from factorylab.world.exchange import FakeExchange
from factorylab.world.venue_tools import VenueTools
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items
from tests.world.test_connector import Transport


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


def market_runtime(path=None):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, exchange=replace(manifest.exchange, coins=("BTC",), spot_pairs=()))
    exchange = FakeExchange(coins=("BTC",), listed_coins=("ETH",),
                            listed_spot_pairs=("ETH/USDC",))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                   ledger_path=str(path) if path else None, drip=False, router_gamma=.1,
                   exchange=exchange)


def register_market(rt, handle, coin="ETH", market="perp"):
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "market", "pair" if market == "spot" else "coin": coin}]}, 0, "ok"))


def trade(rt, handle, coin="ETH", market="perp", side="buy"):
    rt.consequences.start(handle, rt.n)
    return rt._run_tool("seed-decider", handle, {
        "tool": "venue.place_market", "args": {"coin": coin, "market": market,
                                                 "side": side, "size": ".001"}})[0]


def parse(item):
    return parse_proposals({"register": [item]}, event_kinds=frozenset(),
                           known_models=frozenset(), known_assemblies=frozenset())


def test_t10_preflight_path_registers_404_root(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    transport = Transport(status=404)
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "eval-a": "evaluator", "meta-a": "meta"})
    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": "source", "description": "Public data",
        "origin": "https://example.org", "preflight_path": "/v1/data?q=1",
        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    assert rt.registry.available("connector"), rt.registration_feedback
    assert transport.calls[0][1] == "/v1/data?q=1"
    assert rt._proposal_shape_search("connector")["connector"]["preflight_path"]


@pytest.mark.parametrize("path", ["//evil.org", "/a\r\nx:y", "/#fragment", "https://evil.org"])
def test_preflight_path_cannot_escape_origin(path):
    accepted, rejected = parse({"kind": "connector", "id": "source", "description": "d",
                                "origin": "https://example.org", "preflight_path": path})
    assert rejected and not accepted


def test_public_reads_include_unseeded_coin_but_trades_refuse():
    ex = FakeExchange(coins=("BTC", "ETH"))
    venue = VenueTools(ex, coins=("BTC",))
    assert "error" not in venue.call("venue.order_book", {"coin": "ETH", "depth": 2})
    assert "error" not in venue.call("venue.funding_history", {"coin": "ETH", "n": 2})
    assert "ETH" in venue.call("venue.mids", {})["mids"]
    assert {r["coin"] for r in venue.call("venue.instruments", {})["perp"]} == {"BTC", "ETH"}
    assert "error" in venue.call("venue.place_market", {"coin": "ETH", "side": "buy", "size": "1"})


def test_market_registration_trial_and_resume():
    rt = market_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    assert rt.exchange.coins == ("BTC",)
    result = rt.venue_tools.call("venue.place_market", {
        "coin": "ETH", "side": "buy", "size": ".001"})
    assert "error" in result
    before = rt.wallet.balance
    novelty_before = rt.reserve.remaining()
    register_market(rt, handle)
    assert ledger_items(rt, "market.registered"), rt.registration_feedback
    assert rt.reserve.remaining() == novelty_before - rt.ev.trial_amount_micro
    assert rt.wallet.balance == before
    result = trade(rt, decision(rt))
    assert result["status"] == "filled"
    assert any(lot.coin == "ETH" for lot in rt.consequences.table.lots)
    rt2 = market_runtime()
    restore_runtime(rt2, runtime_state(rt))
    coins = rt2.tool_specs["venue.place_market"]["args_schema"]["properties"]["coin"]["enum"]
    assert "ETH" in coins
    assert "ETH" in rt2.venue_tools.coins and "ETH" in rt2.exchange.coins
    assert trade(rt2, decision(rt2))["status"] == "filled"
    before = rt2.wallet.balance
    rt2._apply_registrations(handle, Return(handle, {"register": [
        {"kind": "market", "coin": "NOT-LISTED"}]}, 0, "ok"))
    assert "list" in rt2.registration_feedback[-1]["reason"]
    assert rt2.wallet.balance == before


def test_registered_spot_market_reaches_inventory_and_lots_after_resume():
    rt = market_runtime()
    rt._manage_reserve_window()
    register_market(rt, decision(rt), "ETH/USDC", "spot")
    assert ledger_items(rt, "market.registered"), rt.registration_feedback
    rt.treasury.transfer("perps_to_spot", "20", handle="fund-spot", now_ns=1)
    rt.treasury.tick(2)
    rt.exchange.sync_cash(rt.treasury.venue_balance_usd)
    result = trade(rt, decision(rt), "ETH/USDC", "spot")
    assert result["status"] == "filled", result
    assert rt.spot_inventory["ETH/USDC"][0] == Decimal(".001")
    assert any(lot.coin == "ETH/USDC" and lot.market == "spot"
               for lot in rt.consequences.table.lots)
    restored = market_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.spot_inventory == rt.spot_inventory
    result = trade(restored, decision(restored), "ETH/USDC", "spot", "sell")
    assert result["status"] == "filled" and restored.spot_inventory["ETH/USDC"][0] == 0
    assert not restored.consequences.table.lots
    assert restored.wallet.check_conservation()


def test_resume_restores_unregistered_public_universe_and_keeps_tool_examples():
    rt = market_runtime()
    rt._manage_reserve_window()
    before = rt.tool_specs["venue.place_market"]["args_schema"]["examples"]
    register_market(rt, decision(rt))
    assert rt.tool_specs["venue.place_market"]["args_schema"]["examples"] == before
    restored = Runtime(rt.m, events=0, seed=1, initial_balance_micro=None,
                       ledger_path=None, drip=False, router_gamma=.1,
                       exchange=FakeExchange(coins=("BTC",)))
    restore_runtime(restored, runtime_state(rt))
    result = restored.venue_tools.call("venue.order_book", {"coin": "ETH/USDC", "depth": 2})
    assert "error" not in result
    assert "ETH/USDC" not in restored.venue_tools.spot_pairs
    assert "ETH/USDC" in restored.tool_specs[
        "venue.order_book"]["args_schema"]["properties"]["coin"]["enum"]
    assert restored.tool_specs["venue.place_market"]["args_schema"]["examples"] == before


def test_paid_venue_reads_are_journaled_and_add_book_funding_facts():
    from factorylab.runtime.observations import window_facts
    from factorylab.world.exchange import NS_PER_HOUR

    rt = market_runtime()
    rt._manage_reserve_window()
    rt.ledger.active = True
    rt.exchange.advance(NS_PER_HOUR)
    handle = decision(rt)
    for tool, args in (("order_book", {"coin": "ETH", "depth": 2}),
                       ("funding_history", {"coin": "ETH", "n": 2}), ("mids", {})):
        before = rt.wallet.balance
        result, cost = rt._run_tool("seed-decider", handle, {"tool": f"venue.{tool}", "args": args})
        assert "error" not in result and cost == rt.m.connectors.call_price_micro
        assert rt.wallet.balance == before - cost
    facts = window_facts(rt.window)
    assert facts["books"]["ETH"][0]["ts_ns"] == NS_PER_HOUR
    assert type(facts["books"]["ETH"][0]["bids"][0][0]) is int
    assert facts["funding"]["ETH"] == [[NS_PER_HOUR, .0001]]
    assert facts["mids"]["ETH"]
    calls = {row["name"] for row in ledger_items(rt, "io.call")}
    assert {"exchange.order_book", "exchange.funding_history", "exchange.mids"} <= calls
    assert rt.exchange.coins == ("BTC",)


def test_scripted_access_connector_market_order_note_and_persistent_resume(monkeypatch, tmp_path):
    from tests.audit.test_r3_f2_connector_preflight import RootTransport

    path = tmp_path / "world.jsonl"
    rt = market_runtime(path)
    rt.ledger.active = True
    rt._snapshot("launch")
    rt._launch()
    rt._manage_reserve_window()
    transport = RootTransport()
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "eval-a": "evaluator", "meta-a": "meta"})
    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": "source", "description": "Data API with 404 root",
        "origin": "https://example.org", "preflight_path": "/data",
        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    assert rt.registry.available("connector"), rt.registration_feedback
    assert transport.calls == [("example.org", "/data")]
    register_market(rt, handle)
    assert trade(rt, decision(rt))["status"] == "filled"
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "thesis", "text": "ETH is interesting"}})
    assert "error" not in result and cost == 1  # the flat call price; no per-byte toll (C4)
    assert rt._snapshot("k-audit")
    manifest = rt.m
    rt._ledger_lock.close()
    restored = resume_runtime(manifest, str(path), exchange=FakeExchange(
        coins=("BTC",), listed_coins=("ETH",), listed_spot_pairs=("ETH/USDC",)))
    try:
        result, cost = restored._run_tool("seed-decider", decision(restored), {
            "tool": "note.get", "args": {"key": "thesis"}})
        assert result["text"] == "ETH is interesting" and cost == 1  # flat, no per-byte toll
        assert restored.registry.get("connector:source").input_schema["preflight_path"] == "/data"
        assert trade(restored, decision(restored))["status"] == "filled"
        assert restored.wallet.check_conservation() and restored.ledger.verify()
    finally:
        restored._ledger_lock.close()


def test_paid_connector_shape_exact_cap():
    accepted, rejected = parse({"kind": "connector", "id": "source", "description": "d",
        "origin": "https://example.org", "pay": "x402", "max_call_usd": "0.003"})
    assert not rejected
    assert accepted[0].max_call_micro == 3000


def test_notebook_is_paid_and_survives_resume():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    before = rt.wallet.balance
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": "thesis", "text": "ETH is interesting"}})
    assert "error" not in result
    assert cost > 0 and rt.wallet.balance == before - cost
    assert ledger_items(rt, "note.put")[-1]["text"] == "ETH is interesting"
    rt2 = make_runtime()
    restore_runtime(rt2, runtime_state(rt))
    result, cost = rt2._run_tool("seed-decider", handle, {
        "tool": "note.get", "args": {"key": "thesis"}})
    assert result["text"] == "ETH is interesting" and cost > 0


def test_t22_book_series_are_public_numeric_facts():
    from factorylab.runtime.observations import window_facts

    facts = window_facts({"books": [{"coin": "ETH", "ts_ns": 12,
                                    "bids": [[2000000, 3]], "asks": [[2100000, 4]]}]})
    assert facts["books"]["ETH"] == [{"ts_ns": 12, "bids": [[2000000, 3]], "asks": [[2100000, 4]]}]
    guidance = make_runtime()._world_block()
    assert all(key in guidance["observation_facts"] for key in
               ("mids", "funding", "wallet_balance_micro", "tick_timestamps_ns", "books"))


def test_launch_seed_is_trading_permission_not_a_listing():
    """A manifest seed the venue does not list stays off the adapter.

    The adapter's own configuration says which market classes its account
    holds; downstream readers (the treasury rail's perps/spot split) trust it.
    A seed may only add markets the venue already lists, exactly as a ``market``
    registration is refused for an unlisted coin.
    """
    from factorylab.world.treasury import UnconfiguredRail
    from factorylab.world.venue_tools import seed_markets

    manifest = load_manifest("scripted")
    spec = replace(manifest.exchange, coins=("BTC", "ETH"), spot_pairs=("BTC/USDC",))
    exchange = FakeExchange(coins=("BTC",), listed_coins=("ETH",),
                            start_cash_usd=Decimal("100"))
    rail = UnconfiguredRail(exchange)
    before = rail.balances()
    seed_markets(exchange, spec)
    assert exchange.coins == ("BTC", "ETH")  # listed, so the seed may grant it
    assert exchange.spot_pairs == ()  # the venue lists no spot pair
    assert exchange.listed_spot_pairs == ()
    assert rail.balances() == before  # a perps-only venue keeps one venue pot


def test_an_adapter_that_publishes_no_listing_is_left_alone_by_the_seed():
    """An adapter without a listing keeps none; the manifest seed still grants trading.

    A minimal live-shaped adapter exposes reads and orders, not a market
    universe. Its listing is unknown, not empty: nothing is written onto it, and
    the venue tools go on granting exactly the manifest's seeded markets.
    """
    from factorylab.world.venue_tools import seed_markets

    class BareAdapter:
        name = "bare"

    spec = replace(load_manifest("scripted").exchange, coins=("BTC",),
                   spot_pairs=("BTC/USDC",))
    adapter = BareAdapter()
    seed_markets(adapter, spec)
    assert not hasattr(adapter, "coins") and not hasattr(adapter, "spot_pairs")
    tools = VenueTools(adapter, coins=spec.coins, spot_pairs=spec.spot_pairs)
    assert (tools.coins, tools.spot_pairs) == (("BTC",), ("BTC/USDC",))
    assert tools.public_coins == ("BTC", "BTC/USDC")
