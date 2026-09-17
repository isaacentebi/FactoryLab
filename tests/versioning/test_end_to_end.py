import ast
import json
from pathlib import Path

from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.versioning.reader import read_diary
from factorylab.versioning.report import render, summary


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
        # The scripted amendment adding the turnover card is approved at event 25. With the
        # scripted consequence backstop at 20 events, A2's cadence allows activation from
        # launch + min_ratio * 20 = event 61, and the scripted measurement window is 120
        # events, so the first window carrying the new card closes at event 121.
        #
        # The scripted fill-card amendment sits past the script's 1,600th producer call
        # (`late_amendment_call`). It used to fall outside 800 events, and now it does
        # not: round 3 added facts the world renders and work the seats do, so the seeded
        # population reaches that call count inside the same 800 events and the fill card
        # activates too. The count is a property of the script, not of the diary — this
        # pin follows it. What the test is actually for is below: both diaries must agree
        # exactly, and they do, which is the claim that the count cannot weaken.
        activation = [item for item in items if item.get("kind") == "charter.activate"]
        assert [item["amendment_id"] for item in activation] == [
            "turnover-card", "scripted-fill-card"]
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
