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
            --previous-corpus, then last release's triage and rejected findings; the
            authority text filled from --essay, or no render) and
            ``provenance_prompt.md``, the second prompt (input 6): every commit in
            --range base..head that touches a seat-visible surface, with its message and
            diff. Commit messages may carry behaviour data, so they never enter the
            corpus prompt. The range's head must be the commit checked out, with no
            uncommitted seat-visible change; the key records it (``release_commit``).
            The corpus includes, once under the world ``kernel``, every string the
            kernel's code can return to a seat (``tests/audit/class2_seat_text.py``).
  validate  Score the audit: two corpus samples and two provenance samples (JSON Lines)
            against the key. Refused unless the key is bound to the corpus and the two
            prompts beside it.
            Valid only when every sample echoes its prompt's id (``corpus_sha``,
            ``provenance_id``) and its sample number (1 and 2, once each); every corpus
            finding passes its schema (fields, enums, its leaf in the corpus, its class
            and severity the rubric's); every summary covers every leaf the key records
            (none unread, none unaccounted for) and counts its findings by class; every
            provenance sample answers every commit once; at least 7 of the 8 canaries
            are found in the union and every mandatory one (Q6, Q9, Q10) is; and at most
            1 of 10 controls is flagged. An invalid audit is rerun with the next family.
  triage    From a valid audit, write ``docs/audits/class2/<world>.md``: one row per
            finding the world owns, that world's findings of the union (a finding one
            sample alone made is low confidence) and every commit the provenance pass
            flagged, as HIGH findings. The file records its bindings: the family, the
            world, the corpus, the range and every sample's sha256. Refused, writing
            nothing, for a world the audit did not render, an invalid audit, a family
            that authored kernel text or sits in the world, and the family the last
            triage of the world used (rotation).
  gate      The release gate, recomputed from bound sources: given the key (which must
            have audited the release gated, ``--release`` or HEAD) and the
            sample files, it checks the triage file names the world, the key's corpus
            and range and exactly those samples by sha256; recomputes the audit (valid)
            and the world's findings from the samples; and requires each finding to
            have exactly one row with its severity, question and class, every HIGH or
            MED finding a disposition, a non-FIX disposition a reason, and no charter
            card marked FIX. Nothing the gate trusts is stored beside the triage file.
  baseline  Recompute the static audit's findings and surface registry after the
            architect's triage (tests/audit/class2_findings.json, class2_surfaces.toml).

Every artifact this tool reads is refused (exit 2) unless it passes its schema and is
bound to its origin: the key to its corpus and prompts, a sample to its prompt, a triage
file to its key and samples, a previous corpus to its own hashes, rejected findings and a
previous triage file to their fields. Derived values (the verdict, the findings) are
recomputed from those sources, never stored and trusted (the threat model is in
``docs/audits/class2/auditor-protocol.md``).

The model call itself is the operator's: send ``prompt.md`` with ``auditor_input.jsonl``
to one model family that neither authored kernel text nor sits in the world, at
temperature 0, twice, under the prepaid guard's cap; send ``provenance_prompt.md`` to the
same model twice; save each answer as JSON Lines. See
``docs/audits/class2/auditor-protocol.md``.

Examples::

    uv run python scripts/class2_audit.py render --world edition6-capital-loop \\
        --out work/class2/2026-10 --seed 7 --range <last release>..HEAD
    uv run python scripts/class2_audit.py validate work/class2/2026-10/sample1.jsonl \\
        work/class2/2026-10/sample2.jsonl --provenance-samples \\
        work/class2/2026-10/provenance1.jsonl work/class2/2026-10/provenance2.jsonl \\
        --key work/class2/2026-10/canary_key.json
    uv run python scripts/class2_audit.py triage <the same samples> \\
        --key work/class2/2026-10/canary_key.json --world edition6-capital-loop --family X
    uv run python scripts/class2_audit.py gate --world edition6-capital-loop \\
        --key work/class2/2026-10/canary_key.json --samples <the two samples> \\
        --provenance-samples <the two provenance samples>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
    if kind == "seat_text":
        return {"surface_kind": f"seat_text.{parts[2] if len(parts) > 2 else ''}",
                "audience": ["the seat whose call, proposal or order is answered"],
                "frequency": "on that refusal or error"}
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
    # The seat text in the kernel's code (tool results, refusal reasons, error messages
    # a seat reads back), found statically and read once: it is the same in every world.
    for path, text in corpus.render_seat_text():
        key = leaf_id(path, text)
        if key not in seen:
            seen.add(key)
            records.append({"leaf_id": key, "world": corpus.KERNEL, "path": path,
                            "text": text, "provenance": "kernel", **describe(path)})
    return records


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


def plant(records: list[dict], *, seed: int, world: str, changed: int | None = None,
          spec: dict | None = None) -> tuple[list[dict], dict]:
    """Insert the canaries at seeded positions and choose the controls; return the key.

    ``changed`` is the size of the changed block that leads ``records`` (``changed_first``).
    A canary takes the change tag of the block it lands in, so a planted leaf reads like
    its neighbours: ``added`` inside the changed block, ``unchanged`` after it (and
    ``added`` throughout when no previous corpus was given: ``changed`` is None).
    Every control surface must name a kernel leaf of ``world``: the calibration bar
    reads ten controls, never fewer.
    """
    spec = spec if spec is not None else load_canaries()
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
        if match is None:
            raise AuditInputInvalid(f"the control surface {surface!r} names no leaf of "
                                    f"{world}")
        key["controls"].append({"leaf_id": match["leaf_id"], "path": match["path"]})
    # The trusted record of what is under audit: every leaf the auditor must answer,
    # canaries included. ``validate`` reads the auditor's read set against this, never
    # against counts the auditor reports about itself.
    expected = sorted({r["leaf_id"] for r in out if r["provenance"] == "kernel"})
    key["expected_leaves"] = expected
    key["expected_count"] = len(expected)
    return out, key


