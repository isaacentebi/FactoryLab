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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


def test_a_cross_leaf_allow_entry_names_its_whole_set():
    """Codex on 0f40a8d: a Q11 entry binds its complete leaf set, each leaf with its
    quote and context words; a partial or unkeyed one is malformed."""
    base = {"rule": "IMP", "path": "*/tools/x", "quote": "hold", "reason": "r",
            "passage": "§I (contract / I/O)", "context_words": ["hold"], "question": "Q11",
            "class": "C2"}
    leaves = [{"leaf_id": "a", "quote": "hold", "context_words": ["hold"]},
              {"leaf_id": "b", "quote": "keep", "context_words": ["keep"]}]
    good = base | {"leaf_ids": ["a", "b"], "leaves": leaves}
    assert lexicon.allowlist_problems({"collocation": [], "allow": [good]}) == []
    for bad in (base | {"leaf_ids": ["a", "b"], "leaves": leaves[:1]},
                base | {"leaf_ids": ["a"], "leaves": leaves[:1]},
                base | {"leaves": leaves},
                base | {"leaf_ids": ["a", "b"],
                        "leaves": [leaves[0], leaves[1] | {"context_words": []}]}):
        assert any("whole leaf set" in p
                   for p in lexicon.allowlist_problems({"collocation": [], "allow": [bad]}))


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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
@pytest.mark.parametrize("world", WORLDS)
def test_the_static_corpus_has_no_untriaged_finding_and_no_stale_one(world, static, allowlist):
    result = audit.triage(static[world], allowlist)
    assert not result.review, [f.key for f in result.review]
    drift = lexicon.compare(result.findings, audit.baseline_rows(world, "static"))
    assert drift == {"new": [], "stale": []}, drift


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_the_committee_requests_are_in_the_static_corpus(static):
    """The ballots and the testimony no short run reaches are rendered statically, through
    the real builders (``class2_corpus.render_governance``)."""
    forms = {"/".join(s.split("/")[:2])
             for leaves in static.values() for s in audit.surfaces_of(leaves)}
    assert {"request/vote", "request/testify"} <= forms
    assert audit.static_builders(WORLDS[:1]) == {
        "factorylab/runtime/governance.py::_hold_vote",
        "factorylab/runtime/governance.py::_testify"}


@pytest.mark.gate  # scans the package twice: about 1.3 s serially, at the check limit
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_the_kernel_seat_text_has_no_untriaged_finding_and_no_stale_one(seat, allowlist):
    result = audit.triage(corpus.render_seat_text(seat), allowlist)
    assert not result.review, [f.key for f in result.review]
    drift = lexicon.compare(result.findings, audit.baseline_rows(corpus.KERNEL, "static"))
    assert drift == {"new": [], "stale": []}, drift


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
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


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_the_composition_refusal_the_first_branch_hid_is_in_the_corpus(seat):
    """Codex P2: composition.py's ``_draw_executor`` returns one of three refusals; the
    one in the conditional's second branch is in the corpus too."""
    texts = {t.text for t in seat.texts
             if t.source == "factorylab/runtime/composition.py::CompositionMixin._draw_executor"}
    assert "no live contract other than the requester emits or accepts {kind}" in texts



@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_every_literal_a_seat_receives_in_a_payload_is_in_the_corpus(seat):
    """Codex P2: not only error and reason values: every literal placed into a request's
    text or inputs, or a seat's inbox, whatever its key, through spreads, mutations and
    the functions that build it. ``_action_policy``'s note reaches a seat under
    ``your_action_policy``, spread into the request by ``_action_policy_input``."""
    payloads = {(t.text, t.source) for t in seat.texts if t.kind == "payload"}
    assert any(text.startswith("drawn by the kernel from your ")
               and src == "factorylab/runtime/compute.py::ComputeMixin._action_policy"
               for text, src in payloads)
    # A value added after the dict was bound (payload["account"] = {...}) is read too.
    assert ("unavailable", "factorylab/runtime/loop.py::Runtime._producer_step") in payloads



