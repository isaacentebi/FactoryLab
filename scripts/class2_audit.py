"""The Class 2 audit's operator tool: the auditor's corpus, its calibration, its triage.

Phase-2 design B1/B2, with Astra's H-2 amendment. The static audit (B1) runs in the
test suite; the LLM auditor (B2) is a release step the operator runs, because it makes
a model call and the test suite makes none (``tests/test_no_network.py``). This script
does everything around that call, offline:

  render    Write the auditor's input: every seat-visible leaf of the chosen worlds,
            tagged (surface kind, audience, frequency, provenance, change since the last
            audited release), changed leaves first, with the canaries of
            ``docs/audits/class2/canaries.json`` planted at seeded positions and ten
            clean controls chosen from the smuggling audit's KEEP list. The key (which
            leaves are canaries and controls) and the release corpus (the unplanted
            leaves, next release's --previous-corpus) are written to files the auditor
            is never given. Also writes ``prompt.md`` (protocol inputs 1-5: the rubric,
            the authority text, the allowlist, the corpus diff against
            --previous-corpus, then last release's triage and rejected findings) and
            ``provenance_prompt.md``, the second prompt (input 6): every commit in
            --range base..head that touches a seat-visible surface, with its message and
            diff. Commit messages may carry behaviour data, so they never enter the
            corpus prompt.
  validate  Score one or more auditor samples (JSON Lines) against the key. Valid only
            when every sample's summary covers every leaf the key records as rendered
            (none unread, none unaccounted for), at least 7 of the 8 canaries are found
            in the samples' union and every mandatory one (Q6, Q9, Q10) is, and at most
            1 of 10 controls is flagged. An invalid audit is rerun with the next family.
  triage    From exactly two samples, write ``docs/audits/class2/<world>.md``: the
            skeleton the architect fills, one row per finding of the union (a finding
            one sample alone made is low confidence), with the family and the canary
            score recorded. Refused, writing nothing, for an invalid audit, for a family
            that authored kernel text or sits in the world, and for the family the last
            triage of the world used (rotation).
  gate      The release gate: the triage file records its family and a valid canary
            score, every HIGH or MED finding carries a disposition, a non-FIX
            disposition carries a reason, and no charter card is marked FIX.
  baseline  Recompute the static audit's findings and surface registry after the
            architect's triage (tests/audit/class2_findings.json, class2_surfaces.toml).

The model call itself is the operator's: send ``prompt.md`` with ``auditor_input.jsonl``
to one model family that neither authored kernel text nor sits in the world, at
temperature 0, twice, under the prepaid guard's cap; save each sample as JSON Lines, and
send ``provenance_prompt.md`` to the same model. ``validate`` and ``triage`` take both
samples. See ``docs/audits/class2/auditor-protocol.md``.

Examples::

    uv run python scripts/class2_audit.py render --world edition6-capital-loop \\
        --out work/class2/2026-10 --seed 7 --range <last release>..HEAD
    uv run python scripts/class2_audit.py validate work/class2/2026-10/sample1.jsonl \\
        work/class2/2026-10/sample2.jsonl --key work/class2/2026-10/canary_key.json
    uv run python scripts/class2_audit.py triage work/class2/2026-10/sample1.jsonl \\
        work/class2/2026-10/sample2.jsonl --key work/class2/2026-10/canary_key.json \\
        --world edition6-capital-loop --family X
    uv run python scripts/class2_audit.py gate --world edition6-capital-loop
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from random import Random
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.audit import class2_corpus as corpus  # noqa: E402
from tests.audit.class2_corpus import RenderFailed  # noqa: E402

CANARIES = ROOT / "docs/audits/class2/canaries.json"
#: Last release's REJECTED findings with their reasons (protocol input 4).
REJECTED = ROOT / "docs/audits/class2/rejected.jsonl"
PROTOCOL = ROOT / "docs/audits/class2/auditor-protocol.md"
TRIAGE_DIR = ROOT / "docs/audits/class2"
#: The overall calibration bar (Astra H-2): at least this many of the canaries found.
MIN_CANARIES = 7
#: At most this many of the clean controls may be flagged.
MAX_CONTROLS = 1
#: The auditor is run this many times; the union of the samples is triaged.
SAMPLES = 2
#: The families that author kernel text (Claude through Claude Code, GPT through Codex),
#: as ``factorylab.runtime.families.model_family`` names them.
AUTHOR_FAMILIES = frozenset({"claude", "gpt"})
#: The dispositions a triaged finding may carry. CHARTER: a charter card or norm finding,
#: sent to the charter's next revision as an observation, never fixed in code.
DISPOSITIONS = frozenset({"FIX", "ALLOW", "REJECT", "CHARTER"})

AUDIENCE = {"produce": ["producer"], "judge": ["evaluator"], "meta": ["meta"],
            "counter": ["adversary"], "vote": ["committee"], "testify": ["committee"]}


def leaf_id(path: str, text: str) -> str:
    return hashlib.sha256(f"{path}|{text}".encode()).hexdigest()[:12]


def describe(path: str) -> dict[str, Any]:
    """A leaf's surface kind, audience and frequency, read from its path alone."""
    parts = path.split("/")
    kind = parts[1] if len(parts) > 1 else ""
    if kind == "request":
        form = parts[2] if len(parts) > 2 else ""
        return {"surface_kind": f"request.{parts[3] if len(parts) > 3 else ''}",
                "audience": AUDIENCE.get(form, [form]), "frequency": f"every {form} request"}
    if kind == "tools":
        return {"surface_kind": "tool", "audience": ["every role"],
                "frequency": "every wake (index line); on demand (schema)"}
    if kind == "institutions":
        return {"surface_kind": "institution", "audience": ["every role"],
                "frequency": "every wake (stable prefix) or on demand (world.read)"}
    if kind == "genesis":
        return {"surface_kind": "genesis", "audience": [parts[2] if len(parts) > 2 else ""],
                "frequency": "every wake until the seat overwrites it"}
    if kind == "refusal":
        return {"surface_kind": "refusal", "audience": ["the refused seat"],
                "frequency": "on refusal"}
    if kind == "system":
        return {"surface_kind": "system", "audience": ["every role"], "frequency": "every call"}
    return {"surface_kind": kind, "audience": [], "frequency": "unknown"}


