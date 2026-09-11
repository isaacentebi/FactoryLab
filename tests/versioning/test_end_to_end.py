import ast
import json
from pathlib import Path

from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.versioning import render, summary
from factorylab.versioning.reader import read_diary


def test_scripted_400_event_diaries_have_equal_summaries(tmp_path):
    reports = []
    for name in ("first", "second"):
        ledger_path = tmp_path / f"{name}.jsonl"
        world = run_world(
            load_manifest("scripted"),
            events=400,
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
        assert "turnover" in report["operator"]["dimensions"]
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
                    assert name == "factorylab.kernel.events" or name.startswith(
                        "factorylab.versioning"
                    )
