"""The fast harness, scripted, on a copy of the edition 5 rehearsal world with event markets.

The world is ``fixtures/polymarket-fastloop.toml``: the rehearsal's launch identity
plus ``[polymarket] enabled = true`` on the simulated venue. A scripted population
places a Polymarket order, reads market text, and holds; the seeded market
resolves inside the run. What must come out is the whole path: durable intents,
fills in the polymarket pot, positions scored at the market's midpoint by the
backstop, and a later resolution booked late with a receipt.
"""

import json
from pathlib import Path

import pytest

from factorylab.world import polymarket as world_polymarket
from scripts import fastloop

WORLD = Path(__file__).parent / "fixtures" / "polymarket-fastloop.toml"
#: The seeded venue's first market's "Yes" token (world/polymarket.py, seed 0).
YES = "100000000000000000000"


class EventMarketPolicy(fastloop.PolicyProvider):
    """The harness's scripted population, with some decisions that use event markets."""

    def _decide(self, inputs):
        if "tool_results" not in inputs:
            turn = self.decisions + 1
            if turn % 4 == 1:
                self.decisions += 1
                # At the opening mid, under its ask: the post-only venue rests it, and
                # ``ScriptedMarket`` fills it as a maker.
                return {"action": "order", "tool_calls": [{
                    "tool": "polymarket.place_limit",
                    "args": {"token_id": YES, "side": "buy", "size": "10", "price": "0.40"}}]}
            if turn % 4 == 2:
                self.decisions += 1
                return {"action": "investigate", "tool_calls": [
                    {"tool": "polymarket.search", "args": {"query": "simulated", "limit": 3}}]}
        return super()._decide(inputs)


class ScriptedMarket(world_polymarket.FakePolymarket):
    """The seeded venue with its first market scripted, whatever the run's timing (Sol P2,
    round 5, on #177: a random walk left every resting buy unfilled): it stands still
    until a buy rests on it, then walks one tick down so its ask meets the lowest resting
    buy, which fills there as a maker; later buys at that price would cross and are
    rejected. It resolves YES 900 s after the venue's first step."""

    def __post_init__(self):
        super().__post_init__()
        self.step_ticks = 0

    def advance(self, now_ns):
        if self._started_ns is None:
            self.resolutions = {**self.resolutions, "fake-1": (now_ns + 900 * 10**9, 0)}
        market = self._markets["fake-1"]
        resting = [o["price"] for o in self._orders.values()
                   if self._tokens[o["token_id"]] == ("fake-1", 0)]
        if resting and not market["closed"]:
            market["mid"] = min(market["mid"], min(resting) - self.tick)
        return super().advance(now_ns)


EDITION6 = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"
#: The harness's own population, held before a test swaps it for a subclass.
SCRIPTED = fastloop.PolicyProvider


class ReadAndForecastPolicy(SCRIPTED):
    """The scripted population on a live-read world: producers read market text, then
    try a venue write in the same wake; judges forecast on the seeded market's token."""

    def _decide(self, inputs):
        results = inputs.get("tool_results") or []
        if any(str(r.get("tool", "")).startswith("polymarket.") for r in results):
            return {"action": "order", "tool_calls": [{"tool": "venue.place_limit", "args": {
                "coin": "BTC", "side": "buy", "size": "0.001", "price": "1"}}]}
        if "tool_results" not in inputs and (self.decisions + 1) % 3 == 1:
            self.decisions += 1
            return {"action": "investigate", "tool_calls": [
                {"tool": "polymarket.search", "args": {"query": "simulated", "limit": 3}},
                {"tool": "polymarket.book", "args": {"token_id": YES, "depth": 3}}]}
        return super()._decide(inputs)

    @staticmethod
    def _judge(inputs, model_id=""):
        reply = SCRIPTED._judge(inputs, model_id)
        reply["forecasts"] = [
            {"predicate": "event_pays", "q": 0.4, "params": {"horizon_events": 30,
                                                             "token_id": YES}},
            {"predicate": "event_price_above", "q": 0.5,
             "params": {"horizon_events": 10, "token_id": YES, "level": 0.4}}]
        return reply