def corpus_records(worlds: list[str], *, rendered: bool) -> list[dict]:
    """Every leaf the auditor reads, tagged; charter cards and norms as context.

    Guarantees a rendered world contributes leaves only from a completed run that sent
    requests: the key's ``expected_leaves`` is the trusted record of what is under
    audit, and a partial render would make it silently short. A failed or empty render
    raises ``RenderFailed`` naming the world and the failure.
    """

    records: list[dict] = []
    seen: set[str] = set()
    for world in worlds:
        leaves = corpus.render_static(world)
        if rendered:
            leaves += corpus.require_complete(corpus.render_dynamic(world)).leaves
        for path, text in leaves:
            key = leaf_id(path, text)
            if key not in seen:
                seen.add(key)
                records.append({"leaf_id": key, "world": world, "path": path, "text": text,
                                "provenance": "kernel", **describe(path)})
        charter = corpus.raw_world(world).get("charter") or {}
        for i, norm in enumerate(charter.get("norms") or ()):
            text = norm if isinstance(norm, str) else json.dumps(norm, sort_keys=True)
            path = f"{world}/charter/norms[{i}]"
            records.append({"leaf_id": leaf_id(path, text), "world": world, "path": path,
                            "text": text, "provenance": "context, not under audit (norm)",
                            **describe(path)})
        for card in charter.get("cards") or ():
            path = f"{world}/charter/cards/{card.get('id')}"
            text = json.dumps(card, sort_keys=True)
            records.append({"leaf_id": leaf_id(path, text), "world": world, "path": path,
                            "text": text, "provenance": "context, not under audit (card)",
                            **describe(path)})
    return records


