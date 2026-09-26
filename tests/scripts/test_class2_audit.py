"""The LLM auditor's offline half: its corpus, the planted canaries, the calibration bar.

Phase-2 design B2, as amended by Astra H-2: one canary per rubric question Q3-Q10, and
an audit is valid only if the auditor finds at least 7 of 8 and every mandatory one
(Q6, Q7, Q9, Q10), flags at most 1 of 10 controls, and leaves nothing unread. The model call
is the operator's; nothing here makes one.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scripts import class2_audit as tool

WORLD = "scripted"
ROOT = Path(__file__).resolve().parents[2]

#: A stand-in essay with the headings that bound the authority text (the real one is
#: never committed): ``render`` fills the prompt from it.
ESSAY_TEXT = "\n".join([
    "Preface", "I. On Factories and Darkness", "Class 1, Class 2 and Class 3 (stand-in).",
    "II. The Human and the Loop", "Not quoted.", "CHAPTER II", "THE DARK STACK (stand-in)",
    "CHAPTER III", "Not quoted either."])
ESSAY = Path(tempfile.mkdtemp(prefix="class2-essay-")) / "essay.md"
ESSAY.write_text(ESSAY_TEXT + "\n")


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


#: The policy files the audit tool reads from the release commit (``committed_text``),
#: seeded into every test repository that renders or gates from this checkout's copies.
POLICY = (tool.CANARIES_REL, tool.PROTOCOL_REL, tool.AGENTS_REL, tool.ALLOWLIST_REL)


def _seed_policy(repo):
    """Write the policy files into ``repo``'s worktree and stage them (the next commit
    carries them), with the stand-in essay's digest as the committed anchor and the
    essay itself in the worktree, never committed, as the operator copies it."""
    for rel in POLICY:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / rel).read_text())
        _git(repo, "add", rel)
    (repo / tool.ESSAY_DIGEST_REL).write_text(tool.sha256_file(ESSAY) + "\n")
    _git(repo, "add", tool.ESSAY_DIGEST_REL)
    (repo / "docs/essay.md").write_text(ESSAY.read_text())


def _surface_commit(key):
    """The one surface commit of the history's range (the base, which seeds the policy
    and the essay's digest, is in a first release's provenance pass too)."""
    (sha,) = [c["sha"] for c in key["provenance_commits"] if c["message"] == BEHAVIOUR_MIX]
    return sha


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


@pytest.fixture(scope="module", autouse=True)
def _seat_text_scan():
    """The seat-text scan every render reads (``class2_seat_text``), taken once in setup."""
    from tests.audit import class2_seat_text

    return class2_seat_text.scan()


#: The history fixture's repository, for the gate's release check (set by ``history``).
REPO: list = []


def _rejected_records(surface):
    """The rejected findings the history's release commits, so a REJECT the tests
    write is backed by the release (the gate reads ``rejected.jsonl`` committed there):
    the flagged commit, and Q3, Q4 and Q6 on the first kernel leaves (any a test picks,
    whatever the controls)."""
    rows = [{"finding_id": tool.leaf_id(f"commit:{surface}", ""),
             "path": f"commit:{surface}", "question": "P1", "class": "BEHAVIOUR-MIX",
             "reason": "the auditor misread"}]
    kernel = [r for r in tool.corpus_records([WORLD], rendered=False)
              if r["provenance"] == "kernel"][:15]
    for record in kernel:
        quote = record["text"].strip()[:60].strip()
        for question in ("Q3", "Q4", "Q6"):
            rows.append({"finding_id": tool.leaf_id(record["path"], quote),
                         "path": record["path"], "question": question,
                         "class": tool.CLASS_OF[question], "reason": "the auditor misread"})
    return "".join(json.dumps(r) + "\n" for r in rows)


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    """A repository with a base (carrying the policy files), a surface commit justified
    by a behaviour mix (with the world file), and a commit that touches no seat-visible
    surface and records the rejected findings the tests' REJECTs name."""
    repo = tmp_path_factory.mktemp("repo")
    REPO[:] = [repo]
    _git(repo, "init", "-q")
    _seed_policy(repo)
    base = _commit(repo, "README.md", "x\n", "base")
    world = repo / "worlds" / f"{WORLD}.toml"
    world.parent.mkdir(parents=True, exist_ok=True)
    world.write_text((ROOT / "worlds" / f"{WORLD}.toml").read_text())
    _git(repo, "add", f"worlds/{WORLD}.toml")
    surface = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n",
                      BEHAVIOUR_MIX)
    (repo / tool.REJECTED_REL).write_text(_rejected_records(surface))
    _git(repo, "add", tool.REJECTED_REL)
    head = _commit(repo, "docs/notes.md", "notes\n", "notes only")
    return repo, base, surface, head


@pytest.fixture(autouse=True)
def _root_is_the_history(history, monkeypatch):
    """The CLI audits the repository it runs in (``tool.ROOT``): here, the history's."""
    monkeypatch.setattr(tool, "ROOT", history[0])


def _triage_text(base, rel, world=WORLD):
    """A gated triage file of ``world`` for the release ``rel`` (its range base..rel)."""
    return (f"# Class 2 audit triage: {world}\n\n- Auditor family: fam-x\n"
            f"- World: {world}\n- Corpus: abc\n- Release range: r ({base}..{rel})\n\n"
            "| id | path | question | class | severity | confidence | quote | "
            "disposition | reason |\n|---|---|---|---|---|---|---|---|---|\n")


def _gate_record(repo, rel, prior, triage_text, *, world=WORLD):
    """Commit what a passed gate leaves: ``LAST_RELEASE`` naming ``rel`` (the commit the
    gate ran on) with the corpus and triage digests, and the triage file beside it."""
    triage = repo / tool.TRIAGE_REL / f"{world}.md"
    triage.parent.mkdir(parents=True, exist_ok=True)
    triage.write_text(triage_text)
    record = {"release_commit": rel, "release_corpus_sha": tool.sha256_file(prior),
              "triages": {world: tool.sha256_file(triage)}}
    (repo / tool.LAST_RELEASE).write_text(json.dumps(record) + "\n")
    _git(repo, "add", tool.LAST_RELEASE, str(triage.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "record the gated release")
    return _git(repo, "rev-parse", "HEAD"), triage


def _release_repo(where, prior, *, family="fam-x", rejected=None):
    """A repository whose last audited release is ``rel``: one surface commit before it,
    the gate-recording commit right after it (``LAST_RELEASE`` with ``prior``'s digest,
    and WORLD's triage file, recording ``family``), then one surface commit and the
    head. Returns the repository, (root, early, rel, later, head) and the triage file."""
    repo = where / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _seed_policy(repo)
    root = _commit(repo, "README.md", "x\n", "root")
    world = repo / "worlds" / f"{WORLD}.toml"
    world.parent.mkdir(parents=True, exist_ok=True)
    world.write_text((ROOT / "worlds" / f"{WORLD}.toml").read_text())
    _git(repo, "add", f"worlds/{WORLD}.toml")
    early = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n", "early")
    rel = _commit(repo, "docs/notes.md", "release\n", "the release audited last")
    _recorded, triage = _gate_record(repo, rel, prior,
                                     _triage_text(root, rel).replace("fam-x", family))
    later = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'keep'\n", "later")
    if rejected is not None:
        (repo / tool.REJECTED_REL).write_text(rejected)
        _git(repo, "add", tool.REJECTED_REL)
    head = _commit(repo, "docs/notes.md", "release 2\n", "the release audited now")
    return repo, (root, early, rel, later, head), triage


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, history):
    repo, base, _surface, head = history
    out = tmp_path_factory.mktemp("class2")
    key = tool.render([WORLD], out, seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{base}..{head}", repo=repo)
    return out, key


def test_the_provenance_pass_shows_a_commit_justified_by_a_behaviour_mix(history, tmp_path):
    """Codex review: every surface-touching commit in the release range is in the prompt
    with its message and diff; a commit touching no surface is not."""
    repo, base, surface, head = history
    tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=ESSAY,
                release_range=f"{base}..{head}", repo=repo)
    section = (tmp_path / "provenance_prompt.md").read_text().split("## Provenance pass", 1)[1]
    assert surface in section and BEHAVIOUR_MIX in section
    assert "+HOLD = 'hold'" in section
    assert "notes only" not in section and f"### {head}" not in section
    # The protocol's second prompt: a commit message may carry behaviour data, which the
    # corpus prompt never holds ("It never sees behaviour data").
    prompt = (tmp_path / "prompt.md").read_text()
    assert BEHAVIOUR_MIX not in prompt and "## Provenance pass" not in prompt


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


def test_a_range_with_no_surface_commit_renders_an_explicit_empty_section(tmp_path):
    """A range with no commit to read says so; the first release's range still holds the
    root, which commits the essay's digest (``ESSAY_DIGEST_REL``) and nothing seat-visible."""
    assert "(no commit in this range touched a seat-visible surface or the essay's " \
           "digest)" in tool.provenance_section("a..b", [])
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _seed_policy(repo)
    root = _commit(repo, "README.md", "x\n", "root")
    head = _commit(repo, "docs/notes.md", "notes\n", "notes only")
    key = tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{root}..{head}", repo=repo)
    assert [c["sha"] for c in key["provenance_commits"]] == [root]
    section = ((tmp_path / "out" / "provenance_prompt.md").read_text()
               .split("## Provenance pass", 1)[1])
    assert f"+{tool.sha256_file(ESSAY)}" in section and "notes only" not in section


def test_the_release_range_is_base_dot_dot_head(history, tmp_path):
    repo, _base, _surface, head = history
    with pytest.raises(ValueError, match="base..head"):
        tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=ESSAY,
                    release_range=head, repo=repo)


def test_a_render_whose_world_raises_midway_writes_no_corpus_and_exits_nonzero(
        history, tmp_path, monkeypatch, capsys):
    """Codex review: a rendered world that fails partway is refused, naming the failure;
    its partial requests never become the key's trusted expected leaves."""
    from factorylab.runtime.loop import Runtime

    repo, base, _surface, head = history
    original, calls = Runtime._process_event, {"n": 0}

    def fails_midway(self, ev):
        calls["n"] += 1
        if calls["n"] >= 8:
            raise RuntimeError("world raised midway")
        return original(self, ev)

    monkeypatch.setattr(Runtime, "_process_event", fails_midway)
    monkeypatch.setattr(tool, "ROOT", repo)  # the CLI audits its own repository
    out = tmp_path / "audit"
    code = tool.main(["render", "--world", WORLD, "--out", str(out), "--seed", "7",
                      "--rendered", "--range", f"{base}..{head}",
                      "--essay", str(ESSAY)])
    assert code != 0 and calls["n"] >= 8
    assert not out.exists()
    err = capsys.readouterr().err
    assert "world raised midway" in err and WORLD in err
    with pytest.raises(tool.RenderFailed):
        tool.render([WORLD], out, seed=7, rendered=True, essay=ESSAY,
                    release_range=f"{base}..{head}", repo=repo)
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


def test_the_canary_corpus_has_one_canary_per_question_and_three_mandatory():
    spec = tool.load_canaries((ROOT / tool.CANARIES_REL).read_text())
    questions = [c["question"] for c in spec["canaries"]]
    assert questions == [f"Q{i}" for i in range(3, 11)]
    assert {c["question"] for c in spec["canaries"] if c.get("mandatory")} == {
        "Q6", "Q7", "Q9", "Q10"}
    assert len(spec["control_surfaces"]) == 10


def test_a_malformed_canary_set_is_refused():
    spec = json.loads((ROOT / tool.CANARIES_REL).read_text())
    spec["canaries"][2]["class"] = "C1"  # Q5 yields C2
    with pytest.raises(tool.AuditInputInvalid, match="canary-q5"):
        tool.load_canaries(json.dumps(spec))


def test_render_is_deterministic(rendered, history, tmp_path):
    out, key = rendered
    repo, base, _surface, head = history
    again = tool.render([WORLD], tmp_path, seed=7, rendered=False, essay=ESSAY,
                        release_range=f"{base}..{head}", repo=repo)
    assert again == key
    for name in ("auditor_input.jsonl", "prompt.md", "provenance_prompt.md"):
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
              "frequency", "change"}
    assert all(set(r) == fields for r in records)
    assert "canary" not in text and "mandatory" not in text
    prompt = (out / "prompt.md").read_text()
    assert "canar" not in prompt.casefold() and "mandatory" not in prompt
    assert "Q10" in prompt and "auditor_input.jsonl" in prompt


def test_the_key_records_every_origin_and_the_prompts_name_their_ids(rendered):
    """(i) Bound: the key names the worlds, the range and its commits, the corpus and both
    prompts by hash, and each prompt names the id its answers echo."""
    out, key = rendered
    assert key["worlds"] == [WORLD] and key["schema"] == tool.KEY_SCHEMA
    assert key["corpus_sha"] == tool.sha256_file(out / "auditor_input.jsonl")
    assert key["prompt_sha"] == tool.sha256_file(out / "prompt.md")
    assert key["provenance_prompt_sha"] == tool.sha256_file(out / "provenance_prompt.md")
    assert key["release_corpus_sha"] == tool.sha256_file(out / "release_corpus.jsonl")
    assert len(key["range_shas"]) == 2 and key["range"].split("..")[1] in key["range_shas"]
    assert key["corpus_sha"] in (out / "prompt.md").read_text()
    assert key["provenance_id"] in (out / "provenance_prompt.md").read_text()
    loaded, records = tool.load_key(out / "canary_key.json")
    assert loaded == key and len(records) > key["expected_count"]


