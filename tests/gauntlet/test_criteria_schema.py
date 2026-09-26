"""The criteria's unit fixtures speak the kernel's schema, and the criteria read real rows.

Codex review (TH-3): a unit fixture that models a field differently from the code that
emits it hides a criterion that fails on every real row. So every synthetic ledger row in
``test_criteria.py`` is checked against a real row of the same kind, captured from the
gauntlet's scripted worlds on this kernel (``tests/fixtures/gauntlet_real_rows.json``):
each field path the fixture uses must exist in the real row. And where a criterion's
meaning depends on a field's semantics, it is run over the real rows themselves.
"""

import ast
import json
from pathlib import Path

import pytest

from scripts import gauntlet as g
from tests.gauntlet import test_criteria as unit

ROOT = Path(__file__).resolve().parents[2]
REAL = json.loads((ROOT / "tests/fixtures/gauntlet_real_rows.json").read_text())

#: Kinds a fixture may use that no gauntlet world emits, each with its reason.
NOT_EMITTED = {
    "immune.price_ratchet_saturated": "wave 16 D5/R-E names the saturation row; no kernel "
                                      "on this branch emits it (SF-1d is a strict xfail)",
    "challenge.proposed": "emitted by governance._register_challenge with the challenge "
                          "record (its handle included); no gauntlet world files a challenge",
    "price.register": "emitted by PriceController.register_pending with kind and card_id "
                      "(the fields the fixture uses); the captured set holds none",
    "router.created": "emitted by routing._build_router with learner_id, event_kind and "
                      "replaces (the fields the fixture uses); the captured set holds none",
    "uptake.anticipated": "emitted by runtime/uptake.py; no captured run reached it",
    "uptake.forecast": "emitted by runtime/uptake.py; no captured run reached it",
}


#: Fields whose keys are ids (a card id, an observation id), not field names.
ID_KEYED = frozenset({"values", "regions", "holdouts", "observations"})


def paths(value, prefix="", *, ids=False):
    """Every field path in a row: nested dicts by name, lists of dicts by their items, and
    the entries of an id-keyed mapping under ``*``."""
    out = set()
    if isinstance(value, dict):
        for key, item in value.items():
            name = "*" if ids else key
            out.add(prefix + name)
            out |= paths(item, f"{prefix}{name}.", ids=key in ID_KEYED and not ids)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                out |= paths(item, f"{prefix}[].")
    return out


def real_paths(kind):
    row = REAL["rows"][kind]
    found = paths(row)
    if kind == "event":
        # The Launch is an event row too: the diary's binding to its world and seed.
        found |= paths(REAL["launch"])
    # A list the real row happens to leave empty still has the fields its kind carries.
    if kind == "price.penalty" and not row.get("terms"):
        pytest.fail("the captured price.penalty row has no terms to compare against")
    return found


def built_rows():
    """The rows the unit fixtures build, from their builders."""
    return [
        unit._w(1, acts=True, sf=True, frontier=[{"router": "router:X", "quarantined": True,
                                                   "core": False}]),
        unit._price_window(1, 0.0), *unit._ratchets((3, 1)), *unit._updates("c", [(1, 1, 1)]),
        unit._gain(1, 0.1, 0.15), unit._novelty(), unit._open("d", "a"),
        unit._penalty("d", 0.5), unit._boundary(30, 0), unit._cadence(30),
        unit._consequence("r", 0.5, 0.2), unit._returned_event("e", "p", 1),
    ]


def literal_rows():
    """Every dict literal in ``test_criteria.py`` that names a constant ``kind``, with its
    constant keys and the constant keys of dicts nested under them."""
    tree = ast.parse(Path(unit.__file__).read_text())

    def keys(node, prefix="", ids=False):
        out = set()
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                name = "*" if ids else key.value
                out.add(prefix + name)
                if isinstance(value, ast.Dict):
                    out |= keys(value, f"{prefix}{name}.",
                                ids=key.value in ID_KEYED and not ids)
        return out

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        kind = next((v.value for k, v in zip(node.keys, node.values, strict=True)
                     if isinstance(k, ast.Constant) and k.value == "kind"
                     and isinstance(v, ast.Constant)), None)
        # Ledger kinds are dotted (``price.window``) or captured (``event``); a region's or a
        # window's own ``kind`` ("min", "windows") and an event's kind are not rows.
        if isinstance(kind, str) and ("." in kind or kind in REAL["rows"]):
            found.append((kind, keys(node), node.lineno))
    return found


