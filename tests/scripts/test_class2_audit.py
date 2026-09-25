"""The LLM auditor's offline half: its corpus, the planted canaries, the calibration bar.

Phase-2 design B2, as amended by Astra H-2: one canary per rubric question Q3-Q10, and
an audit is valid only if the auditor finds at least 7 of 8 and every mandatory one
(Q6, Q9, Q10), flags at most 1 of 10 controls, and leaves nothing unread. The model call
is the operator's; nothing here makes one.
"""

import json
import os
import subprocess

import pytest

from scripts import class2_audit as tool

WORLD = "scripted"


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit(repo, path, text, message):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


#: A message that justifies a change to seat-visible text by what the seats did: the
#: behaviour-mix objective AGENTS rule 2 forbids, which the provenance pass must show.
BEHAVIOUR_MIX = "Soften the hold wording: seats held too much in the last run"


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    """A repository with a base, a surface commit justified by a behaviour mix, and a
    commit that touches no seat-visible surface."""
    repo = tmp_path_factory.mktemp("repo")
    _git(repo, "init", "-q")
    base = _commit(repo, "README.md", "x\n", "base")
    surface = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n",
                      BEHAVIOUR_MIX)
    head = _commit(repo, "docs/notes.md", "notes\n", "notes only")
    return repo, base, surface, head


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, history):
    repo, _base, surface, head = history
    out = tmp_path_factory.mktemp("class2")
    key = tool.render([WORLD], out, seed=7, rendered=False, essay=None,
                      release_range=f"{surface}..{head}", repo=repo)
    return out, key


def test_the_provenance_pass_shows_a_commit_justified_by_a_behaviour_mix(history, tmp_path):
    """Codex review: every surface-touching commit in the release range is in the prompt
    with its message and diff; a commit touching no surface is not."""
    repo, base, surface, head = history
    tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=None,
                release_range=f"{base}..{head}", repo=repo)
    prompt = (tmp_path / "prompt.md").read_text()
    section = prompt.split("## Provenance pass", 1)[1].split("## The corpus", 1)[0]
    assert surface in section and BEHAVIOUR_MIX in section
    assert "+HOLD = 'hold'" in section
    assert "notes only" not in section and f"### {head}" not in section


def test_a_merge_whose_conflict_resolution_adds_surface_text_is_in_the_provenance_pass(
        tmp_path):
    """Codex P2: a merge is a commit; the text its conflict resolution writes is in no
    parent, so only the merge itself can show it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n", "base")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'keep'\n", "side wording")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'stay'\n", "main wording")
    merged = subprocess.run(["git", "-C", str(repo), "merge", "-q", "side"],
                            capture_output=True, text=True,
                            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                                 "GIT_CONFIG_NOSYSTEM": "1"})
    assert merged.returncode != 0, "the fixture needs a conflict"
    (repo / "factorylab/cortex/schematics.py").write_text(
        "HOLD = 'you should hold when unsure'\n")
    _git(repo, "add", "factorylab/cortex/schematics.py")
    _git(repo, "commit", "-q", "--no-edit", "-m", f"Merge side: {BEHAVIOUR_MIX}")
    merge = _git(repo, "rev-parse", "HEAD")
    commits = tool.provenance_commits(repo, f"{base}..{merge}")
    assert merge in [c["sha"] for c in commits]
    entry = next(c for c in commits if c["sha"] == merge)
    assert entry["merge"] and "you should hold when unsure" in entry["diff"]
    section = tool.provenance_section(f"{base}..{merge}", commits)
    assert f"### {merge} (merge, combined diff)" in section
    assert BEHAVIOUR_MIX in section and "you should hold when unsure" in section


def test_a_range_with_no_surface_commit_renders_an_explicit_empty_section(rendered):
    out, _key = rendered
    prompt = (out / "prompt.md").read_text()
    section = prompt.split("## Provenance pass", 1)[1].split("## The corpus", 1)[0]
    assert "(no commit in this range touched a seat-visible surface)" in section
    assert BEHAVIOUR_MIX not in section


def test_the_release_range_is_base_dot_dot_head(history, tmp_path):
    repo, _base, _surface, head = history
    with pytest.raises(ValueError, match="base..head"):
        tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=None,
                    release_range=head, repo=repo)


def test_a_render_whose_world_raises_midway_writes_no_corpus_and_exits_nonzero(
        history, tmp_path, monkeypatch, capsys):
    """Codex review: a rendered world that fails partway is refused, naming the failure;
    its partial requests never become the key's trusted expected leaves."""
    from factorylab.runtime.loop import Runtime

    repo, _base, surface, head = history
    original, calls = Runtime._process_event, {"n": 0}

    def fails_midway(self, ev):
        calls["n"] += 1
        if calls["n"] >= 8:
            raise RuntimeError("world raised midway")
        return original(self, ev)

    monkeypatch.setattr(Runtime, "_process_event", fails_midway)
    out = tmp_path / "audit"
    code = tool.main(["render", "--world", WORLD, "--out", str(out), "--seed", "7",
                      "--rendered", "--range", f"{surface}..{head}", "--repo", str(repo),
                      "--essay", str(tmp_path / "absent.md")])
    assert code != 0 and calls["n"] >= 8
    assert not out.exists()
    err = capsys.readouterr().err
    assert "world raised midway" in err and WORLD in err
    with pytest.raises(tool.RenderFailed):
        tool.render([WORLD], out, seed=7, rendered=True, essay=None,
                    release_range=f"{surface}..{head}", repo=repo)
    assert not out.exists()