#: The headings that bound the authority text in the essay: Chapter I §I, and Chapter II.
ESSAY_MARKS = ("I. On Factories and Darkness", "II. The Human and the Loop", "CHAPTER II",
               "CHAPTER III")


def authority_text(essay: Path | None) -> str:
    """Chapter I §I and Chapter II, verbatim from the essay (it is not in the repository).

    Guarantees the prompt is complete when rendered: ``render`` fills the authority
    text itself, so no prompt is edited afterwards (its hash is in the key). An essay
    that is missing, or lacks a heading that bounds the text, refuses the render
    (``AuditInputInvalid``); there is never a placeholder to paste into.
    """
    if essay is None or not Path(essay).exists():
        raise AuditInputInvalid(f"no essay at {essay}: render fills the authority text "
                                "from docs/essay.md (copied into the worktree, never "
                                "committed), and refuses without it")
    lines = Path(essay).read_text().splitlines()
    at = {}
    for mark in ESSAY_MARKS:
        at[mark] = next((i for i, line in enumerate(lines) if mark in line), None)
        if at[mark] is None:
            raise AuditInputInvalid(f"the essay has no heading {mark!r}")
    head, stop, start2, end2 = (at[mark] for mark in ESSAY_MARKS)
    if not (head < stop and start2 < end2):
        raise AuditInputInvalid("the essay's headings are out of order")
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


def write_provenance_prompt(out: Path, provenance: str, *, provenance_id: str) -> Path:
    """Protocol input 6, as the protocol orders it: a second prompt to the same model.
    Commit messages can carry behaviour data, which the corpus prompt never holds. Its
    answer is read back (``provenance_problems``), so the prompt states the format and
    the id every answer echoes."""
    rule2 = _agents_rules().split("2. **Robust simplicity", 1)[-1].split("3. **Physics", 1)[0]
    parts = ["# Class 2 audit: provenance pass", "",
             "AGENTS.md rule 2, Robust simplicity" + rule2.rstrip(), "", provenance, "",
             "## Output format", "",
             "JSON Lines: one object per commit above, then one summary object.", "",
             "```json",
             '{"sha": "<the full commit sha>", "behaviour_mix": true, '
             '"quote": "<the message\'s own words, when true>", "rationale": "..."}',
             f'{{"summary": true, "provenance_id": "{provenance_id}", "sample": 1, '
             '"commits_read": ["<every sha above>"]}',
             "```", "",
             f"`provenance_id` is `{provenance_id}`. `sample` is 1 on the first run and "
             "2 on the second."]
    path = out / "provenance_prompt.md"
    path.write_text("\n".join(parts) + "\n")
    return path


def write_prompt(out: Path, *, authority: str, previous_text: str | None,
                 diff: str, rejected: list[dict], corpus_sha: str) -> Path:
    """The corpus prompt: protocol inputs 1-5, the corpus diff before last release's
    triage so a rejected finding's leaf can be read against its change, and the corpus
    id every sample's summary echoes."""
    from tests.audit import class2_lexicon as lexicon

    rules = _agents_rules()
    rejected_text = "\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False)
                              for r in rejected)
    parts = [
        "# Class 2 audit: reviewer prompt",
        "",
        # The reviewer reads what it is, what it is given, the rubric and the format; the
        # calibration, the dispositions and the operator steps are not its to know.
        PROTOCOL.read_text().split("## Calibration", 1)[0].rstrip(),
        "",
        "## Authority text (verbatim)",
        "",
        authority,
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
        previous_text if previous_text is not None
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
        "",
        f"The corpus id is `{corpus_sha}`: your summary carries it as `corpus_sha`, and "
        "`sample` is 1 on the first run and 2 on the second.",
    ]
    path = out / "prompt.md"
    path.write_text("\n".join(parts) + "\n")
    return path


class AuditInputInvalid(ValueError):
    """An artifact the tool consumes is unbound to its origin or fails its schema."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_range(repo: Path, release_range: str) -> list[str]:
    """The release range's two ends as commit SHAs; refused unless both resolve."""
    if ".." not in release_range:
        raise ValueError("the release range is base..head")
    base, head = release_range.split("..", 1)
    shas = []
    for end in (base, head):
        try:
            shas.append(_git(repo, "rev-parse", "--verify", f"{end}^{{commit}}").strip())
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"the release range end {end!r} is not a commit") from exc
    return shas


def release_commit(repo: Path, release_range: str) -> str:
    """The commit being audited: ``repo``'s HEAD, refused unless it is the release range's
    head and no seat-visible path (``SURFACE_PATHS``) has an uncommitted change, so the
    corpus rendered from the worktree is the text of that commit and the provenance pass
    reads the commits that made it."""
    head = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    range_head = resolve_range(repo, release_range)[1]
    if range_head != head:
        raise AuditInputInvalid(f"the range head {range_head[:12]} is not HEAD {head[:12]}: "
                                "the audited release is the commit checked out")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=all", "--",
                 *SURFACE_PATHS).strip()
    if dirty:
        raise AuditInputInvalid("uncommitted seat-visible changes: the corpus would not be "
                                f"the release commit's text ({dirty.splitlines()[0]})")
    return head