def test_every_built_fixture_row_uses_only_fields_its_kind_really_has():
    problems = []
    for row in built_rows():
        extra = paths(row) - real_paths(row["kind"]) - {"seq"}
        if extra:
            problems.append((row["kind"], sorted(extra)))
    assert problems == []


def test_every_literal_fixture_row_uses_only_fields_its_kind_really_has():
    problems, unknown = [], []
    for kind, used, line in literal_rows():
        if kind in NOT_EMITTED:
            continue
        if kind not in REAL["rows"]:
            unknown.append((kind, line))
            continue
        extra = used - real_paths(kind) - {"seq"}
        if extra:
            problems.append((kind, line, sorted(extra)))
    assert unknown == [], "a fixture kind with no captured real row and no stated reason"
    assert problems == []


def test_th3_reads_the_real_boundary_and_cadence_sequence():
    """TH-3 over the rows the TH-3 world wrote: in the kernel ``earliest_ns`` is the next
    threshold after an activation, and activations share their boundary's instant."""
    rows = REAL["th3_sequence"]
    assert {r["kind"] for r in rows} == {"charter.boundary", "charter.cadence"}
    manifest = {"timing": {"min_ratio": 3}}
    assert g.th3_governance_gap(rows, manifest).ok
    early = [dict(r, boundary_ns=r["previous_ns"] + 1) if r["kind"] == "charter.boundary"
             else r for r in rows]
    assert g.th3_governance_gap(early, manifest).status == g.FAIL


def test_the_criteria_read_real_rows_without_error():
    """Every generic criterion over the captured rows returns a status, never raises."""
    rows = [dict(r, seq=r.get("seq", 0)) for r in [*REAL["rows"].values(), REAL["launch"]]]
    for result in g.replay(sorted(rows, key=lambda r: r["seq"])):
        assert result.status in (g.PASS, g.FAIL, g.UNSUPPORTED)
        # Every field a criterion requires is one the kernel really writes.
        assert "malformed" not in result.evidence, (result.name, result.evidence)


# --- S4: every reward-bearing kind, enumerated from the emitting code (Codex review) ------

#: Kinds whose rows carry a reward-like key that is not a unit-interval score, or that are
#: not ledger rows, each with its reason.
NOT_SCORES = {
    "outcome.undeliverable": "its `consequence` names what could not be delivered (text)",
    "composed_settled": "a seat's inbox item (outcomes.append), not a ledger row; its "
                        "values are the composed.settled row's, which S4 bounds",
}
#: Reward-bearing kinds no run captured here, each with its reason.
UNCAPTURED = {
    "uptake.anticipated": "emitted by runtime/uptake.py when a judge forecasts a "
                          "registration's uptake; no captured run reached it",
    "uptake.forecast": "emitted by runtime/uptake.py with a judge's uptake forecast; no "
                       "captured run reached it",
}
REWARD_KEYS = frozenset({"reward", "score", "grade", "reward_before", "stepped_as",
                         "effective", "raw", "conformity", "consequence", "q", "y",
                         "verdict", "judge_q"})


