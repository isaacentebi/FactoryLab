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
    "propensity.unlearned": "emitted with handle, reason and either assembly_id (compute.py "
                            "3003/3015, feedback.py 2094) or learner_id (feedback.py "
                            "2211/2283/2402); the captured set holds none",
    "router.created": "emitted by routing._build_router with learner_id, event_kind and "
                      "replaces (the fields the fixture uses); the captured set holds none",
    "tool.call": "emitted by compute.py _invoke's tool loop with kind and handle (the "
                 "fields the fixture uses); the captured set holds none",
    "consequence.outcome": "emitted by settlement/consequence.py as asdict(Payoff) (handle, "
                           "y, net_micro, cost_micro, earned_micro, censored, ...); the "
                           "captured set holds none",
    "decision.timeout": "emitted by DecisionQueue.time_out with the timeout return (its "
                        "handle as return.handle); no captured run timed a decision out",
    "runtime.event_done": "emitted by loop.py after every event's _deliver_returns (with "
                          "n and the pace record); the captured set holds none",
    "router.unscored_priced": "emitted by feedback.py (_learn_router_return and "
                              "_credit_abstentions) with the abstention row's fields "
                              "plus status; the captured set holds none",
    "consequence.opportunity": "emitted by feedback.py _final_outcome with grounded.py's "
                               "_net fields (moves, gross/fee/funding/net bps, declined, "
                               "score); no captured gauntlet run named a declined trade",
    "consequence.attempted": "emitted beside consequence.opportunity for a refused answer "
                             "order (attempted); no captured gauntlet run reached it",
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
    "router.unscored_priced": "emitted by feedback.py for a censored or timed-out round "
                              "(wave 16 D4); no captured gauntlet run reached it",
    "consequence.uninformative": "emitted by feedback.py _uninformative (with q and y) and by "
                                 "the settlement-side fee/mark/funding paths; no captured "
                                 "gauntlet run reached it",
    "consequence.outcome": "emitted by settlement/consequence.py as asdict(Payoff) when "
                           "an outcome is fixed; the captured set holds none",
    "uptake.anticipated": "emitted by runtime/uptake.py when a judge forecasts a "
                          "registration's uptake; no captured run reached it",
    "uptake.forecast": "emitted by runtime/uptake.py with a judge's uptake forecast; no "
                       "captured run reached it",
}
REWARD_KEYS = frozenset({"reward", "score", "grade", "reward_before", "stepped_as",
                         "effective", "raw", "conformity", "consequence", "q", "y",
                         "verdict", "judge_q"})


#: The dataclass each ``**asdict(x)`` / ``**vars(x)`` / ``**x.__dict__`` spread in a ledger
#: row expands to, by the spread's source (``module:Class``): its fields are the row's
#: keys (Codex on f127c7b: ``consequence.outcome``'s ``y`` came only through a spread).
SPREADS = {
    "asdict(amendment)": "factorylab.charter.amendment:Amendment",
    "asdict(ballot)": "factorylab.charter.committee:Ballot",
    "vars(decision)": "factorylab.kernel.queue:Decision",
    "asdict(motion)": "factorylab.runtime.governance:Retirement",
    "asdict(after.payoff)": "factorylab.settlement.lots:Payoff",
}


def _spread_fields(value, unresolved):
    """The field names a ``**`` spread of a dataclass contributes (``dataclasses.fields``
    of its class, imported), or none for another spread; an unregistered dataclass
    spread is recorded in ``unresolved``."""
    import dataclasses
    import importlib

    source = ast.unparse(value)
    dataclass_spread = (isinstance(value, ast.Call)
                        and ast.unparse(value.func) in ("asdict", "dataclasses.asdict",
                                                        "vars")) or (
        isinstance(value, ast.Attribute) and value.attr == "__dict__")
    if not dataclass_spread:
        return set()
    if source not in SPREADS:
        unresolved.append(source)
        return set()
    module, _, name = SPREADS[source].partition(":")
    return {f.name for f in dataclasses.fields(getattr(importlib.import_module(module), name))}


