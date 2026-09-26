"""The Class 2 static audit (B1), end to end: render, lint, triage against the baseline.

Shared by the check-tier test (the static corpus), the gate-tier test (the rendered
requests) and ``scripts/class2_audit.py`` (the operator's corpus, and the baseline
rewrite after the architect's triage). Nothing here changes seat-visible text.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable

from tests.audit import class2_corpus as corpus
from tests.audit import class2_lexicon as lexicon

#: The design's day-one findings (phase-2 design §5.1, "Expected day-one findings"), by a
#: quote fragment, so the baseline reads against the triage table the architect has.
DESIGN_REFS = {
    "do not invent a capability": "B1-1",
    "retrieve a full argument schema": "B1-2",
    "give verdict 0-1": "B1-4", "give your own verdict": "B1-4", "assess the": "B1-4",
    "respond to event": "B1-4", "vote on": "B1-4", "testify on": "B1-4",
    "return the final answer": "B1-5", "you may call tools again": "B1-5",
    "reusable capability may be a valuable": "B1-7",
    "best available alternative": "B3 (opportunity lens; Astra M-1)",
}


def reference(quote: str) -> str | None:
    return next((ref for fragment, ref in DESIGN_REFS.items() if fragment in quote), None)


def triage(leaves: list[tuple[str, str]], allowlist: dict) -> lexicon.Triaged:
    """The lint of ``leaves`` after the allowlist's collocations and entries."""
    collocations = [c["text"] for c in allowlist["collocation"]]
    found = lexicon.lint(leaves, collocations=collocations)
    return lexicon.apply_allowlist(found, leaves, allowlist)


def rows(world: str, surface: str, findings: Iterable[lexicon.Finding],
         review: Iterable[lexicon.Finding] = ()) -> list[dict]:
    """Baseline rows for one world's findings on one surface."""
    reviewed = {f.key for f in review}
    return [{"id": lexicon.finding_id(*f.key), "world": world, "surface": surface,
             "rule": f.rule, "path": f.path, "quote": f.quote,
             "design_ref": reference(f.quote),
             "status": "REVIEW" if f.key in reviewed else "untriaged"}
            for f in findings]


def baseline_rows(world: str, surface: str) -> list[dict]:
    return [row for row in lexicon.load_baseline()
            if row["world"] == world and row["surface"] == surface]


SURFACES = lexicon.HERE / "class2_surfaces.toml"

def load_surfaces() -> dict[str, list[str]]:
    """The registry: the static and rendered surfaces, the request builders rendered, and
    the seat-text sources the code holds (``class2_seat_text``).

    Refused (``ValueError``) unless each list is a sorted list of distinct strings, as
    ``write_baseline`` writes it: a hand edit that duplicates or misorders an entry is
    not the registry the renders produced."""
    raw = tomllib.loads(SURFACES.read_text())
    out = {"static": raw.get("static", {}).get("surfaces"),
           "rendered": raw.get("rendered", {}).get("surfaces"),
           "builders": raw.get("builders", {}).get("rendered"),
           "seat_text": raw.get("seat_text", {}).get("sources")}
    for name, items in out.items():
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items) \
                or items != sorted(set(items)):
            raise ValueError(f"class2_surfaces.toml: {name} is not a sorted list of "
                             "distinct strings")
    return out


def static_builders(worlds: Iterable[str]) -> set[str]:
    """The request builders the static corpus renders (the committee's, statically)."""
    return set().union(*(corpus.require_complete(corpus.render_governance(w)).builders
                         for w in worlds))


def surfaces_of(leaves: Iterable[tuple[str, str]]) -> set[str]:
    return {corpus.surface_id(path) for path, _text in leaves}


def write_baseline(worlds: Iterable[str], *, rendered: bool = True) -> dict:
    """Recompute every finding and surface over ``worlds``; write the baseline and registry.

    The operator's step after the architect's triage (a FIX removes a finding, an ALLOW
    adds an allowlist entry): never a way to make a new finding pass unread. Every
    render is taken before anything is written, and a rendered run that did not
    complete refuses the whole rewrite (``RenderFailed``): a partial corpus would drop
    findings and surfaces from the tracked files.
    """
    worlds = list(worlds)
    dynamics = ({world: corpus.require_complete(corpus.render_dynamic(world))
                 for world in worlds} if rendered else {})
    builders = static_builders(worlds) | set().union(*(d.builders for d in dynamics.values()))
    if not rendered:
        builders |= set(load_surfaces()["builders"])
    allowlist = lexicon.load_allowlist()
    out: list[dict] = []
    seen = {"static": set(), "rendered": set()}
    if not rendered:
        # A static-only refresh keeps the rendered half as it was triaged.
        out += [row for row in lexicon.load_baseline() if row["surface"] == "rendered"]
        seen["rendered"] = set(load_surfaces()["rendered"])
    # The seat text in the kernel's code, read once (the same in every world).
    from tests.audit import class2_seat_text

    seat = class2_seat_text.scan()
    seat_leaves = corpus.render_seat_text(seat)
    seen["static"] |= surfaces_of(seat_leaves)
    result = triage(seat_leaves, allowlist)
    out += rows(corpus.KERNEL, "static", result.findings, result.review)
    for world in worlds:
        static = corpus.render_static(world)
        seen["static"] |= surfaces_of(static)
        result = triage(static, allowlist)
        out += rows(world, "static", result.findings, result.review)
        if rendered:
            dynamic = dynamics[world]
            seen["rendered"] |= surfaces_of(dynamic.leaves)
            result = triage(dynamic.leaves, allowlist)
            out += rows(world, "rendered", result.findings, result.review)
    lines = ["# Every seat-visible surface the Class 2 audit reads (phase-2 design B1).",
             "# A rendered surface not listed here fails the audit, which forces its",
             "# classification; a listed surface no run renders fails too: extend the",
             "# population rather than deleting the entry. A request builder the corpus",
             "# never renders fails the check tier. Rewritten by",
             "# `scripts/class2_audit.py baseline` after the architect's triage.", ""]
    for surface in ("static", "rendered"):
        lines.append(f"[{surface}]")
        lines.append("surfaces = [")
        lines += [f'  "{s}",' for s in sorted(seen[surface])]
        lines += ["]", ""]
    lines.append("[builders]")
    lines.append("# The request builders (class2_corpus.REQUEST_BUILDERS) the corpus renders.")
    lines.append("rendered = [")
    lines += [f'  "{b}",' for b in sorted(builders)]
    lines += ["]", ""]
    lines.append("[seat_text]")
    lines.append("# Every function whose text can reach a seat (class2_seat_text): a new one")
    lines.append("# fails the check tier until the baseline is rewritten and triaged.")
    lines.append("sources = [")
    lines += [f'  "{s}",' for s in sorted(seat.sources)]
    lines += ["]"]
    SURFACES.write_text("\n".join(lines) + "\n")
    document = {
        "status": ("Untriaged. The Class 2 audit's findings await the architect's ruling for "
                   "the edition-7 text wave; no seat-visible text is rewritten on the branch "
                   "that found them (phase-2 brief)."),
        # Bound: the worlds read and the allowlist that triaged them (load_baseline).
        "worlds": sorted([*worlds, corpus.KERNEL]),
        "allowlist_sha256": lexicon.allowlist_sha(),
        "findings": sorted(out, key=lambda r: (r["world"], r["surface"], r["rule"],
                                               r["path"], r["quote"])),
    }
    lexicon.BASELINE.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n")
    return document
