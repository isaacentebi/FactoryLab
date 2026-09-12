import pytest

from factorylab.runtime.cli import main
from factorylab.runtime.worlds import (
    NS_PER_HOUR,
    load_manifest,
    manifest_from_dict,
    usd_to_micro,
)


def test_usd_to_micro_exact() -> None:
    assert usd_to_micro("100") == 100_000_000
    assert usd_to_micro("0.000001") == 1
    with pytest.raises(ValueError):
        usd_to_micro("0.0000001")


def test_scripted_manifest_loads_and_hashes_stably() -> None:
    m = load_manifest("scripted")
    assert m.name == "scripted" and m.exchange.kind == "fake"
    assert m.initial_balance_micro == 100_000_000
    assert m.drip is None  # phase 2: one starting balance, no drip in the seed worlds
    # short windows so charter editions can activate within a test run
    assert m.novelty.window_ns == 2 * 60 * 1_000_000_000
    assert m.price_table().cost("fake-opus", 1000, 100) == 1000 * 5 + 100 * 25
    assert m.manifest_hash() == load_manifest("scripted").manifest_hash()
    assert m.manifest_hash() != load_manifest("testnet").manifest_hash()


def test_testnet_manifest_is_not_mainnet() -> None:
    m = load_manifest("testnet")
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    ids = {t.id for t in m.models}
    assert {"z-ai/glm-5.3-flash", "qwen/qwen3.8-flash", "tencent/hy3", "openai/gpt-5.6-luna"} <= ids
    assert all(t.provider == "openrouter" for t in m.models)
    assert "z-ai/glm-5.3-flash:online" in m.price_table().prices


def _base() -> dict:
    return {
        "name": "x",
        "initial_balance_usd": "10",
        "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
        "assemblies": [{"id": "a", "model_id": "m"}],
        "novelty": {"share": 0.1, "window": "1h"},
    }


def test_validation_rejects_unpriced_assembly_and_mainnet_outside_funded() -> None:
    d = _base()
    d["assemblies"][0]["model_id"] = "ghost"
    with pytest.raises(ValueError):
        manifest_from_dict(d)
    d = _base()
    d["exchange"] = {"kind": "hyperliquid", "mainnet": True}
    with pytest.raises(ValueError):
        manifest_from_dict(d)
    d["name"] = "funded"
    assert manifest_from_dict(d).exchange.mainnet is True


def test_duration_strings() -> None:
    d = _base()
    d["novelty"]["window"] = "36h"
    assert manifest_from_dict(d).novelty.window_ns == 36 * NS_PER_HOUR


def test_cli_manifest_command(capsys) -> None:
    assert main(["manifest", "--world", "scripted"]) == 0
    out = capsys.readouterr().out
    assert '"name": "scripted"' in out and '"hash"' in out


def test_cli_probe_refuses_world_without_live_venue(capsys) -> None:
    assert main(["probe", "--world", "scripted"]) == 2


@pytest.mark.network
def test_cli_probe_testnet(capsys) -> None:
    assert main(["probe", "--world", "testnet"]) == 0
    assert "hyperliquid-testnet" in capsys.readouterr().out


def test_prices_section_defaults_and_validation() -> None:
    m = manifest_from_dict(_base())
    assert (m.prices.eta, m.prices.decay, m.prices.lambda_max, m.prices.min_window_events) == (
        0.5,
        0.1,
        1.0,
        1,
    )
    d = _base()
    d["prices"] = {"eta": 0.25, "decay": 0.05, "lambda_max": 2, "min_window_events": 3}
    m2 = manifest_from_dict(d)
    assert m2.prices.lambda_max == 2.0 and m2.prices.min_window_events == 3
    assert (
        m2.manifest_hash() != m.manifest_hash()
    )  # the controller's parameters are part of the seed
    for bad in ({"eta": 0}, {"decay": -1}, {"lambda_max": 0}, {"min_window_events": 0}):
        d = _base()
        d["prices"] = bad
        with pytest.raises(ValueError):
            manifest_from_dict(d)