# --- Sol's coverage pass on the audit tooling (b75003b) --------------------------------------


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_cov1_every_kernel_tool_spec_is_read_connector_fetch_included(static):
    """Sol COV-1: the connector fetch tool is the kernel's (``cortex.tools.connector_spec``);
    only a population tool is excluded, by provenance. A world replaying a tape publishes
    no fetch (``_ensure_connector_tool``), so it has none to read."""
    for world, leaves in static.items():
        rt = corpus.static_runtime(world)
        rt._ensure_connector_tool()
        kernel_tools = {tool_id for tool_id, spec in rt.tool_specs.items()
                        if spec.get("kind") != corpus.POPULATION_TOOL_KIND
                        and tool_id not in rt.population_tools}
        read = {path.split("/")[2] for path, _text in leaves if path.split("/")[1] == "tools"}
        assert kernel_tools <= read, (world, sorted(kernel_tools - read))
        if "connector.fetch" in kernel_tools:
            assert (f"{world}/tools/connector.fetch/description",
                    "GET a registered connector path as text") in leaves


def test_cov3_the_request_headers_are_the_builders_own_and_an_unknown_one_is_refused():
    """Sol COV-3: the headers are read from the request builders' code, and a header the
    corpus does not know is refused rather than folded into its neighbour (this found
    ``OPERATING ACCESS``, a grounded judge's section, folded into WORLD CONTRACT)."""
    assert corpus.request_headers_in_code() == set(corpus.REQUEST_HEADERS)
    with pytest.raises(corpus.UnknownSection, match="TOOL CONTEXT"):
        corpus.split_request("REQUEST\nDo nothing.\nTOOL CONTEXT\nHidden text.\n")
    parts = corpus.split_request("OPERATING ACCESS\nBASE CAPABILITIES\nOne line.\n")
    assert set(parts) == {"OPERATING ACCESS", "BASE CAPABILITIES"}


def test_cov4_population_text_is_dropped_by_provenance_never_by_substring():
    """Sol COV-4: a kernel string a seat echoes stays; a leaf is dropped only when it is a
    whole string the population emitted and not one the kernel writes."""
    kernel = {"publish the final answer"}
    emitted = {"I will publish the final answer", "publish the final answer"}
    assert not corpus._population_authored("publish the final answer", emitted, kernel)
    assert not corpus._population_authored("Then publish the final answer now.", emitted,
                                           kernel)
    assert corpus._population_authored("I will publish the final answer", emitted, kernel)


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_cov5_a_tool_result_under_any_key_is_seat_text(seat):
    """Sol COV-5: a successful tool result's text is read whatever its key: every value a
    tool entry point returns is a payload."""
    payloads = {(t.source, t.text) for t in seat.texts if t.kind == "payload"}
    # ``{"status": "rejected", "error": reason}``: the status is read too, not only the
    # error, and so is every other key of a result a tool returns.
    assert ("factorylab/runtime/compute.py::ComputeMixin._run_tool.execute",
            "rejected") in payloads
    assert not any(t.kind == "result" and t.text == "rejected" for t in seat.texts)


# --- Codex pass on 5ba444a: seat-bound calls read from their real signatures -------------


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_a_multi_argument_exception_is_scanned_by_its_message_arguments(seat):
    """Codex P2 (class2_seat_text.py:652): an exception is scanned by the arguments its
    constructor makes its message of, not its first. ``SectionError(section, reason,
    index)`` hands ``reason`` to ``Exception``, so the reason is seat text and the
    section name is not; a provider error's message template is seat text too."""
    compute = "factorylab/runtime/compute.py::ComputeMixin._validate_output_contract"
    by_source: dict[str, set[str]] = {}
    for t in seat.texts:
        if t.kind == "exception":
            by_source.setdefault(t.source, set()).add(t.text)
    assert "more than {limit} {section} in one turn; call it again next round" \
        in by_source[compute]
    assert "tool_calls" not in by_source[compute]
    assert "Venice error ({status}): {message}" in by_source[
        "factorylab/world/venice.py::VeniceError.__init__"]


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_a_positional_child_request_reaches_the_scan(seat):
    """Codex P2 (class2_seat_text.py:77): ``_invoke_child`` builds its ``Request``
    positionally. Its description, inputs, schema (``with_counterfactual``'s published
    field) and completion criterion are all seat text."""
    child = "factorylab/runtime/composition.py::CompositionMixin._invoke_child"
    texts = {(t.source, t.text) for t in seat.texts}
    assert (child, "a JSON object satisfying the outcome schema") in texts
    assert ("factorylab/cortex/assembly.py::with_counterfactual",
            "a declined trade, coin and side") in texts
    # The inputs, ``{**item.inputs, "world": self._world_block()}``: the world block's
    # builder is followed.
    assert "factorylab/cortex/schematics.py::SchematicsMixin._world_block" \
        in seat.payload_builders


