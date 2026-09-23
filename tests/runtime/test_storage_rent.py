"""Working-state storage rent: the byte-day rate R11 keeps as ``[storage]``."""

from dataclasses import replace

import pytest

from factorylab.runtime.continuity import StorageSpec, charge_window
from factorylab.runtime.worlds import manifest_from_dict
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

#: One micro-USD per byte per two minutes, stated as a byte-day rate (C3).
PER_WINDOW_RATE = "720"


def test_working_state_rent_is_charged_by_byte_time_once():
    rt = make_runtime()
    rt.m = replace(rt.m, storage=StorageSpec(micro_per_byte_day=PER_WINDOW_RATE))
    rt._manage_reserve_window()
    handle = decision(rt)
    head = rt.working_state.put("seed-decider", {"fact": "kept"}, handle=handle)
    before = rt.wallet.balance
    # Two minutes of wall time pass and the price loop falls due (its period is ticks).
    rt.clock.now_ns += 120_000_000_000
    rt.clockwork.force("price", rt.ticks_consumed)
    rt._manage_reserve_window()
    assert rt.wallet.balance == before - head["bytes"]
    assert ledger_items(rt, "state.rent")[-1]["cost"] == head["bytes"]
    charge_window(rt)  # no time has passed: nothing more is due
    assert rt.wallet.balance == before - head["bytes"]


def _world(**tables):
    from tests.seed_charter import seed_charter_table

    return {"name": "x", "initial_balance_usd": "10", "charter": seed_charter_table(),
            "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
            "assemblies": [{"id": "a", "model_id": "m"}],
            "novelty": {"share": 0.1}, "immune": {"price_step": 0.05},
            **tables}


def test_storage_rate_is_read_from_storage_and_from_the_old_notes_key():
    assert manifest_from_dict(_world()).storage == StorageSpec()
    named = manifest_from_dict(_world(storage={"micro_per_byte_day": "0.5"}))
    legacy = manifest_from_dict(_world(notes={"micro_per_byte_day": "0.5"}))
    assert named.storage.micro_per_byte_day == legacy.storage.micro_per_byte_day == "0.5"
    assert named.manifest_hash() == legacy.manifest_hash()


@pytest.mark.parametrize("tables,match", [
    ({"notes": {"max_keys": 128}}, "public notebook was removed"),
    ({"notes": {"micro_per_byte_day": "0.04", "byte_window_micro": 1}},
     "public notebook was removed"),
    ({"notes": {"micro_per_byte_day": "0.04"}, "storage": {"micro_per_byte_day": "0.04"}},
     "not also in"),
    ({"storage": {"max_bytes": 10}}, "storage accepts only"),
    ({"storage": {"micro_per_byte_day": "0"}}, "positive finite"),
])
def test_notebook_keys_and_bad_rates_are_refused(tables, match):
    with pytest.raises(ValueError, match=match):
        manifest_from_dict(_world(**tables))


def test_the_storage_price_is_published_to_every_seat():
    """Codex review of #125: a price is a public schematic (essay II.I.b). Removing
    the notebook had removed the only place the storage rent was stated."""
    from factorylab.cortex.request import Request

    rt = make_runtime()
    rt.m = replace(rt.m, storage=StorageSpec(micro_per_byte_day="3"))
    world = rt._world_block()
    assert world["storage"]["micro_per_byte_day"] == "3"
    assert "reserve-window boundary" in world["storage"]["pricing"]
    text = Request("h", "d", {"world": world}, {}, {"type": "object"}, 0, 1_000_000, None,
                   "answer", "verdict", "seat").prompt_text()
    assert '"micro_per_byte_day":"3"' in text.replace(" ", "")
