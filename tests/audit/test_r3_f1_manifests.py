"""Round three, group F1: the world about to launch must be launchable from its own file.

T4 (seats 5.1, 6.1, 6.2) and the cadence decision. Nothing here touches a network:
``SPOT_META`` is a recording of Hyperliquid testnet's own spot metadata for the
tokens these manifests name, taken on 12 September 2026, and the pair set is
derived from it exactly as ``HyperliquidExchange._configure_spot`` derives it.
"""


import pytest

from factorylab.runtime.cli import _origin, refuse
from factorylab.runtime.reasons import Reason
from factorylab.runtime.worlds import load_manifest

PLACEHOLDER = "0x1111111111111111111111111111111111111111"

#: Hyperliquid testnet spot metadata, recorded. ETH/USDC is absent from testnet
#: (its token is UETH); BTC/USDC exists with szDecimals 0 at a mid near $6,100,
#: so one lot costs more than a rehearsal's whole spot balance.
SPOT_META = {
    "tokens": [
        {"index": 0, "name": "USDC", "szDecimals": 8},
        {"index": 1, "name": "PURR", "szDecimals": 0},
        {"index": 2, "name": "BTC", "szDecimals": 0},
        {"index": 3, "name": "HYPE", "szDecimals": 2},
        {"index": 4, "name": "UETH", "szDecimals": 4},
    ],
    "universe": [
        {"name": "PURR/USDC", "tokens": [1, 0]},
        {"name": "@50", "tokens": [2, 0]},
        {"name": "@1035", "tokens": [3, 0]},
        {"name": "@1137", "tokens": [4, 0]},
    ],
}


def _available_pairs(meta: dict) -> set[str]:
    tokens = {t["index"]: t for t in meta["tokens"]}
    return {f'{tokens[row["tokens"][0]]["name"]}/{tokens[row["tokens"][1]]["name"]}'
            for row in meta["universe"]}


@pytest.mark.parametrize("world", ["testnet"])
def test_every_seeded_spot_pair_exists_on_the_network_the_world_runs_on(world):
    """``_configure_spot`` raises on a pair the venue has never heard of, from a
    constructor that runs before genesis, so a manifest naming one cannot launch."""
    manifest = load_manifest(world)
    assert manifest.exchange.spot_pairs, "a world that advertises spot must name a pair"
    missing = set(manifest.exchange.spot_pairs) - _available_pairs(SPOT_META)
    assert not missing, f"{world} names spot pairs testnet does not have: {sorted(missing)}"


def test_the_testnet_reserve_address_is_not_the_rehearsal_placeholder():
    """``LiveRail.__init__`` refuses whenever ``reserve.key`` is present and the
    address does not derive from it, and the CLI loads that key unconditionally."""
    address = load_manifest("testnet").treasury.reserve_address
    assert address is not None and address.lower() != PLACEHOLDER


def test_the_testnet_reserve_address_is_a_real_checksummed_address():
    """Only the CLI may load a key file; the test checks the public address alone:
    a 20-byte hex address, EIP-55 checksummed, and not the rehearsal placeholder."""
    from eth_utils import is_checksum_address

    address = load_manifest("testnet").treasury.reserve_address
    assert address is not None and len(address) == 42 and address.startswith("0x")
    assert is_checksum_address(address)
    assert address.lower() != PLACEHOLDER


def test_testnet_governance_can_act_every_six_hours_of_world_time():
    """Ten-minute tick, sixty-event consequence backstop, six world events per tick:
    ten ticks (100 minutes) for a trade to be judged, and ``min_ratio`` of those,
    five hours, between charter activations (docs/launch-decisions.md)."""
    manifest = load_manifest("testnet")
    assert manifest.tick_interval_ns == 600 * 1_000_000_000
    assert manifest.evaluation.consequence_backstop_events == 60
    backstop_s = 60 // 6 * manifest.tick_interval_ns / 1_000_000_000
    assert backstop_s == 6000
    assert manifest.timing.min_ratio * backstop_s == 18000

    # The funded draft seeds no spot pair: the population registers pairs itself.
    assert load_manifest("edition1-example").exchange.spot_pairs == ()


def test_a_launch_refusal_names_the_subsystem_that_refused(capsys):
    """``adapter_unavailable`` alone cost two seats a source read each. The class
    and the module are this repository's own words; no message is printed."""
    try:
        load_manifest("no-such-world-for-this-test")
    except Exception as exc:  # noqa: BLE001 - the refusal path is what is under test
        caught = exc
    else:
        raise AssertionError("loading an absent manifest must raise")
    assert _origin(caught).startswith(f"{type(caught).__name__} in factorylab.")
    refuse("run", Reason.ADAPTER_UNAVAILABLE, caught)
    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "factorylab run: adapter_unavailable"
    assert lines[1].startswith("factorylab run: raised ")
    assert "factorylab." in lines[1]