def test_a_failed_render_refuses_the_whole_baseline_rewrite(tmp_path, monkeypatch, capsys):
    """Codex review: the tracked findings and surface registry are never rewritten from a
    partial corpus; the command exits non-zero and both files are untouched."""
    from tests.audit import class2_audit, class2_corpus, class2_lexicon

    baseline, surfaces = tmp_path / "findings.json", tmp_path / "surfaces.toml"
    baseline.write_bytes(class2_lexicon.BASELINE.read_bytes())
    surfaces.write_bytes(class2_audit.SURFACES.read_bytes())
    monkeypatch.setattr(class2_lexicon, "BASELINE", baseline)
    monkeypatch.setattr(class2_audit, "SURFACES", surfaces)
    monkeypatch.setattr(class2_corpus, "launchable_worlds", lambda: [WORLD])

    def partial(world, **_kw):
        return class2_corpus.Rendered(
            world, leaves=[(f"{world}/request/produce/request/#prose", "Partial text.")],
            requests=3, status="failed: RuntimeError: world raised midway")

    monkeypatch.setattr(class2_corpus, "render_dynamic", partial)
    before = (baseline.read_bytes(), surfaces.read_bytes())
    with pytest.raises(class2_corpus.RenderFailed, match="world raised midway"):
        class2_audit.write_baseline([WORLD])
    assert tool.main(["baseline"]) == 2
    assert "world raised midway" in capsys.readouterr().err
    assert (baseline.read_bytes(), surfaces.read_bytes()) == before


def test_triage_of_an_invalid_audit_writes_nothing_and_exits_nonzero(rendered, tmp_path,
                                                                     monkeypatch):
    """Codex review: an invalid audit is rerun with the next family, never triaged."""
    out, key = rendered
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path / "triage")
    (tmp_path / "triage").mkdir()
    rows, summary = _output(key, canaries={c["id"] for c in key["canaries"]} - {"canary-q6"})
    output = tmp_path / "auditor_output.jsonl"
    output.write_text("\n".join(json.dumps(r) for r in [*rows, summary]) + "\n")
    argv = ["triage", str(output), "--key", str(out / "canary_key.json"), "--world", WORLD,
            "--family", "fam-x"]
    assert tool.main(argv) == 1
    assert list((tmp_path / "triage").iterdir()) == []
    valid_rows, valid_summary = _output(key)
    output.write_text("\n".join(json.dumps(r) for r in [*valid_rows, valid_summary]) + "\n")
    assert tool.main(argv) == 0
    assert (tmp_path / "triage" / f"{WORLD}.md").exists()


def test_the_canary_corpus_has_one_canary_per_question_and_three_mandatory():
    spec = json.loads(tool.CANARIES.read_text())
    questions = [c["question"] for c in spec["canaries"]]
    assert questions == [f"Q{i}" for i in range(3, 11)]
    assert {c["question"] for c in spec["canaries"] if c.get("mandatory")} == {"Q6", "Q9",
                                                                                "Q10"}
    assert len(spec["control_surfaces"]) == 10


def test_render_is_deterministic(rendered, history, tmp_path):
    out, key = rendered
    repo, _base, surface, head = history
    again = tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=None,
                        release_range=f"{surface}..{head}", repo=repo)
    assert again == key
    for name in ("auditor_input.jsonl", "prompt.md"):
        assert (tmp_path / name).read_bytes() == (out / name).read_bytes()


def test_the_key_is_kept_out_of_the_auditors_input(rendered):
    out, key = rendered
    text = (out / "auditor_input.jsonl").read_text()
    records = [json.loads(line) for line in text.splitlines()]
    ids = {r["leaf_id"] for r in records}
    assert {c["leaf_id"] for c in key["canaries"]} <= ids  # planted, and readable
    assert len(key["controls"]) == 10
    # Every record has the same fields, planted or not: nothing marks a canary or control.
    fields = {"leaf_id", "world", "path", "text", "provenance", "surface_kind", "audience",
              "frequency"}
    assert all(set(r) == fields for r in records)
    assert "canary" not in text and "mandatory" not in text
    prompt = (out / "prompt.md").read_text()
    assert "canar" not in prompt.casefold() and "mandatory" not in prompt
    assert "Q10" in prompt and "auditor_input.jsonl" in prompt


