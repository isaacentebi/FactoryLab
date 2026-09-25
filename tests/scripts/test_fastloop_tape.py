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
                        tape_from=tmp_path / "short.tape.json", allow_unknown_cutoff=True)
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
    # The manifest's tick from the tape's first instant (after the launch's own real
    # work), ending with the tape: the tape is sampled at the world's tick, never looped.
    # A scripted world is never busy for a whole tick, so every idle wait is skipped
    # and every tick fires exactly on its declared instant.
    assert tape.start_ns <= stamps[0] < tape.start_ns + TICK and stamps[-1] <= tape.end_ns
    assert len(stamps) == (tape.end_ns - stamps[0]) // TICK + 1
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
                        tape_from=tmp_path / "short.tape.json", allow_unknown_cutoff=True)
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
    # The tape ended inside an hour with the position still held: the kill charged the
    # position-hours held since the last boundary, so no cost was left uncharged.
    kill = next(e["seq"] for e in events if e.get("kind") == "kill.production")
    partial = [e for e in events if e.get("kind") == "consequence.funding"
               and e["seq"] < kill and e["payload"]["coin"] == "BTC"
               and float(e["payload"]["paid_usd"]) != 0]
    assert partial and card["fill_band"]["funding_usd"] != "0.000000"


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
        {"kind": "venue.settled", "reason": "funding", "amount": -10_000,
         "custody": "venue_perps"},
    ]
    band = fastloop.fill_band(events)
    # 0.5 realized - 0.006975 fees - 0.01 funding + 0.05 x (120 - 100) still open.
    assert band["pnl_usd"] == "1.483025"
    # Half of 2 bps on 15.5 USD of fills.
    assert band["extra_slippage_usd"] == "0.001550"
    assert band["pessimistic_pnl_usd"] == "1.481475"
    assert fastloop.fill_band(events[1:]) is None  # off a tape there is no band
    assert fastloop.scorecard(events)["fill_band"] == band


def test_the_tape_library_names_regimes_and_seals_its_holdouts(tmp_path):
    """Critique H4: tapes are organised by regime and split into dev and sealed holdout
    tapes; a holdout runs only on a release candidate, so no iteration sees it."""
    library = fastloop.tape_library()
    assert {e["holdout"] for e in library.values()} == {True, False}
    assert all(set(e["regimes"]) <= fastloop.REGIMES and e["source"].startswith("work/")
               for e in library.values())
    tape = Tape.load(OTHER)
    assert fastloop.library_entry(tape) is None  # an unlisted tape runs, and says so
    sealed = tmp_path / "library.toml"
    sealed.write_text(f'[[tape]]\nid = "sealed"\nsource = "work/x"\nsha256 = "{tape.sha256}"\n'
                      'regimes = ["chop"]\nholdout = true\n')
    with pytest.raises(ValueError, match="sealed_holdout"):
        fastloop.library_entry(tape, path=sealed)
    assert fastloop.library_entry(tape, True, path=sealed)["id"] == "sealed"
    sealed.write_text(sealed.read_text().replace('"chop"', '"bullish"'))
    with pytest.raises(ValueError, match="malformed"):
        fastloop.tape_library(sealed)


LATENCY = FIXTURES / "longrun1-call-latency-ms.json"
S = 10**9


def test_call_latencies_are_read_from_a_diarys_journaled_wall_reads(tmp_path):
    """Two wall reads in one event with one model call between them bracket that call."""
    events = [{"kind": "runtime.input", "seq": 1},
              {"kind": "io.call", "seq": 2, "name": "wall.now_ns"},
              {"kind": "io.result", "seq": 3, "call": 2, "result": 100 * S},
              {"kind": "io.call", "seq": 4, "name": "provider.complete"},
              {"kind": "io.call", "seq": 5, "name": "wall.now_ns"},
              {"kind": "io.result", "seq": 6, "call": 5, "result": 106 * S},
              {"kind": "io.call", "seq": 7, "name": "provider.complete"},
              {"kind": "io.call", "seq": 8, "name": "provider.complete"},
              {"kind": "io.call", "seq": 9, "name": "wall.now_ns"},  # two calls: no sample
              {"kind": "io.result", "seq": 10, "call": 9, "result": 130 * S},
              {"kind": "runtime.input", "seq": 11}]
    diary = tmp_path / "events.json"
    diary.write_text(json.dumps(events))
    assert fastloop.call_latencies(diary) == [6000]
    assert len(fastloop.call_latencies(LATENCY)) == 269  # longrun1's, already extracted


