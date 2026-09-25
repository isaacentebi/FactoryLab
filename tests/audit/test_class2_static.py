"""The Class 2 static audit, check tier: the lexicon, the allowlist, the static corpus.

Chapter I §I: a Class 2 factory "takes objectives … as inputs", and an objective is "the
standard by which this or that plan is identified as better". AGENTS.md rules 1 and 3:
no seat-visible text tells a seat what to do or what is good, or restates a kernel
rule as an instruction. The lint reads the surface form of those acts in every string a
seat can see before any seat acts (``class2_corpus.render_static``).

Its findings are not fixed here: they await the architect's triage for the edition-7
text wave, pinned in ``class2_findings.json``. A new finding fails (it must be read); a
finding that is gone fails (its baseline row must go). The allowlist excuses only the
lexicon's misreadings, each with a reason, a Chapter II passage and context words.
"""

import pytest

from tests.audit import class2_audit as audit
from tests.audit import class2_corpus as corpus
from tests.audit import class2_lexicon as lexicon

WORLDS = corpus.launchable_worlds()


@pytest.fixture(scope="module")
def allowlist():
    return lexicon.load_allowlist()


@pytest.fixture(scope="module")
def static():
    """Each launchable world's static corpus, rendered once for the module."""
    return {world: corpus.render_static(world) for world in WORLDS}


# --- the lexicon: planted violations are caught, clean paraphrases pass -------------------

#: The design's six planted violations, one per surface (B1 mutation tests).
PLANTED = [
    ("*/institutions/scoring/producer", "You should hold when unsure.", "DEON"),
    ("*/tools/venue.positions/description", "Consider calling this first.", "ADV"),
    ("*/genesis/p1/lens", "Your starting prior is that building is better.", "EVAL"),
    ("*/request/judge/request/#prose", "Be conservative.", "IMP"),
    ("*/refusal/registration/tool", "Try a smaller size.", "IMP"),
    ("*/request/produce/inputs/continuation", "It is important to act.", "ADV"),
]


@pytest.mark.parametrize(("path", "text", "rule"), PLANTED)
def test_every_planted_violation_is_caught_with_its_rule(path, text, rule):
    rules = {f.rule for f in lexicon.lint_text(path, text)}
    assert rule in rules, (text, rules)


@pytest.mark.parametrize("text", [
    "The price must be a positive integer.",
    "score = 0.5 + 0.5 * (b - a), clipped to [0, 1].",
    "The order book of one outcome token, best price first on both sides.",
    "A decline settles at the router's observed mean less its role's penalty.",
    "Reply with a single JSON object that satisfies the outcome schema.",
    "A counter-verdict earns above 0.5 exactly when it beat the verdict it read.",
    "The best bid and best ask are the venue's own.",
])
def test_clean_paraphrases_pass(text, allowlist):
    collocations = [c["text"] for c in allowlist["collocation"]]
    assert lexicon.lint_text("*/institutions/x", text, collocations=collocations) == []


def test_identifiers_and_enum_values_are_not_read():
    assert lexicon.lint_text("*/institutions/x", "read") == []
    assert lexicon.lint_text("*/institutions/x", "venue.read") == []


def test_planted_violations_are_caught_inside_a_real_corpus(static):
    """The mutation, end to end: each planted sentence, appended to a real leaf of its
    surface in a launch world's corpus, comes out of the lint with its rule."""
    world = "edition6-capital-loop" if "edition6-capital-loop" in static else WORLDS[0]
    leaves = list(static[world])
    by_surface = {}
    for path, text in leaves:
        surface = path.split("/", 2)[1]
        by_surface.setdefault(surface, (path, text))
    planted = []
    for pattern, sentence, rule in PLANTED:
        surface = pattern.split("/")[1]
        path, text = by_surface.get(surface, (pattern.replace("*", world), ""))
        planted.append((path, f"{text}\n{sentence}", rule, sentence))
    found = lexicon.lint([*leaves, *[(p, t) for p, t, _r, _s in planted]])
    for path, _text, rule, sentence in planted:
        assert any(f.path == path and f.rule == rule and sentence.casefold()[:12] in f.quote
                   for f in found), (path, sentence)


# --- the allowlist -------------------------------------------------------------------------


def test_the_allowlist_is_well_formed(allowlist):
    """Every entry has a reason and a passage from the closed set; an [[allow]] entry is
    quote-level (a glob only over its world segment) and carries its context words."""
    assert lexicon.allowlist_problems(allowlist) == []
    print(f"allowlist: {len(allowlist['collocation'])} collocations, "
          f"{len(allowlist['allow'])} quote entries")


def test_a_malformed_allowlist_entry_is_refused():
    bad = {"collocation": [{"text": "best bid", "reason": "", "passage": "readability"}],
           "allow": [{"path": "*/institutions/*", "quote": "x", "rule": "NOPE",
                      "reason": "r", "passage": "§I (contract / I/O)"}]}
    problems = lexicon.allowlist_problems(bad)
    assert any("no reason" in p for p in problems)
    assert any("closed set" in p for p in problems)
    assert any("world segment" in p for p in problems)
    assert any("unknown rule" in p for p in problems)
    assert any("context_words" in p for p in problems)