def _output(key, *, canaries=None, controls=0, summary=True, unread=()):
    rows = [{"leaf_id": c["leaf_id"], "path": c["path"], "question": c["question"],
             "class": c["class"]} for c in key["canaries"]
            if canaries is None or c["id"] in canaries]
    rows += [{"leaf_id": c["leaf_id"], "path": c["path"], "question": "Q4", "class": "C1"}
             for c in key["controls"][:controls]]
    read = sorted(set(key["expected_leaves"]) - set(unread))
    summ = {"summary": True, "leaves_read": len(read),
            "leaves_total": key["expected_count"], "read": read, "unread": list(unread)}
    return rows, (summ if summary else None)


def test_an_auditor_that_finds_every_canary_and_no_control_is_valid(rendered):
    _out, key = rendered
    verdict = tool.validate(*_output(key), key)
    assert verdict["valid"] and verdict["canaries_found"] == "8/8"


@pytest.mark.parametrize("missed", ["canary-q6", "canary-q9", "canary-q10"])
def test_missing_a_mandatory_canary_invalidates_even_at_seven_of_eight(rendered, missed):
    _out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {missed}
    verdict = tool.validate(*_output(key, canaries=found), key)
    assert not verdict["valid"] and verdict["canaries_found"] == "7/8"
    assert any("mandatory" in p for p in verdict["problems"])


def test_missing_a_non_mandatory_canary_at_seven_of_eight_stays_valid(rendered):
    _out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {"canary-q3"}
    assert tool.validate(*_output(key, canaries=found), key)["valid"]


def test_six_of_eight_is_invalid(rendered):
    _out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {"canary-q3", "canary-q4"}
    assert not tool.validate(*_output(key, canaries=found), key)["valid"]


def test_flagging_two_controls_invalidates(rendered):
    _out, key = rendered
    assert tool.validate(*_output(key, controls=1), key)["valid"]
    assert not tool.validate(*_output(key, controls=2), key)["valid"]


def test_a_missing_summary_or_an_unread_leaf_invalidates(rendered):
    _out, key = rendered
    assert not tool.validate(*_output(key, summary=False), key)["valid"]
    real = next(i for i in key["expected_leaves"]
                if i not in {c["leaf_id"] for c in key["canaries"]})
    verdict = tool.validate(*_output(key, unread=[real]), key)
    assert not verdict["valid"] and any("unread" in p for p in verdict["problems"])


def test_the_key_records_every_kernel_leaf_rendered(rendered):
    out, key = rendered
    records = [json.loads(line)
               for line in (out / "auditor_input.jsonl").read_text().splitlines()]
    kernel = {r["leaf_id"] for r in records if r["provenance"] == "kernel"}
    assert set(key["expected_leaves"]) == kernel and key["expected_count"] == len(kernel)
    assert {c["leaf_id"] for c in key["canaries"]} <= kernel


def test_a_summary_claiming_zero_of_zero_is_refused(rendered):
    """Codex P1: counts the auditor reports about itself prove nothing. With every canary
    found and a summary of 0 read of 0, every real leaf went unread: invalid."""
    _out, key = rendered
    rows, _summary = _output(key)
    empty = {"summary": True, "leaves_read": 0, "leaves_total": 0, "unread": []}
    verdict = tool.validate(rows, empty, key)
    assert not verdict["valid"] and verdict["canaries_found"] == "8/8"
    assert any("neither read nor listed" in p for p in verdict["problems"])


def test_a_summary_that_silently_skips_one_real_leaf_is_refused(rendered):
    _out, key = rendered
    rows, summary = _output(key)
    canaries = {c["leaf_id"] for c in key["canaries"]}
    skipped = next(i for i in key["expected_leaves"] if i not in canaries)
    read = [i for i in summary["read"] if i != skipped]
    for claimed in (len(read) + 1, len(read)):  # a forged count, and an honest one
        forged = {**summary, "read": read, "leaves_read": claimed}
        verdict = tool.validate(rows, forged, key)
        assert not verdict["valid"]
        assert any("neither read nor listed" in p for p in verdict["problems"])


def test_the_triage_skeleton_records_the_family_and_leaves_out_the_canaries(rendered):
    _out, key = rendered
    rows, summary = _output(key)
    rows.append({"leaf_id": "x", "path": "scripted/tools/a/description", "question": "Q4",
                 "class": "C1", "severity": "MED", "quote": "Read it first."})
    verdict = tool.validate(rows, summary, key)
    text = tool.triage_skeleton(rows, verdict, world=WORLD, family="fam-x", key=key)
    assert "fam-x" in text and "Read it first." in text
    assert not any(c["path"] in text for c in key["canaries"])
