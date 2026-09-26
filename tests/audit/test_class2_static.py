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


def test_every_static_surface_is_registered_and_every_registered_one_is_rendered(static,
                                                                                  seat):
    """The coverage registry: a new surface forces its classification; a registered surface
    nothing renders means the corpus stopped reaching it. The kernel's seat text is a
    static surface too."""
    rendered = set().union(*(audit.surfaces_of(leaves) for leaves in static.values()),
                           audit.surfaces_of(corpus.render_seat_text(seat)))
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


# --- the baseline and the registry: bound and validated (the class2_audit sweep) ----------


def _baseline_copy(tmp_path, change):
    import json

    document = json.loads(lexicon.BASELINE.read_text())
    change(document)
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize("change, why", [
    (lambda d: d["findings"][0].update(quote="edited"), "the id is not the finding's"),
    (lambda d: d["findings"][0].update(rule="VIBES"), "unknown rule"),
    (lambda d: d["findings"][0].update(world="elsewhere"), "not a path of a world"),
    (lambda d: d["findings"][0].pop("status"), "does not have exactly"),
    (lambda d: d.pop("worlds"), "names no worlds"),
    (lambda d: d.update(allowlist_sha256="0" * 64), "not triaged by the allowlist in force"),
])
def test_a_baseline_that_is_unbound_or_malformed_is_refused(tmp_path, change, why):
    """The tracked findings are bound to the worlds they read and the allowlist that
    triaged them, and every row is the finding its id names."""
    assert lexicon.load_baseline()  # the committed baseline is well formed and bound
    with pytest.raises(ValueError, match=why):
        lexicon.load_baseline(_baseline_copy(tmp_path, change))


def test_a_hand_edited_surface_registry_is_refused(tmp_path, monkeypatch):
    text = audit.SURFACES.read_text()
    first = next(line for line in text.splitlines() if line.startswith('  "'))
    edited = tmp_path / "surfaces.toml"
    edited.write_text(text.replace(first, first + "\n" + first, 1))
    monkeypatch.setattr(audit, "SURFACES", edited)
    with pytest.raises(ValueError, match="sorted list of distinct strings"):
        audit.load_surfaces()


# --- seat text in the kernel's code: every string a seat can read back ----------------------


@pytest.fixture(scope="module")
def seat():
    """The seat-text scan (``class2_seat_text``), taken once for the module."""
    from tests.audit import class2_seat_text

    return class2_seat_text.scan()


def test_every_seat_text_source_is_registered_and_rendered(seat):
    """The class, not the instance (Codex P2, ``_refuse_request``): every function whose
    text can reach a seat is registered, every registered one is still in the code, and
    every one of its texts is a leaf of the corpus, whether a short run reaches it or
    not. A new refusal, error message or result reason fails here until the baseline is
    rewritten and its findings triaged."""
    registered = set(audit.load_surfaces()["seat_text"])
    assert seat.sources - registered == set(), "an unregistered seat-text source"
    assert registered - seat.sources == set(), "a registered seat-text source is gone"
    leaves = corpus.render_seat_text(seat)
    assert len(leaves) == len(seat.texts)
    assert all(corpus.excluded(path) is None for path, _text in leaves), \
        "a seat-text leaf is excluded from the lint"
    assert {text for _path, text in leaves} == {t.text for t in seat.texts}


def test_the_seat_text_scan_reaches_what_short_runs_do_not(seat):
    """The scan finds each class of seat text on its own path: a child request's
    refusal (the reported instance), a tool dispatch's refusal, a recorded venue's
    refusal, a refused registration's parser reason, and an exception message a tool
    path raises, which the tool funnel returns to the seat."""
    by_source: dict[str, set[str]] = {}
    for t in seat.texts:
        by_source.setdefault(t.source, set()).add(t.text)
    composition = "factorylab/runtime/composition.py::CompositionMixin._invoke_child"
    assert any(t.kind == "sink" and t.source == composition for t in seat.texts)
    assert "unknown or disallowed tool" in by_source[
        "factorylab/runtime/compute.py::ComputeMixin._run_tool"]
    assert any("recorded market has ended" in text for texts in by_source.values()
               for text in texts)
    assert any(src.startswith("factorylab/cortex/registration.py::")
               for src in by_source)
    assert {"_refuse_request", "_reject_registration", "_refusal_to_owner"} <= set(seat.sinks)
    assert "factorylab/runtime/compute.py::ComputeMixin._run_tool" in seat.funnels(
        seat.reachable)