def load_canaries() -> dict:
    """``canaries.json``, refused unless it is the protocol's calibration set: one canary
    per question Q3-Q10 with that question's class, Q6/Q9/Q10 and only they mandatory,
    and ten distinct control surfaces."""
    spec = json.loads(CANARIES.read_text())
    problems = []
    canaries = spec.get("canaries") or []
    questions = [c.get("question") for c in canaries]
    if questions != [f"Q{i}" for i in range(3, 11)]:
        problems.append(f"canary questions {questions} are not Q3-Q10 once each")
    for c in canaries:
        if CLASS_OF.get(c.get("question")) != c.get("class"):
            problems.append(f"{c.get('id')}: class {c.get('class')!r} is not its question's")
        if not isinstance(c.get("text"), str) or not c["text"].strip():
            problems.append(f"{c.get('id')}: no text")
        if not isinstance(c.get("surface"), str) or not c["surface"]:
            problems.append(f"{c.get('id')}: no surface")
    if {c.get("question") for c in canaries if c.get("mandatory")} != {"Q6", "Q9", "Q10"}:
        problems.append("the mandatory canaries are not exactly Q6, Q9 and Q10")
    surfaces = spec.get("control_surfaces") or []
    if len(surfaces) != 10 or len(set(surfaces)) != 10:
        problems.append("the control surfaces are not ten distinct surfaces")
    if problems:
        raise AuditInputInvalid("canaries.json: " + "; ".join(problems))
    return spec