def _emitted_reward_kinds(unresolved=None):
    """Every constant-``kind`` dict literal in factorylab/ carrying a reward-like key,
    its ``**`` dataclass spreads resolved to their fields (``SPREADS``)."""
    found = {}
    unresolved = [] if unresolved is None else unresolved
    for path in sorted((ROOT / "factorylab").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            kind = next((v.value for k, v in zip(node.keys, node.values, strict=True)
                         if isinstance(k, ast.Constant) and k.value == "kind"
                         and isinstance(v, ast.Constant)), None)
            if not isinstance(kind, str):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            for k, v in zip(node.keys, node.values, strict=True):
                if k is None:
                    keys |= _spread_fields(v, unresolved)
            if keys & REWARD_KEYS:
                found.setdefault(kind, set()).update(keys & REWARD_KEYS)
    return found


def test_every_dataclass_spread_in_a_ledger_row_is_resolved():
    """A ``**asdict(x)`` / ``**vars(x)`` spread brings the dataclass's fields into the
    row; each one is resolved to its class, so a score it carries is enumerated."""
    unresolved = []
    found = _emitted_reward_kinds(unresolved)
    assert unresolved == [], "register the spread's dataclass in SPREADS"
    assert found["consequence.outcome"] == {"y"}
    manifest = {"prices": {"penalty_cap": 0.5, "lambda_max": 1.0}}
    outcome = {"kind": "consequence.outcome", "handle": "h", "y": 1, "net_micro": 900,
               "cost_micro": 500, "earned_micro": 0, "censored": None}
    assert g.s4_boundedness([outcome], manifest).ok
    assert g.s4_boundedness([outcome | {"y": 7}], manifest).status == g.FAIL


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
            # A field a dataclass spread brings is written (its class's field, always).
            spread = set().union(*(_spread_fields(v, []) for k, v in
                                   zip(node.keys, node.values, strict=True) if k is None))
            for name in g.UNIT_FIELDS[kind.value]:
                value = fields.get(name.split(".")[0])
                if omitted and value is None and "." not in name and name not in spread:
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
            and node.name != "aggregate"  # combines criteria's readings; none of its own
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
            if kind == "decision.open":
                # A decision names its router only when the router drew it (router_draw).
                row |= {"parent_handle": None, "propensity": {"source": "sampled"}}
            assert "router:R" in g.router_presence([row]), (kind, path)
    lifespan = {"kind": "immune.window", "window": 1, "lifespans": [{"loop": "price"}]}
    assert g.diary_loops([lifespan, {"kind": "config.lifespan", "loop": "gain"}]) == [
        "gain", "price"]
    assert set(g.ENTITY_SETS) and all(v.strip() for v in g.ENTITY_SETS.values())
    # The prefix is removed only in fields the organ writes with it (immune.py:303).
    assert all(path in g.CARD_SOURCES.get(kind, ()) for kind, path in g.CARD_PREFIXED)
    assert g.diary_cards([{"kind": "price.update", "card_id": "card:x"}]) == ["card:x"]


# --- Codex pass on 47c5929: acts traced to the write tool that ledgers them ---------------


def test_the_act_tools_are_the_kernels_own_dispatch():
    """``ACT_TOOLS`` is read from ``_run_tool``'s dispatch: the tool ids whose branch calls
    ``_venue_write`` (which ledgers ``order.intent``), and the treasury tool's id (whose
    branch ledgers ``treasury.intent``)."""
    tree = ast.parse((ROOT / "factorylab/runtime/compute.py").read_text())
    venue = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.comparators[0], ast.Tuple)
                and any(isinstance(n, ast.Attribute) and n.attr == "_venue_write"
                        for stmt in node.body for n in ast.walk(stmt))):
            venue |= {e.value for e in node.test.comparators[0].elts}
    assert venue == g.ACT_TOOLS["order.intent"]
    source = (ROOT / "factorylab/runtime/compute.py").read_text()
    assert all(f'"{tool}"' in source for tool in g.ACT_TOOLS["treasury.intent"])
    assert set(g.ACT_TOOLS) <= set(g.ACT_KINDS)


