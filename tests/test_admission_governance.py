"""Governance disclosure stays detached and reads the enforcing limits."""

import json
from types import SimpleNamespace

from factorylab.cortex.admission_governance import governance_admission_schematics


def _manifest():
    return SimpleNamespace(
        evaluation=SimpleNamespace(trial_amount_micro=123456),
        tools=SimpleNamespace(max_routers_per_kind=7),
        treasury=SimpleNamespace(max_request_micro=456789),
        connectors=SimpleNamespace(timeout_s=17, max_bytes=12345),
        committee=SimpleNamespace(quorum=4, seats=9, min_settled=12),
        clock=SimpleNamespace(min_tick_ns=1234),
        max_tick_ns=999999,
        look_ahead_rule=lambda: "world-specific model admission",
    )


def test_governance_manifest_limits_are_not_defaults():
    facts = governance_admission_schematics(_manifest())
    assert facts["trial_and_endowment"]["trial_amount_micro"] == 123456
    assert facts["router"]["max_routers_per_kind"] == 7
    assert facts["connector"]["max_paid_call_micro"] == 456789
    assert facts["connector"]["timeout_s"] == 17
    assert facts["connector"]["max_bytes"] == 12345
    assert facts["committee"]["quorum"] == 4
    assert facts["committee"]["seats"] == 9
    assert facts["committee"]["min_settled"] == 12
    assert facts["amendment"]["clock"]["min_tick_ns"] == 1234
    assert facts["amendment"]["clock"]["max_tick_ns"] == 999999
    assert facts["model"]["look_ahead"] == "world-specific model admission"
    json.dumps(facts, allow_nan=False)


def test_governance_shared_limits_and_schemas_are_detached():
    from factorylab.charter.holdout import BEHAVIOURAL_FACTS
    from factorylab.charter.region import region_schema
    from factorylab.charter.windows import window_schema
    from factorylab.cortex.registration import MAX_CHALLENGE_TRIAL_WINDOWS, MAX_EVIDENCE_CHARS
    from factorylab.runtime.governance import MODEL_BASE_MAX_CHARS, MODEL_REASONING_LEVELS

    facts = governance_admission_schematics(_manifest())
    assert facts["model"]["non_x402_base_max_chars"] == MODEL_BASE_MAX_CHARS
    assert facts["model"]["reasoning_levels"] == list(MODEL_REASONING_LEVELS)
    assert facts["holdout"]["max_trial_windows"] == MAX_CHALLENGE_TRIAL_WINDOWS
    assert facts["holdout"]["max_evidence_chars"] == MAX_EVIDENCE_CHARS
    assert facts["holdout"]["code"]["facts"] == sorted(BEHAVIOURAL_FACTS)
    assert facts["card"]["region_schema"] == region_schema()
    assert facts["card"]["window_schema"] == window_schema()
    facts["holdout"]["code"]["facts"].clear()
    assert governance_admission_schematics(_manifest())["holdout"]["code"]["facts"]


def test_governance_unbounded_clock_is_json_null():
    manifest = _manifest()
    manifest.max_tick_ns = None
    manifest.look_ahead_rule = lambda: None
    facts = governance_admission_schematics(manifest)
    assert facts["amendment"]["clock"]["max_tick_ns"] is None
    json.dumps(facts, allow_nan=False)