def test_clock_bounds_seed_validation_and_hash():
    d = _base()
    d["clock"] = {"min_tick": "10s"}
    d["tick_interval"] = "10s"
    m = manifest_from_dict(d)
    assert m.clock.min_tick_ns == 10_000_000_000
    assert m.max_tick_ns == 1200_000_000_000
    assert "max_tick" not in m.canonical_json()
    d["tick_interval"] = "20m"
    assert manifest_from_dict(d).tick_interval_ns == m.max_tick_ns
    for invalid in ["9s", "1201s"]:
        d["tick_interval"] = invalid
        with pytest.raises(ValueError, match="tick_interval"):
            manifest_from_dict(d)
    d["tick_interval"] = "10s"
    d["clock"]["min_tick"] = "5s"
    assert manifest_from_dict(d).manifest_hash() != m.manifest_hash()
    d["clock"]["max_tick"] = "20m"
    with pytest.raises(ValueError, match="derived"):
        manifest_from_dict(d)


def test_clock_seed_intervals_remain_unchanged():
    assert load_manifest("scripted").tick_interval_ns == 1_000_000_000
    assert load_manifest("scripted-crash").tick_interval_ns == 1_000_000_000
    assert load_manifest("testnet").tick_interval_ns == 60_000_000_000
    assert load_manifest("testnet").clock.min_tick_ns == 10_000_000_000


@pytest.mark.parametrize("world", ["scripted", "scripted-crash", "testnet"])
def test_damping_and_cadence_are_explicit_manifest_parameters(world):
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    with (WORLDS_DIR / f"{world}.toml").open("rb") as source:
        raw = tomllib.load(source)
    assert raw["prices"]["kappa"] == 0.5
    assert raw["timing"]["cadence_sample"] == 200
    m = load_manifest(world)
    assert m.prices.kappa == 0.5 and m.timing.cadence_sample == 200


def test_damping_and_cadence_defaults_overrides_and_hashes():
    default = manifest_from_dict(_base())
    assert default.prices.kappa == 0.5 and default.timing.cadence_sample == 200
    for section, key, value in (("prices", "kappa", 0), ("timing", "cadence_sample", 10)):
        raw = _base()
        raw[section] = {key: value}
        changed = manifest_from_dict(raw)
        assert getattr(getattr(changed, section), key) == value
        assert changed.manifest_hash() != default.manifest_hash()


@pytest.mark.parametrize("value", [-1, True, "0.5", float("nan"), float("inf")])
def test_invalid_kappa_is_rejected(value):
    raw = _base()
    raw["prices"] = {"kappa": value}
    with pytest.raises(ValueError, match="kappa"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "200", None])
def test_invalid_cadence_sample_is_rejected(value):
    raw = _base()
    raw["timing"] = {"cadence_sample": value}
    with pytest.raises(ValueError, match="cadence_sample"):
        manifest_from_dict(raw)


def _with_charter():
    from dataclasses import asdict

    from factorylab.charter.charter import seed_charter

    raw = _base()
    raw["charter"] = asdict(seed_charter())
    raw["charter"]["norms"] = list(raw["charter"]["norms"])
    raw["charter"]["cards"] = list(raw["charter"]["cards"])
    return raw


def test_manifest_charter_defaults_and_explicit_hash():
    from factorylab.charter.charter import seed_charter

    default = manifest_from_dict(_base())
    assert default.charter == seed_charter()
    raw = _with_charter()
    assert manifest_from_dict(raw).manifest_hash() == default.manifest_hash()
    for field, value in (("description", "Population draft"), ("lambda", 0.4)):
        changed = _with_charter()
        changed["charter"]["cards"][0][field] = value
        assert manifest_from_dict(changed).manifest_hash() != default.manifest_hash()


