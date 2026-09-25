"""The closed lexicon of the Class 2 static audit (phase-2 design B1).

Chapter I §I: an objective is "the standard by which this or that plan is identified
as better"; a Class 2 factory "takes objectives … as inputs". AGENTS.md rules 1 and 3:
no prompt, tool description, default or refusal text tells a seat what to do, what is
good or how cautious to be, and a kernel rule is never restated as an instruction,
warning or invitation. This module reads seat-visible text for the surface forms of
those acts. It is pure, closed and dependency-free: a finding is ``(rule, path,
quote)``. What it cannot read (salience, tone, anchoring, contradiction across
surfaces) is the LLM auditor's rubric (B2), not this lexicon's.

Rules:

* **IMP** — a sentence opening with a base-form verb from a closed list, addressed to
  the reader. Allowed: **FORMAT**, a reply/return/answer verb whose object is only the
  output's syntax (a JSON object, the outcome schema) — the I/O contract (§I).
* **DEON** — a modal of obligation on an agent (you, a seat, a judge, …). A modal on a
  field, value or schema ("the price must be positive") is a contract predicate.
* **ADV** — advisory vocabulary.
* **EVAL** — an evaluative predicate. Allowed: a collocation bound to a world measure
  ("best bid"), from the allowlist's global collocations.
* **ANN** — physics announced to the reader ("you will be scored", "in order to earn").
* **QUOTA** — a directional count over an action noun with no formula or refusal.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RULES = ("IMP", "DEON", "ADV", "EVAL", "ANN", "QUOTA")

#: Sentence-initial base-form verbs addressed to the reader (design B1, closed).
IMPERATIVE_VERBS = frozenset({
    "use", "retrieve", "keep", "do", "don't", "dont", "avoid", "consider", "try", "prefer",
    "make", "ensure", "look", "account", "test", "separate", "distinguish", "acknowledge",
    "answer", "give", "assess", "respond", "read", "choose", "pick", "decide", "report",
    "include", "omit", "be", "remember", "note", "focus", "check", "verify", "vote",
    "testify", "reply", "return", "submit", "call", "treat", "never", "always", "think",
    "act", "follow", "ignore", "prioritize", "prioritise", "evaluate", "judge", "grade",
    "estimate", "predict",
})

#: FORMAT: a reply verb whose object is only the output's syntax is the I/O contract.
FORMAT_VERBS = frozenset({"reply", "return", "answer", "respond", "submit"})
FORMAT_OBJECT = re.compile(
    r"\b(json|object|outcome schema|schema|tool_calls|field|fields|form)\b")

AGENTS = (r"(?:you|your \w+|a seat|seats|the seat|this seat|an assembly|assemblies|a judge|"
          r"judges|the judge|a producer|producers|an evaluator|evaluators|a meta|metas)")
DEON = re.compile(rf"\b{AGENTS}\s+(?:should|must|ought|need to|needs to|have to|has to|"
                  r"is expected to|are expected to|is required to|are required to)\b")

ADVISORY = ("consider", "try to", "recommended", "recommend", "advisable", "prudent",
            "careful", "carefully", "caution", "cautious", "make sure", "ideally",
            "preferably", "encouraged", "it is important", "feel free", "best practice",
            "remember to", "beware", "wise", "be conservative")
ADV = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in ADVISORY) + r")\b")

EVALUATIVE = ("better", "best", "worse", "worst", "good", "bad", "valuable", "worthwhile",
              "useful", "harmful", "appropriate", "sensible", "reasonable", "optimal",
              "ideal", "desirable", "poor", "smart", "wisely", "worth")
EVAL = re.compile(r"\b(?:" + "|".join(EVALUATIVE) + r")\b")

ANN = re.compile(r"\byou (?:will|would) be (?:scored|rewarded|paid|penalized|penalised|graded|"
                 r"charged)\b|\bso (?:that )?you\b|\bin order to (?:earn|score|avoid)\b|"
                 r"\bearns? (?:more|less)\b|\bto (?:maximi[sz]e|minimi[sz]e|increase|reduce) "
                 r"your\b")

QUOTA = re.compile(r"\b(?:at least|at most|no more than|no fewer than)\s+(?:\w+\s+){0,3}?"
                   r"(?:trades?|registrations?|orders?|forecasts?|calls?|amendments?)\b")
#: A count bound stated as a formula or an enforced refusal is physics, not a quota.
QUOTA_CONTEXT = re.compile(r"=|\brefused\b|\brefuses\b|\bmaxitems\b|\bmax_|\bper call\b")

_SPLIT = re.compile(r"(?<=[.;:!?])\s+|\n+")
_LEAD = re.compile(r"^[\s\"'`(\[{*\-–—•>#0-9.)]+")


@dataclass(frozen=True)
class Finding:
    """One lexicon hit: the rule, the leaf's path and the sentence it was found in."""

    rule: str
    path: str
    quote: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.path, self.quote)


