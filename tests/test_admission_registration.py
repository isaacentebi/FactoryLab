"""Public parser facts track admission, including the identifier edge cases."""

import re

import pytest

from factorylab.cortex import registration as r
from factorylab.cortex.admission_registration import registration_admission_schematics
from factorylab.cortex.schematics import SchematicsMixin


def _assembly(**changes):
    return {"id": "test-seat", "model_id": "model", "system_prompt": "p",
            "accepts": ["Tick"], **changes}


def _parse_assembly(item):
    return r._assembly(item, frozenset(), frozenset({"model"}), frozenset(), jail=True)


def test_identifier_pattern_bounds_and_normalisation():
    facts = registration_admission_schematics()["identifiers"]
    assert facts["pattern"] == r.SLUG.pattern == r.SLUG_PATTERN
    assert re.fullmatch(r"\^\[a-z\]\[a-z0-9-\]\{1,47\}\$", facts["pattern"])
    for n in (facts["min_characters"], facts["max_characters"]):
        assert r.SLUG.fullmatch("a" * n)
    for n in (facts["min_characters"] - 1, facts["max_characters"] + 1):
        with pytest.raises(ValueError, match="id must match"):
            _parse_assembly(_assembly(id="a" * n))
    # The pre-existing assembly match/$ behavior differs from fullmatch.
    assert _parse_assembly(_assembly(id="ab\n")).id == "ab\n"
    p = r._observation({"id": "  OBS-ID ", "description": "d", "unit": "x",
                        "range": [0, 1], "code": "observe"}, frozenset(), jail=True)
    assert p.id == "obs-id"
    with pytest.raises(ValueError, match="id must match"):
        _parse_assembly(_assembly(id="  OBS-ID "))


@pytest.mark.parametrize(("field", "fact", "accepted", "refused"), [
    ("system_prompt", "max_prompt_characters", lambda n: "x" * n, lambda n: "x" * (n + 1)),
    ("max_tokens", "min_max_tokens", lambda n: n, lambda n: n - 1),
    ("description", "max_description_characters", lambda n: "x" * n,
     lambda n: "x" * (n + 1)),
])
def test_assembly_disclosed_bound_is_actual_boundary(field, fact, accepted, refused):
    n = registration_admission_schematics()["assembly"][fact]
    _parse_assembly(_assembly(**{field: accepted(n)}))
    with pytest.raises(ValueError):
        _parse_assembly(_assembly(**{field: refused(n)}))


@pytest.mark.parametrize(("kind", "field", "fact"), [
    ("program", "code", "max_code_characters"),
    ("tool", "code", "max_code_characters"),
    ("observation", "code", "max_code_characters"),
    ("observation", "description", "max_description_characters"),
    ("observation", "unit", "max_unit_characters"),
])
def test_program_tool_observation_boundaries(kind, field, fact):
    n = registration_admission_schematics()[kind][fact]
    if kind == "program":
        item = _assembly(model_id="program", code="x")
        parse = _parse_assembly
    elif kind == "tool":
        item = {"id": "test-tool", "description": "d", "code": "x", "timeout_s": 1,
                "args_schema": {"type": "object", "properties": {}}}
        def parse(value):
            return r._tool(value, frozenset(), jail=True)
    else:
        item = {"id": "test-observation", "description": "d", "unit": "x",
                "range": [0, 1], "code": "observe"}
        def parse(value):
            return r._observation(value, frozenset(), jail=True)
    prefix = "observe" if kind == "observation" and field == "code" else ""
    parse({**item, field: prefix + "x" * (n - len(prefix))})
    with pytest.raises(ValueError):
        parse({**item, field: prefix + "x" * (n + 1 - len(prefix))})


def test_trial_and_catalogue_follow_nondefault_manifest_without_world():
    from types import SimpleNamespace

    class Facts(SchematicsMixin):
        m = SimpleNamespace(evaluation=SimpleNamespace(trial_amount_micro=1234567))

    facts = Facts()
    for name in ("trials", "reserve"):
        assert facts.institution_section(name)["trial_amount_micro"] == 1234567
    for admission in facts.institution_section("proposals")["proposal_admission"].values():
        assert admission["identifiers"]["pattern"] == r.SLUG.pattern
        assert admission["trial"]["trial_amount_micro"] == 1234567