def test_the_kernel_seat_text_has_no_untriaged_finding_and_no_stale_one(seat, allowlist):
    result = audit.triage(corpus.render_seat_text(seat), allowlist)
    assert not result.review, [f.key for f in result.review]
    drift = lexicon.compare(result.findings, audit.baseline_rows(corpus.KERNEL, "static"))
    assert drift == {"new": [], "stale": []}, drift


def test_a_seat_text_source_the_registry_lacks_fails_the_check(seat, monkeypatch):
    registry = audit.load_surfaces()
    dropped = {**registry, "seat_text": registry["seat_text"][1:]}
    monkeypatch.setattr(audit, "load_surfaces", lambda: dropped)
    with pytest.raises(AssertionError, match="unregistered seat-text source"):
        test_every_seat_text_source_is_registered_and_rendered(seat)



# --- the seat-text renderer: every alternative of every shape (Codex P2) --------------------


@pytest.mark.parametrize("source, expected", [
    # A conditional expression: both branches (composition.py's missed refusal).
    ("def f(e, kind):\n    return 'a judge refusal' if e else f'no contract emits {kind}'",
     {"a judge refusal", "no contract emits {kind}"}),
    # ``or`` / ``and``: every operand.
    ("def f(x):\n    return x or 'first fallback' or 'second fallback'",
     {"first fallback", "second fallback"}),
    ("def f(x):\n    return x and 'only when x'", {"only when x"}),
    # An f-string: its placeholders by name.
    ("def f(n, cap):\n    return f'{n} exceeds the cap of {cap} tokens'",
     {"{n} exceeds the cap of {cap} tokens"}),
    # ``+``: every combination of the operands' alternatives.
    ("def f(e):\n    return 'refused: ' + ('stale' if e else 'unknown')",
     {"refused: stale", "refused: unknown"}),
    # ``%`` and ``.format``: the template.
    ("def f(n):\n    return 'size %s is below the minimum' % n",
     {"size %s is below the minimum"}),
    ("def f(n):\n    return 'size {} is below the minimum'.format(n)",
     {"size {} is below the minimum"}),
    # A local name, every assignment in any branch.
    ("def f(e):\n    if e:\n        why = 'the window is closed'\n    else:\n"
     "        why = 'the seat is retired'\n    return why",
     {"the window is closed", "the seat is retired"}),
    # A module-level constant by name, and a constant built from others.
    ("PREFIX = 'refused'\nWHY = PREFIX + ': no route'\ndef f():\n    return WHY",
     {"refused: no route"}),
    # A class-level constant by attribute.
    ("class C:\n    WRITE_REFUSAL = 'judges do not trade'\n"
     "def f(self):\n    return self.WRITE_REFUSAL", {"judges do not trade"}),
    # A lookup in a constant dict of messages: every value, and a .get default.
    ("MESSAGES = {'a': 'no such market', 'b': 'market closed'}\ndef f(k):\n"
     "    return MESSAGES[k]", {"no such market", "market closed"}),
    ("MESSAGES = {'a': 'no such market'}\ndef f(k):\n"
     "    return MESSAGES.get(k, 'unknown reason')", {"no such market", "unknown reason"}),
    # ``str(exc)``: nothing of its own; the exception's messages are collected at raise.
    ("def f(exc):\n    return str(exc)", set()),
    ("def f(exc):\n    return f'refused: {exc}'", {"refused: {exc}"}),
])
def test_the_renderer_collects_every_alternative_of_every_shape(source, expected):
    from tests.audit import class2_seat_text

    assert class2_seat_text.snippet_texts(source) == expected


def test_the_composition_refusal_the_first_branch_hid_is_in_the_corpus(seat):
    """Codex P2: composition.py's ``_draw_executor`` returns one of three refusals; the
    one in the conditional's second branch is in the corpus too."""
    texts = {t.text for t in seat.texts
             if t.source == "factorylab/runtime/composition.py::CompositionMixin._draw_executor"}
    assert "no live contract other than the requester emits or accepts {kind}" in texts
