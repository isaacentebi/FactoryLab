"""The consolidated charter-time session, run offline (charter audit S1, U4, M5).

The population drafts cards from the norms, a sortition adopts the charter whole,
the export carries the digests the load path verifies, and λ is set against the
dollars it cost in rehearsal diaries.
"""

import json
import tomllib
from pathlib import Path

import pytest

from factorylab.charter.market import lambda_dollars
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts import charter_session
from scripts.rehearsal import voted_charter

pytestmark = pytest.mark.gate


def _diary():
    """Two closed windows of a diary written before the runtime published price.dollars."""
    def penalty(raw, amount, weight):
        return {"kind": "price.penalty", "raw": raw, "penalty": amount,
                "terms": [{"card_id": "cap", "weight": weight, "share": 1.0},
                          {"card_id": "floor", "weight": weight, "share": 0.0}]}

    return [
        {"kind": "invocation", "cost": 300_000},
        {"kind": "price.contribution", "storage": True, "cost": 100_000},
        penalty(0.8, 0.2, 0.5), penalty(0.8, 0.0, 0.0),
        {"kind": "price.window", "window": 1},
        {"kind": "price.update", "card_id": "cap", "lambda_after": 0.4},
        {"kind": "invocation", "cost": 500_000},
        penalty(0.5, 0.1, 0.2),
        {"kind": "price.window", "window": 2},
        {"kind": "price.update", "card_id": "cap", "lambda_after": 0.6},
    ]


def test_the_lambda_report_prices_each_card_s_penalty_at_the_window_s_cost_of_reward():
    report = charter_session.lambda_report(_diary())
    assert report["source"] == "recomputed" and report["windows"] == 2
    cap = report["cards"]["cap"]["windows"]
    # Window 1: 400,000 micro-USD bought 1.6 units of reward; the cap took 0.2 of it.
    assert cap[0] == pytest.approx({"window": 1, "lambda": 0.4, "penalty": 0.2,
                                    "micro_usd": 50_000})
    assert cap[1] == pytest.approx({"window": 2, "lambda": 0.6, "penalty": 0.1,
                                    "micro_usd": 100_000})
    assert report["cards"]["cap"]["micro_usd_total"] == 150_000
    assert report["micro_usd_per_reward"] == round(900_000 / 2.1)
    # Fewer than three windows state no correlation.
    assert report["cards"]["cap"]["lambda_usd_correlation"] is None
    assert lambda_dollars({"cap": 0.3}, 0.0, 10) == {"cap": None}


def test_a_diary_with_the_runtime_statistic_is_read_as_written():
    rows = [{"kind": "price.dollars", "window": w, "reward_mass": 1.0,
             "compute_spend_micro": 1000,
             "cards": {"cap": {"lambda": lam, "penalty": pen, "micro_usd": usd}}}
            for w, lam, pen, usd in ((1, 0.1, 0.1, 100), (2, 0.2, 0.2, 200),
                                     (3, 0.4, 0.3, 300))]
    report = charter_session.lambda_report(rows)
    assert report["source"] == "price.dollars"
    assert report["cards"]["cap"]["lambda_usd_correlation"] == pytest.approx(
        0.9819805060619656)


def test_the_dry_run_drafts_adopts_and_exports_a_charter_the_load_path_accepts(tmp_path):
    diary = tmp_path / "events.json"
    diary.write_text(json.dumps(_diary()))
    out = tmp_path / "session"
    code = charter_session.main(["session", "--world", "scripted", "--out-dir", str(out),
                                 "--dry-run", "--diary", str(diary)])
    assert code == 0
    evidence = json.loads((out / "session.json").read_text())
    manifest = load_manifest("scripted")
    # (a) The architect supplied the norms only; every seat proposed, a sortition voted.
    assert evidence["norms"] == list(manifest.charter.norms)
    proposals = evidence["draft"]["proposals"]
    assert {p["proposer"] for p in proposals} == {a.id for a in manifest.assemblies}
    passed = [p for p in proposals if p["problem"] is None]
    refused = [p for p in proposals if p["problem"] and p["problem"].startswith("preflight")]
    assert len(passed) == 4 and refused  # a second binding of one observation is refused
    assert all(len(p["ballots"]) == manifest.committee.seats for p in passed + refused)
    # (b) A fresh sortition adopted the charter whole, and saw the lambda-to-dollar report.
    assert evidence["approved"] is True
    assert len(evidence["adopt"]["ballots"]) == manifest.committee.seats
    assert evidence["lambda_dollars"][str(diary)]["cards"]["cap"]["micro_usd_total"] == 150_000
    # (c) The export binds the loaded cards and the roster that voted them.
    text = (out / "charter.toml").read_text()
    table = voted_charter(out / "charter.toml", manifest)
    assert table == tomllib.loads(text)["charter"]
    assert all("region" in card and "acceptable_region" not in card for card in table["cards"])
    world = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    world["charter"] = table
    loaded = manifest_from_dict(world)
    assert [c.id for c in loaded.charter.cards] == [p["card"]["id"] for p in passed]
    assert evidence["cost_micro"] == 0 and not any(c["error"] for c in evidence["calls"])
    # A second session into the same directory refuses to overwrite its evidence.
    with pytest.raises(FileExistsError):
        charter_session.main(["session", "--world", "scripted", "--out-dir", str(out),
                              "--dry-run"])


def test_the_report_command_reads_a_jsonl_diary(tmp_path, capsys):
    diary = tmp_path / "ledger.jsonl"
    diary.write_text("\n".join(json.dumps(row) for row in _diary()) + "\n")
    assert charter_session.main(["report", "--diary", str(diary)]) == 0
    report = json.loads(capsys.readouterr().out)[str(diary)]
    assert report["cards"]["cap"]["micro_usd_total"] == 150_000
