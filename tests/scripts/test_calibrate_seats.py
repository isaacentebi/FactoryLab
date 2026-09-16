"""Offline tests for the seat calibration harness (edition 2, C13): zero spend, no network."""

import json
from pathlib import Path
from urllib import request

import pytest

from factorylab.runtime.worlds import load_manifest
from factorylab.world.metering import Infeasible
from scripts.calibrate_seats import (
    COLUMNS,
    SCENARIOS,
    BudgetExhausted,
    BudgetGuard,
    CalibrationProvider,
    MalformedProvider,
    build_runtime,
    build_scenarios,
    calibrate,
    install_seats,
    main,
    markdown_table,
    parse_args,
)

WORLD = "worlds/scripted.toml"
CANDIDATE = "fake-haiku"
ORIGINAL_READ_TEXT = Path.read_text


def guarded_read_text(path, *args, **kwargs):
    assert path.suffix != ".key" and path.name != ".env", "must not read credentials"
    return ORIGINAL_READ_TEXT(path, *args, **kwargs)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(request.OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(Path, "read_text", guarded_read_text)


@pytest.fixture
def manifest():
    return load_manifest(WORLD)


def test_every_scenario_renders_as_a_real_request(manifest):
    rt = build_runtime(manifest, CalibrationProvider(), seed=3)
    seats = install_seats(rt, CANDIDATE, None)
    scenarios = build_scenarios(rt, CANDIDATE, seats, sample=0, long_context_bytes=90_000)
    assert tuple(s.name for s in scenarios) == SCENARIOS
    by_name = {s.name: s for s in scenarios}
    for scenario in scenarios:
        prompt = rt.assemblies[scenario.seat].build_model_request(scenario.request)
        text = prompt.messages[-1]["content"]
        # R3-E: the seat sees the WORLD CONTRACT and the base capability index
        # first — the stable prefix — then YOU, then the world's moving facts,
        # then the request, inputs, schema and the outcome contract.
        assert text.startswith("WORLD CONTRACT\n")
        assert "\nYOU\n" in text and "\nWORLD UPDATE\n" in text
        assert f"REQUEST\n{scenario.request.description}\n" in text
        assert "OUTCOME SCHEMA" in text and "COMPLETION CRITERION" in text
        assert text.count("OUTCOME CONTRACT\n") == 1
        assert prompt.model_id == CANDIDATE
        assert rt.queue.get(scenario.request.handle).propensity.chosen == scenario.seat
    # The judge sees the subject's own propensity; the meta sees the judge's.
    assert "PROPENSITY" in by_name["judge"].request.prompt_text()
    assert by_name["meta"].request.propensity_chosen == "0.6"
    assert by_name["fail"].expected == "refused"
    assert by_name["tool"].check == "tool" and by_name["continuation"].check == "child"
    long_prompt = rt.assemblies[by_name["long_context"].seat].build_model_request(
        by_name["long_context"].request).messages[-1]["content"]
    # R3-E: the long-context scenario pads the registered tool catalogue, and the
    # catalogue is now published as a one-line index rather than as every argument
    # schema, so the same padding renders fewer bytes. What the scenario is for is
    # a prompt several times the seed world's, and it still is.
    assert len(long_prompt.encode()) >= 70_000
    assert len(by_name["produce"].request.prompt_text().encode()) < 90_000


def test_budget_guard_refuses_over_cap_calls(manifest):
    guard = BudgetGuard(budget_micro=1000)
    guard.admit(1000)
    with pytest.raises(BudgetExhausted) as refused:
        guard.admit(1001)
    assert isinstance(refused.value, Infeasible)
    guard.charge(600)
    with pytest.raises(BudgetExhausted):
        guard.admit(401)
    guard.admit(400)
    assert guard.refusals == 2

    # End to end: a cap below the cheapest ceiling starts no call and spends nothing.
    report = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                       seed=3, budget_micro=10, long_context_bytes=90_000)
    rows = report["trees"][CANDIDATE]
    assert len(rows) == len(SCENARIOS)
    assert all(r["status"] == "failed" and r["budget_refused"] for r in rows)
    assert all(r["cost_micro"] == 0 for r in rows)
    assert report["budget"]["spent_micro"] == 0 == report["wallet_spent_micro"]
    assert report["budget"]["refused_calls"] == len(SCENARIOS)
    assert report["candidates"][CANDIDATE]["trees"] == 0

    # A cap that covers some trees stops exactly where the meter says it is spent.
    cheap = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                      seed=3, budget_micro=None, long_context_bytes=90_000)
    first = cheap["trees"][CANDIDATE][0]["cost_micro"]
    ceiling = cheap["planned_first_call_ceiling_micro"][CANDIDATE] // len(SCENARIOS)
    partial = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                        seed=3, budget_micro=first + ceiling, long_context_bytes=90_000)
    ran = [r for r in partial["trees"][CANDIDATE] if not r["budget_refused"]]
    assert 1 <= len(ran) < len(SCENARIOS)
    assert partial["budget"]["spent_micro"] <= first + ceiling
    assert partial["budget"]["spent_micro"] == partial["wallet_spent_micro"]