def test_every_aggregation_goes_through_the_one_rule():
    """The class (Codex P2, populations.py:479): no code in gauntlet.py or populations.py
    filters readings by status (keeping only FAILs, or only PASSes) outside
    ``aggregate``: every combination of readings is ``aggregate``'s."""
    hits = []
    for path in (Path(g.__file__), ROOT / "tests/gauntlet/populations.py"):
        tree = ast.parse(path.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if fn.name == "aggregate":
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp
                              | ast.GeneratorExp):
                    for gen in node.generators:
                        for cond in gen.ifs:
                            if any(isinstance(a, ast.Attribute) and a.attr == "status"
                                   for a in ast.walk(cond)):
                                hits.append((path.name, fn.name, node.lineno))
    assert hits == []


# --- Codex pass on 47c5929: every row field read goes through need() -----------------------


def test_no_criterion_reads_a_row_field_by_direct_subscript():
    """The mechanical sweep: a ``row["field"]`` read raises KeyError on a malformed row
    instead of failing it; every field read in ``gauntlet.py`` goes through ``need()``
    (or ``.get``, the explicitly nullable accessor). Only the diary validators, which
    refuse a malformed diary as ``DiaryInvalid`` before any criterion runs, subscript."""
    tree = ast.parse(Path(g.__file__).read_text())
    hits = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        if fn.name in ("load_events", "diary_identity", "bind_diary"):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                hits.append((fn.name, node.lineno, ast.unparse(node)))
    assert hits == []


# Identity fields: each names the row's subject (its round, seat, card, router, window),
# and every emitter of a kind the criteria read them from always writes it (Decision
# and its ``vars`` spread, queue.py; the invocation row, compute.py; ``price.update``,
# controller.py; ``thrash.charged``, feedback.py). A ``.get`` of one turns a malformed
# row into None, a value that silently matches nothing (Codex on c78f2bc, TH-1c).
IDENTITY_FIELDS = frozenset({"handle", "actor", "window", "card_id", "router",
                             "assembly_id", "status", "role"})
# (function, field): where a ``.get`` of an identity field is the honest read.
IDENTITY_GET_ALLOWED: dict[tuple[str, str], str] = {
    ("Malformed", "handle"): "evidence of a row already refused; any kind, any shape",
    ("Malformed", "window"): "evidence of a row already refused; any kind, any shape",
    ("sf0_relation", "window"): "a manifest card's optional block, not a ledger row",
    ("sf1d_escalation", "window"): "read as nullable and reported as malformed evidence",
    ("router_presence", "window"): "immune.* kinds differ; one without window is placed "
                                   "by the price window it was written in",
    ("ld1a_accrual", "window"): "evidence of a row the criterion already fails",
    ("act_traces", "handle"): "walks every kind, most of which name no handle",
    ("s1_draw_sovereignty", "actor"): "propensity_problem fails a missing actor itself",
    ("s1_draw_sovereignty", "handle"): "evidence of a row propensity_problem refused",
    ("s4_boundedness", "handle"): "evidence over every reward kind; some name no handle",
    ("s4_boundedness", "card_id"): "evidence over every reward kind; some name no card",
    ("s5b_observed_neutral", "router"): "epoch writes router only when carried "
                                        "(RoutingMixin._open_epoch, routing.py)",
}


def _identity_gets():
    tree = ast.parse(Path(g.__file__).read_text())
    hits = set()
    for top in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
        if top.name in ("load_events", "diary_identity", "bind_diary"):
            continue  # the diary validators refuse a malformed diary before any criterion
        for node in ast.walk(top):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in IDENTITY_FIELDS):
                hits.add((top.name, node.args[0].value))
    return hits


def test_no_criterion_reads_an_identity_field_as_nullable():
    """Codex on c78f2bc: an identity field every emitter writes is read with ``need``,
    so its absence fails the criterion; ``.get`` only where the allowlist says why."""
    hits = _identity_gets()
    assert sorted(hits - set(IDENTITY_GET_ALLOWED)) == [], "a nullable identity read"
    assert sorted(set(IDENTITY_GET_ALLOWED) - hits) == [], "a stale allowance"
    assert all(reason.strip() for reason in IDENTITY_GET_ALLOWED.values())


def test_the_identity_guard_catches_a_nullable_handle(monkeypatch, tmp_path):
    """The guard would have caught TH-1c's ``row.get("handle")`` (c78f2bc)."""
    src = Path(g.__file__).read_text() + (
        "\n\ndef _planted(events):\n"
        "    return {row.get('handle') for row in events}\n")
    planted = tmp_path / "gauntlet.py"
    planted.write_text(src)
    monkeypatch.setattr(g, "__file__", str(planted))
    assert ("_planted", "handle") in _identity_gets()


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


