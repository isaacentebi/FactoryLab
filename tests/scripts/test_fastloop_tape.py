"""The harness replays a past paid run's recorded market (``fastloop --tape-from``).

A tape is a ``FakeExchange``, never a new ``exchange.kind``: the world stays simulated,
with no live adapter, rail or real-money branch. Its SHA-256 is fixed in the manifest,
so the Launch record carries it and a resume on another tape is refused. The world
keeps the manifest's tick and samples the tape at it; the run ends with the tape.
"""

import json
from pathlib import Path

import pytest

from factorylab.runtime.bootstrap import TapeMismatch
from factorylab.world.tape import Tape
from scripts import fastloop

WORLD = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "tape"
TAPE = FIXTURES / "longrun1-2100.events.json"
OTHER = FIXTURES / "live4-head.events.json"
TICK = 10 * 10**9


def _short_tape(path, ticks):
    """The fixture tape cut to its first ``ticks`` recorded ticks, written compact."""
    data = Tape.load(TAPE).data
    end = data["ticks"][ticks - 1]
    short = {**data, "ticks": data["ticks"][:ticks],
             "mids": {c: [r for r in rows if r[0] <= end] for c, rows in data["mids"].items()},
             "funding": {c: [r for r in rows if r[0] <= end]
                         for c, rows in data["funding"].items()}}
    tape = Tape.from_data(short)
    tape.write(path)
    return tape


def _events(card):
    return json.loads((Path(card["out"]) / "events.json").read_text())


def _world_events(events, kind):
    return [e["event"] for e in events if e.get("kind") == "event"
            and e["event"]["kind"] == kind]


@pytest.mark.gate
def test_a_scripted_world_runs_on_a_diarys_tape_at_its_own_tick_until_the_tape_ends(
        tmp_path):
    tape = _short_tape(tmp_path / "short.tape.json", 8)
    card = fastloop.run("scripted", None, WORLD, tmp_path / "out", cap_usd="2", seed=1,
                        tape_from=tmp_path / "short.tape.json")
    assert card["status"] == "completed", card.get("error")
    events = _events(card)
    [launch] = _world_events(events, "Launch")
    exchange = launch["payload"]["manifest"]["exchange"]
    # The tape is recorded at launch, and the venue is the fake: no live branch.
    assert exchange["kind"] == "fake" and exchange["tape"]["sha256"] == tape.sha256
    assert card["tape"]["sha256"] == tape.sha256
    assert card["tape"]["venue"] == f"tape:{tape.sha256[:8]}"
    assert launch["ts_ns"] == tape.start_ns
    stamps = [e["ts_ns"] for e in _world_events(events, "Tick")]
    # The manifest's tick from the tape's first instant, ending with the tape: the
    # tape is sampled at the world's tick, never looped.
    assert stamps[0] == tape.start_ns and stamps[-1] <= tape.end_ns
    assert len(stamps) == (tape.end_ns - tape.start_ns) // TICK + 1
    assert {b - a for a, b in zip(stamps, stamps[1:], strict=False)} == {TICK}
    for mid in _world_events(events, "MarketMid"):
        coin = mid["payload"]["coin"]
        assert mid["source"] == f"tape:{tape.sha256[:8]}"
        assert mid["payload"]["mid"] == str(tape.mid_at(coin, mid["ts_ns"])[1])
    band = card["fill_band"]
    assert band["pnl_usd"] is not None and band["pessimistic_pnl_usd"] is not None