def test_the_key_is_refused_when_its_corpus_changed(rendered, tmp_path):
    out, _key = rendered
    for name in ("auditor_input.jsonl", "canary_key.json"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    lines = (tmp_path / "auditor_input.jsonl").read_text().splitlines()
    (tmp_path / "auditor_input.jsonl").write_text("\n".join(lines[1:]) + "\n")
    with pytest.raises(tool.AuditInputInvalid, match="not bound"):
        tool.load_key(tmp_path / "canary_key.json")


# --- samples: what an auditor must return ------------------------------------------------------


def _records(out):
    return [json.loads(line) for line in (out / "auditor_input.jsonl").read_text().splitlines()]


def _finding(record, question, *, severity=None, **changes):
    """A finding in the protocol's output format, valid against its leaf."""
    quote = record["text"].strip()[:60].strip()
    finding = {"finding_id": tool.leaf_id(record["path"], quote), "leaf_id": record["leaf_id"],
               "world": record["world"], "path": record["path"],
               "surface_kind": record["surface_kind"], "audience": record["audience"],
               "frequency": record["frequency"], "quote": quote, "question": question,
               "class": tool.CLASS_OF[question],
               "severity": severity or tool.required_severity(question, record["frequency"]),
               "passage": "§I.a",
               "rationale": "It addresses the seat.", "rewrite": "delete", "confidence": 0.8}
    return finding | changes


def _output(out, key, *, canaries=None, controls=0, summary=True, unread=(), sample=1,
            extra=()):
    records = {r["leaf_id"]: r for r in _records(out)}
    rows = [_finding(records[c["leaf_id"]], c["question"]) for c in key["canaries"]
            if canaries is None or c["id"] in canaries]
    rows += [_finding(records[c["leaf_id"]], "Q4") for c in key["controls"][:controls]]
    rows += list(extra)
    read = sorted(set(key["expected_leaves"]) - set(unread))
    by_class = {}
    for row in rows:
        by_class[row.get("class")] = by_class.get(row.get("class"), 0) + 1
    summ = {"summary": True, "corpus_sha": key["corpus_sha"], "sample": sample,
            "leaves_read": len(read), "leaves_total": key["expected_count"], "read": read,
            "unread": list(unread), "by_class": by_class}
    return rows, (summ if summary else None)


def _provenance(key, *, sample=1, flag=()):
    rows = [{"sha": c["sha"], "behaviour_mix": c["sha"] in flag,
             "quote": c["message"][:30] if c["sha"] in flag else "", "rationale": "..."}
            for c in key["provenance_commits"]]
    return rows, {"summary": True, "provenance_id": key["provenance_id"], "sample": sample,
                  "commits_read": [c["sha"] for c in key["provenance_commits"]]}


def _verdict(out, key, *, second=None, provenance=None, **kw):
    samples = [_output(out, key, sample=1, **kw), second or _output(out, key, sample=2, **kw)]
    return tool.audit_verdict(samples, provenance or [_provenance(key, sample=i)
                                                      for i in (1, 2)],
                              key, _records(out))


def test_an_auditor_that_finds_every_canary_and_no_control_is_valid(rendered):
    out, key = rendered
    verdict = _verdict(out, key)
    assert verdict["valid"], verdict["problems"]
    assert verdict["canaries_found"] == "8/8"


@pytest.mark.parametrize("missed", ["canary-q6", "canary-q9", "canary-q10"])
def test_missing_a_mandatory_canary_invalidates_even_at_seven_of_eight(rendered, missed):
    out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {missed}
    verdict = _verdict(out, key, canaries=found)
    assert not verdict["valid"] and verdict["canaries_found"] == "7/8"
    assert any("mandatory" in p for p in verdict["problems"])


def test_missing_a_non_mandatory_canary_at_seven_of_eight_stays_valid(rendered):
    out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {"canary-q3"}
    assert _verdict(out, key, canaries=found)["valid"]


def test_six_of_eight_is_invalid(rendered):
    out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {"canary-q3", "canary-q4"}
    assert not _verdict(out, key, canaries=found)["valid"]


def test_flagging_two_controls_invalidates(rendered):
    out, key = rendered
    assert _verdict(out, key, controls=1)["valid"]
    assert not _verdict(out, key, controls=2)["valid"]


def test_a_missing_summary_or_an_unread_leaf_invalidates(rendered):
    out, key = rendered
    assert not _verdict(out, key, summary=False)["valid"]
    real = next(i for i in key["expected_leaves"]
                if i not in {c["leaf_id"] for c in key["canaries"]})
    verdict = _verdict(out, key, unread=[real])
    assert not verdict["valid"] and any("unread" in p for p in verdict["problems"])


def test_the_key_records_every_kernel_leaf_rendered(rendered):
    out, key = rendered
    kernel = {r["leaf_id"] for r in _records(out) if r["provenance"] == "kernel"}
    assert set(key["expected_leaves"]) == kernel and key["expected_count"] == len(kernel)
    assert {c["leaf_id"] for c in key["canaries"]} <= kernel


def test_a_summary_claiming_zero_of_zero_is_refused(rendered):
    """Codex P1: counts the auditor reports about itself prove nothing."""
    out, key = rendered
    rows, summary = _output(out, key)
    empty = {**summary, "leaves_read": 0, "leaves_total": 0, "read": [], "unread": []}
    verdict = _verdict(out, key, second=(rows, empty | {"sample": 2}))
    assert not verdict["valid"] and verdict["canaries_found"] == "8/8"
    assert any("neither read nor listed" in p for p in verdict["problems"])


def test_a_summary_that_silently_skips_one_real_leaf_is_refused(rendered):
    out, key = rendered
    rows, summary = _output(out, key, sample=2)
    canaries = {c["leaf_id"] for c in key["canaries"]}
    skipped = next(i for i in key["expected_leaves"] if i not in canaries)
    read = [i for i in summary["read"] if i != skipped]
    for claimed in (len(read) + 1, len(read)):  # a forged count, and an honest one
        verdict = _verdict(out, key, second=(rows, {**summary, "read": read,
                                                    "leaves_read": claimed}))
        assert not verdict["valid"]
        assert any("neither read nor listed" in p for p in verdict["problems"])


def test_two_samples_are_unioned_and_a_one_sample_finding_is_low_confidence(rendered):
    """The protocol: the call runs twice; the union is kept; a finding only one sample
    made is low confidence; each sample's own summary must be complete."""
    out, key = rendered
    real = next(r for r in _records(out) if r["provenance"] == "kernel"
                and r["leaf_id"] not in {c["leaf_id"] for c in key["canaries"] + key["controls"]})
    extra = _finding(real, "Q4")
    first = _output(out, key, extra=[extra])
    verdict = _verdict(out, key, second=None, extra=[])
    assert verdict["valid"]
    merged = tool.union([first, _output(out, key, sample=2)])
    assert [f["low_confidence"] for f in merged if f["finding_id"] == extra["finding_id"]] \
        == [True]
    assert not any(f["low_confidence"] for f in merged if f["finding_id"] != extra["finding_id"])
    rows, summary = _output(out, key, sample=2)
    verdict = _verdict(out, key, second=(rows, {**summary, "read": summary["read"][1:]}))
    assert not verdict["valid"]
    assert any(p.startswith("sample 2: ") for p in verdict["problems"])


# --- (iii) validated: every finding field against the protocol's schema -------------------------


@pytest.mark.parametrize("change, why", [
    ({"severity": None}, "severity missing"),
    ({"severity": "Medium"}, "severity 'Medium'"),
    ({"leaf_id": "000000000000"}, "not in the corpus"),
    ({"class": "C1"}, "is not Q5's"),
    ({"question": "Q1", "class": "C1"}, "yields no finding"),
    ({"quote": "words the leaf never said"}, "the quote is not the leaf's own words"),
    ({"passage": "somewhere"}, "names no authority section"),
    ({"confidence": 1.5}, "confidence outside"),
    ({"path": "scripted/elsewhere"}, "is not its leaf's"),
])
def test_an_invalid_finding_invalidates_its_sample(rendered, change, why):
    """Codex P2: a finding with a missing or misspelled field never reaches triage."""
    out, key = rendered
    q5 = next(c for c in key["canaries"] if c["question"] == "Q5")
    record = {r["leaf_id"]: r for r in _records(out)}[q5["leaf_id"]]
    bad = {**_finding(record, "Q5"), **change}
    if bad.get("severity") is None:
        bad.pop("severity")
    rows, summary = _output(out, key, sample=2)
    rows = [bad if r["leaf_id"] == q5["leaf_id"] else r for r in rows]
    summary["by_class"] = {}
    for r in rows:
        summary["by_class"][r.get("class")] = summary["by_class"].get(r.get("class"), 0) + 1
    verdict = _verdict(out, key, second=(rows, summary))
    assert not verdict["valid"]
    assert any(p.startswith("sample 2: finding") and why in p for p in verdict["problems"]), \
        verdict["problems"]


def test_the_severity_rule_and_the_class_count_are_read(rendered):
    out, key = rendered
    q6 = next(c for c in key["canaries"] if c["question"] == "Q6")
    record = {r["leaf_id"]: r for r in _records(out)}[q6["leaf_id"]]
    assert tool.finding_problems(_finding(record, "Q6", severity="HIGH"),
                                 {record["leaf_id"]: record})
    rows, summary = _output(out, key, sample=2)
    verdict = _verdict(out, key, second=(rows, {**summary, "by_class": {"C1": 99}}))
    assert any("by_class" in p for p in verdict["problems"])


def test_a_sample_bound_to_another_corpus_or_numbered_twice_is_invalid(rendered):
    out, key = rendered
    rows, summary = _output(out, key, sample=2)
    other = _verdict(out, key, second=(rows, {**summary, "corpus_sha": "f" * 64}))
    assert any("not bound to this corpus" in p for p in other["problems"])
    twice = _verdict(out, key, second=_output(out, key, sample=1))
    assert any("numbered 1..2" in p for p in twice["problems"])
    floated = _verdict(out, key, second=(rows, {**summary, "sample": 2.0}))
    assert any("sample id 2.0" in p for p in floated["problems"])
    one = tool.audit_verdict([_output(out, key)], [_provenance(key), _provenance(key, sample=2)],
                             key, _records(out))
    assert any("1 corpus sample(s)" in p for p in one["problems"])


# --- (ii) consumed: the provenance pass's answer ------------------------------------------------


@pytest.fixture(scope="module")
def with_commit(tmp_path_factory, history):
    """A render whose range holds the surface commit justified by a behaviour mix."""
    repo, base, _surface, head = history
    out = tmp_path_factory.mktemp("class2-prov")
    key = tool.render([WORLD], out, seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{base}..{head}", repo=repo)
    return out, key


def test_the_provenance_answer_is_validated(with_commit):
    """Codex P2: the second prompt's answer is a required, validated input: every commit
    answered once, the prompt's id echoed, a yes quoting the message."""
    out, key = with_commit
    sha = _surface_commit(key)
    good = [_provenance(key, sample=i, flag={sha}) for i in (1, 2)]
    assert _verdict(out, key, provenance=good)["valid"]
    rows, summary = _provenance(key, sample=2, flag={sha})
    unanswered = _verdict(out, key, provenance=[good[0], ([], summary)])
    assert any("answered exactly once" in p for p in unanswered["problems"])
    misquoted = [r | {"quote": "seats liked it"} if r["sha"] == sha else r for r in rows]
    assert any("does not quote" in p for p in _verdict(
        out, key, provenance=[good[0], (misquoted, summary)])["problems"])
    unbound = _verdict(out, key, provenance=[good[0], (rows, {**summary,
                                                              "provenance_id": "x"})])
    assert any("provenance_id" in p for p in unbound["problems"])
    with pytest.raises(SystemExit):
        tool.main(["validate", "a.jsonl", "b.jsonl", "--key", str(out / "canary_key.json")])


def _sample(path, rows, summary):
    path.write_text("\n".join(json.dumps(r) for r in [*rows, summary]) + "\n")
    return path


def _paths(tmp_path, out, key, *, flag=(), **kw):
    """Two corpus samples and two provenance samples on disk."""
    samples = [_sample(tmp_path / f"s{i}.jsonl", *_output(out, key, sample=i, **kw))
               for i in (1, 2)]
    prov = [_sample(tmp_path / f"p{i}.jsonl", *_provenance(key, sample=i, flag=flag))
            for i in (1, 2)]
    return samples, prov


def _files(tmp_path, out, key, *, flag=(), **kw):
    samples, prov = _paths(tmp_path, out, key, flag=flag, **kw)
    return [*map(str, samples), "--provenance-samples", *map(str, prov),
            "--key", str(out / "canary_key.json")]


def test_triage_of_an_invalid_audit_writes_nothing_and_exits_nonzero(rendered, tmp_path,
                                                                     monkeypatch):
    """Codex review: an invalid audit is rerun with the next family, never triaged."""
    out, key = rendered
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path / "triage")
    (tmp_path / "triage").mkdir()
    missing = {c["id"] for c in key["canaries"]} - {"canary-q6"}
    argv = ["triage", *_files(tmp_path, out, key, canaries=missing), "--world", WORLD,
            "--family", "fam-x"]
    assert tool.main(argv) == 1
    assert list((tmp_path / "triage").iterdir()) == []
    argv = ["triage", *_files(tmp_path, out, key), "--world", WORLD, "--family", "fam-x"]
    assert tool.main(argv) == 0
    assert [p.name for p in (tmp_path / "triage").iterdir()] == [f"{WORLD}.md"]


def test_triage_refuses_a_world_the_audit_did_not_render(rendered, tmp_path, monkeypatch,
                                                         capsys):
    """Codex P1: samples of world A never triage as world B."""
    out, key = rendered
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path)
    argv = ["triage", *_files(tmp_path, out, key), "--world", "edition6-capital-loop",
            "--family", "fam-x"]
    assert tool.main(argv) == 1 and "not a world this audit rendered" in capsys.readouterr().err
    assert not (tmp_path / "edition6-capital-loop.md").exists()


