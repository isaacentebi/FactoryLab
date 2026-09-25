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
    rows = [dict(r, seq=r.get("seq", 0)) for r in REAL["rows"].values()]
    for result in g.replay(sorted(rows, key=lambda r: r["seq"]), {}):
        assert result.status in (g.PASS, g.FAIL, g.UNSUPPORTED)
