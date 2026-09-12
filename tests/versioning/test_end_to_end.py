import ast
import json
from pathlib import Path

from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.versioning import render, summary
from factorylab.versioning.reader import read_diary


def test_scripted_800_event_diaries_have_equal_summaries(tmp_path):
    reports = []
    for name in ("first", "second"):
        ledger_path = tmp_path / f"{name}.jsonl"
        world = run_world(
            load_manifest("scripted"),
            events=800,
            seed=1,
            ledger_path=str(ledger_path),
            kill_at_end=True,
        )
        assert world["ledger_verify"] and world["wallet_conservation"]
        assert world["seal_key_released"]
        items = read_diary(ledger_path, str(ledger_path) + ".key")
        report = summary(items)
        assert set(report) == {
            "params",
            "windows",
            "operator",
            "versions",
            "pathologies",
            "ews",
            "settling",
        }
        assert len(report["windows"]) == sum(item.get("kind") == "price.window" for item in items)
        assert len(report["windows"]) >= 3
        assert report["operator"]["matrix"] and report["versions"]
        # The scripted amendment adding the turnover card is approved at event 25, but A2's
        # cadence floors the period at the consequence backstop (200 events) while fewer
        # than timing.min_support forecasts have settled, so the next activation cannot
        # precede launch + min_ratio * 200 = event 601. A3 opens a dimension only for a
        # card with a live region in a closed window, and the scripted measurement window
        # is 120 events, so the first window carrying the new card closes at event 721.
        # The budget must therefore outrun that boundary, not merely the activation.
        activation = [item for item in items if item.get("kind") == "charter.activate"]
        assert [item["amendment_id"] for item in activation] == ["turnover-card"]
        assert "card:turnover" in report["operator"]["dimensions"]
        assert any(w["profile"]["verdict"] is not None for w in report["windows"])
        assert report["settling"] and report["settling"][0]["edition"] == 2
        assert json.loads(json.dumps(report, allow_nan=False)) == report
        assert "Dobrushin" in render(report)
        reports.append(report)
    assert reports[0] == reports[1]
    assert render(reports[0]) == render(reports[1])


def test_package_import_boundary():
    package = Path(__file__).resolve().parents[2] / "factorylab" / "versioning"
    for source in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith("factorylab."):
                    assert (name in ("factorylab.kernel.events", "factorylab.charter.controller")
                            or name.startswith("factorylab.versioning"))
