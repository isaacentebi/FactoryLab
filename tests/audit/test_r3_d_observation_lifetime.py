"""T41: measurement execution expires unless the population still prices it."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.cortex.registration import ObservationProposal
from factorylab.kernel.events import EventKind
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from tests.audit.test_v3_seat4_boundaries import _decision
from tests.conftest import make_runtime


def registered_runtime():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._close_price_window()
    rt.tool_jail_available = True
    runs = []
    rt.observation_runner = SimpleNamespace(run=lambda code, facts: (runs.append(facts),
                                                                     (0.5, None))[1])
    rt._register(_decision(rt, "seed-decider"), ObservationProposal(
        "trial-measure", "Trial measurement", "fraction", (0.0, 1.0),
        "def observe(facts): return 0.5"))
    event = next(e for e in rt.internal if e.kind is EventKind.REGISTERED
                 and e.payload.get("id") == "trial-measure")
    rt._observe_delivered_event(event)
    runs.clear()
    return rt, runs


def close(rt, index):
    rt.window = MeasureWindow(index, rt.wallet.balance)
    rt.n = index * 10
    rt._close_price_window()


def test_observation_trial_expires_at_max_lifetime_without_further_execution():
    rt, runs = registered_runtime()
    born = rt.registered_observations["trial-measure"]["trial_window"]
    for index in range(born, born + rt.m.novelty.max_lifetime_windows):
        close(rt, index)
    assert len(runs) == rt.m.novelty.max_lifetime_windows
    close(rt, born + rt.m.novelty.max_lifetime_windows)
    assert len(runs) == rt.m.novelty.max_lifetime_windows
    assert rt.observations.get("trial-measure") is None
    assert "trial-measure" not in {o.id for o in rt.observations.all()}
    assert rt.registered_observations["trial-measure"]["history"] == [1]
    assert rt.registry.get("observation:trial-measure").version == 1


def test_named_observation_survives_expiry_then_retires_when_card_is_removed():
    rt, runs = registered_runtime()
    card = MetricCard("trial-card", rt.charter.norms[1], "Trial", "fraction",
                      {"kind": "windows", "n": 1, "per": None}, "at most 0.4",
                      "trial-measure", "all")
    rt.charter = replace(rt.charter, cards=(card,))
    rt._derive_regions()
    expiry = rt.window.index + rt.m.novelty.max_lifetime_windows
    close(rt, expiry)
    assert runs
    assert rt.observations.get("trial-measure") is not None
    assert rt.window.closed_values == {card.id: 0.5}
    count = len(runs)
    rt.charter = replace(rt.charter, cards=())
    rt._derive_regions()
    close(rt, expiry + 1)
    assert len(runs) == count
    assert rt.observations.get("trial-measure") is None


def test_retirement_is_ledger_first_and_lifetime_survives_resume(monkeypatch):
    rt, _ = registered_runtime()
    restored = make_runtime()
    # Restore the durable state with the normal journalled observation adapter.
    rt.observation_runner = restored.observation_runner
    restore_runtime(restored, runtime_state(rt))
    restored.observation_runner = SimpleNamespace(run=lambda code, facts: (0.5, None))
    expiry = restored.window.index + restored.m.novelty.max_lifetime_windows
    append = restored.ledger.append

    def reject(item):
        if item["kind"] == "observation.retired":
            raise RuntimeError("ledger unavailable")
        return append(item)

    monkeypatch.setattr(restored.ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        close(restored, expiry)
    assert restored.observations.get("trial-measure") is not None
    monkeypatch.setattr(restored.ledger, "append", append)
    close(restored, expiry)
    assert restored.observations.get("trial-measure") is None


def test_untracked_observation_never_runs_and_has_bounded_inactive_lifetime():
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.registered_observations["untracked"] = {
        "description": "d", "units": "fraction", "unit_range": [0, 1],
        "code": "def observe(facts): return 0.5", "version": 1, "history": [1],
    }
    runs = []
    rt.observation_runner = SimpleNamespace(run=lambda *args: (runs.append(args), (0.5, None))[1])
    close(rt, 1)
    close(rt, 1 + rt.m.novelty.max_lifetime_windows)
    assert runs == []
    assert rt.observations.get("untracked") is None
