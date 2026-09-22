"""The fast harness, scripted, on a copy of the edition 5 rehearsal world with event markets.

The world is ``fixtures/polymarket-fastloop.toml``: the rehearsal's launch identity
plus ``[polymarket] enabled = true`` on the simulated venue. A scripted population
places a Polymarket order, reads market text, and holds; the seeded market
resolves inside the run. What must come out is the whole path: durable intents,
fills in the polymarket pot, a resolution that realises the position into the
consequence book with a receipt, and a kill that winds the pot down.
"""

import json
from pathlib import Path

import pytest

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
                return {"action": "order", "tool_calls": [{
                    "tool": "polymarket.place_limit",
                    "args": {"token_id": YES, "side": "buy", "size": "10", "price": "0.45"}}]}
            if turn % 4 == 2:
                self.decisions += 1
                return {"action": "investigate", "tool_calls": [
                    {"tool": "polymarket.search", "args": {"query": "simulated", "limit": 3}}]}
        return super()._decide(inputs)


@pytest.mark.gate
def test_scripted_fastloop_run_settles_an_event_market_position(tmp_path, monkeypatch):
    monkeypatch.setattr(fastloop, "PolicyProvider", EventMarketPolicy)
    card = fastloop.run("scripted", 30, WORLD, tmp_path, cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    events = json.loads(Path(card["out"], "events.json").read_text())
    kinds = [e.get("kind") for e in events]
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
    # Each decision that held the token is told what the resolution realised for it,
    # and a grounded contract waited for that rather than closing on a guess.
    assert {r["receipt"]["handle"] for r in receipts} == {
        e["handle"] for e in events if e.get("kind") == "polymarket.intent"}
    assert "consequence.awaiting_resolution" in kinds
    # After the market closed, new orders on it are refused before any intent.
    assert any(e.get("kind") == "polymarket.refused"
               and e.get("reason") == "market is not accepting orders" for e in events)