# --- Astra G-2: S2 reads the one list of pathology labels ----------------------------------


def test_s2_scans_for_every_pathology_word():
    """S2's request scan is derived from ``PATHOLOGY_WORDS``: a thrash or overfit label as
    a JSON key or value is a diagnosis in a request, like the others."""
    from tests.gauntlet import populations as P

    assert P._LABEL_TOKENS == tuple(f'"{w}' for w in g.PATHOLOGY_WORDS)
    for word in g.PATHOLOGY_WORDS:
        assert P._diagnosis_labels(json.dumps({word: True})) == [word]
    assert "thrash" in P._diagnosis_labels(json.dumps({"flag": "thrash"}))
    assert "overfit" in P._diagnosis_labels(json.dumps({"overfitting_divergence": 1}))
    assert P._diagnosis_labels("the thrash price is lambda times movement") == []
    # The published price's key is the schematic, not a diagnosis.
    assert P._diagnosis_labels(json.dumps({"thrash_price": {"lambda": 0.1}})) == []
    assert P._diagnosis_labels(json.dumps({"thrash_price": 1, "thrash": 1})) == ["thrash"]


# --- Astra V-3: S3 is registered as population-only, with its reason -----------------------


def test_s3_is_registered_and_replay_names_it_unsupported():
    """S3 has no diary reading (it compares two runs); replay reports it UNSUPPORTED with
    the registered reason rather than omitting it."""
    assert g.POPULATION_METAMORPHIC["S3"].strip()
    rows = unit._seq([unit._launch(name="w"), unit._w(1)])
    s3 = [r for r in g.replay(rows) if r.name == "S3"]
    assert len(s3) == 1 and s3[0].status == g.UNSUPPORTED
    assert s3[0].evidence["why"] == g.POPULATION_METAMORPHIC["S3"]


# --- Codex on b7ae050: sweep runs each population's defining criteria ------------------


def test_every_defining_criterion_is_a_sweepable_populations_and_population_only():
    """``sweep`` dispatches each sweepable population's defining criteria (``DEFINING``);
    each one it names belongs to a population ``sweep`` runs."""
    from tests.gauntlet import populations as P

    assert set(P.DEFINING) <= P.SWEEPABLE
    assert {"th1", "th4", "i10", "of2"} <= set(P.DEFINING)


# --- Codex on b1c8590: no reading loops forever ------------------------------------------

#: ``while`` loops gauntlet.py may keep, each with its reason. None today: every open
#: iteration is a ``for`` over an explicit bound derived from the evidence.
WHILE_ALLOWED: dict[str, str] = {}


def test_no_open_ended_loop_in_the_gauntlet():
    """Every iteration in gauntlet.py has an explicit bound (a ``for`` over a range the
    evidence or the arithmetic fixes): a hang would bypass the criterion wrapper. A
    ``while`` loop, or a ``for`` over ``itertools.count``, fails here unless allowed with
    a reason."""
    source = Path(g.__file__).read_text()
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.FunctionDef):
                return node.name
        return "<module>"
    loops = [owner(node) for node in ast.walk(tree) if isinstance(node, ast.While)]
    loops += [owner(node) for node in ast.walk(tree) if isinstance(node, ast.For)
              and "count(" in ast.unparse(node.iter)]
    assert sorted(set(loops) - set(WHILE_ALLOWED)) == [], "an unbounded loop"
    assert all(reason.strip() for reason in WHILE_ALLOWED.values())


def _launched(manifest):
    launched = {**manifest, "name": manifest.get("name", "unit"), "seed": 1}
    return [{"kind": "event", "seq": 1, "event": {"id": "launch", "kind": "Launch", "payload": {
        "manifest": launched, "manifest_hash": g.manifest_hash(launched),
        "launch_nonce": "0" * 32}}}]


@pytest.mark.parametrize("block,field,value", [
    ("novelty", "share", -0.1), ("novelty", "share", 1.5), ("prices", "decay", 0.0),
    ("prices", "penalty_cap", 1.0), ("immune", "gain_step", 0.0), ("timing", "min_ratio", 2)])