def test_every_seat_bound_call_is_mapped_from_its_real_signature():
    """The general fix: positions and keywords come from each constructor's or builder's
    signature at scan time, so positional and keyword arguments are both covered, and a
    renamed or reordered parameter changes the mapping (or fails the scan) instead of
    dropping coverage."""
    import dataclasses
    import inspect

    from factorylab.cortex.request import Request
    from tests.audit import class2_seat_text as st

    fields = [f.name for f in dataclasses.fields(Request)]
    positions, keywords = st.PAYLOAD_CALLS["Request"]
    assert positions == {fields.index(n) for n in ("description", "inputs", "outcome_schema",
                                                   "completion_criterion", "settlement")}
    assert {"description", "inputs", "outcome_schema", "completion_criterion"} <= keywords
    for name, path in st.SINK_TARGETS.items():
        params = [p for p in inspect.signature(st._target(path)).parameters if p != "self"]
        assert st.SINKS[name] == params.index("reason"), name
    with pytest.raises(ValueError, match="no parameter"):
        st.call_mapping("factorylab.cortex.request:Request", frozenset({"prompt"}))
    positions, keywords = st.call_mapping("factorylab.runtime.compute:ComputeMixin._request",
                                          frozenset({"description", "settlement"}))
    assert positions == {1, 7} and keywords == {"description", "settlement"}


def _published_by_returns(seat):
    """Every (source, key, text) a Return publishes through ``public_return``: each key of
    a dict literal built as a Return's ``outputs`` (``Return(h, outputs, …)``,
    ``replace(ret, outputs=…)``, or a wrapper that passes it on, like ``malformed``),
    its value rendered at every expression shape the Renderer reads (literals, local
    names and their bindings, f-strings, branches)."""
    import ast

    from tests.audit.class2_seat_text import (
        Renderer,
        _call_name,
        _own_nodes,
        has_literal_text,
        local_bindings,
    )

    out = []
    wrappers = {name for name, (positions, _kw) in seat.payload_calls.items()
                if name not in ("Return", "_emit") and positions}
    for key, fn in seat.fns.items():
        renderer = None
        for node in _own_nodes(fn.node):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            outputs = [kw.value for kw in node.keywords if kw.arg == "outputs"]
            if name == "Return" and len(node.args) > 1:
                outputs.append(node.args[1])
            elif name in wrappers and name == "malformed" and node.args:
                outputs.append(node.args[0])
            elif name not in ("Return", "replace", "malformed"):
                continue
            for value in outputs:
                if not isinstance(value, ast.Dict):
                    continue
                renderer = renderer or Renderer(seat, fn.module, local_bindings(fn.node))
                for k, v in zip(value.keys, value.values, strict=True):
                    label = k.value if isinstance(k, ast.Constant) else "**"
                    out += [(key, label, text) for text in renderer.render(v)
                            if has_literal_text(text)]
    return out


@pytest.mark.gate  # reads a whole corpus, 4 to 5 s to build once per worker
def test_every_key_a_return_publishes_is_a_traced_seat_text(seat):
    """Codex on b6b1d1e: ``public_return`` publishes every key of a Return's outputs to
    the judges, a parent and the world block, so each is a seat-bound sink, not only
    ``error`` and ``reason``. Every kernel string any key can carry (``validation_error``
    among them) is a traced seat text of the function that builds it; one that is not
    fails here."""
    published = _published_by_returns(seat)
    keys = {label for _key, label, _text in published}
    assert {"validation_error", "reason"} <= keys, keys
    texts = {(t.source, t.text) for t in seat.texts}
    untraced = sorted({(key, label, text) for key, label, text in published
                       if (key, text) not in texts})
    assert untraced == [], untraced[:10]
    assert ("factorylab/cortex/assembly.py::Assembly.invoke",
            "answer is not a JSON object") in texts