@pytest.mark.gate
def test_a_market_decision_fills_a_tick_later_and_is_accounted_to_its_decision(
        tmp_path, monkeypatch):
    """Through the whole runtime: the answer's market order is acknowledged resting, fills
    at a later tick against a newer recorded row than the one its decision was shown,
    at the taker rate, and its fill is booked to the decision that sent it."""
    tape = _short_tape(tmp_path / "short.tape.json", 12)
    decide = fastloop.PolicyProvider._decide_trade

    def buys_once(self, inputs, n):
        if n == 2:
            return {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001",
                    "rationale": "scripted market order"}
        return decide(self, inputs, n)

    monkeypatch.setattr(fastloop.PolicyProvider, "_decide_trade", buys_once)
    card = fastloop.run("scripted", None, WORLD, tmp_path / "out", cap_usd="2", seed=1,
                        tape_from=tmp_path / "short.tape.json")
    assert card["status"] == "completed", card.get("error")
    events = _events(card)
    [intent] = [e for e in events if e.get("kind") == "order.intent"
                and e["operation"] == "venue.place_market"]
    [ack] = [e for e in events if e.get("kind") == "order.acknowledged"
             and e["client_id"] == intent["client_id"]]
    assert ack["result"]["status"] == "resting"
    [fill] = [e for e in events if e.get("kind") == "fill.counted"
              and e["order_id"] == ack["result"]["order_id"]]
    sent = intent["ts"]
    assert fill["ts"] > sent
    assert tape.mid_at("BTC", fill["ts"])[0] > tape.mid_at("BTC", sent)[0]
    assert fill["fee_micro"] == round(fill["notional_micro"] * 0.00045)
    receipts = [e for e in events if e.get("kind") == "consequence.fill"
                and e["payload"]["order_id"] == fill["order_id"]]
    assert receipts, "the fill is booked to the decision that sent it"
    assert card["fill_band"]["fills"] == 1


def test_the_fill_band_shows_the_tapes_pnl_beside_a_pessimistic_shadow_of_it():
    """Both numbers, always: the P&L the tape's fill rules booked, and the same with
    every fill charged an extra half of the tape's stated spread, in integer micro-USD."""
    tape = {"sha256": "ab" * 32, "start_ns": 1, "end_ns": 2, "markets": ["BTC"],
            "spread_bps": [["BTC", "2"]]}
    events = [
        {"kind": "event", "event": {"kind": "Launch", "payload": {
            "manifest": {"exchange": {"tape": tape}}}}},
        {"kind": "fill.counted", "coin": "BTC", "is_buy": True, "size": "0.1", "px": "100",
         "notional_micro": 10_000_000, "realized_micro": 0, "fee_micro": 4_500},
        {"kind": "fill.counted", "coin": "BTC", "is_buy": False, "size": "0.05", "px": "110",
         "notional_micro": 5_500_000, "realized_micro": 500_000, "fee_micro": 2_475},
        {"kind": "event", "event": {"kind": "MarketMid", "payload": {"coin": "BTC",
                                                                      "mid": "120"}}},
        {"kind": "event", "event": {"kind": "Funding", "payload": {"coin": "BTC",
                                                                    "paid_usd": "0.01"}}},
    ]
    band = fastloop.fill_band(events)
    # 0.5 realized - 0.006975 fees - 0.01 funding + 0.05 x (120 - 100) still open.
    assert band["pnl_usd"] == "1.483025"
    # Half of 2 bps on 15.5 USD of fills.
    assert band["extra_slippage_usd"] == "0.001550"
    assert band["pessimistic_pnl_usd"] == "1.481475"
    assert fastloop.fill_band(events[1:]) is None  # off a tape there is no band
    assert fastloop.scorecard(events)["fill_band"] == band


class _Died(BaseException):
    """The process dies here."""


@pytest.mark.gate
def test_a_tape_run_resumes_only_on_the_tape_it_launched_on(tmp_path, monkeypatch):
    _short_tape(tmp_path / "short.tape.json", 12)
    calls = {"n": 0}
    complete = fastloop.PolicyProvider.complete

    def dies(self, req):
        calls["n"] += 1
        if calls["n"] == 30:
            raise _Died
        return complete(self, req)

    monkeypatch.setattr(fastloop.PolicyProvider, "complete", dies)
    with pytest.raises(_Died):
        fastloop.run("scripted", None, WORLD, tmp_path / "out", cap_usd="2", seed=1,
                     tape_from=tmp_path / "short.tape.json")
    monkeypatch.setattr(fastloop.PolicyProvider, "complete", complete)
    [target] = (tmp_path / "out").iterdir()
    before = (target / "ledger.jsonl").read_bytes()
    with pytest.raises(TapeMismatch, match="tape_mismatch"):
        fastloop.resume(target, OTHER)
    assert (target / "ledger.jsonl").read_bytes() == before  # refused before any append
    card = fastloop.resume(target)
    assert card["status"] == "completed", card.get("error")
    tape = Tape.load(tmp_path / "short.tape.json")
    assert card["tape"]["sha256"] == tape.sha256
    stamps = [e["ts_ns"] for e in _world_events(_events(card), "Tick")]
    assert stamps[-1] <= tape.end_ns and len(stamps) == len(set(stamps))
