import pytest

from factorylab.cortex.assembly import validate_proposal
from factorylab.cortex.registration import (
    AssemblyProposal,
    ModelProposal,
    RouterProposal,
    parse_proposals,
)

KINDS = frozenset({"Tick", "MarketMid", "Fill", "ProducerReturn", "Verdict", "MetaVerdict"})
MODELS = frozenset({"glm-flash", "ds-flash"})
ASSEMBLIES = frozenset({"seed-decider"})


def _parse(items):
    return parse_proposals(
        {"register": items},
        event_kinds=KINDS,
        known_models=MODELS,
        known_assemblies=ASSEMBLIES,
    )


def test_no_register_key_is_nothing() -> None:
    assert parse_proposals(
        {"action": "hold"}, event_kinds=KINDS, known_models=MODELS, known_assemblies=ASSEMBLIES
    ) == ([], [])


def test_valid_proposals_of_each_kind() -> None:
    acc, rej = _parse(
        [
            {"kind": "model", "openrouter_id": "meta/muse-spark-1.3"},
            {
                "kind": "assembly",
                "id": "funding-watcher",
                "role": "producer",
                "model_id": "ds-flash",
                "system_prompt": "Watch funding and reply with JSON.",
                "accepts": ["MarketMid", "MarketMid", "Tick"],
                "max_tokens": 256,
            },
            {"kind": "router", "event_kind": "Tick", "learner": "blum_mansour", "gamma": 0.2},
        ]
    )
    assert rej == []
    assert acc[0] == ModelProposal("meta/muse-spark-1.3")
    assert isinstance(acc[1], AssemblyProposal) and acc[1].accepts == ("MarketMid", "Tick")
    assert acc[1].effort == "low"
    assert acc[2] == RouterProposal("Tick", "blum_mansour", 0.2)


def test_assembly_max_tokens_uses_provider_native_allowance_when_unspecified() -> None:
    for max_tokens in ({}, {"max_tokens": None}):
        accepted, rejected = _parse([{
            "kind": "assembly", "id": "native-budget", "model_id": "ds-flash",
            "system_prompt": "Use the available context.", "accepts": ["Tick"],
            **max_tokens,
        }])

        assert rejected == []
        assert accepted[0].max_tokens is None
    validate_proposal({"kind": "assembly", "id": "native-budget", "model_id": "ds-flash",
                       "system_prompt": "Use the available context.", "accepts": ["Tick"],
                       "max_tokens": None})


def test_assembly_max_tokens_has_no_arbitrary_upper_bound() -> None:
    accepted, rejected = _parse([
        {
            "kind": "assembly", "id": "large-context", "model_id": "ds-flash",
            "system_prompt": "Use the available context.", "accepts": ["Tick"],
            "max_tokens": 8192,
        },
        {
            "kind": "assembly", "id": "very-large-context", "model_id": "ds-flash",
            "system_prompt": "Use the available context.", "accepts": ["Tick"],
            "max_tokens": 32768,
        },
    ])

    assert rejected == []
    assert [proposal.max_tokens for proposal in accepted] == [8192, 32768]
    validate_proposal({"kind": "assembly", "id": "very-large-context", "model_id": "ds-flash",
                       "system_prompt": "Use the available context.", "accepts": ["Tick"],
                       "max_tokens": 32768})


@pytest.mark.parametrize("max_tokens", [15, -1, True, 16.0, "8192"])
def test_assembly_max_tokens_stays_an_integer_of_at_least_16(max_tokens) -> None:
    accepted, rejected = _parse([{
        "kind": "assembly", "id": "bad-budget", "model_id": "ds-flash",
        "system_prompt": "Use the available context.", "accepts": ["Tick"],
        "max_tokens": max_tokens,
    }])

    assert accepted == []
    assert [item.reason for item in rejected] == [
        "max_tokens must be null or an int of at least 16"
    ]


def test_rejections_carry_reasons_and_cap_is_enforced() -> None:
    acc, rej = _parse(
        [
            {
                "kind": "assembly",
                "id": "seed-decider",
                "model_id": "ds-flash",
                "system_prompt": "x",
                "accepts": ["Tick"],
            },
            {
                "kind": "assembly",
                "id": "Bad Id",
                "model_id": "ds-flash",
                "system_prompt": "x",
                "accepts": ["Tick"],
            },
            {
                "kind": "assembly",
                "id": "ghost",
                "model_id": "nope",
                "system_prompt": "x",
                "accepts": ["Tick"],
            },
            {
                "kind": "assembly",
                "id": "judge",
                "role": "evaluator",
                "model_id": "ds-flash",
                "system_prompt": "x",
                "accepts": [],
            },
            {
                "kind": "assembly",
                "id": "long",
                "model_id": "ds-flash",
                "system_prompt": "x" * 5000,
                "accepts": ["Tick"],
            },
            {"kind": "router", "event_kind": "Nope", "learner": "exp3"},
            {"kind": "router", "event_kind": "Tick", "learner": "exp3", "gamma": 0},
            {"kind": "model", "openrouter_id": "no-slash"},
            {"kind": "thing"},
            "not an object",
        ]
    )
    assert acc == []
    reasons = [r.reason for r in rej]
    assert "id already registered" in reasons
    assert "id must be a slug of 2-48 chars" in reasons
    assert "model_id must name a registered model" in reasons
    assert "accepts must be a non-empty list of event kinds" in reasons
    assert any("exceeds" in r for r in reasons)
    assert "event_kind must name a world or population-declared event kind" in reasons
    assert "gamma must be in (0, 1]" in reasons
    assert "openrouter_id must look like vendor/model" in reasons
    assert "unknown proposal kind" in reasons
    assert "proposal must be an object" in reasons

    many = [{"kind": "model", "openrouter_id": f"v/m{i}"} for i in range(5)]
    acc, rej = _parse(many)
    assert len(acc) == 3 and [r.reason for r in rej] == ["proposal cap reached for this return"] * 2


def test_register_must_be_list() -> None:
    acc, rej = _parse({"kind": "model"})
    assert acc == [] and rej[0].reason == "register must be a list"


@pytest.mark.parametrize("accepts", [["Verdict"], ["MetaVerdict"]])
def test_meta_accepts_one_evaluation_kind(accepts):
    accepted, rejected = _parse([{
        "kind": "assembly", "id": "recursive-meta", "role": "meta",
        "model_id": "ds-flash", "system_prompt": "Judge the supplied return.",
        "accepts": accepts,
    }])
    assert not rejected
    assert accepted[0].accepts == tuple(accepts)


@pytest.mark.parametrize("role,accepts", [
    ("meta", []), ("meta", ["Verdict", "MetaVerdict"]),
    ("meta", ["MetaVerdict", "MetaVerdict"]), ("meta", ["Verdict", "Verdict"]),
    ("meta", ["Tick"]), ("meta", ["ProducerReturn"]),
    ("producer", ["MetaVerdict"]), ("antagonist", ["MetaVerdict"]),
    ("evaluator", ["MetaVerdict"]),
])
def test_role_labels_do_not_restrict_accepted_kinds(role, accepts):
    accepted, rejected = _parse([{
        "kind": "assembly", "id": "bad-meta", "role": role,
        "model_id": "ds-flash", "system_prompt": "Judge.", "accepts": accepts,
    }])
    if not accepts:
        assert not accepted and len(rejected) == 1
    else:
        assert not rejected
        assert accepted[0].accepts == tuple(dict.fromkeys(accepts))
