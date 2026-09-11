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
    assert m.novelty.window_ns == 2 * 60 * 1_000_000_000  # short windows so charter editions can activate in tests
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
