"""The consolidated charter-time session, run offline (charter audit S1, U4, M5).

The population drafts cards from the norms, a sortition adopts the charter whole,
the export carries the digests the load path verifies, and λ is set against the
dollars it cost in rehearsal diaries.
"""

import json
import tomllib
from pathlib import Path

import pytest

from factorylab.charter.market import margin
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts import charter_session
from scripts.rehearsal import voted_charter

pytestmark = pytest.mark.gate


def _diary():
    """Three windows' price.margin rows, as the runtime ledgers them."""
    def points(slope, dollars):
        return [{"v": v, "consequence": 0.2 + slope * v, "micro_usd": 100 + dollars * v,
                 "n": 2} for v in (0.0, 0.5, 1.0)]

    return [
        {"kind": "price.margin", "window": 1, "card_id": "cap", "lambda": 0.1,
         "points": points(0.3, 1000)},
        {"kind": "price.margin", "window": 2, "card_id": "cap", "lambda": 0.2,
         "points": points(0.1, 2000)},
        {"kind": "price.margin", "window": 3, "card_id": "cap", "lambda": 0.4,
         "points": points(0.0, 4100)},
        {"kind": "price.margin", "window": 3, "card_id": "floor", "lambda": 0.0,
         "points": points(0.1, 0)[:2]},
    ]


def test_the_report_is_the_runtime_s_own_margin_computation():
    """Cold review: the two paths disagreed and divided by average reward. The report
    now recomputes each window's margins from the ledgered points with the runtime's
    function, so it cannot disagree with it."""
    report = charter_session.lambda_report(_diary())
    assert report["source"] == "price.margin" and report["windows"] == 3
    cap = report["cards"]["cap"]
    assert [w["marginal_consequence"] for w in cap["windows"]] == pytest.approx([0.3, 0.1, 0.0])
    assert [w["micro_usd_per_violation"] for w in cap["windows"]] == pytest.approx(
        [1000, 2000, 4100])
    assert [w["micro_usd_per_violation"] for w in cap["windows"]] == pytest.approx(
        [margin(e["points"])["micro_usd_per_violation"] for e in _diary()[:3]])
    assert cap["identified_windows"] == 3
    assert cap["lambda_usd_correlation"] == pytest.approx(0.9993, abs=1e-3)
    floor = report["cards"]["floor"]
    assert floor["identified_windows"] == 0 and floor["lambda_usd_correlation"] is None
    assert charter_session.lambda_report([{"kind": "price.window", "window": 1}]) == {
        "source": "price.margin", "windows": 0, "cards": {}}


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
    assert evidence["lambda_dollars"][str(diary)]["cards"]["cap"]["identified_windows"] == 3
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
    assert report["cards"]["cap"]["identified_windows"] == 3


def test_the_dry_run_runs_on_edition6(tmp_path):
    """Cold review: the session could not build edition 6's world (provider-native
    completions); its launch world now reads the manifest's own catalogue."""
    out = tmp_path / "edition6"
    world = Path(__file__).parents[2] / "worlds/edition6-testnet-rehearsal.toml"
    assert charter_session.main(["session", "--world", str(world), "--out-dir", str(out),
                                 "--dry-run"]) == 0
    evidence = json.loads((out / "session.json").read_text())
    manifest = load_manifest(str(world))
    assert evidence["approved"] is True
    assert {p["proposer"] for p in evidence["draft"]["proposals"]} == {
        a.id for a in manifest.assemblies}
    table = voted_charter(out / "charter.toml", manifest)
    assert table["norms"][-1]["id"] == "fidelity"
    # The card contract is the world's own section, never request text.
    assert not hasattr(charter_session, "WHAT_A_CARD_IS")
    world_block = charter_session.launch_world(manifest)
    assert "region {rule, lo, hi}" in world_block["mechanics"]["committee"]["card_contract"]