@pytest.mark.parametrize("family, why", [
    ("anthropic/claude-sonnet-5", "authored kernel text"),
    ("openai/gpt-5.6-luna", "authored kernel text"),
    ("fake-haiku", "sits in"),
])
def test_triage_refuses_an_authoring_or_seated_family(rendered, tmp_path, monkeypatch,
                                                      capsys, family, why):
    out, key = rendered
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path)
    argv = ["triage", *_files(tmp_path, out, key), "--world", WORLD, "--family", family]
    assert tool.main(argv) == 1 and why in capsys.readouterr().err
    assert not (tmp_path / f"{WORLD}.md").exists()


def test_triage_rotates_the_family_across_releases(rendered, tmp_path, monkeypatch, capsys):
    """The family the prior release's gated triage of the world records (read from its
    gate-recording commit, never a worktree triage file) may not audit the next."""
    prior = tmp_path / "prior.jsonl"
    prior.write_bytes((rendered[0] / "release_corpus.jsonl").read_bytes())
    repo, (*_commits, rel, _later, head), triage = _release_repo(
        tmp_path, prior, family="google/gemini-3")
    out = tmp_path / "out"
    key = tool.render([WORLD], out, seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{rel}..{head}", repo=repo, previous=triage,
                      previous_corpus=prior)
    monkeypatch.setattr(tool, "ROOT", repo)
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path / "triage")
    (tmp_path / "triage").mkdir()
    argv = ["triage", *_files(tmp_path, out, key), "--world", WORLD, "--family"]
    assert tool.main([*argv, "gemini-3-flash"]) == 1  # the same family, next release
    assert "rotates" in capsys.readouterr().err
    assert tool.main([*argv, "mistralai/mistral-large"]) == 0


# --- the release gate, bound to the key and its findings ----------------------------------------


def _dispose(text, disposition="REJECT", reason="the auditor misread", rejected=None):
    """Every open row disposed of; each REJECT also recorded in ``rejected`` (the
    protocol's rejected.jsonl), as the gate requires."""
    lines, records = [], []
    for line in text.splitlines():
        if line.startswith("| ") and not line.startswith("| id ") and line.endswith("|  |  |"):
            line = line[:-len("|  |  |")] + f"| {disposition} | {reason} |"
            cells = tool._cells(line)
            records.append({"finding_id": cells[0], "path": cells[1].strip("`"),
                            "question": cells[2], "class": cells[3], "reason": reason})
        lines.append(line)
    if rejected is not None and disposition == "REJECT":
        with rejected.open("a") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in records)
    return "\n".join(lines) + "\n"


@pytest.fixture
def triaged(with_commit, tmp_path, monkeypatch):
    """A valid audit triaged for WORLD, with the behaviour-mix commit flagged by one
    provenance sample and a real finding of the world by both corpus samples."""
    out, key = with_commit
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path)
    sha = _surface_commit(key)
    real = next(r for r in _records(out) if r["provenance"] == "kernel"
                and r["leaf_id"] not in {c["leaf_id"] for c in key["canaries"] + key["controls"]})
    samples, prov = _paths(tmp_path, out, key, flag={sha}, extra=[_finding(real, "Q4")])
    argv = ["triage", *map(str, samples), "--provenance-samples", *map(str, prov),
            "--key", str(out / "canary_key.json"), "--world", WORLD, "--family", "fam-x"]
    assert tool.main(argv) == 0
    return out, key, tmp_path / f"{WORLD}.md", sha, samples, prov


def _gate(triaged, world=WORLD, samples=None, prov=None, release=None, reviewed=None):
    out, key, path, _sha, s, p = triaged
    return tool.gate(world, path, out / "canary_key.json", samples or s, prov or p,
                     release=release or key["release_commit"], repo=REPO[0],
                     triage_sha256=reviewed or tool.sha256_file(path))


def test_the_provenance_finding_reaches_the_triage_and_the_gate(triaged, monkeypatch):
    """Codex P2: a flagged behaviour-mix commit is a HIGH finding the gate holds."""
    out, key, path, sha, samples, prov = triaged
    monkeypatch.setattr(tool, "ROOT", REPO[0])  # the CLI gates its own repository
    text = path.read_text()
    assert f"`commit:{sha}` | P1 | BEHAVIOUR-MIX | HIGH |" in text
    def gate():
        return ["gate", "--world", WORLD, "--key", str(out / "canary_key.json"),
                "--samples", *map(str, samples), "--provenance-samples", *map(str, prov),
                "--release", key["release_commit"],
                "--triage-sha256", tool.sha256_file(path)]
    assert any("untriaged HIGH" in p for p in _gate(triaged))
    assert tool.main(gate()) == 1
    path.write_text(_dispose(text, rejected=path.parent / "rejected.jsonl"))
    assert _gate(triaged) == []
    last = REPO[0] / tool.LAST_RELEASE
    try:
        assert tool.main(gate()) == 0
        # The gate records the release it passed, for the next range to start at,
        # with its release corpus's digest and the triage file it gated.
        assert json.loads(last.read_text()) == {
            "release_commit": key["release_commit"],
            "release_corpus_sha": key["release_corpus_sha"],
            "triages": {WORLD: tool.sha256_file(path)}}
    finally:
        last.unlink(missing_ok=True)


def test_the_gate_recomputes_the_findings_from_the_bound_samples(triaged, tmp_path):
    """Codex P1: the gate trusts no stored findings. It recomputes them from the samples
    the triage file records by hash, so rewriting the table (and its header) cannot drop
    a finding."""
    out, key, path, sha, samples, prov = triaged
    path.write_text(_dispose(path.read_text(), rejected=path.parent / "rejected.jsonl"))
    assert _gate(triaged) == []
    assert any("'edition6-capital-loop'" in p for p in _gate(triaged, "edition6-capital-loop"))
    text = path.read_text()
    q4 = next(line for line in text.splitlines() if "| Q4 | C1 | HIGH |" in line)
    path.write_text(text.replace(q4, q4.replace("| HIGH |", "| LOW |")))
    assert any("severity 'LOW' is not the finding's 'HIGH'" in p for p in _gate(triaged))
    path.write_text(text.replace(q4 + "\n", ""))
    assert any("(HIGH) has no row" in p for p in _gate(triaged))
    commit_row = next(line for line in text.splitlines() if f"commit:{sha}" in line)
    path.write_text(text.replace(commit_row + "\n", ""))
    assert any("(HIGH) has no row" in p for p in _gate(triaged))
    path.write_text(text + "| ffffffffffff | `scripted/x` | Q4 | C1 | MED | low | x | FIX |  |\n")
    assert any("names no finding of this audit" in p for p in _gate(triaged))
    path.write_text(text)
    assert _gate(triaged) == []


def test_the_gate_reads_only_the_samples_and_family_the_triage_records(triaged, tmp_path):
    """Codex P1, continued: a sample file that changed is not the one the triage read,
    and a recorded family must be one that may audit the world."""
    out, key, path, sha, samples, prov = triaged
    text = _dispose(path.read_text(), rejected=path.parent / "rejected.jsonl")
    path.write_text(text)
    # A sample edited after triage (a finding removed) is not the sample the file records.
    rows, summary = _output(out, key, sample=2)
    edited = _sample(tmp_path / "edited.jsonl", rows, summary)
    assert any("not the ones the triage file records" in p
               for p in _gate(triaged, samples=[samples[0], edited]))
    # A recorded family that may not audit the world, however the header was edited.
    path.write_text(text.replace("- Auditor family: fam-x", "- Auditor family: fake-haiku"))
    assert any("may not audit" in p for p in _gate(triaged))
    path.write_text(text)
    assert _gate(triaged) == []
    assert not list(tmp_path.glob("*.findings.jsonl"))  # no stored findings at all


def test_the_gate_recomputes_the_verdict(triaged, tmp_path):
    """A triage file that says valid, over samples whose audit is not, fails the gate."""
    out, key, path, _sha, samples, prov = triaged
    path.write_text(_dispose(path.read_text(), rejected=path.parent / "rejected.jsonl"))
    missing = {c["id"] for c in key["canaries"]} - {"canary-q6"}
    bad = [_sample(tmp_path / f"b{i}.jsonl", *_output(out, key, sample=i, canaries=missing))
           for i in (1, 2)]
    header_hashes = ", ".join(tool.sha256_file(f) for f in bad)
    text = path.read_text()
    old = next(line for line in text.splitlines() if line.startswith("- Samples sha256:"))
    path.write_text(text.replace(old, f"- Samples sha256: {header_hashes}"))
    problems = _gate(triaged, samples=bad)
    assert any(p.startswith("the audit is invalid") and "mandatory" in p for p in problems)


def _triage_file(tmp_path, rows):
    header = ("# Class 2 audit triage: scripted\n\n- Auditor family: gemini\n"
              "- Canaries found: 8/8; controls flagged: 0/10; valid: True\n\n"
              "| id | path | question | class | severity | confidence | quote | disposition "
              "| reason |\n|---|---|---|---|---|---|---|---|---|\n")
    path = tmp_path / "triage.md"
    path.write_text(header + "\n".join(rows) + "\n")
    return path


def test_the_release_gate_row_rules(tmp_path):
    """The protocol's release gate: zero untriaged HIGH or MED findings; a valid canary
    score recorded; a charter card is not the kernel's to fix; a severity from the enum."""
    done = ["| f1 | `scripted/tools/a` | Q4 | C1 | MED | low | Read it. | REJECT | x |",
            "| f2 | `scripted/tools/b` | Q5 | C2 | HIGH | both samples | Best. | REJECT "
            "| a formula \\| exact |",
            "| f3 | `scripted/tools/c` | Q8 | C1 | LOW | low | x |  |  |",
            "| f7 | `scripted/tools/d` | Q8 | C1 | LOW | low | x | FIX |  |"]
    assert tool.release_gate(_triage_file(tmp_path, done).read_text()) == []
    open_med = [done[0].replace("| REJECT | x |", "|  |  |")]
    assert any("untriaged MED" in p
               for p in tool.release_gate(_triage_file(tmp_path, open_med).read_text()))
    # FIX is not releasable on a HIGH or MED finding: it is still in the gated corpus.
    fixed = [done[0].replace("| REJECT | x |", "| FIX |  |")]
    assert any("fix, re-render, re-audit" in p
               for p in tool.release_gate(_triage_file(tmp_path, fixed).read_text()))
    charter = ["| f4 | `scripted/charter/cards/c` | Q10 | C2 | MED | low | x | FIX |  |"]
    assert any("not the kernel's to fix" in p
               for p in tool.release_gate(_triage_file(tmp_path, charter).read_text()))
    unreasoned = ["| f5 | `scripted/tools/a` | Q4 | C1 | MED | low | x | ALLOW |  |"]
    assert any("without a reason" in p
               for p in tool.release_gate(_triage_file(tmp_path, unreasoned).read_text()))
    misspelled = ["| f6 | `scripted/tools/a` | Q4 | C1 | Medium | low | x |  |  |"]
    assert any("severity 'Medium'" in p
               for p in tool.release_gate(_triage_file(tmp_path, misspelled).read_text()))
    unrecorded = _triage_file(tmp_path, done).read_text().replace("valid: True", "valid: False")
    assert any("canary score" in p for p in tool.release_gate(unrecorded))


# --- the sweep against the protocol: inputs 4-5 -------------------------------------------------


def _diffed(rendered, history, tmp_path):
    """A release rendered against a previous corpus in which one real leaf had other
    text and another did not exist yet."""
    out, _key = rendered
    repo, base, _surface, head = history
    previous = [json.loads(line)
                for line in (out / "release_corpus.jsonl").read_text().splitlines()]
    kernel = [r for r in previous if r["provenance"] == "kernel"]
    changed, added = kernel[5], kernel[9]
    old = "The old wording."
    prior = [r | {"text": old, "leaf_id": tool.leaf_id(r["path"], old)} if r is changed else r
             for r in previous if r is not added]
    (tmp_path / "prior.jsonl").write_text("\n".join(json.dumps(r) for r in prior) + "\n")
    rejected = json.dumps({"finding_id": "f1", "path": changed["path"], "question": "Q4",
                           "class": "C1", "reason": "a formula"}) + "\n"
    repo, (_root, _early, rel, _later, head), triage = _release_repo(
        tmp_path, tmp_path / "prior.jsonl", rejected=rejected)
    release = tmp_path / "release"
    tool.render([WORLD], release, seed=7, rendered=False, essay=ESSAY,
                release_range=f"{rel}..{head}", repo=repo, previous=triage,
                previous_corpus=tmp_path / "prior.jsonl")
    return release, changed, added