def _emitted_reward_kinds():
    """Every constant-``kind`` dict literal in factorylab/ carrying a reward-like key."""
    found = {}
    for path in sorted((ROOT / "factorylab").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            kind = next((v.value for k, v in zip(node.keys, node.values, strict=True)
                         if isinstance(k, ast.Constant) and k.value == "kind"
                         and isinstance(v, ast.Constant)), None)
            if isinstance(kind, str) and keys & REWARD_KEYS:
                found.setdefault(kind, set()).update(keys & REWARD_KEYS)
    return found


def test_s4_enumerates_every_reward_bearing_kind_the_kernel_emits():
    emitted = _emitted_reward_kinds()
    assert "decision.settle" in g.UNIT_FIELDS  # its score is nested under ``return``
    missing = sorted(set(emitted) - set(g.UNIT_FIELDS) - set(NOT_SCORES))
    assert missing == [], "a reward-bearing kind S4 does not bound"
    for kind, keys in emitted.items():
        if kind in g.UNIT_FIELDS:
            assert keys <= set(g.UNIT_FIELDS[kind]), (kind, keys)


def test_every_s4_field_exists_in_its_real_row():
    for kind, fields in g.UNIT_FIELDS.items():
        if kind in UNCAPTURED:
            continue
        assert kind in REAL["rows"], kind
        real = paths(REAL["rows"][kind])
        assert set(fields) & real, (kind, fields)


def _set(row, path, value):
    row = json.loads(json.dumps(row))
    target = row
    *parents, leaf = path.split(".")
    for part in parents:
        target = target[part]
    target[leaf] = value
    return row


def test_s4_refuses_an_out_of_range_score_on_every_reward_bearing_kind():
    """Codex review: a 1.5 in any learned, settled or graded score of any kind fails S4;
    the captured real rows pass."""
    rows = [REAL["rows"][kind] for kind in g.UNIT_FIELDS if kind in REAL["rows"]]
    manifest = {"prices": {"penalty_cap": 0.5, "lambda_max": 1.0}}
    assert g.s4_boundedness(rows, manifest).ok
    tried = 0
    for kind, fields in g.UNIT_FIELDS.items():
        if kind not in REAL["rows"]:
            continue
        for name in fields:
            if g._field(REAL["rows"][kind], name) is None:
                continue
            tried += 1
            bad = _set(REAL["rows"][kind], name, 1.5)
            result = g.s4_boundedness([bad], manifest)
            assert result.status == g.FAIL, (kind, name)
            assert result.evidence["bad"][0]["field"] == name
    assert tried >= len(g.UNIT_FIELDS) - len(UNCAPTURED)


# --- S4: required and nullable fields, from the emitting code (Codex pass on 11ea116) ----

#: Nullable fields whose None comes from a value, not a literal, in the emitter: the AST
#: cannot see it, so each names the expression that is None.
NULLABLE_BY_VALUE = {
    ("price.penalty", "effective"): "pricing.py: effective = None when unresolved",
    ("policy.outcome", "y"): "governance.py: outcome = ... if SETTLED else None",
    ("evaluator.settled", "grade"): "PendingJudgement.grade: None with no grade",
    ("evaluator.settled", "consequence"): "PendingJudgement.consequence: None, censored",
    ("evaluator.settled", "reward"): "evaluation_reward: None when neither signal is",
    ("composed.settled", "verdict"): "composition.py: verdict None with no verdicts",
    ("composed.settled", "reward"): "composed_reward: None when no signal is",
}


def _literally_nullable(*, omitted):
    """Every (kind, field) of ``UNIT_FIELDS`` some emitter leaves out (``omitted``), or
    writes as the constant None or as a conditional with a None branch (not ``omitted``)."""
    found = set()
    for path in sorted((ROOT / "factorylab").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            fields = {k.value: v for k, v in zip(node.keys, node.values, strict=True)
                      if isinstance(k, ast.Constant)}
            kind = fields.get("kind")
            if not (isinstance(kind, ast.Constant) and kind.value in g.UNIT_FIELDS):
                continue
            for name in g.UNIT_FIELDS[kind.value]:
                value = fields.get(name.split(".")[0])
                if omitted and value is None and "." not in name:
                    found.add((kind.value, name))
                elif not omitted and value is not None and any(
                        isinstance(n, ast.Constant) and n.value is None
                        for n in ast.walk(value)):
                    found.add((kind.value, name))
    return found


def test_s4_nullable_fields_are_exactly_what_the_emitters_write_as_none():
    """Codex P2 (gauntlet.py:2012): an absent field and an explicit null are told apart,
    each pinned to the emitters: ``OMITTED`` is exactly what some emitter leaves out,
    ``NULLABLE`` exactly what some emitter writes as None."""
    assert set(g.OMITTED) == _literally_nullable(omitted=True)
    assert set(g.NULLABLE) == _literally_nullable(omitted=False) | set(NULLABLE_BY_VALUE)


def test_s4_a_missing_required_field_fails_on_every_real_row():
    """Codex P2: every field the kernel always writes is required: dropping it from the
    real row of its kind fails S4."""
    manifest = {"prices": {"penalty_cap": 0.5, "lambda_max": 1.0}}
    tried = 0
    for kind, fields in g.UNIT_FIELDS.items():
        if kind not in REAL["rows"]:
            continue
        for name in fields:
            if (kind, name) in g.OMITTED or "." in name:
                continue
            row = json.loads(json.dumps(REAL["rows"][kind]))
            row.pop(name, None)
            tried += 1
            result = g.s4_boundedness([row], manifest)
            assert result.status == g.FAIL, (kind, name)
            assert result.evidence["bad"][0]["missing"], (kind, name)
    assert tried >= 20
    settle = json.loads(json.dumps(REAL["rows"]["decision.settle"]))
    del settle["return"]["score"]
    assert g.s4_boundedness([settle], manifest).status == g.FAIL


# --- the sweep (Codex pass on 144323c): every criterion replayed, every entity set whole --


def _criteria():
    """Every public criterion ``gauntlet.py`` defines: a top-level function returning a
    ``Result``."""
    tree = ast.parse(Path(g.__file__).read_text())
    return {node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
            and isinstance(node.returns, ast.Constant | ast.Name)
            and getattr(node.returns, "value", getattr(node.returns, "id", None)) == "Result"}


def test_every_criterion_is_replayed_or_population_only_with_a_reason():
    """Codex P2 (gauntlet.py:2090): a criterion defined and never replayed is a check no
    diary gets. Each is in a replay registry, or population-only with a reason and an
    input the diary cannot supply (a required keyword the replay never passes, or not a
    diary at all)."""
    import inspect

    registered = {fn.__name__ for table in (g.GENERIC, g.PER_CARD, g.PER_LOOP)
                  for fn in table.values()}
    accounted = registered | set(g.POPULATION_ONLY) | set(g.REPLAY_DIRECT)
    criteria = _criteria()
    assert "th1b2_frozen" in criteria and "of2d_authorship" in criteria
    assert sorted(criteria - accounted) == [], "a criterion replay never runs"
    assert sorted(accounted - criteria) == [], "a registry names no criterion"
    assert not registered & set(g.POPULATION_ONLY)
    for name, reason in g.POPULATION_ONLY.items():
        params = inspect.signature(getattr(g, name)).parameters
        needs = [p for p in params.values() if p.kind is p.KEYWORD_ONLY
                 and p.default is p.empty]
        assert reason.strip() and (needs or list(params)[:2] != ["events", "manifest"]), name
    for table, supplied in ((g.GENERIC, set()), (g.PER_CARD, {"card"}),
                            (g.PER_LOOP, {"loop"})):
        for name, fn in table.items():
            required = {p.name for p in inspect.signature(fn).parameters.values()
                        if p.kind is p.KEYWORD_ONLY and p.default is p.empty}
            assert required <= supplied, (name, required)


def _emitted_with(keys):
    """Every constant ``kind`` of a dict literal in factorylab/ carrying one of ``keys``."""
    found = set()
    for path in sorted((ROOT / "factorylab").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            fields = {k.value: v for k, v in zip(node.keys, node.values, strict=True)
                      if isinstance(k, ast.Constant)}
            kind = fields.get("kind")
            if isinstance(kind, ast.Constant) and set(keys) & set(fields):
                found.add(kind.value)
    return found


#: Dict literals with a constant ``kind`` that are not ledger rows, each with its reason.
NOT_ROWS = {
    "challenge": "cortex/schematics.py: the example payload of a challenge a seat files "
                 "(ledgered as challenge.proposed with the record, no card_id key)",
    "learner": "cortex/schematics.py and world/scripted.py: a learner registration "
               "payload a seat returns, not a row",
    "retire": "cortex/schematics.py and world/scripted.py: a retirement payload a seat "
              "returns, not a row",
}


def test_every_kind_that_names_a_card_is_a_card_source():
    """Codex P2 (gauntlet.py:2128): the card set is built from every row kind that names
    a card, never from one."""
    missing = sorted(_emitted_with({"card_id"}) - set(g.CARD_SOURCES) - set(NOT_ROWS))
    assert missing == [], "a kind names a card that diary_cards does not read"
    assert "price.window" in g.CARD_SOURCES and "immune.window" in g.CARD_SOURCES


def test_every_kind_that_names_a_router_is_a_router_source():
    emitted = _emitted_with({"router", "learner_id", "actor"}) | {"decision.open"}
    missing = sorted(emitted - set(g.ROUTER_SOURCES) - set(NOT_ROWS))
    assert missing == [], "a kind names a router that router_presence does not read"


def test_the_entity_builders_read_every_source():
    """Each source kind alone makes its entity known to the builder."""
    for kind, paths_ in g.CARD_SOURCES.items():
        for path in paths_:
            row = {"kind": kind, "window": 3}
            target, *parts = path.replace("[]", "").split(".")
            if path.endswith("[]"):
                row[target] = ["card:c9"] if not parts else None
            elif parts == ["*"]:
                row[target] = {"card:c9" if kind == "immune.window" else "c9": 1.0}
            else:
                row[target] = "c9"
            assert g.diary_cards([row]) == ["c9"], (kind, path)
    for kind, paths_ in g.ROUTER_SOURCES.items():
        for path in paths_:
            parts = path.split(".")
            leaf: object = "router:R"
            for part in reversed(parts):
                name = part.removesuffix("[]")
                leaf = {name: [leaf] if part.endswith("[]") else leaf}
            row = {"kind": kind, "window": 3, **leaf}
            assert "router:R" in g.router_presence([row]), (kind, path)
    lifespan = {"kind": "immune.window", "window": 1, "lifespans": [{"loop": "price"}]}
    assert g.diary_loops([lifespan, {"kind": "config.lifespan", "loop": "gain"}]) == [
        "gain", "price"]
    assert set(g.ENTITY_SETS) and all(v.strip() for v in g.ENTITY_SETS.values())
    # The prefix is removed only in fields the organ writes with it (immune.py:303).
    assert all(path in g.CARD_SOURCES.get(kind, ()) for kind, path in g.CARD_PREFIXED)
    assert g.diary_cards([{"kind": "price.update", "card_id": "card:x"}]) == ["card:x"]


# --- Codex pass on 7c714a2: the loader drops nothing a criterion reads ---------------------


def _read_set():
    """Every ledger kind ``gauntlet.py`` can read, as it names one: an argument of
    ``rows_of``, a string compared (``==``, ``in``) with anything, a key of a module-level
    table keyed by kind; and each prefix or suffix it matches kinds by."""
    tree = ast.parse(Path(g.__file__).read_text())

    def strings(node):
        return {n.value for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    constants, affixes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "rows_of":
            constants |= strings(ast.Tuple(elts=node.args[1:]))
        elif isinstance(node, ast.Compare):
            constants |= strings(node)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("startswith", "endswith") and node.args
                and isinstance(node.args[0], ast.Constant)):
            affixes.add((node.func.attr, node.args[0].value))
    for table in (g.ACT_KINDS, g.UNIT_FIELDS, g.CAPPED_FIELDS, g.CARD_SOURCES,
                  g.ROUTER_SOURCES, g.LOOP_SOURCES):
        constants |= set(table)
    constants |= {kind for kind, _field in [*g.NULLABLE, *g.OMITTED]}
    constants |= set(g.WEIGHTED_PENALTY_KINDS)
    return constants, affixes


def test_the_loader_never_skips_a_kind_an_entity_set_or_a_criterion_reads():
    """Codex P2 (gauntlet.py:53): the opened-diary loader's skip set (``HEAVY_KINDS``) is
    disjoint from every ``ENTITY_SETS`` source and from every kind a criterion names
    (``compute.route``, a router source, was skipped)."""
    sources = set(g.CARD_SOURCES) | set(g.ROUTER_SOURCES) | set(g.LOOP_SOURCES)
    assert "compute.route" in sources
    assert not g.HEAVY_KINDS & sources
    constants, affixes = _read_set()
    assert not g.HEAVY_KINDS & constants, g.HEAVY_KINDS & constants
    for kind in g.HEAVY_KINDS:
        assert not any(getattr(kind, how)(affix) for how, affix in affixes
                       if affix), (kind, affixes)
    assert not g.HEAVY_KINDS & set(g.ACT_KINDS) and not g.HEAVY_KINDS & set(g.UNIT_FIELDS)


def test_every_criterion_turns_a_malformed_row_into_a_failure():
    """The sweep's mechanism: every criterion is wrapped (``criterion``), so a row
    lacking a field the kernel always writes fails it, naming the field."""
    for name in _criteria():
        assert hasattr(getattr(g, name), "criterion"), name
    with pytest.raises(g.Malformed):
        g.need({"kind": "immune.window"}, "thrash.lambda")
    assert g.need({"kind": "x", "a": {"b": None}}, "a.b") is None