@pytest.mark.parametrize(("field", "value"), [
    ("observation", "missing"), ("acceptable_region", "roughly adequate"),
    ("norm", "unknown"), ("description", None), ("observation", 12),
    ("lambda", -0.1), ("lambda", 1.1), ("lambda", True), ("lambda", "0.5"),
    ("lambda", float("nan")), ("lambda", float("inf")),
])
def test_manifest_charter_rejects_card_field_with_identity(field, value):
    raw = _with_charter()
    raw["charter"]["cards"][0][field] = value
    with pytest.raises(ValueError, match=f"cost_per_return.*{field}"):
        manifest_from_dict(raw)


def test_manifest_charter_duplicate_ids_and_lambda_bounds():
    raw = _with_charter()
    raw["charter"]["cards"].append(raw["charter"]["cards"][0])
    with pytest.raises(ValueError, match="cost_per_return.*id"):
        manifest_from_dict(raw)
    for value in (0, 2):
        raw = _with_charter()
        raw["prices"] = {"lambda_max": 2}
        raw["charter"]["cards"][0]["lambda"] = value
        assert manifest_from_dict(raw).charter_prices == (("cost_per_return", value),)


@pytest.mark.parametrize("norms", [None, [], "a norm", [1], [""]])
def test_manifest_charter_requires_norm_list(norms):
    raw = _with_charter()
    raw["charter"]["norms"] = norms
    with pytest.raises(ValueError, match="norms"):
        manifest_from_dict(raw)


def test_example_manifest_and_cli_resolve_population_charter(capsys):
    import json
    from dataclasses import replace

    example = load_manifest("edition1-example")
    base = load_manifest("testnet")
    assert replace(example, name=base.name, charter=base.charter, treasury=base.treasury) == base
    assert {c.id: c.observation for c in example.charter.cards} == {
        "model_cost_efficiency": "cost_per_return", "revision_rate": "revision_rate",
        "well_formed_rate": "well_formed_rate",
        "verdict_mean_score": "verdict_mean",
    }
    assert main(["manifest", "--world", "edition1-example"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["charter"] == json.loads(example.canonical_json())["charter"]
    assert out["charter"]["edition"] == 1


@pytest.mark.parametrize("field", ["answers_for"])
def test_manifest_card_requires_explicit_role(field):
    raw = _with_charter()
    del raw["charter"]["cards"][0][field]
    with pytest.raises(ValueError, match="cost_per_return.*answers_for"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("value", [True, None, "", "unknown"])
def test_manifest_card_rejects_unknown_role(value):
    raw = _with_charter()
    raw["charter"]["cards"][0]["answers_for"] = value
    with pytest.raises(ValueError, match="cost_per_return.*answers_for"):
        manifest_from_dict(raw)


@pytest.mark.parametrize("section,field,value", [
    ("novelty", "trial_invocations", True), ("novelty", "trial_invocations", 0),
    ("novelty", "trial_invocations", 1.5), ("committee", "min_settled", 0),
    ("committee", "min_settled", False), ("immune", "k", 1), ("immune", "k", 3.0),
    ("immune", "bins", 1), ("immune", "tv_threshold", -1),
    ("immune", "gamma_max", 1.1), ("immune", "gap_threshold", float("nan")),
    ("immune", "gain_step", True), ("immune", "decay_step", 0),
])
def test_fidelity_casts_reject_invalid_values(section, field, value):
    raw = _base()
    raw.setdefault(section, {})[field] = value
    with pytest.raises(ValueError, match=rf"{section}\.{field}"):
        manifest_from_dict(raw)


def test_fidelity_casts_are_explicit_in_all_worlds_and_hashed():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    for path in WORLDS_DIR.glob("*.toml"):
        raw = tomllib.loads(path.read_text())
        manifest = manifest_from_dict(raw)
        assert raw["novelty"]["trial_invocations"] == manifest.novelty.trial_invocations == 3
        assert raw["committee"]["min_settled"] == manifest.committee.min_settled == 5
        for key in ("k", "bins", "tv_threshold", "gap_threshold", "gain_step", "gamma_max",
                    "decay_step"):
            assert raw["immune"][key] == getattr(manifest.immune, key)
        raw["immune"]["k"] += 1
        assert manifest.manifest_hash() != manifest_from_dict(raw).manifest_hash()
