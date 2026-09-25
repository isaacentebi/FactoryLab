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
