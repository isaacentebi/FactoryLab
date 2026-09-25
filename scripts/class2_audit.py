"""The Class 2 audit's operator tool: the auditor's corpus, its calibration, its triage.

Phase-2 design B1/B2, with Astra's H-2 amendment. The static audit (B1) runs in the
test suite; the LLM auditor (B2) is a release step the operator runs, because it makes
a model call and the test suite makes none (``tests/test_no_network.py``). This script
does everything around that call, offline:

  render    Write the auditor's input: every seat-visible leaf of the chosen worlds,
            tagged (surface kind, audience, frequency, provenance), with the canaries
            of ``docs/audits/class2/canaries.json`` planted at seeded positions and ten
            clean controls chosen from the smuggling audit's KEEP list. The key (which
            leaves are canaries and controls) is written to a separate file the auditor
            is never given. Also writes ``prompt.md``: the protocol, the rubric, the
            authority text, the allowlist and last release's triage.
  validate  Score an auditor's JSON Lines output against the key. Valid only when the
            summary is present, nothing is unread, at least 7 of the 8 canaries are
            found and every mandatory one (Q6, Q9, Q10) is, and at most 1 of 10
            controls is flagged. An invalid audit is rerun with the next family.
  triage    Write ``docs/audits/class2/<world>.md``: the skeleton the architect fills,
            one row per finding, with the family and the canary score recorded.
  baseline  Recompute the static audit's findings and surface registry after the
            architect's triage (tests/audit/class2_findings.json, class2_surfaces.toml).

The model call itself is the operator's: send ``prompt.md`` with ``auditor_input.jsonl``
to one model family that neither authored kernel text nor sits in the world, at
temperature 0, twice, under the prepaid guard's cap; save the union of the two outputs
as JSON Lines and run ``validate``. See ``docs/audits/class2/auditor-protocol.md``.

Examples::

    uv run python scripts/class2_audit.py render --world edition6-capital-loop \\
        --out work/class2/2026-10 --seed 7
    uv run python scripts/class2_audit.py validate work/class2/2026-10/auditor_output.jsonl \\
        --key work/class2/2026-10/canary_key.json
    uv run python scripts/class2_audit.py triage work/class2/2026-10/auditor_output.jsonl \\
        --key work/class2/2026-10/canary_key.json --world edition6-capital-loop --family X
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from random import Random
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CANARIES = ROOT / "docs/audits/class2/canaries.json"
PROTOCOL = ROOT / "docs/audits/class2/auditor-protocol.md"
TRIAGE_DIR = ROOT / "docs/audits/class2"
#: The overall calibration bar (Astra H-2): at least this many of the canaries found.
MIN_CANARIES = 7
#: At most this many of the clean controls may be flagged.
MAX_CONTROLS = 1

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
    """Every leaf the auditor reads, tagged; charter cards and norms as context."""
    from tests.audit import class2_corpus as corpus

    records: list[dict] = []
    seen: set[str] = set()
    for world in worlds:
        leaves = corpus.render_static(world)
        if rendered:
            leaves += corpus.render_dynamic(world).leaves
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


def plant(records: list[dict], *, seed: int, world: str) -> tuple[list[dict], dict]:
    """Insert the canaries at seeded positions and choose the controls; return the key."""
    spec = json.loads(CANARIES.read_text())
    rng = Random(seed)
    out = list(records)
    key: dict[str, Any] = {"seed": seed, "canaries": [], "controls": []}
    for canary in spec["canaries"]:
        path = f"{world}/{canary['surface']}"
        record = {"leaf_id": leaf_id(path, canary["text"]), "world": world, "path": path,
                  "text": canary["text"], "provenance": "kernel", **describe(path)}
        out.insert(rng.randrange(len(out) + 1), record)
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


def write_prompt(out: Path, *, essay: Path | None, previous: Path | None) -> Path:
    from tests.audit import class2_lexicon as lexicon

    agents = (ROOT / "AGENTS.md").read_text()
    rules = agents.split("## Chapter II, as design rules", 1)[-1].split("6. **The reward", 1)[0]
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
        "## Last release's triage",
        "",
        previous.read_text() if previous is not None and previous.exists()
        else "(none: this is the first release audited)",
        "",
        "## The corpus",
        "",
        "One JSON object per line in auditor_input.jsonl: leaf_id, world, path, text, "
        "surface_kind, audience, frequency, provenance. Answer every leaf whose provenance "
        "is `kernel`; read the others as context only.",
    ]
    path = out / "prompt.md"
    path.write_text("\n".join(parts) + "\n")
    return path


def render(worlds: list[str], out: Path, *, seed: int, rendered: bool,
           essay: Path | None = None, previous: Path | None = None) -> dict:
    """Write the auditor's input, its prompt and the separate key; return the key."""
    out.mkdir(parents=True, exist_ok=True)
    records = corpus_records(worlds, rendered=rendered)
    planted, key = plant(records, seed=seed, world=worlds[0])
    with (out / "auditor_input.jsonl").open("w") as handle:
        for record in planted:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    (out / "canary_key.json").write_text(json.dumps(key, indent=1, sort_keys=True) + "\n")
    write_prompt(out, essay=essay, previous=previous)
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