def test_an_allow_entry_whose_context_drifted_comes_back_as_review(allowlist):
    """Astra M-4: the same quote at the same path, with its surroundings rewritten so the
    context words are gone, is no longer excused; it is a finding again, as REVIEW."""
    entry = allowlist["allow"][0]
    path = entry["path"].replace("*", "w")
    drifted = [(path, f"lambda facts: facts\n{entry['quote']}")]
    kept = [(path, f"{' '.join(entry['context_words'])}:\n    {entry['quote']}")]
    for leaves, excused in ((kept, True), (drifted, False)):
        found = lexicon.lint(leaves)
        result = lexicon.apply_allowlist(found, leaves, allowlist)
        mine = [f for f in found if entry["quote"].casefold() in f.quote]
        assert mine
        assert (not set(mine) & set(result.findings)) is excused
        assert bool(result.review) is not excused


def test_every_allow_entry_matches_a_current_finding(static, allowlist):
    """An entry that matches nothing fails, so the list cannot silently cover text that has
    since changed. Entries on rendered surfaces are checked by the gate test."""
    used = set()
    for leaves in static.values():
        result = audit.triage(leaves, allowlist)
        used |= result.used
    static_entries = {i for i, e in enumerate(allowlist["allow"])
                      if "/request/" not in e["path"] and "/system/" not in e["path"]}
    assert static_entries <= used, [allowlist["allow"][i] for i in static_entries - used]


# --- the static corpus against the triage baseline -------------------------------------


@pytest.mark.parametrize("world", WORLDS)
def test_the_static_corpus_has_no_untriaged_finding_and_no_stale_one(world, static, allowlist):
    result = audit.triage(static[world], allowlist)
    assert not result.review, [f.key for f in result.review]
    drift = lexicon.compare(result.findings, audit.baseline_rows(world, "static"))
    assert drift == {"new": [], "stale": []}, drift


def test_every_static_surface_is_registered_and_every_registered_one_is_rendered(static):
    """The coverage registry: a new surface forces its classification; a registered surface
    nothing renders means the corpus stopped reaching it."""
    rendered = set().union(*(audit.surfaces_of(leaves) for leaves in static.values()))
    registered = set(audit.load_surfaces()["static"])
    assert rendered - registered == set(), "unregistered surfaces"
    assert registered - rendered == set(), "registered but not rendered"


def test_the_baseline_names_the_design_findings_it_confirms():
    """The day-one findings the design read from the code are in the baseline."""
    refs = {row["design_ref"] for row in lexicon.load_baseline()}
    assert {"B1-1", "B1-2", "B1-4", "B1-5", "B1-7"} <= refs


def _request_builders_in_code() -> dict[str, list[int]]:
    """Every function in ``factorylab`` that builds a seat request, by ``file::function``:
    one constructing a ``factorylab.cortex.request.Request``, one calling the runtime's
    request constructor (``self._request``), or one building a tool-round continuation."""
    import ast

    found: dict[str, list[int]] = {}
    for path in sorted((corpus.ROOT / "factorylab").rglob("*.py")):
        tree = ast.parse(path.read_text())
        rel = path.relative_to(corpus.ROOT).as_posix()
        imports_request = any(
            isinstance(node, ast.ImportFrom) and node.module == "factorylab.cortex.request"
            and any(alias.name == "Request" for alias in node.names)
            for node in ast.walk(tree))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                builds = (
                    (imports_request and isinstance(f, ast.Name) and f.id == "Request")
                    or (isinstance(f, ast.Attribute) and f.attr == "continuation")
                    or (rel.startswith("factorylab/runtime/") and isinstance(f, ast.Attribute)
                        and f.attr == "_request" and isinstance(f.value, ast.Name)
                        and f.value.id == "self"))
                if builds:
                    found.setdefault(f"{rel}::{fn.name}", []).append(node.lineno)
    return found


def test_every_request_builder_in_the_code_is_rendered_by_the_corpus():
    """The registry may not accept a seat-visible request builder the corpus never renders:
    every builder the code holds is registered, and every registered one is rendered
    (``class2_surfaces.toml [builders]``, which the gate tier checks against the renders)."""
    in_code = set(_request_builders_in_code()) - corpus.REQUEST_HELPERS
    assert in_code - corpus.REQUEST_BUILDERS == set(), "a request builder the corpus ignores"
    assert corpus.REQUEST_BUILDERS - in_code == set(), "a registered builder is gone"
    assert corpus.REQUEST_BUILDERS - set(audit.load_surfaces()["builders"]) == set(), \
        "a request builder the corpus never renders"


def test_the_committee_requests_are_in_the_static_corpus(static):
    """The ballots and the testimony no short run reaches are rendered statically, through
    the real builders (``class2_corpus.render_governance``)."""
    forms = {"/".join(s.split("/")[:2])
             for leaves in static.values() for s in audit.surfaces_of(leaves)}
    assert {"request/vote", "request/testify"} <= forms
    assert audit.static_builders(WORLDS[:1]) == {
        "factorylab/runtime/governance.py::_hold_vote",
        "factorylab/runtime/governance.py::_testify"}


def test_an_unrendered_or_unregistered_builder_fails_the_check(monkeypatch):
    """The builder check bites: a builder the registry says no render reached fails, and
    so does a builder in the code the corpus does not name."""
    registry = audit.load_surfaces()
    dropped = {**registry, "builders": registry["builders"][1:]}
    monkeypatch.setattr(audit, "load_surfaces", lambda: dropped)
    with pytest.raises(AssertionError, match="never renders"):
        test_every_request_builder_in_the_code_is_rendered_by_the_corpus()
    monkeypatch.setattr(audit, "load_surfaces", lambda: registry)
    monkeypatch.setattr(corpus, "REQUEST_BUILDERS",
                        corpus.REQUEST_BUILDERS - {"factorylab/runtime/governance.py::_testify"})
    with pytest.raises(AssertionError, match="ignores"):
        test_every_request_builder_in_the_code_is_rendered_by_the_corpus()