def test_the_corpus_diff_is_rendered_before_the_triage_and_changed_leaves_lead(
        rendered, history, tmp_path):
    """Codex P2 (protocol input 5): the prompt carries the leaf diff against the last
    audited release's corpus, before last release's triage and rejected findings, and the
    auditor's input reads the changed leaves first."""
    release, changed, added = _diffed(rendered, history, tmp_path)
    prompt = (release / "prompt.md").read_text()
    diff = prompt.index("## The corpus diff since the last audited release")
    assert diff < prompt.index("## Last release's triage")
    assert diff < prompt.index("## Last release's rejected findings")
    section = prompt[diff:prompt.index("## Last release's triage")]
    assert f"`{changed['leaf_id']}` changed" in section and "The old wording." in section
    assert f"`{added['leaf_id']}` added" in section
    # The diff names the rejected finding on the changed leaf by its full identity.
    assert "rejected last release: f1 Q4 C1" in section
    assert '"reason": "a formula"' in prompt  # input 4: the rejected findings
    records = _records(release)
    leading = [r for r in records if r["change"] in ("added", "changed")]
    assert records[:len(leading)] == leading  # the changed block leads
    assert {changed["leaf_id"], added["leaf_id"]} <= {r["leaf_id"] for r in leading}
    unchanged = [r for r in records if r["change"] == "unchanged"]
    assert unchanged and all(r["provenance"] == "kernel" for r in unchanged)
    key = json.loads((release / "canary_key.json").read_text())
    assert key["previous_corpus_sha"] == tool.sha256_file(tmp_path / "prior.jsonl")


def test_a_canary_reads_like_its_block_and_the_release_corpus_holds_none(
        rendered, history, tmp_path):
    release, _changed, _added = _diffed(rendered, history, tmp_path)
    key = json.loads((release / "canary_key.json").read_text())
    planted = {c["leaf_id"] for c in key["canaries"]}
    records = _records(release)
    boundary = sum(1 for r in records if r["change"] in ("added", "changed"))
    for i, record in enumerate(records):
        if record["leaf_id"] in planted:
            assert record["change"] == ("added" if i < boundary else "unchanged")
    corpus = (release / "release_corpus.jsonl").read_text()
    assert not any(leaf in corpus for leaf in planted)


def test_the_first_release_says_it_has_no_previous_corpus(rendered):
    out, _key = rendered
    prompt = (out / "prompt.md").read_text()
    assert "(no previous audited corpus" in prompt
    assert {r["change"] for r in _records(out)} <= {"added", "context"}


@pytest.mark.parametrize("name, content, why", [
    ("prior.jsonl", '{"leaf_id": "000000000000", "world": "scripted", "path": "p", '
                    '"text": "t", "provenance": "kernel"}\n', "not the hash"),
    ("prior.jsonl", "not json\n", "not JSON"),
    ("rejected.jsonl", '{"finding_id": "f1", "path": "p"}\n', "line 1 lacks"),
    ("previous.md", "# Something else\n", "not a triage file"),
])
def test_render_refuses_an_unbound_or_malformed_input(rendered, history, tmp_path, name,
                                                      content, why):
    """(iii) validated: every input render reads passes its schema before anything is
    written."""
    repo, base, _surface, head = history
    (tmp_path / name).write_text(content)
    read = {"prior.jsonl": tool.read_corpus,
            "rejected.jsonl": lambda path: tool.read_rejected(path.read_text()),
            "previous.md": lambda path: tool.read_previous_triage(path, [WORLD])}[name]
    with pytest.raises(tool.AuditInputInvalid, match=why):
        read(tmp_path / name)
    if name == "rejected.jsonl":
        return  # read from the release commit, never passed in (the committed test)
    kw = {"prior.jsonl": {"previous_corpus": tmp_path / name},
          "previous.md": {"previous": tmp_path / name}}[name]
    # On a first release a previous file is refused before it is read at all.
    with pytest.raises(tool.AuditInputInvalid, match="the first release has no previous"):
        tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                    release_range=f"{base}..{head}", repo=repo, **kw)
    assert not (tmp_path / "out").exists()


def test_render_refuses_a_range_end_that_is_not_a_commit(history, tmp_path):
    repo, _base, surface, _head = history
    with pytest.raises(ValueError, match="not a commit"):
        tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                    release_range=f"{surface}..no-such-ref", repo=repo)


# --- the prompts are bound to the key, and complete when rendered --------------------------


def test_the_key_is_refused_when_a_prompt_changed(rendered, tmp_path):
    """Codex P2: the key binds both prompts; an edited prompt is not the one rendered."""
    out, _key = rendered
    for name in ("auditor_input.jsonl", "canary_key.json", "prompt.md",
                 "provenance_prompt.md"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    assert tool.load_key(tmp_path / "canary_key.json")
    for name in ("prompt.md", "provenance_prompt.md"):
        original = (tmp_path / name).read_text()
        (tmp_path / name).write_text(original + "An operator's edit.\n")
        with pytest.raises(tool.AuditInputInvalid, match=f"the {name} beside it"):
            tool.load_key(tmp_path / "canary_key.json")
        (tmp_path / name).write_text(original)
    key = json.loads((tmp_path / "canary_key.json").read_text())
    key.pop("prompt_sha")
    (tmp_path / "canary_key.json").write_text(json.dumps(key))
    with pytest.raises(tool.AuditInputInvalid, match="schema"):
        tool.load_key(tmp_path / "canary_key.json")


def test_render_fills_the_authority_text_or_refuses(rendered, history, tmp_path):
    """Codex P2: no prompt is edited after rendering. The essay's text is in the prompt,
    and without an essay (or with one missing a heading) nothing is rendered."""
    out, _key = rendered
    prompt = (out / "prompt.md").read_text()
    assert "Class 1, Class 2 and Class 3 (stand-in)." in prompt
    assert "THE DARK STACK (stand-in)" in prompt and "Not quoted" not in prompt
    assert "[The operator pastes" not in prompt
    repo, base, _surface, head = history
    headless = tmp_path / "essay.md"
    headless.write_text(ESSAY_TEXT.replace("CHAPTER III", "Afterword"))
    for essay, why in ((None, "no essay"), (tmp_path / "absent.md", "no essay"),
                       (headless, "no heading 'CHAPTER III'")):
        with pytest.raises(tool.AuditInputInvalid, match=why):
            tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=essay,
                        release_range=f"{base}..{head}", repo=repo)
        assert not (tmp_path / "out").exists()



# --- the provenance range is the release being audited --------------------------------------


def test_render_binds_the_release_commit_and_refuses_another(rendered, history, tmp_path):
    """Codex P1: the range head is the commit checked out, with no uncommitted
    seat-visible change; the key records it, and the gate re-verifies it."""
    out, key = rendered
    repo, base, surface, head = history
    assert key["release_commit"] == head == key["range_shas"][1]
    with pytest.raises(tool.AuditInputInvalid, match="is not HEAD"):
        tool.render([WORLD], tmp_path / "old", seed=7, rendered=False, essay=ESSAY,
                    release_range=f"{base}..{surface}", repo=repo)
    schematics = repo / "factorylab/cortex/schematics.py"
    original = schematics.read_text()
    schematics.write_text(original + "HOLD_MORE = 'hold more'\n")
    try:
        with pytest.raises(tool.AuditInputInvalid, match="uncommitted seat-visible"):
            tool.render([WORLD], tmp_path / "dirty", seed=7, rendered=False, essay=ESSAY,
                        release_range=f"{base}..{head}", repo=repo)
    finally:
        schematics.write_text(original)
    assert not (tmp_path / "old").exists() and not (tmp_path / "dirty").exists()


def test_the_gate_refuses_a_key_of_another_release(triaged, history):
    out, key, path, _sha, _samples, _prov = triaged
    path.write_text(_dispose(path.read_text(), rejected=path.parent / "rejected.jsonl"))
    assert _gate(triaged) == []
    _repo, base, _surface, _head = history
    assert any("not the release gated" in p for p in _gate(triaged, release=base))


def test_an_invalid_release_ref_is_refused_as_an_invalid_ref(triaged):
    """Sol EXIT-3: a --release that names no commit is refused as such, never compared."""
    with pytest.raises(tool.AuditInputInvalid, match="invalid ref"):
        _gate(triaged, release="no-such-ref")



# --- a finding's identity is its id with its question and class (Codex P2) -----------------


def test_one_quote_under_two_questions_is_two_findings_through_triage_and_gate(
        with_commit, tmp_path, monkeypatch):
    """The same quote at the same path, flagged under Q3 and Q6, is two findings: both
    are unioned, both get a row, both need a disposition, and neither row stands for
    the other."""
    out, key = with_commit
    monkeypatch.setattr(tool, "TRIAGE_DIR", tmp_path)
    real = next(r for r in _records(out) if r["provenance"] == "kernel"
                and r["leaf_id"] not in {c["leaf_id"] for c in key["canaries"] + key["controls"]})
    q3, q6 = _finding(real, "Q3"), _finding(real, "Q6")
    assert q3["finding_id"] == q6["finding_id"]
    samples, prov = _paths(tmp_path, out, key, extra=[q3, q6])
    verdict = tool.audit_verdict([tool.read_output(p) for p in samples],
                                 [tool.read_output(p) for p in prov], key, _records(out))
    assert verdict["valid"], verdict["problems"]
    merged = [f for f in tool.union([tool.read_output(p) for p in samples])
              if f["finding_id"] == q3["finding_id"]]
    assert {(f["question"], f["class"]) for f in merged} == {("Q3", "ANNOUNCED-PHYSICS"),
                                                              ("Q6", "C1")}
    argv = ["triage", *map(str, samples), "--provenance-samples", *map(str, prov),
            "--key", str(out / "canary_key.json"), "--world", WORLD, "--family", "fam-x"]
    assert tool.main(argv) == 0
    path = tmp_path / f"{WORLD}.md"
    rows = [r for r in tool.table_rows(path.read_text()) if r["id"] == q3["finding_id"]]
    assert {(r["question"], r["class"]) for r in rows} == {("Q3", "ANNOUNCED-PHYSICS"),
                                                           ("Q6", "C1")}

    def gate():
        return tool.gate(WORLD, path, out / "canary_key.json", samples, prov,
                         release=key["release_commit"], repo=REPO[0],
                         triage_sha256=tool.sha256_file(path))

    disposed = _dispose(path.read_text(), rejected=path.parent / "rejected.jsonl")
    path.write_text(disposed)
    assert gate() == []
    q6_row = next(line for line in disposed.splitlines()
                  if q3["finding_id"] in line and "| Q6 |" in line)
    path.write_text(disposed.replace(q6_row + "\n", ""))
    assert any("has no row" in p and "'Q6'" in p for p in gate())
    # The Q3 row cannot stand for the Q6 finding twice over.
    q3_row = next(line for line in disposed.splitlines()
                  if q3["finding_id"] in line and "| Q3 |" in line)
    path.write_text(disposed.replace(q6_row, q3_row))
    problems = gate()
    assert any("has 2 rows" in p for p in problems) and any("'Q6'" in p for p in problems)
    # A sample that gives one identity twice is invalid; two questions are not twice.
    rows_, summary = _output(out, key, sample=2, extra=[q3, q3])
    twice = _verdict(out, key, second=(rows_, summary))
    assert any("given twice" in p for p in twice["problems"])



# --- severity is fixed by the question and the leaf's reach (Codex P2) ----------------------


@pytest.mark.parametrize("question, frequency, severity", [
    ("Q4", "every call", "HIGH"), ("Q3", "every judge request", "HIGH"),
    ("Q5", "every wake (stable prefix) or on demand (world.read)", "HIGH"),
    ("Q4", "on refusal", "MED"), ("Q5", "on that refusal or error", "MED"),
    ("Q7", "every call", "MED"), ("Q9", "on refusal", "MED"),
    ("Q6", "unknown", "MED"), ("Q11", "unknown", "LOW"), ("Q12", "unknown", "LOW"),
])
def test_the_severity_is_derived_from_question_and_reach(question, frequency, severity):
    assert tool.required_severity(question, frequency) == severity


def test_a_q3_to_q5_finding_on_an_every_call_leaf_below_high_is_invalid(rendered):
    """Codex P2: a Q4 finding on a system prompt (every call) filed LOW or MED fails its
    sample; on a refusal (on refusal) HIGH fails."""
    out, key = rendered
    records = {r["leaf_id"]: r for r in _records(out)}
    system = next(r for r in records.values() if r["frequency"] == "every call"
                  and r["provenance"] == "kernel" and r["text"].strip())
    refusal = next(r for r in records.values() if r["frequency"] == "on refusal")
    assert not tool.finding_problems(_finding(system, "Q4"), records)
    for low in ("LOW", "MED"):
        problems = tool.finding_problems(_finding(system, "Q4", severity=low), records)
        assert any("is not 'HIGH'" in p for p in problems), problems
    assert tool.finding_problems(_finding(refusal, "Q4", severity="HIGH"), records)
    assert not tool.finding_problems(_finding(refusal, "Q4"), records)



# --- Sol's pass on the audit tooling (b75003b) ----------------------------------------------


