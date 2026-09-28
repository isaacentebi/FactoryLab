"""Structural schematics share admission definitions (Chapter II §I.b)."""

import json

import pytest

from factorylab.cortex.admission_structure import structural_admission_schematics
from factorylab.cortex.assembly import (
    CHILD_SCHEMA_KEYWORDS,
    MAX_PROGRAM_STATE_BYTES,
    PROPOSAL_MIN_MAX_TOKENS,
    child_schema_shape,
    proposal_field_schema,
    reserved_return_fields,
    validate_proposal,
)


def test_structural_schematics_share_enforced_shapes():
    facts = structural_admission_schematics()
    assert facts["return_envelope"] == reserved_return_fields()
    assert facts["proposals"]["generic_schema"] == proposal_field_schema()
    assert set(child_schema_shape()["properties"]) == CHILD_SCHEMA_KEYWORDS
    assert facts["program_return"]["state_max_bytes"] == MAX_PROGRAM_STATE_BYTES
    json.dumps(facts, allow_nan=False)


def test_structural_schematics_are_detached():
    original = structural_admission_schematics()
    modified = structural_admission_schematics()
    modified["kind_fields"]["Verdict"]["verdict"]["maximum"] = -1
    modified["return_envelope"]["requests"]["items"]["required"].clear()
    assert structural_admission_schematics() == original


def test_generic_proposal_schema_keeps_existing_token_boundary():
    validate_proposal({"kind": "unknown", "max_tokens": PROPOSAL_MIN_MAX_TOKENS})
    with pytest.raises(ValueError, match="number out of range"):
        validate_proposal({"kind": "unknown", "max_tokens": PROPOSAL_MIN_MAX_TOKENS - 1})
    with pytest.raises(ValueError, match="wrong field type"):
        validate_proposal({"kind": "unknown", "max_tokens": True})
