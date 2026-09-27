"""The fast harness, scripted, on the edition 6 world with its event-market reader.

Edition 6 reads Polymarket and writes nothing to it. A scripted population reads
market text and tries a venue write in the same wake; judges forecast on the seeded
market. The write path (intents, fills, custody, late resolution, refusals) is owned
by ``test_polymarket_surface`` on the simulated venue.
"""

import json
from pathlib import Path

import pytest

from scripts import fastloop

#: The seeded venue's first market's "Yes" token (world/polymarket.py, seed 0).
YES = "100000000000000000000"
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
def test_the_edition6_world_reads_event_markets_and_settles_forecasts_on_them_offline(
        tmp_path, monkeypatch):
    """Wave 9: edition 6 with its live reader answered by the simulated venue. The world
    loads, publishes the three reads and no write, jails the market text it read, and
    settles judges' event forecasts on the market's own price and resolution."""
    manifest = fastloop.simulation_manifest(EDITION6, 1)
    assert manifest.polymarket.enabled and manifest.polymarket.venue == "live"
    monkeypatch.setattr(fastloop, "PolicyProvider", ReadAndForecastPolicy)
    card = fastloop.run("scripted", 150, EDITION6, tmp_path, cap_usd="2", seed=1)
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
