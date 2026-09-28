"""Review-linked drift guards for the public admission inventory (Chapter II §I.b)."""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from factorylab.cortex.schematics import SchematicsMixin
from factorylab.runtime.worlds import load_manifest

ROOT = Path(__file__).resolve().parents[1]
REVIEW = json.loads((Path(__file__).parent / "admission_inventory.json").read_text())


def _fingerprint(path):
    tree = ast.parse((ROOT / path).read_text())
    # Documentation wording and line moves do not alter a validator. Constants,
    # comparisons, called helpers and return/exception paths do (§II.b hard cast).
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


@pytest.fixture(scope="module")
def public_facts():
    reader = SchematicsMixin()
    reader.m = load_manifest("scripted")
    return reader.institution_section("admission")


@pytest.mark.parametrize("row", REVIEW["rules"], ids=lambda row: row["id"])
def test_every_reviewed_rule_remains_published(row, public_facts):
    value = public_facts[row["surface"]]
    for key in row["path"]:
        value = value[key]
    assert value == row["value"], (
        f"Public admission changed for {row['rule']}; reconcile its enforcement and table")
    assert row["enforced"] and row["published"]
    for index in row["enforced"]:
        anchor = REVIEW["audit_rows"][index]
        assert anchor["enforced"] and anchor["published"]


def test_every_audit_row_has_a_retrieved_leaf_and_real_publication():
    linked = {index for row in REVIEW["rules"] for index in row["enforced"]}
    assert linked == set(range(len(REVIEW["audit_rows"])))
    for row in REVIEW["audit_rows"]:
        path, rest = row["published"].split(":", 1)
        line = int(rest.split()[0].split("–")[0].split("-")[0])
        assert 0 < line <= len((ROOT / path).read_text().splitlines())


@pytest.mark.parametrize("path", sorted(REVIEW["enforcement"]))
def test_enforcement_cannot_drift_without_publication_review(path):
    assert _fingerprint(path) == REVIEW["enforcement"][path], (
        f"Admission enforcement or its constants changed in {path}; review every linked "
        "rule in admission_inventory.json and its retrieved publication before updating this guard")
