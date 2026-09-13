"""A3: fixed observations yield the same live and offline diagnoses."""

from dataclasses import replace

import pytest

from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.runtime.immune import close_window
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.versioning.report import manifest_parameters, summary
from factorylab.versioning.versions import diagnose


def runtime():
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=0.1)
    norm = rt.charter.norms[0]
    rt.charter = Charter(1, rt.charter.norms, (
        MetricCard("cost", norm, "cost", "micro-USD", MetricWindow("windows", 1, None),
                   "at most 500", "cost_per_return", "producer"),
        MetricCard("skill", norm, "skill", "score difference", MetricWindow("windows", 1, None),
                   "above zero", "forecast_skill", "evaluator"),
        MetricCard("turnover", norm, "turnover", "ratio", MetricWindow("windows", 1, None),
                   "at most 5", "turnover", "producer"),
    ))
    rt._derive_regions()
    return rt


def freeze(rt, index, cost=920, skill=-0.3, turnover=230, registrations=0, revision=0):
    rt.window.index = index
    rt.window.closed_values = {"cost": cost, "skill": skill, "turnover": turnover}
    rt.window.closed_regions = dict(rt.regions)
    for cid in rt.regions:
        rt.controller.set_price(cid, 1, amendment_id="fixture")
    close_window(rt, dict(cost_per_return=cost, forecast_skill=skill, turnover=turnover,
                         verdict_mean=0.6, registrations=registrations, revision_rate=revision))


def test_a3_frozen_run_flags_failure_and_learning_death():
    rt = runtime()
    for i, (cost, skill, turnover) in enumerate(((983, -.303, 229), (919, -.302, 258),
                                              (921, -.302, 229)), 1):
        freeze(rt, i, cost, skill, turnover)
    assert rt.stats.pathologies == {"stable_failure": True, "learning_death": True, "thrash": False}
    assert rt.controller.price("cost") == .5
    assert rt.routers["Tick"][0].learner.gamma > rt.router_gamma
    # A3's learning-death response is that flag alone; the reserve reads it at the next
    # boundary and issues the one extra novelty trial per assembly for the window that
    # opens (A13, the single novelty_grant implementation).
    rt.stats.reserve_windows += 1
    rt._issue_novelty_grant()
    assert rt.novelty_grant == {"window": rt.stats.reserve_windows, "consumed": []}


def test_a3_noisy_frozen_synthetic_never_flags_thrash():
    rt = runtime()
    for i in range(8):
        noise = 1 + (i % 3 - 1) / 1000
        freeze(rt, i + 1, 920 * noise, -.3 * noise, 230 * noise)
        assert not rt.stats.pathologies["thrash"]
    assert rt.stats.pathologies["stable_failure"]


def test_a3_one_recovery_followed_by_stability_flags_nothing():
    rt = runtime()
    for i, verdict in enumerate([0, 0, 0, 1, 1, 1, 1, 1], 1):
        rt.window.index = i
        rt.window.closed_values = {"cost": 400, "skill": .2, "turnover": 0}
        rt.window.closed_regions = dict(rt.regions)
        close_window(rt, dict(cost_per_return=400, forecast_skill=.2, turnover=0,
                              verdict_mean=verdict, registrations=1, revision_rate=0))
        assert not any(rt.stats.pathologies.values())


def test_a3_editions_and_removed_dimensions_do_not_clear_surviving_evidence():
    rt = runtime()
    freeze(rt, 1)
    freeze(rt, 2)
    rt.charter = replace(rt.charter, edition=2, cards=rt.charter.cards[:2])
    rt._derive_regions()
    freeze(rt, 3)
    assert rt.stats.pathologies["stable_failure"]
    assert len(rt.stats.immune_windows) == 3
    assert set(rt.stats.immune_windows[-1]["regions"]) == {"card:cost", "card:skill"}


def test_a3_new_card_needs_its_own_support():
    region = dict(card_id="cost", kind="max", hi=1, lo=None, scale=1)
    windows = [dict(profile={"registrations": 1, "revision": 0}, regions={}) for _ in range(2)]
    windows.append(dict(profile={"cost": 3, "registrations": 1, "revision": 0},
                        regions={"cost": region}))
    assert not diagnose(windows, k=3, registration_bins=(0, 2), revision_bins=(0,))["flags"][
        "stable_failure"]


def test_a3_flags_and_price_relief_expire_and_resume():
    rt = runtime()
    for i in range(1, 4):
        freeze(rt, i)
    restored = runtime()
    restore_runtime(restored, runtime_state(rt))
    for world in (rt, restored):
        world.window.index = 4
        # The diagnosis itself is what resumes; the extra trial is derived from it by the
        # reserve, so there is no second piece of novelty state to restore.
        assert world.stats.pathologies["learning_death"]
        world.controller.expire_relief(window=4)
        assert world.controller.price("cost") == 1
        assert world.controller.snapshot()["cards"]["cost"]["relief_window"] is None
    assert runtime_state(rt)["runtime"] == runtime_state(restored)["runtime"]


def test_a3_scripted_diary_agrees_with_live_flags_and_genesis(w1_scripted_diary):
    rt, entries = w1_scripted_diary
    report = summary(entries)
    assert manifest_parameters(entries)["k"] == 2
    assert report["params"]["gap_threshold"] == .6
    live = [item for item in entries if item["kind"] == "immune.window"]
    assert len(live) >= 3
    owned = {"stable_failure", "learning_death", "thrash"}
    for index, window in enumerate(live):
        offline = {flag["kind"] for flag in report["pathologies"]
                   if flag["start_window"] <= index <= flag["end_window"] and flag["kind"] in owned}
        assert offline == {kind for kind, value in window["flags"].items() if value}
        assert report["windows"][index]["profile"]["revision"] == window["profile"]["revision"]
    assert rt._world_block()["governance"]["slowest_period"] != "0s"
    assert (rt.cadence.world_block(rt.tick_clock.interval_ns)["outstanding_forecasts"]
            == rt.book.outstanding())
    # Recompute from measurements, even when persisted flags have been corrupted in this copy.
    altered = [dict(item, flags=dict.fromkeys(owned, True))
               if item["kind"] == "immune.window" else item for item in entries]
    assert summary(altered)["pathologies"] == report["pathologies"]


def test_a3_versions_has_no_silent_default_thresholds():
    with pytest.raises(ValueError, match="genesis manifest"):
        summary([])


def test_a3_versions_cli_uses_genesis_thresholds(w1_scripted_diary, monkeypatch, capsys):
    import json
    from argparse import Namespace

    from factorylab.runtime.cli import _cmd_versions

    _, entries = w1_scripted_diary
    monkeypatch.setattr("factorylab.versioning.reader.read_diary", lambda *_args: entries)
    assert _cmd_versions(Namespace(ledger="in-memory", key="unused", json=True)) == 0
    assert json.loads(capsys.readouterr().out)["params"]["k"] == 2
