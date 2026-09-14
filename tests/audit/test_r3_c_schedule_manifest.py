"""Round three: observable cadence and explicit live charter requirements."""

import tomllib
from dataclasses import replace

import pytest

from factorylab.runtime.worlds import WORLDS_DIR, load_manifest, manifest_from_dict
from tests.audit.test_c3_governance import _boundary, _experienced
from tests.conftest import make_runtime
from tests.runtime.test_fidelity import decision
from tests.runtime.test_manifests import _base, _with_charter


def test_world_block_publishes_each_next_activation_boundary():
    rt = make_runtime()
    _experienced(rt)
    first = rt._world_block()["governance"]["earliest_activation_event"]
    author = decision(rt, "seed-decider")
    rt._propose_amendment(
        author,
        {
            "id": "change-clock",
            "tick_interval": "2s",
            "predicted_effect": {
                "card_id": "cost_per_return",
                "direction": "decrease",
                "window": 1,
            },
        },
    )
    _boundary(rt)
    assert rt.charter.edition == 2
    second = rt._world_block()["governance"]["earliest_activation_event"]
    assert second == rt.n + rt.m.timing.min_ratio * rt.cadence.slowest_period_events()
    assert second > first
    for _ in range(3):
        rt.n += 1
        rt.cadence.advance(rt.n)
        rt.stats.reserve_windows += 1
        rt._manage_reserve_window()
        assert rt._world_block()["governance"]["earliest_activation_event"] == second


@pytest.mark.parametrize("mainnet", [False, True])
def test_live_exchange_requires_explicit_charter(mainnet):
    raw = _base()
    raw["name"] = "funded" if mainnet else "testnet-fixture"
    raw["exchange"] = {"kind": "hyperliquid", "mainnet": mainnet}
    if mainnet:
        with pytest.raises(ValueError, match="live_exchange_requires_explicit_charter"):
            manifest_from_dict(raw)
    else:
        # A testnet rehearsal may run on the seed charter before edition 1 is drafted.
        assert manifest_from_dict(raw).charter_explicit is False
    raw["charter"] = _with_charter()["charter"]
    if mainnet:
        # A funded world needs its own venue identity space as well as the charter.
        with pytest.raises(ValueError, match="client_namespace"):
            manifest_from_dict(raw)
    else:
        assert manifest_from_dict(raw).exchange.kind == "hyperliquid"


def test_direct_manifest_validation_refuses_implicit_live_charter():
    seed = load_manifest("scripted")
    live = replace(seed, name="funded",
                   exchange=replace(seed.exchange, kind="hyperliquid", mainnet=True))
    with pytest.raises(ValueError, match="live_exchange_requires_explicit_charter"):
        live.validate()


def test_shipped_testnet_loads_on_the_seed_charter_but_funded_would_not():
    raw = tomllib.loads((WORLDS_DIR / "testnet.toml").read_text())
    assert "charter" not in raw
    assert manifest_from_dict(raw).charter_explicit is False
    funded = {**raw, "name": "funded", "exchange": {**raw["exchange"], "mainnet": True}}
    with pytest.raises(ValueError, match="live_exchange_requires_explicit_charter"):
        manifest_from_dict(funded)


def test_stale_retirement_head_does_not_block_the_next_retirement():
    from factorylab.cortex.request import Return

    rt = make_runtime()
    _experienced(rt)
    author = decision(rt, "seed-decider")
    for target in ("eval-a", "eval-b"):
        rt._apply_registrations(
            author,
            Return(
                author,
                {
                    "register": [
                        {
                            "kind": "retire",
                            "assembly_id": target,
                            "predicted_effect": {
                                "card_id": "cost_per_return",
                                "direction": "decrease",
                                "window": 1,
                            },
                        }
                    ]
                },
                0,
                "ok",
            ),
        )
    rows = list(rt.retirement_proposals.values())
    assert [r["status"] for r in rows] == ["passed", "passed"]
    rt.assemblies["eval-a"].spec = replace(rt.assemblies["eval-a"].spec, version=2)
    _boundary(rt)
    assert rows[0]["status"] == "stale"
    assert rows[1]["status"] == "activated"
    assert "eval-a" not in rt.retired_assemblies
    assert "eval-b" in rt.retired_assemblies
    assert rt.cadence.world_block(rt.tick_clock.interval_ns)["waiting"] == []