def test_a_launched_manifest_the_kernel_refuses_is_diary_invalid(block, field, value):
    """Codex on b56e793: a diary's launched physics is validated by the kernel's own load
    validation (``WorldManifest.validate`` on the rebuilt manifest), never restated: a
    novelty share of -0.1, a decay of 0 or a ratio below 3 is refused as DiaryInvalid."""
    from factorylab.runtime.worlds import load_manifest

    good = json.loads(load_manifest("scripted").canonical_json())
    assert g.bind_diary(_launched(good))[1]["world"] == "scripted"
    bad = json.loads(json.dumps(good))
    bad[block][field] = value
    with pytest.raises(g.DiaryInvalid, match="the kernel would not have launched"):
        g.bind_diary(_launched(bad))


def _longrun1_manifest():
    events = g.load_events(ROOT / "tests/fixtures/longrun1_gauntlet_slice.json")
    return g.diary_identity(events)["manifest"]


@pytest.mark.parametrize("block,field,value,why", [
    ("novelty", "share", -0.1, "novelty share"), ("prices", "decay", 0.0, "decay"),
    ("immune", "gain_step", 0.0, "gain_step"), ("timing", "min_ratio", 2, "min_ratio")])
def test_a_pre_wave_16_launch_takes_the_full_kernel_validation(block, field, value, why):
    """Codex on d3dc486: a pre-wave-16 diary loses only the fields wave 16 retired
    (``RETIRED_FIELDS``: prices.lambda_max); the full kernel validation then runs, and a
    rule it breaks refuses it with the kernel's own error (longrun1 with a novelty share
    of -0.1 is refused). Longrun1 as launched binds."""
    launched = _longrun1_manifest()
    assert "lambda_max" in launched["prices"]
    assert g.kernel_problem(launched) is None
    bad = json.loads(json.dumps(launched))
    bad[block][field] = value
    assert why in (g.kernel_problem(bad) or "")
    with pytest.raises(g.DiaryInvalid, match="the kernel would not have launched"):
        g.bind_diary(_launched(bad))


def test_the_iterative_readings_are_bounded_and_kernel_exact():
    ph = g.physics({"prices": {"decay": 0.1, "penalty_cap": 0.5}})
    assert g.t_release(ph, 0.5) == 6  # the kernel's float loop, not ⌈0.5 / 0.1⌉
    assert g.t_release(ph, 0.0) == 0
    with pytest.raises(g.Malformed):
        g.t_release(ph, float("inf"))
    # Physics a reading could not finish under is bounded, never a hang.
    stalled = g.physics({"prices": {"decay": 0.0}})
    with pytest.raises(g.Malformed):
        g.t_release(stalled, 0.5)


# --- Codex on 45c3ccd: an evidence map never silently overwrites a row ---------------------

#: Subscript assignments with a computed key gauntlet.py keeps, by (function, map), each
#: with its reason: an accumulator or running state, not one row per key.
KEYED_ASSIGN_ALLOWED: dict[tuple[str, str], str] = {
    ("_runs", "result"): "extends the last run in place (a list, by index)",
    ("router_presence", "first"): "the earliest window each router is named in (a min)",
    ("router_round_periods", "out"): "a p90 computed per router from its accumulated rounds",
    ("th3_governance_gap", "slowest"): "the largest period seen per instant (a max)",
    ("i3c_niche_no_worse_than_noop", "noop_penalty"): "the least NOOP penalty per window "
                                                      "(a min)",
    ("expected_thrash_charges", "last"): "each router's previous draw: running state",
    ("thrash_attributed", "emits"): "a contract id re-registered with a new version "
                                    "emits what its latest registration says",
    ("hand_over", "successor"): "the router succession, rewritten as _hand_over does",
    ("s5b_observed_neutral", "raws"): "a fresh identity inherits its predecessor's rounds",
    ("s8_gain_rows_uniform", "seeds"): "set once per router, guarded by `not in`",
}
#: Dict comprehensions over ledger rows gauntlet.py keeps, by function, with reasons.
ROW_COMPREHENSION_ALLOWED: dict[str, str] = {
    "split_members": "several invocation rows per handle (tool rounds), one role",
    "update_windows": "keyed by id(row): one entry per row object",
}


