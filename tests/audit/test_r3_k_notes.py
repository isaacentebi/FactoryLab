"""Notebook byte bounds, caller liability, rent, and durable public content."""

from dataclasses import replace

from factorylab.runtime.notes import NotesSpec, charge_window
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

#: The notebook's flat per-call price. Edition 3 removed the per-byte transfer toll
#: (C4): a put or a get pays this and whatever rent the key already owes, and nothing
#: for the bytes it moves. Byte-time rent, which the cases below measure, stays.
FLAT = 1


def put(rt, handle, key="fact", text="public fact"):
    return rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": key, "text": text}})


def get(rt, handle, key="fact"):
    return rt._run_tool("eval-a", handle, {"tool": "note.get", "args": {"key": key}})


#: One micro-USD per byte per two-minute scripted window, stated as a byte-day rate (C3).
PER_WINDOW_RATE = "720"


def test_note_rent_is_charged_by_byte_time_once_and_survives_resume():
    rt = make_runtime()
    rt.m = replace(rt.m, notes=NotesSpec(micro_per_byte_day=PER_WINDOW_RATE))
    rt._manage_reserve_window()
    handle = decision(rt)
    assert put(rt, handle)[1] == FLAT and rt.notes["fact"]["bytes"] == 15
    before = rt.wallet.balance
    rt.clock.now_ns += rt.m.novelty.window_ns
    rt._manage_reserve_window()
    assert rt.wallet.balance == before - 15
    assert ledger_items(rt, "note.rent")[-1]["cost"] == 15
    charge_window(rt)  # no time has passed: nothing more is due
    assert rt.wallet.balance == before - 15
    restored = make_runtime()
    restored.m = rt.m
    restore_runtime(restored, runtime_state(rt))
    assert restored.notes == rt.notes
    assert get(restored, handle)[1] == FLAT  # the rent was paid; the read itself is flat
