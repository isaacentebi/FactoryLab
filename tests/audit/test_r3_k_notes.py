"""Notebook byte bounds, caller liability, rent, and durable public content."""

import json
import tomllib
from dataclasses import replace

import pytest

from factorylab.runtime.notes import NotesSpec, charge_window, counts
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.wake import public_window_item, render_wake
from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items


def put(rt, handle, key="fact", text="public fact"):
    return rt._run_tool("seed-decider", handle, {
        "tool": "note.put", "args": {"key": key, "text": text}})


def get(rt, handle, key="fact"):
    return rt._run_tool("eval-a", handle, {"tool": "note.get", "args": {"key": key}})


def test_note_utf8_bounds_and_overwrites_never_erase_on_refusal():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.m = replace(rt.m, notes=NotesSpec(max_keys=1, max_bytes=8))
    handle = decision(rt)
    result, cost = put(rt, handle, key="ab", text="ééé")
    assert result["text"] == "ééé" and cost == 8
    before = dict(rt.notes)
    for key, text, reason in (("cd", "x", "max_keys"), ("ab", "éééé", "max_bytes")):
        result, cost = put(rt, handle, key=key, text=text)
        assert reason in result["error"] and cost == 0 and rt.notes == before
    result, cost = put(rt, handle, key="ab", text="é")
    assert cost == 4 and result["version"] == 2 and counts(rt.notes) == {"keys": 1, "bytes": 4}


def test_note_rent_is_charged_once_per_window_and_survives_resume():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    assert put(rt, handle)[1] == 15
    before = rt.wallet.balance
    rt.clock.now_ns += rt.m.novelty.window_ns
    rt._manage_reserve_window()
    assert rt.wallet.balance == before - 15
    assert ledger_items(rt, "note.rent")[-1]["cost"] == 15
    charge_window(rt)
    assert rt.wallet.balance == before - 15
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert restored.notes == rt.notes
    assert get(restored, handle)[1] == 15


def test_unaffordable_rent_retains_text_and_cannot_be_escaped_by_overwriting(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    put(rt, handle)
    original_available = rt.wallet.available_for
    monkeypatch.setattr(rt.wallet, "available_for", lambda *a: 0)
    rt.window.index += 2
    charge_window(rt)
    assert rt.notes["fact"]["text"] == "public fact"
    assert ledger_items(rt, "note.rent_due")[-1]["cost"] == 30
    assert "error" in get(rt, handle)[0]
    assert "error" in put(rt, handle, text="")[0]
    monkeypatch.setattr(rt.wallet, "available_for", original_available)
    result, cost = put(rt, handle, text="")
    assert "error" not in result and cost == 34


def test_note_read_is_journaled_paid_and_wake_contains_only_counts():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    rt.ledger.active = True
    text = "NOTE_CONTENT_SENTINEL"
    put(rt, handle, text=text)
    before = rt.wallet.balance
    result, cost = get(rt, handle)
    assert result["text"] == text and rt.wallet.balance == before - cost
    assert any(r["name"] == "note.read" for r in ledger_items(rt, "io.call"))
    assert ledger_items(rt, "note.put")[-1]["text"] == text
    wake = public_window_item(rt, window=rt.window.index, event=rt.n)
    assert wake["notes"] == {"keys": 1, "bytes": len(text) + 4}
    assert text not in json.dumps(wake)
    assert "Notes" in render_wake(wake)


@pytest.mark.parametrize("fields", [
    {"max_keys": 0}, {"max_keys": True}, {"max_bytes": -1}, {"max_bytes": 3.5},
    {"byte_window_micro": 0}, {"byte_window_micro": 0.1}, {"ttl": 2},
])
def test_notes_manifest_rejects_invalid_bounds(fields):
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw["notes"] = fields
    with pytest.raises(ValueError, match="notes"):
        manifest_from_dict(raw)


def test_note_request_ceiling_cannot_buy_bytes_from_the_novelty_reserve(monkeypatch):
    from factorylab.cortex.request import Return

    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    calls = iter([
        Return(handle, {}, 1, "ok", tool_calls=({"tool": "note.put",
                "args": {"key": "fact", "text": "expensive"}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Produce", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", replace(req, cost_ceiling=3), "producer")
    assert ret.cost == 1 and not rt.notes and not ledger_items(rt, "note.put")


def test_fetched_text_can_be_registered_as_a_note_in_the_continuation(monkeypatch):
    from factorylab.cortex.request import Return
    from factorylab.world.connector import ConnectorProxy
    from tests.runtime.test_connectors import register
    from tests.world.test_connector import Transport

    rt = make_runtime()
    register(rt, monkeypatch)
    assert "note" in rt._world_block()["connectors"]["continuation_tool_kinds"]
    text = "A public source supplied this durable notebook fact."
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(text.encode()))
    handle = decision(rt)
    responses = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/data"}},)),
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "note.put",
                "args": {"key": "fact", "text": text}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(responses))
    req = rt._request(handle, "Produce", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and ret.cost == 1000 + len(text) + 4
    assert ledger_items(rt, "note.put")[-1]["text"] == text
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert get(restored, handle)[0]["text"] == text
