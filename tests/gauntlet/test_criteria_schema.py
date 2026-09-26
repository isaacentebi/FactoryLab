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


def _literally_nullable():
    """Every (kind, field) of ``UNIT_FIELDS`` some emitter leaves out, writes as the
    constant None, or writes as a conditional with a None branch."""
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
                if (value is None and "." not in name) or any(
                        isinstance(n, ast.Constant) and n.value is None
                        for n in ast.walk(value or ast.Constant(0))):
                    found.add((kind.value, name))
    return found


def test_s4_nullable_fields_are_exactly_what_the_emitters_write_as_none():
    assert set(g.NULLABLE) == _literally_nullable() | set(NULLABLE_BY_VALUE)


def test_s4_a_missing_required_field_fails_on_every_real_row():
    """Codex P2: every field the kernel always writes is required: dropping it from the
    real row of its kind fails S4."""
    manifest = {"prices": {"penalty_cap": 0.5, "lambda_max": 1.0}}
    tried = 0
    for kind, fields in g.UNIT_FIELDS.items():
        if kind not in REAL["rows"]:
            continue
        for name in fields:
            if (kind, name) in g.NULLABLE or "." in name:
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