def test_exit2_the_baseline_and_registry_are_written_together_or_not_at_all(tmp_path,
                                                                            monkeypatch):
    """Sol EXIT-2: a failure swapping the second file puts the first back: never new
    surfaces with old findings."""
    from tests.audit import class2_audit

    one, two = tmp_path / "surfaces.toml", tmp_path / "findings.json"
    one.write_text("old surfaces\n")
    two.write_text("old findings\n")
    real, calls = os.replace, {"n": 0}

    def failing(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(class2_audit.os, "replace", failing)
    with pytest.raises(OSError, match="disk full"):
        class2_audit.write_together({one: "new surfaces\n", two: "new findings\n"})
    assert (one.read_text(), two.read_text()) == ("old surfaces\n", "old findings\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["findings.json", "surfaces.toml"]


def test_exit1_a_partial_governance_render_raises_by_itself(monkeypatch):
    """Sol EXIT-1: render_governance never returns a partial render to its caller."""
    from factorylab.runtime.loop import Runtime
    from tests.audit import class2_corpus

    def fails(self, *args, **kwargs):
        raise RuntimeError("the committee could not sit")

    monkeypatch.setattr(Runtime, "_testify", fails)
    with pytest.raises(class2_corpus.RenderFailed, match="the committee could not sit"):
        class2_corpus.render_governance(WORLD)


def test_cov2_every_surface_kind_is_described_and_an_unknown_one_is_refused():
    """Sol COV-2: every kind the corpus renders has an audience and a reach; a kind the
    tool does not know is refused, never given an empty audience."""
    from tests.audit import class2_audit as registry

    surfaces = registry.load_surfaces()
    kinds = {s.split("/")[0] for s in surfaces["static"] + surfaces["rendered"]}
    for kind in sorted(kinds | {"charter"}):
        path = {"request": "w/request/judge/inputs/x", "genesis": "w/genesis/seat/lens",
                "seat_text": "kernel/seat_text/sink/x/abcd"}.get(kind, f"w/{kind}/x")
        described = tool.describe(path)
        assert described["audience"] and described["frequency"] != "unknown", kind
    with pytest.raises(tool.AuditInputInvalid, match="unknown surface kind"):
        tool.describe("w/inference_hint/x")


def test_bnd2_the_canary_score_is_the_headers_field_not_a_substring():
    """Sol BND-2: a quote reading "valid: True" does not stand in for the header."""
    body = ("# Class 2 audit triage: scripted\n\n- Auditor family: gemini\n"
            "- Canaries found: 6/8; controls flagged: 0/10; valid: False\n\n"
            "| id | path | question | class | severity | confidence | quote | disposition "
            "| reason |\n|---|---|---|---|---|---|---|---|---|\n"
            "| f1 | `scripted/tools/a` | Q8 | C1 | LOW | low | valid: True |  |  |\n")
    assert any("canary score" in p for p in tool.release_gate(body))
    assert not any("canary score" in p for p in tool.release_gate(
        body.replace("6/8; controls flagged: 0/10; valid: False",
                     "8/8; controls flagged: 0/10; valid: True")))


def test_bnd3_the_previous_corpus_is_required_and_verified(rendered, history, tmp_path):
    """Sol BND-3: the key names the previous corpus it diffed against; a key without the
    field, or a previous corpus changed since, is refused."""
    out, _key = rendered
    prior = tmp_path / "prior.jsonl"
    prior.write_bytes((out / "release_corpus.jsonl").read_bytes())
    repo, (_root, _early, rel, _later, head), triage = _release_repo(tmp_path, prior)
    release = tmp_path / "release"
    tool.render([WORLD], release, seed=7, rendered=False, essay=ESSAY,
                release_range=f"{rel}..{head}", repo=repo, previous=triage,
                previous_corpus=prior)
    key, _records = tool.load_key(release / "canary_key.json")
    assert key["previous_corpus"] == str(prior.resolve())
    prior.write_text(prior.read_text() + "\n")
    with pytest.raises(tool.AuditInputInvalid, match="previous corpus"):
        tool.load_key(release / "canary_key.json")
    bare = json.loads((out / "canary_key.json").read_text())
    bare.pop("previous_corpus")
    for name in ("auditor_input.jsonl", "prompt.md", "provenance_prompt.md"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    (tmp_path / "canary_key.json").write_text(json.dumps(bare))
    with pytest.raises(tool.AuditInputInvalid, match="records no previous corpus"):
        tool.load_key(tmp_path / "canary_key.json")


def test_rub2_a_malformed_provenance_finding_invalidates_the_audit(with_commit,
                                                                   monkeypatch):
    """Sol RUB-2: provenance findings pass their own schema at validate time."""
    out, key = with_commit
    sha = _surface_commit(key)
    good = [_provenance(key, sample=i, flag={sha}) for i in (1, 2)]
    assert _verdict(out, key, provenance=good)["valid"]
    original = tool.provenance_findings

    def malformed(samples):
        return [{**f, "severity": "LOW", "quote": ""} for f in original(samples)]

    monkeypatch.setattr(tool, "provenance_findings", malformed)
    verdict = _verdict(out, key, provenance=good)
    assert not verdict["valid"]
    assert any(p.startswith(f"provenance finding commit:{sha}") for p in verdict["problems"])


def test_can1_controls_rotate_with_the_release_and_repeat_within_it(rendered):
    """Sol CAN-1: controls are drawn by the release commit: reproducible for one release,
    different across releases."""
    out, key = rendered
    planted = {c["leaf_id"] for c in key["canaries"]}
    base = [r for r in _records(out) if r["leaf_id"] not in planted]
    spec = tool.load_canaries((ROOT / tool.CANARIES_REL).read_text())
    _, one = tool.plant(base, seed=7, world=WORLD, control_seed="a" * 40, spec=spec)
    _, again = tool.plant(base, seed=7, world=WORLD, control_seed="a" * 40, spec=spec)
    _, other = tool.plant(base, seed=7, world=WORLD, control_seed="b" * 40, spec=spec)
    assert one["controls"] == again["controls"]
    assert one["controls"] != other["controls"]


def test_can2_a_canary_is_added_text_inside_the_changed_block(rendered, history, tmp_path):
    """Sol CAN-2: a canary reads as new text wherever it lands, and it lands inside the
    block of added and changed leaves, so no added leaf stands outside that block."""
    release, _changed, _added = _diffed(rendered, history, tmp_path)
    key = json.loads((release / "canary_key.json").read_text())
    planted = {c["leaf_id"] for c in key["canaries"]}
    records = _records(release)
    boundary = sum(1 for r in records if r["change"] in ("added", "changed"))
    assert all(r["change"] in ("added", "changed") for r in records[:boundary])
    assert not any(r["change"] in ("added", "changed") for r in records[boundary:])
    assert all(records[i]["change"] == "added" for i in range(len(records))
               if records[i]["leaf_id"] in planted)


def test_can3_missing_the_q7_canary_invalidates_the_audit(rendered):
    """The architect's ruling: Q7 (false physics) is mandatory."""
    out, key = rendered
    found = {c["id"] for c in key["canaries"]} - {"canary-q7"}
    verdict = _verdict(out, key, canaries=found)
    assert not verdict["valid"] and verdict["canaries_found"] == "7/8"
    assert any("mandatory" in p for p in verdict["problems"])



def test_bnd1_a_disposition_must_be_one_the_rubric_allows_and_the_protocol_backs(triaged):
    """Sol BND-1: the gate reads each disposition against the finding it names: CHARTER is
    never a kernel leaf's, ALLOW needs an allowlist entry covering the finding, REJECT a
    rejected.jsonl record, a provenance finding is FIX or REJECT; and the triage file is
    the one reviewed (its sha256 is a gate input)."""
    out, key, path, sha, samples, prov = triaged
    rejected = path.parent / "rejected.jsonl"
    text = path.read_text()
    reviewed = _dispose(text, rejected=rejected)
    path.write_text(reviewed)
    assert _gate(triaged) == []
    kernel_row = next(line for line in reviewed.splitlines() if "| Q4 | C1 | HIGH |" in line)
    commit_row = next(line for line in reviewed.splitlines() if f"commit:{sha}" in line)
    for row, replacement, why in (
            (kernel_row, "| CHARTER |", "not a disposition the rubric allows"),
            (kernel_row, "| ALLOW |", "no allowlist entry"),
            (commit_row, "| ALLOW |", "not a disposition the rubric allows")):
        path.write_text(reviewed.replace(row, row.replace("| REJECT |", replacement)))
        assert any(why in p for p in _gate(triaged)), why
    # A REJECT with no rejected.jsonl record is not the protocol's REJECT.
    path.write_text(reviewed)
    expected = tool.world_findings(
        tool.union([tool.read_output(f) for f in samples]),
        tool.provenance_findings([tool.read_output(f) for f in prov]), key, WORLD)
    assert any("not recorded in rejected.jsonl" in p for p in tool.disposition_problems(
        reviewed, expected, allowlist={"allow": []}, rejected=[]))
    # An edit after review is refused: the reviewed sha256 is a gate input.
    _dispose(text, rejected=rejected)
    as_reviewed = hashlib.sha256(reviewed.encode()).hexdigest()
    path.write_text(reviewed.replace("the auditor misread", "the auditor misread twice"))
    assert any("not the one reviewed" in p for p in _gate(triaged, reviewed=as_reviewed))
    with pytest.raises(tool.AuditInputInvalid, match="reviewed triage"):
        tool.gate(WORLD, path, out / "canary_key.json", samples, prov,
                  release=key["release_commit"], repo=REPO[0])


# --- Codex pass on 11ea116: the range starts at the last release; identity everywhere -----


@pytest.fixture(scope="module")
def released(tmp_path_factory, rendered):
    """A repository whose last audited release is ``rel`` (``LAST_RELEASE`` committed at
    the head, with its gated release corpus and triage file), with one surface commit
    before it and one after. Returns the repository, its commits and the two files."""
    where = tmp_path_factory.mktemp("released")
    prior = where / "prior.jsonl"
    prior.write_bytes((rendered[0] / "release_corpus.jsonl").read_bytes())
    repo, (root, early, rel, later, head), triage = _release_repo(where, prior)
    return repo, root, early, rel, later, head, prior, triage


def _previous(released):
    """The previous files a render of ``released``'s next release must name."""
    return {"previous": released[7], "previous_corpus": released[6]}


def test_the_range_base_is_the_last_audited_release(released, tmp_path):
    """Codex P1 (class2_audit.py:670): a base after the last release (``HEAD^``) would
    hide the commits between them from the provenance pass; the base is the release
    ``LAST_RELEASE`` names as committed at the head, and nothing else."""
    repo, root, early, rel, later, head, *_files = released
    for base in (later, root, early):
        with pytest.raises(tool.AuditInputInvalid, match="not the last audited release"):
            tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                        release_range=f"{base}..{head}", repo=repo, **_previous(released))
        assert not (tmp_path / "out").exists()
    key = tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{rel}..{head}", repo=repo, **_previous(released))
    assert [c["sha"] for c in key["provenance_commits"]] == [later]
    assert tool.range_problems(repo, key) == []


def test_a_release_after_the_first_needs_the_gated_previous_files(released, tmp_path):
    """Codex P1 (class2_audit.py:782): after the first release ``--previous`` and
    ``--previous-corpus`` are required, and each must be the file the last release's
    gate recorded (``LAST_RELEASE``: the corpus digest and the world's triage digest);
    a schema-valid corpus of another release, or another triage, is refused."""
    repo, _root, _early, rel, _later, head, prior, triage = released
    render = lambda **kw: tool.render([WORLD], tmp_path / "out", seed=7,  # noqa: E731
                                      rendered=False, essay=ESSAY,
                                      release_range=f"{rel}..{head}", repo=repo, **kw)
    for kw in ({}, {"previous": triage}, {"previous_corpus": prior}):
        with pytest.raises(tool.AuditInputInvalid, match="needs the last release's"):
            render(**kw)
    other = tmp_path / "other.jsonl"
    other.write_text("\n".join(prior.read_text().splitlines()[1:]) + "\n")
    assert tool.read_corpus(other)  # schema-valid, and still not the release's corpus
    with pytest.raises(tool.AuditInputInvalid, match="release corpus the last release"):
        render(previous=triage, previous_corpus=other)
    edited = tmp_path / f"{WORLD}.md"
    edited.write_text(triage.read_text() + "\n")
    with pytest.raises(tool.AuditInputInvalid, match="triage file of 'scripted'"):
        render(previous=edited, previous_corpus=prior)
    assert not (tmp_path / "out").exists()


# --- Codex pass on 4024237: FIX is not releasable --------------------------------------------


def test_a_fix_disposition_fails_until_the_fixed_leaf_is_re_rendered_and_re_audited(
        triaged, history, tmp_path, monkeypatch):
    """Codex P1 (class2_audit.py:1516): the key audits the very commit being gated, so a
    HIGH or MED finding marked FIX still ships, and the gate fails ("fix, re-render,
    re-audit"). Once the leaf is changed and the corpus re-rendered, the finding is
    gone from the new audit and the release gates on the remaining dispositions."""
    out, key, path, sha, _samples, _prov = triaged
    rejected = path.parent / "rejected.jsonl"
    reviewed = _dispose(path.read_text(), rejected=rejected)
    kernel_row = next(line for line in reviewed.splitlines() if "| Q4 | C1 | HIGH |" in line)
    fixed = reviewed.replace(kernel_row, kernel_row.replace("| REJECT |", "| FIX |"))
    path.write_text(fixed)
    assert any("fix, re-render, re-audit" in p
               for p in _gate(triaged, reviewed=tool.sha256_file(path)))
    # The fix: the leaf the finding quoted now reads otherwise, and the corpus is
    # rendered again from it.
    leaf = tool._cells(kernel_row)[1].strip("`")
    original = tool.corpus_records

    def fixed_corpus(worlds, *, rendered):
        records = original(worlds, rendered=rendered)
        return [r | {"text": "A declarative fact.",
                     "leaf_id": tool.leaf_id(r["path"], "A declarative fact.")}
                if r["path"] == leaf else r for r in records]

    monkeypatch.setattr(tool, "corpus_records", fixed_corpus)
    repo, base, _surface, head = history
    again = tmp_path / "again"
    key2 = tool.render([WORLD], again, seed=7, rendered=False, essay=ESSAY,
                       release_range=f"{base}..{head}", repo=repo)
    assert leaf in {r["path"] for r in _records(again)}
    samples, prov = _paths(tmp_path / "again", again, key2, flag={sha})
    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.setattr(tool, "TRIAGE_DIR", second)
    argv = ["triage", *map(str, samples), "--provenance-samples", *map(str, prov),
            "--key", str(again / "canary_key.json"), "--world", WORLD, "--family", "fam-x"]
    assert tool.main(argv) == 0
    retriaged = second / f"{WORLD}.md"
    assert "| Q4 | C1 | HIGH |" not in retriaged.read_text()  # the fixed finding is gone
    rejected2 = second / "rejected.jsonl"
    retriaged.write_text(_dispose(retriaged.read_text(), rejected=rejected2))
    problems = tool.gate(WORLD, retriaged, again / "canary_key.json", samples, prov,
                         release=key2["release_commit"], repo=REPO[0],
                         triage_sha256=tool.sha256_file(retriaged))
    assert problems == []


# --- REVERTED: a true behaviour-mix commit whose seat-visible text is gone --------------


def _flagged_repo(tmp_path):
    """A repository with a behaviour-mix commit adding seat-visible text, and a later
    commit removing it: (repo, flagged, still, reverted)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n", "root")
    flagged = _commit(repo, "factorylab/cortex/schematics.py",
                      "HOLD = 'hold'\nADVICE = 'you should hold when unsure'\n)\n",
                      BEHAVIOUR_MIX)
    still = _commit(repo, "docs/notes.md", "notes\n", "notes only")
    reverted = _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n)\n",
                       "Revert the advice")
    return repo, flagged, still, reverted


def _provenance_row(sha, disposition):
    path = f"commit:{sha}"
    finding = {"finding_id": tool.leaf_id(path, ""), "path": path, "question": "P1",
               "class": "BEHAVIOUR-MIX", "severity": "HIGH", "provenance_pass": True}
    row = (f"| {finding['finding_id']} | `{path}` | P1 | BEHAVIOUR-MIX | HIGH | both "
           f"samples | {BEHAVIOUR_MIX[:20]} | {disposition} | undone |")
    return finding, row


def test_a_flagged_commit_whose_text_is_gone_passes_as_reverted(tmp_path):
    """A true behaviour-mix commit stays in the range, but when every seat-visible line it
    added is gone from the release (whitespace-normalised; a line of punctuation carries
    no text), REVERTED is backed, recomputed from the repository."""
    repo, flagged, still, reverted = _flagged_repo(tmp_path)
    assert tool.reverted_problems(repo, flagged, reverted) == []
    finding, row = _provenance_row(flagged, "REVERTED")
    text = _triage_file(tmp_path, [row]).read_text()
    assert tool.release_gate(text, expected=[finding]) == []
    assert tool.disposition_problems(text, [finding], allowlist={"allow": []}, rejected=[],
                                     repo=repo, release=reverted) == []


def test_a_flagged_commit_whose_text_is_still_present_fails_reverted(tmp_path):
    repo, flagged, still, _reverted = _flagged_repo(tmp_path)
    problems = tool.reverted_problems(repo, flagged, still)
    assert problems and "you should hold when unsure" in problems[0]
    finding, row = _provenance_row(flagged, "REVERTED")
    text = _triage_file(tmp_path, [row]).read_text()
    assert any("REVERTED, but" in p for p in tool.disposition_problems(
        text, [finding], allowlist={"allow": []}, rejected=[], repo=repo, release=still))


def test_a_flagged_line_moved_to_another_seat_visible_module_fails_reverted(tmp_path):
    """Moving the flagged text is not reverting it: each added line is looked for in
    every seat-visible file at the release (the corpus's scope), not only its own."""
    repo, flagged, _still, reverted = _flagged_repo(tmp_path)
    moved = _commit(repo, "worlds/advice.toml",
                    "  ADVICE   =  'you should hold when unsure'\n", "Move the advice")
    problems = tool.reverted_problems(repo, flagged, moved)
    assert problems and "worlds/advice.toml" in problems[0]
    assert tool.reverted_problems(repo, flagged, reverted) == []
    # Outside the seat-visible scope the line is no seat's text.
    _git(repo, "rm", "-q", "worlds/advice.toml")
    _git(repo, "commit", "-q", "-m", "Drop the moved advice")
    elsewhere = _commit(repo, "docs/advice.md", "ADVICE = 'you should hold when unsure'\n",
                        "Keep the advice in the docs")
    assert tool.reverted_problems(repo, flagged, elsewhere) == []


def _schematics_repo(tmp_path, *texts):
    """A repository whose seat-visible file takes each of ``texts`` in turn, one commit
    each: (repo, [commit, ...])."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo, [_commit(repo, "factorylab/cortex/schematics.py", text, f"step {i}")
                  for i, text in enumerate(texts)]


DISCLOSURE = "FEE = 'every fill pays the venue fee'\n"


def test_a_deletion_only_commit_fails_reverted_until_the_line_is_restored(tmp_path):
    """Codex P1 (class2_audit.py:1974): a flagged commit that deletes seat-visible text
    (a disclosure) adds nothing, yet its effect stands while the deletion does. REVERTED
    holds only once the deleted line is back somewhere in the seat-visible scope."""
    repo, (_root, flagged, restored) = _schematics_repo(
        tmp_path, "HOLD = 'hold'\n" + DISCLOSURE, "HOLD = 'hold'\n",
        "HOLD = 'hold'\n" + DISCLOSURE)
    problems = tool.reverted_problems(repo, flagged, flagged)
    assert problems and "every fill pays the venue fee" in problems[0]
    assert "not back" in problems[0]
    assert tool.reverted_problems(repo, flagged, restored) == []
    # Back in another seat-visible file counts; back outside the scope does not.
    _commit(repo, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n", "drop it again")
    docs = _commit(repo, "docs/fees.md", DISCLOSURE, "fees in the docs")
    assert tool.reverted_problems(repo, flagged, docs)
    world = _commit(repo, "worlds/fees.toml", "  " + DISCLOSURE, "fees in a world")
    assert tool.reverted_problems(repo, flagged, world) == []


def test_a_replacement_passes_reverted_only_with_the_old_line_back_and_the_new_gone(
        tmp_path):
    """A flagged replacement is two changes: its new line must be gone AND its old line
    back. Either half alone still stands."""
    old, new = DISCLOSURE, "FEE = 'fees are small, trade freely'\n"
    repo, (_root, flagged, both_gone, both_back, reverted) = _schematics_repo(
        tmp_path, "HOLD = 'hold'\n" + old, "HOLD = 'hold'\n" + new, "HOLD = 'hold'\n",
        "HOLD = 'hold'\n" + old + new, "HOLD = 'hold'\n" + old)
    assert any("not back" in p for p in tool.reverted_problems(repo, flagged, both_gone))
    assert any("still in" in p for p in tool.reverted_problems(repo, flagged, both_back))
    assert tool.reverted_problems(repo, flagged, reverted) == []


def test_reverted_on_a_corpus_finding_is_refused(tmp_path):
    finding = {"finding_id": "f1", "path": "scripted/tools/a", "question": "Q4",
               "class": "C1", "severity": "HIGH"}
    row = "| f1 | `scripted/tools/a` | Q4 | C1 | HIGH | low | Read it. | REVERTED | gone |"
    text = _triage_file(tmp_path, [row]).read_text()
    assert any("REVERTED is not a disposition the rubric allows" in p
               for p in tool.disposition_problems(text, [finding], allowlist={"allow": []},
                                                  rejected=[]))


def test_the_gate_recomputes_reverted_from_the_repository(triaged):
    """The triage row is never trusted: the history's flagged commit added ``HOLD =
    'hold'``, which is still at the release, so REVERTED on it fails the gate."""
    _out, _key, path, sha, _samples, _prov = triaged
    reviewed = _dispose(path.read_text(), rejected=path.parent / "rejected.jsonl")
    commit_row = next(line for line in reviewed.splitlines() if f"commit:{sha}" in line)
    path.write_text(reviewed.replace(commit_row,
                                     commit_row.replace("| REJECT |", "| REVERTED |")))
    assert any("REVERTED, but" in p and "HOLD = 'hold'" in p
               for p in _gate(triaged, reviewed=tool.sha256_file(path)))


# --- Codex pass on 1de5c37: last_release edited only by gate-recording commits; rotation --


@pytest.mark.parametrize("edit", ["beside another file", "naming another release"])
def test_a_range_with_a_commit_editing_last_release_is_refused(rendered, tmp_path, edit):
    """Codex P2 (class2_audit.py:579): only a gate-recording commit (``LAST_RELEASE`` and
    triage files alone, its record naming its parent, the release it gated) may edit the
    record; the record in force must be the most recent one's. A commit that edits it
    alongside other files, or re-points it, is refused."""
    prior = tmp_path / "prior.jsonl"
    prior.write_bytes((rendered[0] / "release_corpus.jsonl").read_bytes())
    repo, (_root, _early, rel, later, _head), _triage = _release_repo(tmp_path, prior)
    record = json.loads((repo / tool.LAST_RELEASE).read_text())
    if edit == "beside another file":
        (repo / "docs/notes.md").write_text("edited\n")
        _git(repo, "add", "docs/notes.md")
    else:
        record["release_commit"] = later
    (repo / tool.LAST_RELEASE).write_text(json.dumps(record | {"note": edit}) + "\n")
    _git(repo, "add", tool.LAST_RELEASE)
    _git(repo, "commit", "-q", "-m", "an unreviewed edit")
    head = _git(repo, "rev-parse", "HEAD")
    base = later if edit == "naming another release" else rel
    with pytest.raises(tool.AuditInputInvalid, match="gate-recording commit"):
        tool.release_base(repo, base, head)


def test_before_the_first_release_no_commit_may_have_edited_last_release(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    root = _commit(repo, "README.md", "x\n", "root")
    _commit(repo, tool.LAST_RELEASE, json.dumps({"release_commit": root}), "added")
    _git(repo, "rm", "-q", tool.LAST_RELEASE)
    _git(repo, "commit", "-q", "-m", "removed")
    head = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(tool.AuditInputInvalid, match="outside a gate-recording commit"):
        tool.release_base(repo, root, head)


def test_the_gate_rechecks_rotation_against_the_prior_releases_triage(rendered, tmp_path,
                                                                     triaged, monkeypatch):
    """Codex P2 (class2_audit.py:1605): rotation is re-read at gate time from the prior
    release's gated triage of the world (from its gate-recording commit, verified against
    the recorded digest): the family it records may not audit the next release."""
    prior = tmp_path / "prior.jsonl"
    prior.write_bytes((rendered[0] / "release_corpus.jsonl").read_bytes())
    repo, (*_commits, head), _triage = _release_repo(tmp_path, prior,
                                                     family="google/gemini-3")
    key = {"release_commit": head}
    assert any("the family rotates" in p for p in
               tool.rotation_problems(repo, key, WORLD, "gemini-3-flash"))
    assert tool.rotation_problems(repo, key, WORLD, "mistralai/mistral-large") == []
    assert tool.rotation_problems(repo, key, "another-world", "gemini-3-flash") == []
    # The gate runs it: a rotation refusal is a gate problem.
    monkeypatch.setattr(tool, "rotation_problems", lambda *a: ["rotation checked"])
    assert any("rotation checked" in p for p in _gate(triaged))


def test_the_gate_record_adds_each_world_of_one_release(tmp_path):
    key = {"release_commit": "a" * 40, "release_corpus_sha": "b" * 64}
    tool.write_last_release(tmp_path, key, world="w1", triage_sha256="c" * 64)
    path = tool.write_last_release(tmp_path, key, world="w2", triage_sha256="d" * 64)
    assert json.loads(path.read_text())["triages"] == {"w1": "c" * 64, "w2": "d" * 64}
    later = key | {"release_commit": "e" * 40}
    tool.write_last_release(tmp_path, later, world="w1", triage_sha256="f" * 64)
    assert json.loads(path.read_text())["triages"] == {"w1": "f" * 64}


def test_the_first_release_starts_at_the_repository_root(history, tmp_path):
    """Before any release is recorded, the range starts at the root, and the root's own
    commit is in the provenance pass."""
    repo, base, surface, head = history
    with pytest.raises(tool.AuditInputInvalid, match="not the repository root"):
        tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                    release_range=f"{surface}..{head}", repo=repo)
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q")
    first = _commit(root, "factorylab/cortex/schematics.py", "HOLD = 'hold'\n", "first")
    tip = _commit(root, "docs/notes.md", "notes\n", "notes")
    assert tool.release_base(root, first, tip) is True
    assert [c["sha"] for c in tool.provenance_commits(root, f"{first}..{tip}",
                                                      from_root=True)] == [first]


def test_a_malformed_or_foreign_last_release_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    root = _commit(repo, "README.md", "x\n", "root")
    head = _commit(repo, tool.LAST_RELEASE, "HEAD~1\n", "not a sha")
    with pytest.raises(tool.AuditInputInvalid, match="not the gate's record"):
        tool.release_base(repo, root, head)
    head = _commit(repo, tool.LAST_RELEASE, json.dumps({"release_commit": "HEAD~1"}),
                   "not a sha either")
    with pytest.raises(tool.AuditInputInvalid, match="not one commit SHA"):
        tool.release_base(repo, root, head)
    head = _commit(repo, tool.LAST_RELEASE, json.dumps({"release_commit": root}),
                   "a release with no digests")
    with pytest.raises(tool.AuditInputInvalid, match="no release corpus digest"):
        tool.release_base(repo, root, head)
    main = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side", root)
    stray = _commit(repo, "README.md", "y\n", "a commit on another line")
    _git(repo, "checkout", "-q", main)
    record = {"release_commit": stray, "release_corpus_sha": "0" * 64,
              "triages": {WORLD: "1" * 64}}
    head = _commit(repo, tool.LAST_RELEASE, json.dumps(record), "a release not in this history")
    with pytest.raises(tool.AuditInputInvalid, match="not a commit before"):
        tool.release_base(repo, stray, head)


def test_the_gate_re_verifies_the_range_against_the_last_release(released, tmp_path):
    """The gate recomputes the range's base from the release commit and the provenance
    prompt from the range: a key naming another base, or a prompt of another range, is
    refused at gate time."""
    repo, _root, _early, rel, later, head, *_files = released
    key = tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                      release_range=f"{rel}..{head}", repo=repo, **_previous(released))
    assert tool.range_problems(repo, key) == []
    # The key names the gated previous files the render was bound to.
    assert key["previous_corpus_sha"] == tool.sha256_file(released[6])
    assert key["previous_triage_sha256"] == tool.sha256_file(released[7])
    for field in ("previous_corpus_sha", "previous_triage_sha256"):
        other = key | {field: "2" * 64}
        assert any("last release's gate recorded" in p
                   for p in tool.range_problems(repo, other)), field
    moved = key | {"range_shas": [later, head], "range": f"{later}..{head}"}
    assert any("not the last audited release" in p for p in tool.range_problems(repo, moved))
    other = key | {"provenance_id": "0" * 64}
    assert any("provenance prompt" in p for p in tool.range_problems(repo, other))


def test_a_previous_triage_must_be_the_last_releases(released, tmp_path):
    """``--previous`` is the last release's triage: one recording another release is
    refused, and so is any on the first release."""
    repo, root, early, rel, _later, head, prior, _triage = released
    stale = tmp_path / f"{WORLD}.md"
    stale.write_text(_triage_text(root, early))
    with pytest.raises(tool.AuditInputInvalid, match="the last release's gate recorded"):
        tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=ESSAY,
                    release_range=f"{rel}..{head}", repo=repo, previous=stale,
                    previous_corpus=prior)
    assert tool.triage_release(_triage_text(root, rel)) == rel


def test_a_rejected_record_backs_only_the_finding_of_its_question_and_class(triaged):
    """Codex P2 (class2_audit.py:1299): rejected.jsonl names a finding by its full
    identity. A REJECT recorded for the same quote under another question does not back
    this one's."""
    _out, key, path, _sha, samples, prov = triaged
    reviewed = _dispose(path.read_text())
    path.write_text(reviewed)
    assert _gate(triaged, reviewed=tool.sha256_file(path)) == []
    expected = tool.world_findings(
        tool.union([tool.read_output(f) for f in samples]),
        tool.provenance_findings([tool.read_output(f) for f in prov]), key, WORLD)
    rows = [{"finding_id": f["finding_id"], "path": f["path"], "question": f["question"],
             "class": f["class"], "reason": "r"} for f in expected]
    moved = [r | {"question": "Q6"} if r["question"] == "Q4" else r for r in rows]
    problems = tool.disposition_problems(reviewed, expected, allowlist={"allow": []},
                                         rejected=moved)
    assert any("Q4" in p and "not recorded in rejected.jsonl" in p for p in problems)


def _q4_allowed(triaged):
    """The triage file with the kernel Q4 finding marked ALLOW, and that finding."""
    _out, _key, path, _sha, samples, _prov = triaged
    reviewed = _dispose(path.read_text())
    findings = [json.loads(line) for line in samples[0].read_text().splitlines()]
    row = next(line for line in reviewed.splitlines() if "| Q4 | C1 | HIGH |" in line)
    finding = next(f for f in findings if f.get("finding_id") == tool._cells(row)[0]
                   and f.get("question") == "Q4")
    return reviewed.replace(row, row.replace("| REJECT |", "| ALLOW |")), finding


def test_an_allowlist_entry_backs_only_the_finding_of_its_question_and_class(triaged):
    text, finding = _q4_allowed(triaged)
    expected = [finding]
    for question, ok in (("Q6", False), ("Q4", True)):
        entry = {"path": finding["path"], "quote": finding["quote"], "question": question,
                 "class": "C1"}
        problems = tool.disposition_problems(text, expected, rejected=[],
                                             allowlist={"collocation": [], "allow": [entry]})
        assert any("no allowlist entry" in p for p in problems) is not ok, problems


def test_no_policy_or_evidence_read_bypasses_the_release_commit():
    """The class (Codex P1, class2_audit.py:1781): every policy or evidence file is read
    through ``committed_text`` from the release commit. A worktree read survives only
    where the file is not policy: the essay (never committed; its sha256 is in the key),
    files bound by digest (samples, key, corpus, previous corpus and triage, the triage
    file gated), the record the gate writes, and the corpus render (``release_commit``
    pins the worktree to the release). A new worktree read anywhere else fails here."""
    import ast
    import inspect

    source = inspect.getsource(tool)
    reads = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            body = ast.get_source_segment(source, node)
            found = [p for p in ("read_text(", "read_bytes(", "raw_world(", "corpus.WORLDS",
                                 "lexicon.ALLOWLIST", "load_allowlist(")
                     if p in body]
            if found:
                reads[node.name] = found
    assert reads == {
        "authority_text": ["read_text("],       # the essay: never committed
        "sha256_file": ["read_bytes("],         # digests, of any file
        "_jsonl": ["read_text("],               # samples, corpus, previous corpus
        "read_previous_triage": ["read_text("],  # bound by the recorded digest
        "previous_problems": ["read_text("],     # the same file, digest-checked
        "load_key": ["read_text("],              # bound to its corpus and prompts
        "calibration_problems": ["read_bytes("],  # the input, compared to its recompute
        "beside_text": ["read_bytes("],          # a prompt, compared to its recompute
        "gate": ["read_text("],                  # the triage file, by its sha256
        "write_last_release": ["read_text("],    # the record the gate writes
        "corpus_records": ["raw_world("],        # the render, pinned by release_commit
        "render": ["corpus.WORLDS"],             # the renderer's own world files
    }, reads
    for gone in ("CANARIES", "REJECTED", "PROTOCOL"):
        assert not hasattr(tool, gone), gone


# --- Codex pass on 2c85f43: the key's calibration is recomputed, never trusted -----------


def _key_copy(rendered, tmp_path):
    out, _key = rendered
    for name in ("auditor_input.jsonl", "canary_key.json", "prompt.md",
                 "provenance_prompt.md", "release_corpus.jsonl"):
        (tmp_path / name).write_bytes((out / name).read_bytes())
    return tmp_path / "canary_key.json"


@pytest.mark.parametrize("edit", ["q7 removed", "seven canaries", "q7 not mandatory",
                                  "no controls"])
def test_a_key_whose_calibration_is_not_the_releases_is_refused(rendered, tmp_path,
                                                                capsys, edit):
    """Codex P1 (class2_audit.py:1188): the canaries and controls are planted again from
    the release's committed canaries.json, the key's seed and release commit; a key that
    differs (a canary removed, a flag flipped, the controls emptied) is refused at
    validate and gate, however valid its leaf ids."""
    path = _key_copy(rendered, tmp_path)
    assert tool.load_calibrated_key(path, REPO[0])
    key = json.loads(path.read_text())
    if edit == "q7 removed":
        key["canaries"] = [c for c in key["canaries"] if c["question"] != "Q7"]
    elif edit == "seven canaries":
        key["canaries"] = [c for c in key["canaries"] if c["question"] != "Q3"]
    elif edit == "q7 not mandatory":
        key["canaries"] = [c | {"mandatory": False} if c["question"] == "Q7" else c
                           for c in key["canaries"]]
    else:
        key["controls"] = []
    path.write_text(json.dumps(key))
    tool.load_key(path)  # every leaf id still exists: the old check passed it
    with pytest.raises(tool.AuditInputInvalid, match="not the ones the release plants"):
        tool.load_calibrated_key(path, REPO[0])
    out, _ = rendered
    argv = ["validate", "a.jsonl", "b.jsonl", "--provenance-samples", "c.jsonl", "d.jsonl",
            "--key", str(path)]
    assert tool.main(argv) == 2
    assert "not the ones the release plants" in capsys.readouterr().err


def test_an_auditor_input_missing_an_ordinary_leaf_is_refused(rendered, tmp_path):
    """Codex P1 (class2_audit.py:1218): the whole auditor input is recomputed and compared
    byte for byte. One ordinary leaf removed from auditor_input.jsonl, with the key's
    digests and expected leaves rewritten to match, is still refused."""
    path = _key_copy(rendered, tmp_path)
    key = json.loads(path.read_text())
    planted = {c["leaf_id"] for c in key["canaries"] + key["controls"]}
    lines = (tmp_path / "auditor_input.jsonl").read_text().splitlines()
    victim = next(i for i, line in enumerate(lines)
                  if json.loads(line)["provenance"] == "kernel"
                  and json.loads(line)["leaf_id"] not in planted)
    gone = json.loads(lines.pop(victim))["leaf_id"]
    (tmp_path / "auditor_input.jsonl").write_text("\n".join(lines) + "\n")
    key["corpus_sha"] = tool.sha256_file(tmp_path / "auditor_input.jsonl")
    key["expected_leaves"] = [leaf for leaf in key["expected_leaves"] if leaf != gone]
    key["expected_count"] = len(key["expected_leaves"])
    path.write_text(json.dumps(key))
    tool.load_key(path)  # consistent with itself: the old checks passed it
    with pytest.raises(tool.AuditInputInvalid, match="expected_leaves|auditor_input"):
        tool.load_calibrated_key(path, REPO[0])
    # The same leaves in another order (the key's digest rewritten) are refused too.
    path = _key_copy(rendered, tmp_path)
    key = json.loads(path.read_text())
    lines = (tmp_path / "auditor_input.jsonl").read_text().splitlines()
    lines[-1], lines[-2] = lines[-2], lines[-1]
    (tmp_path / "auditor_input.jsonl").write_text("\n".join(lines) + "\n")
    key["corpus_sha"] = tool.sha256_file(tmp_path / "auditor_input.jsonl")
    path.write_text(json.dumps(key))
    with pytest.raises(tool.AuditInputInvalid, match="auditor_input.jsonl beside the key"):
        tool.load_calibrated_key(path, REPO[0])


# --- Codex pass on 38b4789: the gate trusts nothing it can recompute from the release ----


def _jsonl_of(records):
    """A corpus written as render writes it."""
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)


def test_a_leaf_removed_from_both_corpora_and_the_key_is_refused(rendered, tmp_path):
    """Codex P1 (class2_audit.py:1196): an ordinary leaf removed from the release corpus
    and the auditor input together, planted again and the key rewritten to match, is
    self-consistent; the corpus the release renders is recomputed, so it is refused."""
    path = _key_copy(rendered, tmp_path)
    key = json.loads(path.read_text())
    planted_ids = {c["leaf_id"] for c in key["canaries"] + key["controls"]}
    records = tool.read_corpus(tmp_path / "release_corpus.jsonl")
    gone = next(r["leaf_id"] for r in records
                if r["provenance"] == "kernel" and r["leaf_id"] not in planted_ids)
    records = [r for r in records if r["leaf_id"] != gone]
    (tmp_path / "release_corpus.jsonl").write_text(_jsonl_of(records))
    ordered, _ = tool.changed_first(records, tool.corpus_diff(None, records))
    spec = tool.load_canaries(tool.committed_text(REPO[0], key["release_commit"],
                                                  tool.CANARIES_REL))
    planted, expected = tool.plant(ordered, seed=key["seed"], world=key["worlds"][0],
                                   spec=spec, control_seed=key["release_commit"])
    (tmp_path / "auditor_input.jsonl").write_text(_jsonl_of(planted))
    key |= {field: expected[field] for field in ("canaries", "controls", "expected_leaves",
                                                  "expected_count")}
    key["corpus_sha"] = tool.sha256_file(tmp_path / "auditor_input.jsonl")
    key["release_corpus_sha"] = tool.sha256_file(tmp_path / "release_corpus.jsonl")
    path.write_text(json.dumps(key))
    tool.load_key(path)  # consistent with itself
    with pytest.raises(tool.AuditInputInvalid,
                       match="release corpus beside the key is not the corpus the release"):
        tool.load_calibrated_key(path, REPO[0])


def test_an_emptied_commit_list_with_a_rewritten_provenance_prompt_is_refused(
        with_commit, tmp_path):
    """Codex P1 (class2_audit.py:1036): the key's provenance_commits emptied and the
    provenance prompt rendered again without them (its digest rewritten, the id kept) is
    self-consistent; the range's commits and the prompt are recomputed, so it is
    refused."""
    path = _key_copy(with_commit, tmp_path)
    key = json.loads(path.read_text())
    assert key["provenance_commits"]
    tool.load_calibrated_key(path, REPO[0])
    agents = tool.committed_text(REPO[0], key["release_commit"], tool.AGENTS_REL)
    tool.write_provenance_prompt(tmp_path, tool.provenance_section(key["range"], []),
                                 provenance_id=key["provenance_id"], agents=agents)
    key["provenance_commits"] = []
    key["provenance_prompt_sha"] = tool.sha256_file(tmp_path / "provenance_prompt.md")
    path.write_text(json.dumps(key))
    tool.load_key(path)  # consistent with itself
    with pytest.raises(tool.AuditInputInvalid,
                       match="provenance_commits are not the range's.*provenance_prompt.md"):
        tool.load_calibrated_key(path, REPO[0])


def test_a_rewritten_corpus_prompt_is_refused(rendered, tmp_path):
    """The same class: prompt.md is rendered again from the release, so a rubric line
    dropped from it, with the key's digest rewritten, is refused."""
    path = _key_copy(rendered, tmp_path)
    key = json.loads(path.read_text())
    prompt = (tmp_path / "prompt.md").read_text()
    line = next(x for x in prompt.splitlines() if x.startswith("| Q7"))
    (tmp_path / "prompt.md").write_text(prompt.replace(line + "\n", ""))
    key["prompt_sha"] = tool.sha256_file(tmp_path / "prompt.md")
    path.write_text(json.dumps(key))
    tool.load_key(path)  # consistent with itself
    with pytest.raises(tool.AuditInputInvalid, match="prompt.md beside the key is not"):
        tool.load_calibrated_key(path, REPO[0])


# --- Codex pass on 05e678b: the essay is anchored by a digest committed at the release -------


def test_an_altered_essay_with_the_right_headings_is_refused(rendered, history, tmp_path):
    """Codex P1 (class2_audit.py:1340): the essay is never committed, but its digest is
    (``ESSAY_DIGEST_REL``). An essay with every heading but other text is refused at
    render and at validate and gate; the canonical one passes."""
    repo, base, _surface, head = history
    altered = tmp_path / "essay.md"
    altered.write_text(ESSAY_TEXT.replace("(stand-in).", "(an edited stand-in)."))
    tool.authority_text(altered)  # every heading is there
    with pytest.raises(tool.AuditInputInvalid, match="is not the one .*essay.sha256 commits"):
        tool.render([WORLD], tmp_path / "out", seed=7, rendered=False, essay=altered,
                     release_range=f"{base}..{head}", repo=repo)
    assert not (tmp_path / "out").exists()
    (tmp_path / "copy").mkdir()
    path = _key_copy(rendered, tmp_path / "copy")
    assert tool.load_calibrated_key(path, REPO[0], ESSAY)
    with pytest.raises(tool.AuditInputInvalid, match="is not the one .*essay.sha256 commits"):
        tool.load_calibrated_key(path, REPO[0], altered)


def test_a_rewritten_authority_section_is_refused(rendered, tmp_path):
    """The prompt's authority text is extracted again from the verified essay, so an
    authority section rewritten in prompt.md, with the key's digest rewritten to match,
    is refused."""
    path = _key_copy(rendered, tmp_path)
    key = json.loads(path.read_text())
    prompt = (tmp_path / "prompt.md").read_text()
    assert "THE DARK STACK (stand-in)" in prompt
    (tmp_path / "prompt.md").write_text(prompt.replace("THE DARK STACK (stand-in)",
                                                       "THE DARK STACK (abridged)"))
    key["prompt_sha"] = tool.sha256_file(tmp_path / "prompt.md")
    path.write_text(json.dumps(key))
    tool.load_key(path)  # consistent with itself
    with pytest.raises(tool.AuditInputInvalid, match="prompt.md beside the key is not"):
        tool.load_calibrated_key(path, REPO[0], ESSAY)


# --- Codex pass on 50ce3f8: the renderer's own executed code is the release's ------------


def test_the_executed_code_is_every_repository_module_that_ran():
    """The set is read from ``sys.modules`` (the code that actually ran), never a list:
    the corpus and seat-text renderers and the tool itself are in it; a virtualenv's
    or a cache's files (ignored by git) are not."""
    from tests.audit import class2_corpus, class2_seat_text  # noqa: F401  (they ran)

    ran = tool.executed_code(ROOT)
    assert {"tests/audit/class2_corpus.py", "tests/audit/class2_seat_text.py",
            "scripts/class2_audit.py"} <= set(ran)
    assert not any(rel.startswith(".venv/") or "__pycache__" in rel for rel in ran)
    assert ran["scripts/class2_audit.py"] == tool.sha256_file(ROOT / "scripts/class2_audit.py")


@pytest.fixture
def renderer_repo(tmp_path, monkeypatch):
    """A repository holding a copy of the corpus renderer at its own path, loaded as a
    module of this process (it "ran"), committed at the head."""
    import types

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _seed_policy(repo)
    root = _commit(repo, "README.md", "x\n", "root")
    module = repo / "tests/audit/class2_corpus.py"
    head = _commit(repo, "tests/audit/class2_corpus.py",
                   (ROOT / "tests/audit/class2_corpus.py").read_text(), "the renderer")
    ran = types.ModuleType("class2_corpus_in_the_release_repo")
    ran.__file__ = str(module)
    monkeypatch.setitem(sys.modules, ran.__name__, ran)
    return repo, root, head, module


def test_an_uncommitted_edit_to_the_renderer_makes_render_refuse(renderer_repo, tmp_path):
    """Codex P1 (class2_audit.py:427): the renderer's code is outside the seat-visible
    scope, yet it made the corpus. Every repository module that ran must be committed
    and unmodified at the release: an uncommitted edit to class2_corpus.py refuses the
    render; committed, its path and hash are in the key."""
    repo, root, head, module = renderer_repo
    render = lambda out: tool.render([WORLD], out, seed=7, rendered=False,  # noqa: E731
                                     essay=ESSAY, release_range=f"{root}..{head}",
                                     repo=repo)
    original = module.read_text()
    module.write_text(original + "# an uncommitted edit\n")
    with pytest.raises(tool.AuditInputInvalid, match="class2_corpus.py ran with bytes"):
        render(tmp_path / "edited")
    assert not (tmp_path / "edited").exists()
    module.write_text(original)
    key = render(tmp_path / "out")
    assert key["executed_code"] == {"tests/audit/class2_corpus.py": tool.sha256_file(module)}
    # The gate re-verifies the record against the release commit.
    assert tool.executed_code_problems(repo, head, key["executed_code"]) == []
    forged = {"tests/audit/class2_corpus.py": "0" * 64}
    assert tool.executed_code_problems(repo, head, forged)


def test_an_uncommitted_allowlist_entry_does_not_back_an_allow(triaged):
    """Codex P1 (class2_audit.py:1781): the gate reads the allowlist committed in the
    release, never the worktree's: an entry written but not committed backs nothing."""
    _out, key, path, _sha, _samples, _prov = triaged
    text, finding = _q4_allowed(triaged)
    path.write_text(text)
    allowlist = REPO[0] / tool.ALLOWLIST_REL
    original = allowlist.read_text()
    entry = (f'\n[[allow]]\npath = "{finding["path"]}"\nquote = {json.dumps(finding["quote"])}'
             '\nquestion = "Q4"\nclass = "C1"\nrule = "imperative"\n'
             'context_words = ["x"]\nreason = "uncommitted"\npassage = "§I.b(1)"\n')
    try:
        allowlist.write_text(original + entry)
        problems = _gate(triaged, reviewed=tool.sha256_file(path))
        assert any("no allowlist entry" in p for p in problems), problems
        assert "uncommitted" not in tool.committed_text(REPO[0], key["release_commit"],
                                                        tool.ALLOWLIST_REL)
    finally:
        allowlist.write_text(original)


@pytest.mark.parametrize("row, why", [
    ({"finding_id": "f1", "path": "p", "reason": "r", "class": "C1"}, "lacks"),
    ({"finding_id": "f1", "path": "p", "reason": "r", "question": "Q4"}, "lacks"),
    ({"finding_id": "f1", "path": "p", "reason": "r", "question": "Q4", "class": "C2"},
     "not a finding's identity"),
])
def test_a_rejected_record_without_its_full_identity_is_refused(tmp_path, row, why):
    with pytest.raises(tool.AuditInputInvalid, match=why):
        tool.read_rejected(json.dumps(row) + "\n")


def test_a_previous_triage_row_without_its_full_identity_is_refused(tmp_path):
    head = ("# Class 2 audit triage: scripted\n\n- Auditor family: fam-x\n- World: scripted\n"
            "- Corpus: abc\n\n| id | path | question | class | severity | confidence | "
            "quote | disposition | reason |\n|---|---|---|---|---|---|---|---|---|\n")
    good = "| f1 | `p` | Q4 | C1 | HIGH | both samples | q | REJECT | r |\n"
    (tmp_path / "t.md").write_text(head + good)
    assert tool.read_previous_triage(tmp_path / "t.md", [WORLD])
    for bad, why in ((good.replace("| C1 |", "|  |"), "no full finding identity"),
                     (good.replace("| C1 |", "| C2 |"), "not a finding's identity"),
                     (good + good, "two rows")):
        (tmp_path / "t.md").write_text(head + bad)
        with pytest.raises(tool.AuditInputInvalid, match=why):
            tool.read_previous_triage(tmp_path / "t.md", [WORLD])


# --- Codex pass on 144323c ----------------------------------------------------------------


def test_the_release_scope_is_the_corpus_scanners_own_sources():
    """Codex P1 (class2_audit.py:351): the dirty check and the provenance pass read the
    paths the corpus is rendered from, by one function, so they cannot diverge: every
    module the seat-text scan indexed, and every world file, lies under one."""
    from tests.audit import class2_corpus, class2_seat_text

    assert tool.SURFACE_PATHS == class2_corpus.corpus_sources()
    under = lambda rel: any(rel == s or rel.startswith(s + "/") for s in tool.SURFACE_PATHS)  # noqa: E731
    modules = class2_seat_text.scan().modules
    assert modules and all(under(rel) for rel in modules)
    assert all(under(p.relative_to(class2_corpus.ROOT).as_posix())
               for p in class2_corpus.WORLDS.glob("*.toml"))


@pytest.mark.parametrize("path", ["factorylab/kernel/queue.py",
                                  "factorylab/learners/exp3.py",
                                  "factorylab/versioning/live.py"])
def test_a_kernel_change_is_in_the_provenance_pass_and_a_dirty_one_is_refused(tmp_path, path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    root = _commit(repo, "README.md", "x\n", "root")
    touched = _commit(repo, path, "REASON = 'hold'\n", BEHAVIOUR_MIX)
    head = _commit(repo, "docs/notes.md", "notes\n", "notes only")
    commits = tool.provenance_commits(repo, f"{root}..{head}")
    assert [c["sha"] for c in commits] == [touched]
    assert tool.release_commit(repo, f"{root}..{head}") == head
    (repo / path).write_text("REASON = 'hold harder'\n")
    with pytest.raises(tool.AuditInputInvalid, match="uncommitted seat-visible"):
        tool.release_commit(repo, f"{root}..{head}")


def _documented_commands(text):
    """Every ``uv run python scripts/class2_audit.py`` command in ``text``: continuation
    lines joined, an optional ``[...]`` kept (it must parse too) and each ``<placeholder>``
    one argument."""
    import re
    import shlex

    commands, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if "uv run python scripts/class2_audit.py" not in line:
            continue
        command = line.strip()
        j = i
        while command.endswith("\\"):
            j += 1
            # A docstring's continuation is its source's escaped ``\\``.
            command = command.rstrip("\\").rstrip() + " " + lines[j].strip()
        command = command.split("scripts/class2_audit.py", 1)[1]
        # ``<n>`` names a number (``--seed <n>``); every other placeholder a string.
        command = re.sub(r"<[^<>]*>", "X", command.replace("<n>", "7"))
        command = command.replace("[", " ").replace("]", " ")
        commands.append(shlex.split(command.replace("…", "X")))
    return commands


@pytest.mark.parametrize("source", ["docs/audits/class2/auditor-protocol.md",
                                    "scripts/class2_audit.py"])
def test_every_documented_command_parses_with_the_real_command_line(source):
    """Codex P2 (auditor-protocol.md:204): every command example the protocol and the
    tool document is parsed by the tool's own parser (``build_parser``), so a required
    argument the documentation leaves out (the gate's ``--triage-sha256``) fails here."""
    commands = _documented_commands((ROOT / source).read_text())
    assert {c[0] for c in commands} >= {"render", "validate", "triage", "gate"}, commands
    for argv in commands:
        try:
            tool.build_parser().parse_args(argv)
        except SystemExit:
            pytest.fail(f"{source}: {' '.join(argv)} does not parse")
    gate = next(c for c in commands if c[0] == "gate")
    with pytest.raises(SystemExit):
        tool.build_parser().parse_args(gate[:gate.index("--triage-sha256")])


def test_an_allowlist_entry_names_its_question_with_its_class():
    from tests.audit import class2_lexicon

    entry = {"path": "*/p", "quote": "q", "rule": next(iter(class2_lexicon.RULES)),
             "context_words": ["w"], "reason": "r",
             "passage": next(iter(class2_lexicon.PASSAGES))}
    assert class2_lexicon.allowlist_problems({"collocation": [], "allow": [entry]}) == []
    half = entry | {"question": "Q4"}
    assert class2_lexicon.allowlist_problems({"collocation": [], "allow": [half]})
