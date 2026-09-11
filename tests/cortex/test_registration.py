from factorylab.cortex.registration import (
    AssemblyProposal,
    ModelProposal,
    RouterProposal,
    parse_proposals,
)

KINDS = frozenset({"Tick", "MarketMid", "Fill", "ProducerReturn", "Verdict"})
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
                "accepts": ["Tick"],
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
    assert "evaluators accept exactly ProducerReturn" in reasons
    assert any("exceeds" in r for r in reasons)
    assert "event_kind must be a known event kind" in reasons
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