def test_every_evidence_map_keyed_by_a_row_is_unique_or_explicitly_accumulating():
    """Codex on 45c3ccd: a map the kernel writes one row per key for goes through
    ``unique_put`` / ``unique_map`` (a duplicate is Malformed); a subscript assignment
    with a computed key, or a dict comprehension over ledger rows, is allowed only where
    it accumulates on purpose, with its reason."""
    tree = ast.parse(Path(g.__file__).read_text())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.FunctionDef):
                return node.name
        return "<module>"
    assigned, comprehended = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and not isinstance(target.slice,
                                                                          ast.Constant)
                        and owner(node) != "unique_put"):
                    assigned.add((owner(node), ast.unparse(target.value)))
        if isinstance(node, ast.DictComp):
            source = ast.unparse(node.generators[0].iter)
            # Keyed by a field of the row it iterates (``need(row, …)``,
            # ``row.get(…)``), or iterating ledger rows at all (Codex on d3dc486).
            targets = {t.id for gen in node.generators for t in ast.walk(gen.target)
                       if isinstance(t, ast.Name)}
            reads_row = any(
                isinstance(call, ast.Call)
                and (ast.unparse(call.func) == "need"
                     or ast.unparse(call.func).endswith(".get"))
                and targets & {n.id for n in ast.walk(call) if isinstance(n, ast.Name)}
                for call in ast.walk(node.key))
            if reads_row or any(word in source for word in ("rows_of", "card_windows",
                                                            "windows(", "events")):
                comprehended.add(owner(node))
    assert sorted(assigned - set(KEYED_ASSIGN_ALLOWED)) == [], "a keyed assignment"
    assert sorted(comprehended - set(ROW_COMPREHENSION_ALLOWED)) == [], "a row comprehension"
    assert all(r.strip() for r in [*KEYED_ASSIGN_ALLOWED.values(),
                                   *ROW_COMPREHENSION_ALLOWED.values()])


def test_a_duplicate_world_fact_is_malformed_not_overwritten():
    """Codex on 45c3ccd: the runtime writes one external fact per return, so a second
    fact for the same return fails OF-1a as malformed, never hides the first."""
    fact = {"kind": "consequence.outcome", "handle": "r", "y": 1, "net_micro": 900,
            "earned_micro": 200, "cost_micro": 1000, "censored": None}
    verdicts = [{"kind": "verdict.consequence", "about_handle": "r", "q": q, "y": 1.0,
                 "outcome": "return_paid_off"} for q in (0.2, 0.8)]
    assert g.of1a_outside_the_loop([fact, *verdicts], {}).ok
    second = fact | {"net_micro": 0, "earned_micro": 0}
    result = g.of1a_outside_the_loop([fact, second, *verdicts], {})
    assert result.status == g.FAIL and "single row" in result.evidence["malformed"]["field"]
    with pytest.raises(g.Malformed):
        g.decision_seats([{"kind": "decision.open", "handle": "d", "propensity": {"chosen": "a"}},
                          {"kind": "decision.open", "handle": "d", "propensity": {"chosen": "b"}}])


def test_a_duplicate_ratchet_row_is_malformed_not_overwritten():
    """Codex on d3dc486: one ratchet per card per window; a second row for it fails
    SF-1b as malformed, never overwrites the first's duration."""
    ratchet = {"kind": "immune.price_ratchet", "card_id": "c", "window": 4, "duration": 1}
    rows = [unit._w(4, acts=True, sf=True), ratchet, ratchet | {"duration": 9}]
    result = g.sf1b_ratchet_cadence(rows, unit.M)
    assert result.status == g.FAIL and "single row" in result.evidence["malformed"]["field"]


def test_the_row_map_guard_catches_a_comprehension_keyed_by_a_row_field(monkeypatch):
    source = Path(g.__file__).read_text() + (
        "\n\ndef _planted(rows):\n    return {need(r, 'handle'): r for r in rows}\n")
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: source)
    with pytest.raises(AssertionError, match="a row comprehension"):
        test_every_evidence_map_keyed_by_a_row_is_unique_or_explicitly_accumulating()