def validate(findings: list[dict], summary: dict | None, key: dict) -> dict:
    """Whether an audit is valid, and why not (design B2 calibration, Astra H-2)."""
    def flagged(target: dict) -> list[dict]:
        # A finding names its leaf by id; a path alone is read only when it names none,
        # since a planted leaf may share its path with a real one.
        return [f for f in findings if f.get("leaf_id") == target["leaf_id"]
                or (not f.get("leaf_id") and f.get("path") == target["path"])]

    problems: list[str] = []
    if summary is None:
        problems.append("no summary object")
    else:
        if summary.get("unread"):
            problems.append(f"unread leaves: {len(summary['unread'])}")
        if summary.get("leaves_read") != summary.get("leaves_total"):
            problems.append("leaves_read differs from leaves_total")
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
    return {"valid": not problems, "problems": problems,
            "canaries_found": f"{len(found)}/{len(key['canaries'])}",
            "missed": [c["id"] for c in missed],
            "controls_flagged": f"{len(controls)}/{len(key['controls'])}"}


def triage_skeleton(findings: list[dict], verdict: dict, *, world: str, family: str,
                    key: dict) -> str:
    """The architect's triage file: one row per finding that is not a canary."""
    planted = {c["leaf_id"] for c in key["canaries"]}
    rows = [f for f in findings if f.get("leaf_id") not in planted]
    lines = [f"# Class 2 audit triage: {world}", "",
             f"- Auditor family: {family}",
             f"- Canaries found: {verdict['canaries_found']}; controls flagged: "
             f"{verdict['controls_flagged']}; valid: {verdict['valid']}",
             "- Dispositions: FIX (a rewrite or deletion, plus a regression entry), ALLOW (an "
             "allowlist entry with a reason and a passage), REJECT (the auditor is wrong; "
             "copied to rejected.jsonl with the reason).", "",
             "| id | path | question | class | severity | quote | disposition | reason |",
             "|---|---|---|---|---|---|---|---|"]
    for f in rows:
        quote = str(f.get("quote", "")).replace("|", "\\|")[:120]
        lines.append(f"| {f.get('finding_id', '')} | `{f.get('path', '')}` | "
                     f"{f.get('question', '')} | {f.get('class', '')} | "
                     f"{f.get('severity', '')} | {quote} |  |  |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render")
    r.add_argument("--world", action="append", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--seed", type=int, required=True)
    r.add_argument("--rendered", action="store_true", help="also run each world 60 ticks")
    r.add_argument("--essay", type=Path, default=ROOT / "docs/essay.md")
    r.add_argument("--previous", type=Path, default=None)
    for name in ("validate", "triage"):
        v = sub.add_parser(name)
        v.add_argument("output", type=Path)
        v.add_argument("--key", type=Path, required=True)
        if name == "triage":
            v.add_argument("--world", required=True)
            v.add_argument("--family", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "render":
        key = render(args.world, args.out, seed=args.seed, rendered=args.rendered,
                     essay=args.essay, previous=args.previous)
        print(f"wrote {args.out}/auditor_input.jsonl, prompt.md and canary_key.json "
              f"({len(key['canaries'])} canaries, {len(key['controls'])} controls)")
        return 0
    if args.command == "baseline":
        from tests.audit import class2_audit, class2_corpus

        document = class2_audit.write_baseline(class2_corpus.launchable_worlds(),
                                               rendered=not args.static_only)
        print(f"{len(document['findings'])} findings written")
        return 0
    findings, summary = read_output(args.output)
    key = json.loads(args.key.read_text())
    verdict = validate(findings, summary, key)
    if args.command == "validate":
        print(json.dumps(verdict, indent=1))
        return 0 if verdict["valid"] else 1
    text = triage_skeleton(findings, verdict, world=args.world, family=args.family, key=key)
    path = TRIAGE_DIR / f"{args.world}.md"
    path.write_text(text)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