def sentences(text: str) -> list[str]:
    """The text split on sentence ends, semicolons, colons and newlines, casefolded."""
    return [s.strip().casefold() for s in _SPLIT.split(text) if s and s.strip()]


def _first_word(sentence: str) -> str:
    stripped = _LEAD.sub("", sentence)
    match = re.match(r"[a-z']+", stripped)
    return match.group(0) if match else ""


def lint_text(path: str, text: str, *,
              collocations: Iterable[str] = ()) -> list[Finding]:
    """Every finding in one seat-visible leaf, in sentence order.

    A leaf with no whitespace is an identifier or an enum value, not an address to
    the reader, and is not read. Collocations are removed before the EVAL rule reads
    a sentence (``"best bid"`` names a world measure, not a standard of betterness).
    """
    if not isinstance(text, str) or not re.search(r"\s", text.strip()):
        return []
    colloc = [c.casefold() for c in collocations]
    found: list[Finding] = []
    for sentence in sentences(text):
        quote = sentence[:200]
        word = _first_word(sentence)
        if word in IMPERATIVE_VERBS and re.search(r"\s", sentence):
            rest = sentence[sentence.find(word) + len(word):]
            if not (word in FORMAT_VERBS and FORMAT_OBJECT.search(rest)):
                found.append(Finding("IMP", path, quote))
        if DEON.search(sentence):
            found.append(Finding("DEON", path, quote))
        if ADV.search(sentence):
            found.append(Finding("ADV", path, quote))
        scrubbed = sentence
        for phrase in colloc:
            scrubbed = scrubbed.replace(phrase, " ")
        if EVAL.search(scrubbed):
            found.append(Finding("EVAL", path, quote))
        if ANN.search(sentence):
            found.append(Finding("ANN", path, quote))
        if QUOTA.search(sentence) and not QUOTA_CONTEXT.search(sentence):
            found.append(Finding("QUOTA", path, quote))
    return found


def lint(leaves: Iterable[tuple[str, str]], *,
         collocations: Iterable[str] = ()) -> list[Finding]:
    """Every finding over ``(path, text)`` leaves, deduplicated, in a stable order."""
    colloc = tuple(collocations)
    seen: dict[tuple[str, str, str], Finding] = {}
    for path, text in leaves:
        for finding in lint_text(path, text, collocations=colloc):
            seen.setdefault(finding.key, finding)
    return sorted(seen.values(), key=lambda f: f.key)


# --- the allowlist and the triage baseline ----------------------------------------------

#: The Chapter II passages an allowlist reason may cite (design B1, closed).
PASSAGES = frozenset({
    "§I (contract / I/O)", "§I.b(1) (schematics public)", "§I.b (request channel rich)",
    "§II.b (hard cast)", "§IV.a (norm house)",
    "Ch. I §I (Class 3 input: norms and constraints)",
})
HERE = Path(__file__).resolve().parent
ALLOWLIST = HERE / "class2_allowlist.toml"
BASELINE = HERE / "class2_findings.json"


def load_allowlist(path: Path = ALLOWLIST) -> dict:
    """The allowlist's collocations and quote-level entries, as the TOML states them."""
    raw = tomllib.loads(path.read_text())
    return {"collocation": list(raw.get("collocation", ())), "allow": list(raw.get("allow", ()))}


def allowlist_problems(allowlist: dict) -> list[str]:
    """What makes the allowlist malformed: a missing reason or context, a passage outside
    the closed set, a path glob over anything but its world segment, an unknown rule."""
    problems = []
    for entry in [*allowlist["collocation"], *allowlist["allow"]]:
        if not str(entry.get("reason", "")).strip():
            problems.append(f"no reason: {entry}")
        if entry.get("passage") not in PASSAGES:
            problems.append(f"passage outside the closed set: {entry.get('passage')!r}")
    for entry in allowlist["allow"]:
        path = str(entry.get("path", ""))
        world, _, rest = path.partition("/")
        if not rest or "*" in rest or "?" in rest or ("*" in world and world != "*"):
            problems.append(f"a glob only over the world segment: {path!r}")
        if entry.get("rule") not in RULES:
            problems.append(f"unknown rule: {entry.get('rule')!r}")
        words = entry.get("context_words")
        if not isinstance(words, list) or not words or not all(
                isinstance(w, str) and w.strip() for w in words):
            problems.append(f"context_words missing: {path!r}")
        if not str(entry.get("quote", "")).strip():
            problems.append(f"no quote: {path!r}")
    return problems