def test_a_stand_ins_call_takes_a_measured_latency_and_expires_past_its_deadline():
    from factorylab.runtime.live import IdleSkipClock
    from factorylab.world.models import ModelRequest
    from factorylab.world.openai_wire import CALL_EXPIRED
    from factorylab.world.openrouter import OpenRouterError

    class Answers:
        def complete(self, req):
            return "answer"

    clock = IdleSkipClock(10 * S, 5, origin_ns=0, monotonic=lambda: 0)
    latent = fastloop.Latent(Answers(), [4000], seed=1)
    latent.clock = clock
    request = ModelRequest("m", "s", ({"role": "user", "content": "x"},))
    assert latent.complete(request) == "answer" and clock.modelled_ns == 4 * S
    with pytest.raises(OpenRouterError, match=CALL_EXPIRED):
        latent.complete(ModelRequest("m", "s", request.messages, timeout_s=1.5))
    assert clock.modelled_ns == 4 * S + 1_500_000_000  # the deadline, then it expired


@pytest.mark.gate
def test_a_scripted_latency_model_reproduces_the_paid_runs_delivered_gaps(tmp_path):
    """Critique C1: a zero-latency stand-in on the idle-skipping clock delivers every tick
    on time, blind to the lateness the paid run lived with. Given the per-call latencies
    longrun1's own diary measured, the scripted population on longrun1's tape delivers
    about the gaps longrun1 delivered (p50 16.1 s, p90 38.3 s over its six hours)."""
    blind = fastloop.run("scripted", None, WORLD, tmp_path / "blind", cap_usd="2", seed=1,
                         tape_from=TAPE, allow_unknown_cutoff=True)
    assert blind["pace"]["delivered_s"]["p90"] == 10.0 and blind["pace"]["late_share"] == 0
    card = fastloop.run("scripted", None, WORLD, tmp_path / "paced", cap_usd="2", seed=1,
                        tape_from=TAPE, latency_from=LATENCY, allow_unknown_cutoff=True)
    assert card["status"] == "completed", card.get("error")
    delivered = card["pace"]["delivered_s"]
    assert 0.6 * 16.07 <= delivered["p50"] <= 1.4 * 16.07
    assert 0.6 * 38.30 <= delivered["p90"] <= 1.4 * 38.30
    assert card["pace"]["late_share"] > 0.3 and card["pace"]["modelled_busy_s"] > 0
    assert card["pace"]["latency_model"] == str(LATENCY)
    # A later tick is never earlier: busy time stays real and the tape is never outrun.
    stamps = [e["ts_ns"] for e in _world_events(_events(card), "Tick")]
    assert stamps == sorted(set(stamps)) and stamps[-1] <= Tape.load(TAPE).end_ns


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
                     tape_from=tmp_path / "short.tape.json", allow_unknown_cutoff=True)
    monkeypatch.setattr(fastloop.PolicyProvider, "complete", complete)
    [target] = (tmp_path / "out").iterdir()
    before = (target / "ledger.jsonl").read_bytes()
    with pytest.raises(TapeMismatch, match="tape_mismatch"):
        fastloop.resume(target, OTHER)
    assert (target / "ledger.jsonl").read_bytes() == before  # refused before any append
    # A before_replay hook binds stand-ins; one that swaps the venue is refused.
    from factorylab.runtime.resume import resume_runtime
    from factorylab.world.tape import TapeVenue

    spec = json.loads((target / "run.json").read_text())
    tape = Tape.load(tmp_path / "short.tape.json")
    manifest, _admission, provider, exchange, _latent = fastloop._world_parts(
        "scripted", Path(spec["world"]), 1, "2", None, tape, None, True)

    def swaps(rt):
        rt.exchange = TapeVenue(Tape.load(OTHER), coins=manifest.exchange.coins)

    with pytest.raises(TapeMismatch, match="tape_mismatch"):
        resume_runtime(manifest, str(target / "ledger.jsonl"), provider=provider,
                       exchange=exchange, clock_source=fastloop.tape_clock(tape, manifest, None),
                       before_replay=swaps)
    card = fastloop.resume(target)
    assert card["status"] == "completed", card.get("error")
    tape = Tape.load(tmp_path / "short.tape.json")
    assert card["tape"]["sha256"] == tape.sha256
    stamps = [e["ts_ns"] for e in _world_events(_events(card), "Tick")]
    assert stamps[-1] <= tape.end_ns and len(stamps) == len(set(stamps))