@pytest.mark.gate
# 100 ticks in the gate (the seeded market has resolved and a claim due after it has read
# its payout by then); the original 150 in the soak tier.
@pytest.mark.parametrize("ticks", [100, pytest.param(150, marks=pytest.mark.soak)])
def test_the_edition6_world_reads_event_markets_and_settles_forecasts_on_them_offline(
        tmp_path, monkeypatch, ticks):
    """Wave 9: edition 6 with its live reader answered by the simulated venue. The world
    loads, publishes the three reads and no write, jails the market text it read, and
    settles judges' event forecasts on the market's own price and resolution."""
    manifest = fastloop.simulation_manifest(EDITION6, 1)
    assert manifest.polymarket.enabled and manifest.polymarket.venue == "live"
    monkeypatch.setattr(fastloop, "PolicyProvider", ReadAndForecastPolicy)
    card = fastloop.run("scripted", ticks, EDITION6, tmp_path, cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    text = Path(card["out"], "events.json").read_text()
    events = json.loads(text)
    kinds = [e.get("kind") for e in events]
    # The reads reached the simulated venue; nothing was written to any Polymarket venue.
    assert kinds.count("polymarket.read") >= 2
    assert "polymarket.intent" not in kinds and "polymarket.refused" not in kinds
    # Outside text is jailed: the round after a read runs no venue write, and no market
    # question reaches a durable surface. The recovery plane (io.call, io.result) is
    # exempt by design: it is how a read is replayed.
    assert "tool.calls_ignored" in kinds
    from factorylab.world.polymarket import DEFAULT_FAKE_MARKETS

    durable = json.dumps([e for e in events if e.get("kind") not in ("io.call", "io.result")])
    assert not any(m["question"] in durable for m in DEFAULT_FAKE_MARKETS)
    assert any(m["question"] in text for m in DEFAULT_FAKE_MARKETS)  # it was read
    # The judges' event claims settle on what the world read at their due ticks.
    settled = [e["event"]["payload"] for e in events if e.get("kind") == "event"
               and (e.get("event") or {}).get("kind") == "ForecastSettled"
               and e["event"]["payload"]["predicate"].startswith("event_")]
    assert {s["predicate"] for s in settled} == {"event_pays", "event_price_above"}
    assert all(s["y"] in (0, 1) for s in settled if s["status"] != "censored")
    assert any(s["y"] is not None for s in settled)
    reads = [e for e in events if e.get("kind") == "polymarket.event_read"]
    assert reads and all(r["token_id"] == YES for r in reads)
    # The seeded market resolved inside the run, and a claim due after it read its payout.
    assert any(r["payout"] is not None for r in reads)


@pytest.mark.gate
def test_scripted_fastloop_run_settles_an_event_market_position(tmp_path, monkeypatch):
    """The write path through the reward chain, which the surface tests (check tier)
    own piece by piece: intents, fills, the pot, a late resolution with a receipt to
    every holder, and a learning signal. 95 ticks is the fewest that reach the seeded
    market's resolution (900 s) and the refusal after it closes."""
    monkeypatch.setattr(fastloop, "PolicyProvider", EventMarketPolicy)
    monkeypatch.setattr(world_polymarket, "FakePolymarket", ScriptedMarket)
    card = fastloop.run("scripted", 95, WORLD, tmp_path, cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    events = json.loads(Path(card["out"], "events.json").read_text())
    kinds = [e.get("kind") for e in events]
    # A resting buy filled as a maker, a later one would have crossed and was rejected.
    acknowledged = [e["result"]["status"] for e in events
                    if e.get("kind") == "polymarket.acknowledged"]
    assert "resting" in acknowledged and "rejected" in acknowledged
    assert kinds.count("polymarket.intent") >= 1
    assert "polymarket.fill" in kinds and "polymarket.read" in kinds
    assert "polymarket.resolution" in kinds and "consequence.resolution" in kinds
    receipts = [e for e in events if e.get("kind") == "receipt.execution"
                and (e.get("receipt") or {}).get("kind") == "resolution"]
    settled = [e for e in events if e.get("kind") == "venue.settled"
               and e.get("custody") == "polymarket"]
    assert settled, "the resolution is booked in the polymarket pot"
    # No decision traded on the text it read in the same wake.
    assert not any(e.get("kind") == "polymarket.intent" and e.get("handle") in {
        r.get("handle") for r in events if r.get("kind") == "polymarket.read"} for e in events)
    # Each decision that held the token is told what the resolution realised for it (the
    # venue is post-only: an order that would cross is rejected and one that rests may
    # never fill, so only a filled order's decision holds the token).
    owner = {e["order_id"]: e["handle"] for e in events if e.get("kind") == "consequence.order"}
    held = {owner[e["order_id"]] for e in events if e.get("kind") == "polymarket.fill"}
    assert held and {r["receipt"]["handle"] for r in receipts} == held
    # Each holder is credited what the resolution realised for it, exactly: the payout
    # on every share its orders bought, less what it paid for them, in integer
    # micro-USD, as its claim on the pot.
    from decimal import Decimal

    (resolution,) = [e for e in events if e.get("kind") == "polymarket.resolution"]
    payout = Decimal(resolution["payout"])
    fills = [e for e in events if e.get("kind") == "polymarket.fill"]
    assert fills and all(f["is_buy"] for f in fills)  # this population only buys
    expected = {}
    for fill in fills:
        handle = owner[fill["order_id"]]
        assert fill["fee_usd"] == "0"  # a maker pays no fee
        usd = (payout - Decimal(fill["px"])) * Decimal(fill["size"])
        expected[handle] = expected.get(handle, 0) + int(usd * 1_000_000)
    credited = {}
    for claim in (e for e in events if e.get("kind") == "polymarket.claim"
                  and e.get("reason") == "late_consequence"):
        credited[claim["handle"]] = credited.get(claim["handle"], 0) + claim["amount"]
    assert credited == expected and all(type(v) is int for v in credited.values()), (
        credited, expected)
    assert {r["receipt"]["handle"]: r["receipt"]["facts"]["realized_micro"]
            for r in receipts} == expected
    # The market's price marked the positions first; the resolution then booked late.
    assert "consequence.late" in kinds and "polymarket.drift" not in kinds
    assert card["learning_signal_rate"] > 0
    # After the market closed, new orders on it are refused before any intent.
    assert any(e.get("kind") == "polymarket.refused"
               and e.get("reason") == "market is not accepting orders" for e in events)
