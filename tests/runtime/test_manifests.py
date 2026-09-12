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