def test_report_has_every_column(tmp_path, capsys):
    out = tmp_path / "calibration.json"
    assert main(["--world", WORLD, CANDIDATE, "--repeats", "2", "--out", str(out),
                 "--long-context-bytes", "90000"]) == 0
    report = json.loads(out.read_text())
    assert report["mode"] == "offline" and report["offline"] is True
    assert report["production_effects"] is False and report["scripted_exchange"] is True
    assert report["columns"] == list(COLUMNS)
    summary = report["candidates"][CANDIDATE]
    assert set(COLUMNS) <= set(summary)
    assert summary["trees"] == 2 * len(SCENARIOS)
    assert summary["completion"] == summary["well_formed"] == summary["task_met"] == 1.0
    assert summary["refusal_correct"] == 1.0
    assert summary["cost_p50_micro"] > 0 and summary["cost_p95_micro"] >= summary["cost_p50_micro"]
    assert summary["latency_p50_ms"] >= 0
    assert summary["cached_share"] is None  # the scripted provider reports no cache
    assert set(summary["scenarios"]) == set(SCENARIOS)
    for name, scenario in summary["scenarios"].items():
        assert scenario["trees"] == 2, name
        assert scenario["cost_p50_micro"] is not None
    # The tool tree paid for its venue read; the continuation ran its child.
    trees = {r["scenario"]: r for r in report["trees"][CANDIDATE]}
    assert trees["tool"]["tool_calls"] == 1 and trees["continuation"]["children"] == 1
    assert trees["fail"]["status"] == "refused"
    # Whole-tree cost is what the wallet was debited, and it makes the roster number.
    assert all(r["cost_micro"] == r["wallet_delta_micro"] for r in report["trees"][CANDIDATE])
    tick = report["tick_cost"]
    assert tick["per_candidate"][CANDIDATE] == summary["tick_cost_micro"] > 0
    roster = tick["manifest_roster"]
    assert {p["model"] for p in roster["parts"].values()} == {CANDIDATE}
    assert roster["tick_cost_micro"] == sum(
        summary["scenarios"][name]["cost_p50_micro"] for name in ("produce", "judge", "meta"))
    table = markdown_table(report)
    printed = capsys.readouterr().out
    assert table in printed
    for column in ("candidate", "completion", "well-formed", "task met", "refusal ok",
                   "cost p50", "cost p95", "latency p50", "cached share", "tick"):
        assert column in table.splitlines()[0]
    assert f"| {CANDIDATE} | 14 | 100% | 100% | 100% | 100% |" in table


def test_manifest_roster_tick_prices_every_calibrated_seat(tmp_path):
    out = tmp_path / "menu.json"
    assert main(["--world", WORLD, "--all-menu", "--repeats", "1", "--out", str(out),
                 "--long-context-bytes", "90000"]) == 0
    report = json.loads(out.read_text())
    assert set(report["candidates"]) == {"fake-haiku", "fake-opus"}
    roster = report["tick_cost"]["manifest_roster"]
    assert roster["tick_cost_micro"] == sum(
        p["cost_p50_micro"] for p in roster["parts"].values())
    assert roster["tick_cost_micro"] > 0
    # A roster whose seat models were not calibrated is left null, never estimated.
    other = calibrate(load_manifest(WORLD), ["fake-opus"], provider=CalibrationProvider(),
                      repeats=1, seed=3, budget_micro=None, long_context_bytes=90_000)
    assert other["tick_cost"]["manifest_roster"]["tick_cost_micro"] is None
    assert other["tick_cost"]["per_candidate"]["fake-opus"] > 0


def test_malformed_candidate_scores_zero_well_formed(tmp_path):
    out = tmp_path / "malformed.json"
    assert main(["--world", WORLD, CANDIDATE, "--repeats", "1", "--out", str(out),
                 "--long-context-bytes", "90000"], provider=MalformedProvider()) == 0
    summary = json.loads(out.read_text())["candidates"][CANDIDATE]
    assert summary["completion"] == 1.0  # every call came back and was billed
    assert summary["well_formed"] == 0.0
    assert summary["task_met"] == 0.0
    assert summary["refusal_correct"] == 0.0
    assert summary["cost_p50_micro"] > 0
    assert summary["tick_cost_micro"] > 0  # malformed replies still cost money


def test_paid_mode_needs_a_budget_and_candidates_from_the_menu(tmp_path, manifest):
    with pytest.raises(SystemExit):
        parse_args(["--world", WORLD, CANDIDATE, "--paid", "--out", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit):
        parse_args(["--world", WORLD, "--out", str(tmp_path / "x.json")])
    with pytest.raises(ValueError, match="not on the manifest's model menu"):
        calibrate(manifest, ["not-a-menu-model"], provider=CalibrationProvider(), repeats=1,
                  seed=3, budget_micro=None, long_context_bytes=90_000)
    out = tmp_path / "exists.json"
    out.write_text("{}")
    with pytest.raises(FileExistsError):
        main(["--world", WORLD, CANDIDATE, "--out", str(out)])