def _context(text: str, quote: str) -> str:
    """The sentences within two of the one holding ``quote``, casefolded."""
    parts = sentences(text)
    hit = next((i for i, s in enumerate(parts) if quote.casefold() in s), None)
    if hit is None:
        return text.casefold()
    return " ".join(parts[max(0, hit - 2):hit + 3])


@dataclass(frozen=True)
class Triaged:
    """The lint after the allowlist: what remains, what each entry excused, what drifted."""

    findings: list[Finding]
    used: set[int]
    review: list[Finding]


def apply_allowlist(findings: Iterable[Finding], leaves: Iterable[tuple[str, str]],
                    allowlist: dict) -> Triaged:
    """Drop the findings an entry excuses; one whose ``context_words`` drifted stays, as
    REVIEW (Astra M-4). ``used`` names the entries that excused or reviewed a finding."""
    texts: dict[str, list[str]] = {}
    for path, text in leaves:
        texts.setdefault(path, []).append(text)
    remaining, review, used = [], [], set()
    for finding in findings:
        excused = False
        for index, entry in enumerate(allowlist["allow"]):
            if (entry["rule"] != finding.rule
                    or not fnmatch.fnmatchcase(finding.path, entry["path"])
                    or entry["quote"].casefold() not in finding.quote):
                continue
            used.add(index)
            context = " ".join(_context(t, entry["quote"]) for t in texts.get(finding.path, ())
                               if entry["quote"].casefold() in t.casefold())
            if all(w.casefold() in context for w in entry["context_words"]):
                excused = True
            else:
                review.append(finding)
            break
        if not excused:
            remaining.append(finding)
    return Triaged(remaining, used, review)


def finding_id(rule: str, path: str, quote: str) -> str:
    """A finding's stable id: ``sha256(path|rule|quote)[:12]``."""
    return hashlib.sha256(f"{path}|{rule}|{quote}".encode()).hexdigest()[:12]


BASELINE_FIELDS = ("id", "world", "surface", "rule", "path", "quote", "design_ref", "status")


def allowlist_sha(path: Path = ALLOWLIST) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline(path: Path = BASELINE, *, allowlist: Path = ALLOWLIST) -> list[dict]:
    """The findings awaiting the architect's triage, as committed.

    Refused (``ValueError``) unless bound and well formed: the document names the worlds
    it read and the allowlist that triaged it, and that allowlist is the one in force
    (an allowlist edit needs a new baseline); every row has exactly its fields, a known
    rule, surface and status, a world the document read, a path in that world, and the
    id ``finding_id(rule, path, quote)``.
    """
    document = json.loads(path.read_text())
    problems = []
    worlds = document.get("worlds")
    if not isinstance(worlds, list) or not worlds:
        problems.append("the baseline names no worlds")
        worlds = []
    if document.get("allowlist_sha256") != allowlist_sha(allowlist):
        problems.append("the baseline was not triaged by the allowlist in force")
    for i, row in enumerate(document.get("findings") or ()):
        if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(BASELINE_FIELDS)):
            problems.append(f"row {i} does not have exactly {BASELINE_FIELDS}")
            continue
        if row["rule"] not in RULES or row["surface"] not in ("static", "rendered") \
                or row["status"] not in ("untriaged", "REVIEW"):
            problems.append(f"row {i} ({row['id']}): unknown rule, surface or status")
        if row["world"] not in worlds or not str(row["path"]).startswith(row["world"] + "/"):
            problems.append(f"row {i} ({row['id']}): not a path of a world the baseline read")
        if row["id"] != finding_id(row["rule"], row["path"], row["quote"]):
            problems.append(f"row {i} ({row['id']}): the id is not the finding's")
    if problems:
        raise ValueError("class2_findings.json: " + "; ".join(problems[:10]))
    return document["findings"]


def compare(findings: Iterable[Finding], baseline: Iterable[dict]) -> dict[str, list]:
    """``new``: findings not yet triaged; ``stale``: baseline rows no longer found."""
    found = {finding_id(*f.key): f for f in findings}
    known = {row["id"]: row for row in baseline}
    return {"new": [found[k].key for k in sorted(set(found) - set(known))],
            "stale": [known[k] for k in sorted(set(known) - set(found))]}