def read_corpus(path: Path) -> list[dict]:
    """A release corpus as ``render`` wrote it (``release_corpus.jsonl``)."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def corpus_diff(previous: list[dict] | None, records: list[dict]) -> dict[str, Any]:
    """Each kernel leaf's change since the last audited release, and the leaves removed.

    Guarantees: a leaf whose id the previous corpus holds is ``unchanged``; a leaf at a
    path the previous corpus held with other text is ``changed`` and carries the text it
    replaced (``was``); any other leaf is ``added``. With no previous corpus every leaf is
    ``added``. ``removed`` lists previous leaves whose path no longer appears at all.
    """
    kernel = [r for r in records if r["provenance"] == "kernel"]
    if previous is None:
        return {"previous": False, "status": {r["leaf_id"]: "added" for r in kernel},
                "was": {}, "removed": []}
    before = [r for r in previous if r.get("provenance") == "kernel"]
    ids = {r["leaf_id"] for r in before}
    by_path: dict[str, list[str]] = {}
    for r in before:
        by_path.setdefault(r["path"], []).append(r["text"])
    status, was = {}, {}
    for r in kernel:
        if r["leaf_id"] in ids:
            status[r["leaf_id"]] = "unchanged"
        elif r["path"] in by_path:
            status[r["leaf_id"]] = "changed"
            was[r["leaf_id"]] = sorted(t for t in by_path[r["path"]] if t != r["text"])
        else:
            status[r["leaf_id"]] = "added"
    paths = {r["path"] for r in kernel}
    removed = [{"path": r["path"], "text": r["text"]} for r in before if r["path"] not in paths]
    return {"previous": True, "status": status, "was": was, "removed": removed}


def changed_first(records: list[dict], diff: dict[str, Any]) -> tuple[list[dict], int]:
    """The records with every added or changed kernel leaf first (protocol input 5: "reads
    changed leaves first"), each tagged with its ``change``; and the changed block's size.
    Context records are tagged ``context``."""
    status = diff["status"]
    tagged = [{**r, "change": status.get(r["leaf_id"], "context")} for r in records]
    first = [r for r in tagged if r["change"] in ("added", "changed")]
    rest = [r for r in tagged if r["change"] not in ("added", "changed")]
    return first + rest, len(first)


def plant(records: list[dict], *, seed: int, world: str,
          changed: int | None = None) -> tuple[list[dict], dict]:
    """Insert the canaries at seeded positions and choose the controls; return the key.

    ``changed`` is the size of the changed block that leads ``records`` (``changed_first``).
    A canary takes the change tag of the block it lands in, so a planted leaf reads like
    its neighbours: ``added`` inside the changed block, ``unchanged`` after it (and
    ``added`` throughout when no previous corpus was given: ``changed`` is None).
    """
    spec = json.loads(CANARIES.read_text())
    rng = Random(seed)
    out = list(records)
    boundary = changed
    key: dict[str, Any] = {"seed": seed, "canaries": [], "controls": []}
    for canary in spec["canaries"]:
        path = f"{world}/{canary['surface']}"
        record = {"leaf_id": leaf_id(path, canary["text"]), "world": world, "path": path,
                  "text": canary["text"], "provenance": "kernel", **describe(path)}
        position = rng.randrange(len(out) + 1)
        if boundary is None:
            record["change"] = "added"
        elif position < boundary:
            record["change"] = "added"
            boundary += 1
        else:
            record["change"] = "unchanged"
        out.insert(position, record)
        key["canaries"].append({"id": canary["id"], "leaf_id": record["leaf_id"],
                                "path": path, "question": canary["question"],
                                "class": canary["class"],
                                "mandatory": bool(canary.get("mandatory"))})
    for surface in spec["control_surfaces"]:
        prefix = f"{world}/{surface}"
        match = next((r for r in sorted(records, key=lambda r: r["path"])
                      if r["provenance"] == "kernel" and r["path"].startswith(prefix)), None)
        if match is not None:
            key["controls"].append({"leaf_id": match["leaf_id"], "path": match["path"]})
    # The trusted record of what is under audit: every leaf the auditor must answer,
    # canaries included. ``validate`` reads the auditor's read set against this, never
    # against counts the auditor reports about itself.
    expected = sorted({r["leaf_id"] for r in out if r["provenance"] == "kernel"})
    key["expected_leaves"] = expected
    key["expected_count"] = len(expected)
    return out, key


def authority_text(essay: Path | None) -> str:
    """Chapter I §I and Chapter II, verbatim from the essay (it is not in the repository)."""
    if essay is None or not essay.exists():
        return ("[The operator pastes Chapter I §I (lines 73-81, the three classes) and "
                "Chapter II §I, §I.a, §I.b, §II.b and §IV.a of The Superdark Factory here, "
                "verbatim: docs/essay.md is not checked in.]")
    lines = essay.read_text().splitlines()
    head = next(i for i, line in enumerate(lines) if "I. On Factories and Darkness" in line)
    stop = next(i for i, line in enumerate(lines) if "II. The Human and the Loop" in line)
    start2 = next(i for i, line in enumerate(lines) if "CHAPTER II" in line)
    end2 = next(i for i, line in enumerate(lines) if "CHAPTER III" in line)
    return "\n".join(lines[head:stop] + ["", "---", ""] + lines[start2:end2])


#: Where seat-visible text is written: every module that renders a request, a tool, a
#: schematic, a refusal or a charter, and the world files (lenses, seat ids, prompts).
#: Deliberately wide: a commit touching one of these is read by the provenance pass
#: whether or not its diff turns out to change a seat-visible string.
SURFACE_PATHS = ("factorylab/cortex", "factorylab/runtime", "factorylab/settlement",
                 "factorylab/world", "factorylab/charter", "worlds")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def provenance_commits(repo: Path, release_range: str) -> list[dict]:
    """Every commit in ``release_range`` (``base..head``) that touches a seat-visible
    surface, merges included, oldest first, with its full message and its diff to those
    paths.

    A merge's diff is its combined diff (``git show --cc``): the hunks it holds that no
    parent holds, which is where a conflict resolution writes text of its own. A merge
    with none says so rather than vanishing, since its message still stands.

    Design B2 input 6: the auditor asks of each message whether it justifies text by a
    behaviour mix (AGENTS rule 2), which the leaves alone cannot show.
    """
    if ".." not in release_range:
        raise ValueError("the release range is base..head")
    # Full history: no commit on either side of a merge is simplified away.
    shas = _git(repo, "rev-list", "--reverse", "--full-history", release_range, "--",
                *SURFACE_PATHS).split()
    commits = []
    for sha in shas:
        message = _git(repo, "log", "-1", "--format=%B", sha).strip()
        merge = len(_git(repo, "rev-list", "--parents", "-n", "1", sha).split()) > 2
        diff = _git(repo, "show", "--no-color", "--format=", *(["--cc"] if merge else []),
                    sha, "--", *SURFACE_PATHS)
        if merge and not diff.strip():
            diff = "(merge: every surface hunk is one of its parents', shown with that commit)"
        commits.append({"sha": sha, "message": message, "diff": diff, "merge": merge})
    return commits


def provenance_section(release_range: str, commits: list[dict]) -> str:
    """The prompt's provenance pass: the question, then every commit, or an explicit none."""
    lines = ["## Provenance pass", "",
             f"Release range `{release_range}`. Each commit below touched a seat-visible "
             f"surface ({', '.join(SURFACE_PATHS)}). Answer, for each: does its message "
             "justify the change by a behaviour mix (what seats did, how often they did it, "
             "what scores they got)? A yes is a finding (AGENTS rule 2), whatever the diff "
             "itself says.", ""]
    if not commits:
        lines.append("(no commit in this range touched a seat-visible surface)")
    for commit in commits:
        title = f"### {commit['sha']}" + (" (merge, combined diff)" if commit.get("merge")
                                             else "")
        lines += [title, "", "```text", commit["message"], "```", "",
                  "```diff", commit["diff"].rstrip(), "```", ""]
    return "\n".join(lines)


def diff_section(planted: list[dict], diff: dict[str, Any]) -> str:
    """Protocol input 5: every leaf added or changed since the last audited release (the
    replaced text shown), then every path removed; or an explicit first release."""
    lines = ["## The corpus diff since the last audited release", ""]
    if not diff["previous"]:
        lines.append("(no previous audited corpus: this is the first release audited, and "
                     "every leaf is new)")
        return "\n".join(lines)
    changed = [r for r in planted if r.get("change") in ("added", "changed")]
    lines.append(f"{len(changed)} leaves added or changed, {len(diff['removed'])} removed. "
                 "They lead auditor_input.jsonl; read them first. A REJECTED finding of the "
                 "last release is re-read only if its leaf is listed here.")
    lines.append("")
    for r in changed:
        lines.append(f"- `{r['leaf_id']}` {r['change']}: `{r['path']}`")
        for text in diff["was"].get(r["leaf_id"], ()):
            lines.append(f"  - was: {json.dumps(text, ensure_ascii=False)}")
    for r in diff["removed"]:
        lines.append(f"- removed: `{r['path']}` was {json.dumps(r['text'], ensure_ascii=False)}")
    return "\n".join(lines)


def _agents_rules() -> str:
    agents = (ROOT / "AGENTS.md").read_text()
    return agents.split("## Chapter II, as design rules", 1)[-1].split("6. **The reward", 1)[0]


def write_provenance_prompt(out: Path, provenance: str) -> Path:
    """Protocol input 6, as the protocol orders it: a second prompt to the same model.
    Commit messages can carry behaviour data, which the corpus prompt never holds."""
    rule2 = _agents_rules().split("2. **Robust simplicity", 1)[-1].split("3. **Physics", 1)[0]
    parts = ["# Class 2 audit: provenance pass", "",
             "AGENTS.md rule 2, Robust simplicity" + rule2.rstrip(), "", provenance]
    path = out / "provenance_prompt.md"
    path.write_text("\n".join(parts) + "\n")
    return path


def write_prompt(out: Path, *, essay: Path | None, previous: Path | None,
                 diff: str, rejected: Path | None = None) -> Path:
    """The corpus prompt: protocol inputs 1-5, the corpus diff before last release's
    triage so a rejected finding's leaf can be read against its change."""
    from tests.audit import class2_lexicon as lexicon

    rules = _agents_rules()
    rejected_text = (rejected.read_text().strip()
                     if rejected is not None and rejected.exists() else "")
    parts = [
        "# Class 2 audit: reviewer prompt",
        "",
        # The reviewer reads what it is, what it is given, the rubric and the format; the
        # calibration, the dispositions and the operator steps are not its to know.
        PROTOCOL.read_text().split("## Calibration", 1)[0].rstrip(),
        "",
        "## Authority text (verbatim)",
        "",
        authority_text(essay),
        "",
        "## AGENTS.md rules 1-5",
        "",
        rules.strip(),
        "",
        "## The allowlist and its reasons",
        "",
        "```toml\n" + lexicon.ALLOWLIST.read_text() + "```",
        "",
        diff,
        "",
        "## Last release's triage",
        "",
        previous.read_text() if previous is not None and previous.exists()
        else "(none: this is the first release audited)",
        "",
        "## Last release's rejected findings, with their reasons",
        "",
        ("```jsonl\n" + rejected_text + "\n```") if rejected_text
        else "(none)",
        "",
        "## The corpus",
        "",
        "One JSON object per line in auditor_input.jsonl: leaf_id, world, path, text, "
        "surface_kind, audience, frequency, provenance, change. Answer every leaf whose "
        "provenance is `kernel`; read the others as context only. The added and changed "
        "leaves come first.",
    ]
    path = out / "prompt.md"
    path.write_text("\n".join(parts) + "\n")
    return path


def render(worlds: list[str], out: Path, *, seed: int, rendered: bool, release_range: str,
           repo: Path = ROOT, essay: Path | None = None, previous: Path | None = None,
           previous_corpus: Path | None = None, rejected: Path | None = REJECTED) -> dict:
    """Write the auditor's input, its two prompts, the separate key and the release
    corpus; return the key.

    Guarantees the protocol's inputs and orders: the corpus prompt carries inputs 1-5,
    the corpus diff against ``previous_corpus`` (the last release's
    ``release_corpus.jsonl``) rendered before last release's triage and rejected
    findings, and the auditor's input leads with the added and changed leaves; the
    provenance pass over ``release_range`` in ``repo`` is its own prompt. The release
    corpus holds no canary and, like the key, is never the auditor's input.
    """
    provenance = provenance_section(release_range, provenance_commits(repo, release_range))
    prior = read_corpus(previous_corpus) if previous_corpus is not None else None
    # Rendered before anything is written: a failed render leaves no corpus behind.
    records = corpus_records(worlds, rendered=rendered)
    diff = corpus_diff(prior, records)
    ordered, changed = changed_first(records, diff)
    out.mkdir(parents=True, exist_ok=True)
    planted, key = plant(ordered, seed=seed, world=worlds[0],
                         changed=changed if prior is not None else None)
    with (out / "auditor_input.jsonl").open("w") as handle:
        for record in planted:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    with (out / "release_corpus.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    (out / "canary_key.json").write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    write_prompt(out, essay=essay, previous=previous, diff=diff_section(planted, diff),
                 rejected=rejected)
    write_provenance_prompt(out, provenance)
    return key


def read_output(path: Path) -> tuple[list[dict], dict | None]:
    findings, summary = [], None
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("summary") is True:
            summary = row
        else:
            findings.append(row)
    return findings, summary


def _finding_key(finding: dict) -> tuple:
    return (finding.get("finding_id") or None, finding.get("leaf_id"), finding.get("path"),
            finding.get("question"), finding.get("class"))


def union(samples: list[tuple[list[dict], dict | None]]) -> list[dict]:
    """The findings of every sample, once each, in first-seen order.

    With two or more samples, a finding not every sample made is ``low_confidence`` (the
    protocol: "a finding only one sample made is marked low confidence").
    """
    merged: dict[tuple, dict] = {}
    seen: dict[tuple, set[int]] = {}
    for i, (findings, _summary) in enumerate(samples):
        for finding in findings:
            key = _finding_key(finding)
            merged.setdefault(key, dict(finding))
            seen.setdefault(key, set()).add(i)
    out = []
    for key, finding in merged.items():
        finding["samples"] = len(seen[key])
        finding["low_confidence"] = len(samples) > 1 and len(seen[key]) < len(samples)
        out.append(finding)
    return out


def _completeness(summary: dict | None, expected: set[str]) -> list[str]:
    """Why a sample's summary is not complete against the key (none when it is).

    Judged against the key render wrote, never the numbers the auditor reports about
    itself: every expected leaf is in the read set or listed unread, and an unread leaf
    fails the audit.
    """
    if summary is None:
        return ["no summary object"]
    problems = []
    read = summary.get("read")
    read = set(read) if isinstance(read, list) else set()
    unread = summary.get("unread")
    unread = set(unread) if isinstance(unread, list) else set()
    if unread:
        problems.append(f"unread leaves: {len(unread)}")
    unaccounted = expected - read - unread
    if unaccounted:
        problems.append(f"expected leaves neither read nor listed unread: "
                        f"{len(unaccounted)}/{len(expected)}")
    if summary.get("leaves_total") != len(expected):
        problems.append(f"leaves_total {summary.get('leaves_total')!r} is not the "
                        f"{len(expected)} leaves rendered")
    if summary.get("leaves_read") != len(read & expected):
        problems.append("leaves_read differs from the expected leaves in the read set")
    return problems


def validate(findings: list[dict], summary: dict | None, key: dict) -> dict:
    """Whether a one-sample audit is valid, and why not (``validate_samples``)."""
    return validate_samples([(findings, summary)], key)


def validate_samples(samples: list[tuple[list[dict], dict | None]], key: dict) -> dict:
    """Whether an audit is valid, and why not (design B2 calibration, Astra H-2).

    Every sample's summary is complete against the key on its own; the canaries and
    controls are scored on the samples' union.
    """
    problems: list[str] = []
    expected = set(key["expected_leaves"])
    if len(expected) != key["expected_count"]:
        problems.append("the key's expected leaf set does not match its count")
    if not samples:
        problems.append("no sample")
    for i, (_findings, summary) in enumerate(samples):
        prefix = f"sample {i + 1}: " if len(samples) > 1 else ""
        problems += [prefix + p for p in _completeness(summary, expected)]
    findings = union(samples)

    def flagged(target: dict) -> list[dict]:
        # A finding names its leaf by id; a path alone is read only when it names none,
        # since a planted leaf may share its path with a real one.
        return [f for f in findings if f.get("leaf_id") == target["leaf_id"]
                or (not f.get("leaf_id") and f.get("path") == target["path"])]

    found = []
    for canary in key["canaries"]:
        hits = flagged(canary)
        if any(h.get("question") == canary["question"] or h.get("class") == canary["class"]
               for h in hits):
            found.append(canary["id"])
    missed = [c for c in key["canaries"] if c["id"] not in found]
    mandatory = [c["id"] for c in missed if c["mandatory"]]
    if len(found) < MIN_CANARIES:
        problems.append(f"canaries found {len(found)}/{len(key['canaries'])} < {MIN_CANARIES}")
    if mandatory:
        problems.append(f"a mandatory canary was missed: {mandatory}")
    controls = [c for c in key["controls"] if flagged(c)]
    if len(controls) > MAX_CONTROLS:
        problems.append(f"controls flagged {len(controls)}/{len(key['controls'])} "
                        f"> {MAX_CONTROLS}")
    return {"valid": not problems, "problems": problems, "samples": len(samples),
            "canaries_found": f"{len(found)}/{len(key['canaries'])}",
            "missed": [c["id"] for c in missed],
            "controls_flagged": f"{len(controls)}/{len(key['controls'])}"}


def world_families(world: str) -> set[str]:
    """Every foundation family the world seats or offers on its menu."""
    from factorylab.runtime.families import model_family

    raw = corpus.raw_world(world)
    ids = [a.get("model_id") for a in raw.get("assemblies") or ()]
    ids += [m.get("id") for m in raw.get("models") or ()]
    return {model_family(i) for i in ids if i}


def recorded_family(text: str) -> str | None:
    """The auditor family a triage file records, or None."""
    for line in text.splitlines():
        if line.startswith("- Auditor family:"):
            return line.split(":", 1)[1].strip() or None
    return None


def family_refusal(family: str, world: str, previous_triage: Path | None) -> str | None:
    """Why ``family`` may not audit ``world`` this release, or None (the protocol's "Who
    reads": neither an authoring family nor one the world seats, and rotated)."""
    from factorylab.runtime.families import model_family

    fam = model_family(family)
    if fam in AUTHOR_FAMILIES:
        return f"{family} ({fam}) authored kernel text"
    if fam in world_families(world):
        return f"{family} ({fam}) sits in {world}"
    last = (recorded_family(previous_triage.read_text())
            if previous_triage is not None and previous_triage.exists() else None)
    if last is not None and model_family(last) == fam:
        return f"{family} ({fam}) audited the last release: the family rotates"
    return None


def triage_skeleton(findings: list[dict], verdict: dict, *, world: str, family: str,
                    key: dict) -> str:
    """The architect's triage file: one row per finding that is not a canary."""
    planted = {c["leaf_id"] for c in key["canaries"]}
    rows = [f for f in findings if f.get("leaf_id") not in planted]
    lines = [f"# Class 2 audit triage: {world}", "",
             f"- Auditor family: {family}",
             f"- Samples: {verdict.get('samples', 1)}",
             f"- Canaries found: {verdict['canaries_found']}; controls flagged: "
             f"{verdict['controls_flagged']}; valid: {verdict['valid']}",
             "- Dispositions: FIX (a rewrite or deletion, plus a regression entry), ALLOW (an "
             "allowlist entry with a reason and a passage), REJECT (the auditor is wrong; "
             "copied to rejected.jsonl with the reason), CHARTER (a charter card or norm: "
             "sent to the charter's next revision, never fixed in code).", "",
             "| id | path | question | class | severity | confidence | quote | disposition "
             "| reason |",
             "|---|---|---|---|---|---|---|---|---|"]
    for f in rows:
        quote = str(f.get("quote", "")).replace("|", "\\|")[:120]
        confidence = "low" if f.get("low_confidence") else "both samples"
        lines.append(f"| {f.get('finding_id', '')} | `{f.get('path', '')}` | "
                     f"{f.get('question', '')} | {f.get('class', '')} | "
                     f"{f.get('severity', '')} | {confidence} | {quote} |  |  |")
    return "\n".join(lines) + "\n"


def _cells(line: str) -> list[str]:
    """A markdown table row's cells, an escaped ``\\|`` kept inside its cell."""
    parts, cell, i = [], "", 0
    body = line.strip().strip("|")
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            cell += "|"
            i += 2
            continue
        if body[i] == "|":
            parts.append(cell.strip())
            cell = ""
        else:
            cell += body[i]
        i += 1
    parts.append(cell.strip())
    return parts


def release_gate(text: str) -> list[str]:
    """Why a triage file does not pass the release gate (none when it does).

    The protocol's gate: the file records the family and a valid canary score; zero
    untriaged HIGH or MED findings; each disposition is one of ``DISPOSITIONS``, and a
    non-FIX one carries its reason; a charter card or norm is never FIX ("not the
    kernel's to fix").
    """
    problems = []
    if recorded_family(text) is None:
        problems.append("the triage file records no auditor family")
    if "valid: True" not in text:
        problems.append("the triage file records no valid canary score")
    header = None
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = _cells(line)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        row = dict(zip(header, cells, strict=False))
        disposition = row.get("disposition", "").upper()
        where = f"{row.get('id') or row.get('path')}"
        if row.get("severity", "").upper() in ("HIGH", "MED") and not disposition:
            problems.append(f"untriaged {row.get('severity')} finding {where}")
        if disposition and disposition not in DISPOSITIONS:
            problems.append(f"unknown disposition {disposition!r} on {where}")
        if disposition in DISPOSITIONS - {"FIX"} and not row.get("reason"):
            problems.append(f"{disposition} without a reason on {where}")
        if disposition == "FIX" and "/charter/" in row.get("path", ""):
            problems.append(f"a charter finding is not the kernel's to fix: {where}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render")
    r.add_argument("--world", action="append", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--rendered", action="store_true", help="also run each world 60 ticks")
    r.add_argument("--essay", type=Path, default=ROOT / "docs/essay.md")
    r.add_argument("--previous", type=Path, default=None,
                   help="the last release's triage file")
    r.add_argument("--previous-corpus", type=Path, default=None,
                   help="the last audited release's release_corpus.jsonl")
    r.add_argument("--rejected", type=Path, default=REJECTED,
                   help="the rejected findings with their reasons")
    r.add_argument("--range", required=True,
                   help="the release range, base..head, read by the provenance pass")
    r.add_argument("--repo", type=Path, default=ROOT, help="the repository the range is in")
    for name in ("validate", "triage"):
        v = sub.add_parser(name)
        v.add_argument("output", type=Path, nargs="+", help="one JSON Lines file per sample")
        v.add_argument("--key", type=Path, required=True)
        if name == "triage":
            v.add_argument("--world", required=True)
            v.add_argument("--family", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--world", required=True)
    gate.add_argument("--triage", type=Path, default=None)
    b = sub.add_parser("baseline")
    b.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "render":
        try:
            key = render(args.world, args.out, seed=args.seed, rendered=args.rendered,
                         release_range=args.range, repo=args.repo, essay=args.essay,
                         previous=args.previous, previous_corpus=args.previous_corpus,
                         rejected=args.rejected)
        except RenderFailed as exc:
            print(f"no audit: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {args.out}/auditor_input.jsonl, prompt.md, provenance_prompt.md, "
              f"release_corpus.jsonl and canary_key.json ({len(key['canaries'])} canaries, "
              f"{len(key['controls'])} controls)")
        return 0
    if args.command == "baseline":
        from tests.audit import class2_audit, class2_corpus

        try:
            document = class2_audit.write_baseline(class2_corpus.launchable_worlds(),
                                                   rendered=not args.static_only)
        except RenderFailed as exc:
            print(f"no baseline rewrite: {exc}", file=sys.stderr)
            return 2
        print(f"{len(document['findings'])} findings written")
        return 0
    if args.command == "gate":
        path = args.triage or TRIAGE_DIR / f"{args.world}.md"
        problems = (release_gate(path.read_text()) if path.exists()
                    else [f"no triage file at {path}"])
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    samples = [read_output(path) for path in args.output]
    key = json.loads(args.key.read_text())
    verdict = validate_samples(samples, key)
    if args.command == "validate":
        print(json.dumps(verdict, indent=1))
        return 0 if verdict["valid"] else 1
    if len(samples) != SAMPLES:
        print(f"no triage: the protocol runs the auditor {SAMPLES} times, and "
              f"{len(samples)} sample(s) were given", file=sys.stderr)
        return 1
    if not verdict["valid"]:
        # An invalid audit is rerun with the next family; its findings are not triaged,
        # so no triage file is written for it.
        print(json.dumps(verdict, indent=1), file=sys.stderr)
        print("no triage: the audit is invalid", file=sys.stderr)
        return 1
    path = TRIAGE_DIR / f"{args.world}.md"
    refusal = family_refusal(args.family, args.world, path)
    if refusal is not None:
        print(f"no triage: {refusal}", file=sys.stderr)
        return 1
    text = triage_skeleton(union(samples), verdict, world=args.world, family=args.family,
                           key=key)
    path.write_text(text)
    print(f"wrote {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