def _jsonl(path: Path, what: str) -> list[dict]:
    rows = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditInputInvalid(f"{what} line {i} is not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise AuditInputInvalid(f"{what} line {i} is not an object")
        rows.append(row)
    return rows


RECORD_FIELDS = ("leaf_id", "world", "path", "text", "provenance")


def read_corpus(path: Path) -> list[dict]:
    """A release corpus as ``render`` wrote it (``release_corpus.jsonl``), refused unless
    every record has its fields and its ``leaf_id`` is the hash of its path and text."""
    records = _jsonl(path, "the previous corpus")
    for i, r in enumerate(records, 1):
        missing = [f for f in RECORD_FIELDS if not isinstance(r.get(f), str)]
        if missing:
            raise AuditInputInvalid(f"the previous corpus record {i} lacks {missing}")
        if r["leaf_id"] != leaf_id(r["path"], r["text"]):
            raise AuditInputInvalid(f"the previous corpus record {i}'s leaf_id is not the "
                                    "hash of its path and text")
    return records


def read_rejected(path: Path | None) -> list[dict]:
    """Last release's rejected findings, refused unless each names its finding, its path
    and a reason."""
    if path is None or not Path(path).exists():
        return []
    rows = _jsonl(path, "rejected.jsonl")
    for i, row in enumerate(rows, 1):
        bad = [f for f in ("finding_id", "path", "reason")
               if not isinstance(row.get(f), str) or not row[f].strip()]
        if bad:
            raise AuditInputInvalid(f"rejected.jsonl line {i} lacks {bad}")
    return rows


def triage_header(text: str) -> dict[str, str]:
    """The ``- Name: value`` lines of a triage file's header."""
    out = {}
    for line in text.splitlines():
        if line.startswith("|"):
            break
        if line.startswith("- ") and ":" in line:
            name, value = line[2:].split(":", 1)
            out[name.strip()] = value.strip()
    return out


def read_previous_triage(path: Path | None, worlds: list[str]) -> str | None:
    """Last release's triage file, refused unless it is a triage file of one of the
    worlds rendered, recording its family and corpus."""
    if path is None or not Path(path).exists():
        return None
    text = Path(path).read_text()
    header = triage_header(text)
    first = text.splitlines()[0] if text else ""
    world = first.removeprefix("# Class 2 audit triage: ").strip()
    if not first.startswith("# Class 2 audit triage: ") or world not in worlds:
        raise AuditInputInvalid(f"{path} is not a triage file of {worlds}")
    if not header.get("Auditor family") or not header.get("Corpus"):
        raise AuditInputInvalid(f"{path} records no auditor family or corpus")
    return text


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

    Binding: every input is validated before anything is written, and the key records
    the origin of everything the audit reads (worlds, seed, the range and its two
    commits, the corpus, both prompts, the release corpus and the previous one, the
    essay) and the provenance commits the second prompt asks about. Each prompt names
    the id its answers must echo (``corpus_sha``, ``provenance_id``).
    """
    if not worlds or len(set(worlds)) != len(worlds):
        raise AuditInputInvalid("the worlds rendered are none, or repeat")
    missing = [w for w in worlds if not (corpus.WORLDS / f"{w}.toml").exists()]
    if missing:
        raise AuditInputInvalid(f"no world file for {missing}")
    range_shas = resolve_range(repo, release_range)
    released = release_commit(repo, release_range)
    commits = provenance_commits(repo, release_range)
    provenance = provenance_section(release_range, commits)
    provenance_id = hashlib.sha256(provenance.encode()).hexdigest()
    prior = read_corpus(previous_corpus) if previous_corpus is not None else None
    rejected_rows = read_rejected(rejected)
    previous_text = read_previous_triage(previous, worlds)
    spec = load_canaries()
    authority = authority_text(essay)
    # Rendered before anything is written: a failed render leaves no corpus behind.
    records = corpus_records(worlds, rendered=rendered)
    diff = corpus_diff(prior, records)
    ordered, changed = changed_first(records, diff)
    planted, key = plant(ordered, seed=seed, world=worlds[0],
                         changed=changed if prior is not None else None, spec=spec)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "auditor_input.jsonl").open("w") as handle:
        for record in planted:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    with (out / "release_corpus.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    corpus_sha = sha256_file(out / "auditor_input.jsonl")
    write_prompt(out, authority=authority, previous_text=previous_text,
                 diff=diff_section(planted, diff), rejected=rejected_rows,
                 corpus_sha=corpus_sha)
    write_provenance_prompt(out, provenance, provenance_id=provenance_id)
    key.update({
        "schema": KEY_SCHEMA, "worlds": list(worlds), "rendered": rendered,
        "range": release_range, "range_shas": range_shas, "release_commit": released,
        "corpus_sha": corpus_sha,
        "prompt_sha": sha256_file(out / "prompt.md"),
        "provenance_prompt_sha": sha256_file(out / "provenance_prompt.md"),
        "provenance_id": provenance_id,
        "provenance_commits": [{"sha": c["sha"], "message": c["message"]} for c in commits],
        "release_corpus_sha": sha256_file(out / "release_corpus.jsonl"),
        "previous_corpus_sha": (sha256_file(previous_corpus)
                                if previous_corpus is not None else None),
        "essay_sha": sha256_file(essay) if essay is not None and essay.exists() else None,
    })
    (out / "canary_key.json").write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    return key


# --- what the tool reads back: the key, the samples, their schemas ---------------------------

KEY_SCHEMA = 2
SEVERITIES = ("HIGH", "MED", "LOW")
#: Each rubric question that yields a finding, and the class it yields (the rubric).
CLASS_OF = {"Q3": "ANNOUNCED-PHYSICS", "Q4": "C1", "Q5": "C2", "Q6": "C1",
            "Q7": "FALSE-PHYSICS", "Q8": "C1", "Q9": "DISCLOSURE", "Q10": "C2",
            "Q11": "C2", "Q12": "NORM-OBJECTIVE"}
def reach(frequency: str) -> str:
    """How far a leaf reaches, from its ``frequency`` tag (``describe``): ``every`` when
    it is in every wake or call of its audience ("every …"), ``some`` when it reaches
    some requests only ("on …": on demand, on refusal, on error), else ``none``."""
    if frequency.startswith("every"):
        return "every"
    if frequency.startswith("on "):
        return "some"
    return "none"


def required_severity(question: str, frequency: str) -> str:
    """The rubric's severity, derived from the question and the leaf's reach together:
    HIGH for Q3-Q5 on a leaf that reaches every wake; MED for Q6, Q8 and Q10, and for any
    question on a leaf that reaches requests; LOW otherwise."""
    where = reach(frequency)
    if question in ("Q3", "Q4", "Q5") and where == "every":
        return "HIGH"
    if question in ("Q6", "Q8", "Q10") or where in ("every", "some"):
        return "MED"
    return "LOW"
#: The questions a context leaf (a charter card or norm) may be flagged under.
CONTEXT_QUESTIONS = frozenset({"Q10", "Q11", "Q12"})
#: The provenance pass's one question, its class and its severity (AGENTS rule 2 at the
#: point of authorship reaches every seat the text reaches).
PROVENANCE = {"question": "P1", "class": "BEHAVIOUR-MIX", "severity": "HIGH"}
#: A finding's passage names a section of the authority text or an AGENTS rule.
PASSAGE = re.compile(r"^((Ch\. I )?§(I|II|III|IV)(\.[a-c])?(\(\d\))?|(AGENTS )?rule [1-5])"
                     r"( .*)?$")
FINDING_FIELDS: dict[str, type | tuple[type, ...]] = {
    "finding_id": str, "leaf_id": str, "world": str, "path": str, "surface_kind": str,
    "audience": list, "frequency": str, "quote": str, "question": str, "class": str,
    "severity": str, "passage": str, "rationale": str, "rewrite": str,
    "confidence": (int, float)}


def _words(text: str) -> int:
    return len(text.split())


def finding_problems(f: dict, records: dict[str, dict]) -> list[str]:
    """Why a finding is invalid (none when it is valid): every field of the protocol's
    output format present and typed; its leaf in the corpus and its world, path and tags
    that leaf's; its id ``sha256(path|quote)[:12]``; its quote the leaf's own words (at
    most 25); its class its question's and its severity one the rubric admits; a context
    leaf flagged only under Q10-Q12 and Q12 only on a norm; a passage from the authority
    text; a rationale of at most 60 words and a rewrite; a confidence in [0, 1]."""
    problems = []
    for name, kind in FINDING_FIELDS.items():
        value = f.get(name)
        if not isinstance(value, kind) or isinstance(value, bool):
            problems.append(f"{name} missing or not {getattr(kind, '__name__', kind)}")
    if problems:
        return problems
    leaf = records.get(f["leaf_id"])
    if leaf is None:
        return [f"leaf {f['leaf_id']} is not in the corpus"]
    for name in ("world", "path", "surface_kind", "audience", "frequency"):
        if f[name] != leaf.get(name):
            problems.append(f"{name} {f[name]!r} is not its leaf's {leaf.get(name)!r}")
    if f["finding_id"] != leaf_id(f["path"], f["quote"]):
        problems.append("finding_id is not sha256(path|quote)[:12]")
    if not f["quote"].strip() or f["quote"] not in leaf["text"]:
        problems.append("the quote is not the leaf's own words")
    if _words(f["quote"]) > 25:
        problems.append("the quote is longer than 25 words")
    question = f["question"]
    if question not in CLASS_OF:
        problems.append(f"question {question!r} yields no finding")
    else:
        if f["class"] != CLASS_OF[question]:
            problems.append(f"class {f['class']!r} is not {question}'s {CLASS_OF[question]}")
        wanted = required_severity(question, str(leaf.get("frequency", "")))
        if f["severity"] != wanted:
            problems.append(f"severity {f['severity']!r} is not {wanted!r}, what {question} "
                            f"on a leaf read {leaf.get('frequency')!r} is")
        if leaf["provenance"] != "kernel" and question not in CONTEXT_QUESTIONS:
            problems.append(f"a context leaf flagged under {question}")
        if question == "Q12" and "norm" not in leaf["provenance"]:
            problems.append("Q12 on a leaf that is not a norm")
    if not PASSAGE.match(f["passage"]):
        problems.append(f"passage {f['passage']!r} names no authority section or rule")
    if _words(f["rationale"]) > 60 or not f["rationale"].strip():
        problems.append("the rationale is empty or longer than 60 words")
    if not f["rewrite"].strip():
        problems.append("no rewrite")
    if not 0 <= f["confidence"] <= 1:
        problems.append("confidence outside [0, 1]")
    return problems


def load_key(path: Path) -> tuple[dict, list[dict]]:
    """The key and the corpus it was rendered with, refused unless bound together.

    Guarantees: the key has its schema's fields; the ``auditor_input.jsonl``,
    ``prompt.md`` and ``provenance_prompt.md`` beside it hash to its ``corpus_sha``,
    ``prompt_sha`` and ``provenance_prompt_sha`` (the auditor read exactly the prompts
    rendered); every record has its fields and its id is the hash of
    its path and text; the expected leaves are exactly the corpus's kernel leaves; and
    every canary and control names a leaf of it.
    """
    path = Path(path)
    key = json.loads(path.read_text())
    required = {"schema": int, "worlds": list, "range": str, "range_shas": list,
                "release_commit": str, "corpus_sha": str, "prompt_sha": str,
                "provenance_prompt_sha": str,
                "provenance_id": str, "provenance_commits": list,
                "canaries": list, "controls": list, "expected_leaves": list,
                "expected_count": int}
    bad = [n for n, t in required.items() if not isinstance(key.get(n), t)]
    if bad or key.get("schema") != KEY_SCHEMA:
        raise AuditInputInvalid(f"the key is not a schema-{KEY_SCHEMA} key: {bad}")
    for name, field in (("auditor_input.jsonl", "corpus_sha"), ("prompt.md", "prompt_sha"),
                        ("provenance_prompt.md", "provenance_prompt_sha")):
        beside = path.parent / name
        if not beside.exists() or sha256_file(beside) != key[field]:
            raise AuditInputInvalid(f"the key is not bound to the {name} beside it")
    corpus_path = path.parent / "auditor_input.jsonl"
    records = _jsonl(corpus_path, "auditor_input.jsonl")
    for r in records:
        if any(not isinstance(r.get(f), str) for f in RECORD_FIELDS) \
                or r["leaf_id"] != leaf_id(r["path"], r["text"]):
            raise AuditInputInvalid("a corpus record is malformed or its id is not its hash")
    kernel = sorted({r["leaf_id"] for r in records if r["provenance"] == "kernel"})
    if kernel != sorted(key["expected_leaves"]) or key["expected_count"] != len(kernel):
        raise AuditInputInvalid("the key's expected leaves are not the corpus's kernel leaves")
    ids = {r["leaf_id"] for r in records}
    if any(c.get("leaf_id") not in ids for c in key["canaries"] + key["controls"]):
        raise AuditInputInvalid("a canary or control names a leaf the corpus lacks")
    return key, records


def read_output(path: Path) -> tuple[list[dict], dict | None]:
    """A sample's findings and its one summary (JSON Lines); refused if a line is not a
    JSON object or two summaries are given."""
    findings, summary = [], None
    for row in _jsonl(path, str(path)):
        if row.get("summary") is True:
            if summary is not None:
                raise AuditInputInvalid(f"{path} holds two summaries")
            summary = row
        else:
            findings.append(row)
    return findings, summary


def _sample_number(value: Any) -> bool:
    """A sample id is an integer 1..SAMPLES (never a bool, a float or a string)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= SAMPLES


def _completeness(summary: dict, expected: set[str], all_ids: set[str]) -> list[str]:
    """Why a sample's summary is not complete against the key (none when it is).

    Judged against the key render wrote, never the numbers the auditor reports about
    itself: every expected leaf is in the read set or listed unread, and an unread leaf
    fails the audit.
    """
    problems = []
    read, unread = summary.get("read"), summary.get("unread")
    if not isinstance(read, list) or not all(isinstance(i, str) for i in read):
        problems.append("read is not a list of leaf ids")
        read = []
    if not isinstance(unread, list) or not all(isinstance(i, str) for i in unread):
        problems.append("unread is not a list of leaf ids")
        unread = []
    read, unread = set(read), set(unread)
    if (read | unread) - all_ids:
        problems.append("the summary names leaves the corpus lacks: "
                        f"{len((read | unread) - all_ids)}")
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


def sample_problems(findings: list[dict], summary: dict | None, key: dict,
                    records: dict[str, dict]) -> list[str]:
    """Why one corpus sample is invalid (none when it is): its summary is bound to this
    corpus (``corpus_sha``) and names its sample id, is complete, and counts its own
    findings by class; every finding is valid (``finding_problems``) and appears once."""
    if summary is None:
        return ["no summary object"]
    problems = []
    if summary.get("corpus_sha") != key["corpus_sha"]:
        problems.append("the summary is not bound to this corpus (corpus_sha)")
    if not _sample_number(summary.get("sample")):
        problems.append(f"sample id {summary.get('sample')!r} is not 1..{SAMPLES}")
    problems += _completeness(summary, set(key["expected_leaves"]), set(records))
    counts: dict[str, int] = {}
    for i, f in enumerate(findings, 1):
        for p in finding_problems(f, records):
            problems.append(f"finding {i} ({f.get('finding_id')}): {p}")
        if isinstance(f.get("class"), str):
            counts[f["class"]] = counts.get(f["class"], 0) + 1
    if summary.get("by_class") != counts:
        problems.append(f"by_class {summary.get('by_class')!r} is not the findings' {counts}")
    ids = [finding_identity(f) for f in findings]
    if len(ids) != len(set(ids)):
        problems.append("a finding (id, question, class) is given twice")
    return problems


def provenance_problems(rows: list[dict], summary: dict | None, key: dict) -> list[str]:
    """Why one provenance sample is invalid (none when it is): its summary echoes the
    prompt's ``provenance_id`` and its sample id and lists every commit asked about;
    every commit is answered exactly once, ``behaviour_mix`` a boolean, and a yes quotes
    the commit's own message."""
    if summary is None:
        return ["no summary object"]
    messages = {c["sha"]: c["message"] for c in key["provenance_commits"]}
    problems = []
    if summary.get("provenance_id") != key["provenance_id"]:
        problems.append("the summary is not bound to this provenance prompt (provenance_id)")
    if not _sample_number(summary.get("sample")):
        problems.append(f"sample id {summary.get('sample')!r} is not 1..{SAMPLES}")
    read = summary.get("commits_read")
    if not isinstance(read, list) or set(read) != set(messages):
        problems.append("commits_read is not every commit asked about")
    answered = [row.get("sha") for row in rows]
    if sorted(a for a in answered if isinstance(a, str)) != sorted(messages) \
            or len(answered) != len(messages):
        problems.append("not every commit is answered exactly once")
    for row in rows:
        sha = row.get("sha")
        if sha not in messages:
            problems.append(f"an answer names a commit not asked about: {sha!r}")
            continue
        if not isinstance(row.get("behaviour_mix"), bool):
            problems.append(f"{sha[:12]}: behaviour_mix is not a boolean")
        elif row["behaviour_mix"]:
            quote = row.get("quote")
            if not isinstance(quote, str) or not quote.strip() or quote not in messages[sha]:
                problems.append(f"{sha[:12]}: a yes that does not quote the message")
        if not isinstance(row.get("rationale"), str):
            problems.append(f"{sha[:12]}: no rationale")
    return problems


def finding_identity(finding: dict) -> tuple:
    """A finding's identity everywhere (union, triage, gate): its protocol
    ``finding_id`` (``sha256(path|quote)[:12]``) with its question and class, so one
    quote read under two questions is two findings, never one."""
    return (finding.get("finding_id"), finding.get("question"), finding.get("class"))


def _finding_key(finding: dict) -> tuple:
    return finding_identity(finding)


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


def provenance_findings(samples: list[tuple[list[dict], dict | None]]) -> list[dict]:
    """Each commit any sample read as justified by a behaviour mix, once, as a HIGH
    finding (AGENTS rule 2); low confidence when not every sample said so."""
    flagged: dict[str, list[dict]] = {}
    for rows, _summary in samples:
        for row in rows:
            if row.get("behaviour_mix") is True:
                flagged.setdefault(row["sha"], []).append(row)
    out = []
    for sha, rows in flagged.items():
        path = f"commit:{sha}"
        out.append({"finding_id": leaf_id(path, ""), "leaf_id": None, "world": "*",
                    "path": path, "quote": rows[0]["quote"], **PROVENANCE,
                    "rationale": rows[0].get("rationale", ""), "provenance_pass": True,
                    "samples": len(rows), "low_confidence": len(rows) < len(samples)})
    return out


def _sample_ids(samples: list[tuple[list, dict | None]]) -> list:
    return [s.get("sample") if isinstance(s, dict) else None for _rows, s in samples]


def audit_verdict(samples: list[tuple[list[dict], dict | None]],
                  provenance: list[tuple[list[dict], dict | None]], key: dict,
                  records: list[dict]) -> dict:
    """Whether an audit is valid, and why not (design B2 calibration, Astra H-2).

    Guarantees: exactly ``SAMPLES`` corpus samples and ``SAMPLES`` provenance samples,
    each set numbered 1..SAMPLES once; every sample valid on its own (bound, complete,
    schema-valid findings); the canaries and controls scored on the corpus samples'
    union.
    """
    by_id = {r["leaf_id"]: r for r in records}
    problems: list[str] = []
    for name, group in (("corpus", samples), ("provenance", provenance)):
        if len(group) != SAMPLES:
            problems.append(f"{len(group)} {name} sample(s): the protocol runs the auditor "
                            f"{SAMPLES} times")
        elif sorted(i for i in _sample_ids(group) if isinstance(i, int)) != list(
                range(1, SAMPLES + 1)):
            problems.append(f"the {name} samples are not numbered 1..{SAMPLES} once each")
    for i, (findings, summary) in enumerate(samples, 1):
        problems += [f"sample {i}: {p}" for p in sample_problems(findings, summary, key, by_id)]
    for i, (rows, summary) in enumerate(provenance, 1):
        problems += [f"provenance sample {i}: {p}"
                     for p in provenance_problems(rows, summary, key)]
    findings = union(samples)

    def flagged(target: dict) -> list[dict]:
        return [f for f in findings if f.get("leaf_id") == target["leaf_id"]]

    found = []
    for canary in key["canaries"]:
        if any(h.get("question") == canary["question"] and h.get("class") == canary["class"]
               for h in flagged(canary)):
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
            "provenance_samples": len(provenance),
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
    return triage_header(text).get("Auditor family") or None


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


def world_findings(findings: list[dict], provenance: list[dict], key: dict,
                   world: str) -> list[dict]:
    """The findings one world's triage owns: that world's non-canary findings, the
    kernel's seat text (the same code runs in every world) and every provenance finding
    (a commit's text reaches every world it touches)."""
    planted = {c["leaf_id"] for c in key["canaries"]}
    own = [f for f in findings if f.get("world") in (world, corpus.KERNEL)
           and f.get("leaf_id") not in planted]
    return own + provenance


def triage_skeleton(rows: list[dict], verdict: dict, *, world: str, family: str,
                    key: dict, bindings: dict[str, str]) -> str:
    """The architect's triage file: its bindings, then one row per finding it owns."""
    lines = [f"# Class 2 audit triage: {world}", "",
             f"- Auditor family: {family}",
             f"- World: {world}",
             f"- Corpus: {key['corpus_sha']}",
             f"- Release range: {key['range']} ({'..'.join(key['range_shas'])})",
             *(f"- {name}: {value}" for name, value in bindings.items()),
             f"- Samples: {verdict.get('samples')}; provenance samples: "
             f"{verdict.get('provenance_samples')}",
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


def table_rows(text: str) -> list[dict[str, str]]:
    """A triage file's finding rows, by column name."""
    header, out = None, []
    for line in text.splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = _cells(line)
        if header is None:
            header = [c.lower() for c in cells]
            continue
        out.append(dict(zip(header, cells, strict=False)))
    return out


def release_gate(text: str, *, expected: list[dict] | None = None) -> list[str]:
    """Why a triage file's rows do not pass the release gate (none when they do).

    The protocol's gate: zero untriaged HIGH or MED findings; each disposition is one of
    ``DISPOSITIONS`` and a non-FIX one carries its reason; a charter card or norm is
    never FIX; every severity is from the enum. With ``expected`` (the findings ``gate``
    recomputed from the bound samples), the rows are exactly those findings, one row
    each, with the severity, question and class each carries; without it (a bare
    reading of a file), the file must also record its family and a valid canary score.
    """
    problems = []
    header = triage_header(text)
    if expected is None:
        if not header.get("Auditor family"):
            problems.append("the triage file records no auditor family")
        if "valid: True" not in text:
            problems.append("the triage file records no valid canary score")
    rows = table_rows(text)
    if expected is not None:
        # A row names its finding by the finding's identity (``finding_identity``): the
        # id, question and class columns together.
        want = {finding_identity(f): f for f in expected}
        seen: dict[tuple, int] = {}
        for row in rows:
            ident = (row.get("id", ""), row.get("question", ""), row.get("class", ""))
            seen[ident] = seen.get(ident, 0) + 1
            f = want.get(ident)
            if f is None:
                problems.append(f"a row names no finding of this audit: {ident!r}")
                continue
            if row.get("severity") != f.get("severity"):
                problems.append(f"{ident}: severity {row.get('severity')!r} is not the "
                                f"finding's {f.get('severity')!r}")
        problems += [f"finding {i} ({want[i]['severity']}) has no row" for i in want
                     if i not in seen]
        problems += [f"finding {i} has {n} rows" for i, n in seen.items() if n > 1]
    for row in rows:
        disposition = row.get("disposition", "").upper()
        where = f"{row.get('id') or row.get('path')}"
        severity = row.get("severity", "")
        if severity not in SEVERITIES:
            problems.append(f"{where}: severity {severity!r} is not one of {SEVERITIES}")
        if severity in ("HIGH", "MED") and not disposition:
            problems.append(f"untriaged {severity} finding {where}")
        if disposition and disposition not in DISPOSITIONS:
            problems.append(f"unknown disposition {disposition!r} on {where}")
        if disposition in DISPOSITIONS - {"FIX"} and not row.get("reason"):
            problems.append(f"{disposition} without a reason on {where}")
        if disposition == "FIX" and "/charter/" in row.get("path", ""):
            problems.append(f"a charter finding is not the kernel's to fix: {where}")
    return problems


def _hashes(value: str | None) -> list[str]:
    return sorted(h.strip() for h in (value or "").split(",") if h.strip())


def gate(world: str, triage: Path, key_path: Path, samples: list[Path],
         provenance: list[Path], *, release: str | None = None,
         repo: Path | None = None) -> list[str]:
    """The release gate for ``world``: recomputed from bound sources, never read from a
    stored result.

    Guarantees: the key is bound to its corpus and prompts (``load_key``) and audited
    the release being gated (``release``, or ``repo``'s HEAD: the key's
    ``release_commit``); the triage
    file names ``world`` (rendered by the key), the key's corpus and range, and exactly
    the corpus and provenance sample files given, by sha256; the audit those samples
    make is recomputed and must be valid (``audit_verdict``); the recorded family may
    audit the world (``family_refusal``, less the rotation, which the triage checked
    against the file it replaced); and the findings the world owns are recomputed from
    the samples (``world_findings``), each needing exactly one row with its severity,
    question and class, and each HIGH or MED one a disposition (``release_gate``).
    """
    if not triage.exists():
        return [f"no triage file at {triage}"]
    key, records = load_key(key_path)
    text = triage.read_text()
    header = triage_header(text)
    problems = []
    repo = repo or ROOT
    gated = release or _git(repo, "rev-parse", "--verify", "HEAD^{commit}").strip()
    try:
        gated = _git(repo, "rev-parse", "--verify", f"{gated}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        pass
    if key["release_commit"] != gated:
        problems.append(f"the key audited {key['release_commit'][:12]}, not the release "
                        f"gated {gated[:12]}")
    if header.get("World") != world or world not in key["worlds"]:
        problems.append(f"the triage file is of {header.get('World')!r}, not {world!r} of "
                        f"the rendered worlds {key['worlds']}")
    if header.get("Corpus") != key["corpus_sha"]:
        problems.append("the triage file is not bound to the key's corpus")
    if not str(header.get("Release range", "")).startswith(key["range"] + " "):
        problems.append("the triage file is not bound to the key's release range")
    for label, files in (("Samples sha256", samples),
                         ("Provenance samples sha256", provenance)):
        if _hashes(header.get(label)) != sorted(sha256_file(f) for f in files):
            problems.append(f"the sample files given are not the ones the triage file "
                            f"records ({label})")
    family = header.get("Auditor family")
    refusal = family_refusal(family, world, None) if family else "no auditor family"
    if refusal is not None:
        problems.append(f"the recorded family may not audit {world}: {refusal}")
    parsed = [read_output(f) for f in samples]
    parsed_provenance = [read_output(f) for f in provenance]
    verdict = audit_verdict(parsed, parsed_provenance, key, records)
    if not verdict["valid"]:
        problems += [f"the audit is invalid: {p}" for p in verdict["problems"]]
    expected = world_findings(union(parsed), provenance_findings(parsed_provenance), key,
                              world)
    return problems + release_gate(text, expected=expected)


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
    for name in ("validate", "triage"):
        v = sub.add_parser(name)
        v.add_argument("output", type=Path, nargs="+", help="one JSON Lines file per sample")
        v.add_argument("--provenance-samples", type=Path, nargs="+", required=True,
                       help="one JSON Lines file per provenance sample")
        v.add_argument("--key", type=Path, required=True)
        if name == "triage":
            v.add_argument("--world", required=True)
            v.add_argument("--family", required=True)
    g = sub.add_parser("gate")
    g.add_argument("--world", required=True)
    g.add_argument("--key", type=Path, required=True)
    g.add_argument("--samples", type=Path, nargs="+", required=True,
                   help="the corpus sample files the triage file records")
    g.add_argument("--provenance-samples", type=Path, nargs="+", required=True,
                   help="the provenance sample files the triage file records")
    g.add_argument("--triage", type=Path, default=None)
    g.add_argument("--release", default=None,
                   help="the release commit being gated (default: HEAD)")
    b = sub.add_parser("baseline")
    b.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except AuditInputInvalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    if args.command == "render":
        try:
            key = render(args.world, args.out, seed=args.seed, rendered=args.rendered,
                         release_range=args.range, repo=ROOT, essay=args.essay,
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
        problems = gate(args.world, path, args.key, args.samples, args.provenance_samples,
                        release=args.release)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    key, records = load_key(args.key)
    samples = [read_output(path) for path in args.output]
    provenance = [read_output(path) for path in args.provenance_samples]
    verdict = audit_verdict(samples, provenance, key, records)
    if args.command == "validate":
        print(json.dumps(verdict, indent=1))
        return 0 if verdict["valid"] else 1
    if args.world not in key["worlds"]:
        print(f"no triage: {args.world} is not a world this audit rendered "
              f"({key['worlds']})", file=sys.stderr)
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
    owned = world_findings(union(samples), provenance_findings(provenance), key, args.world)
    # The samples are the source: the gate recomputes the findings from them, so the
    # triage file records them by hash and stores no findings of its own.
    bindings = {"Samples sha256": ", ".join(sha256_file(p) for p in args.output),
                "Provenance samples sha256": ", ".join(sha256_file(p)
                                                       for p in args.provenance_samples)}
    path.write_text(triage_skeleton(owned, verdict, world=args.world, family=args.family,
                                    key=key, bindings=bindings))
    print(f"wrote {path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
