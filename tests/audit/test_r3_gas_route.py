"""Round three: the exit route is self-serve, bounded, disclosed and replayed.

The treasury chooses the Base mint from its own observed gas position, ledgers the
choice in ``treasury.gas_route`` before signing, caps forwarding fees per reserve
window, publishes the position in the pots view every request reads, and replays
all of it on resume. Every case is offline with a recorded rail.
"""

import json
from copy import deepcopy

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.treasury_cli import AcceptanceSession
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.world.evm import Pending, RailError
from factorylab.world.treasury import FakeTreasury, Treasury
from tests.runtime.test_fidelity import runtime

ROUTE = {"forward": True, "reason": "no_base_eth", "mode": "on_empty_gas",
         "base_eth_wei": 0, "base_gas_remaining_wei": 10**15, "base_mint_estimate_wei": 4 * 10**13,
         "core_hype_wei": 10**18, "core_hype_required_wei": 4 * 10**13,
         "forward_fee_micro": 200_000, "cctp_max_fee_micro": 200_000,
         "quote_source": "CoreDepositWallet.calculateCrossChainWithdrawalFee"}


class Crash(BaseException):
    pass


class GasRail:
    """A recorded forward-on-empty rail: the withdrawal burns, the forwarder mints later."""

    name = "recorded-gas-rail"

    def __init__(self, external=None):
        self.external = external if external is not None else {
            "sends": [], "minted": False, "forward": True, "unavailable": False,
            "view_fails": False}

    def balances(self):
        return {"venue": 100_000_000, "reserve": 0}

    def gas_view(self, gas_spent):
        if self.external["view_fails"]:
            raise Pending("RPC transport failed; reference remains pending")
        return {"route": "forwarded", "refill_ready": True, "blocked_by": None,
                "forward_fee_micro": 200_000, "base_eth_wei": 0, "core_hype": "1",
                "base_gas_remaining_wei": 10**15 - gas_spent.get("base", 0)}

    def preflight(self, direction, amount, gas_spent):
        if self.external["unavailable"]:
            raise RailError("CoreDepositWallet cannot currently forward the destination mint")

    def plan(self, direction):
        return ("withdraw_burn", "mint_base")

    def prepare(self, step, state, spent):
        if step == "withdraw_burn":
            forward = self.external["forward"]
            route = {**ROUTE, "forward": forward,
                     "reason": "no_base_eth" if forward else "base_eth_available",
                     "cctp_max_fee_micro": 200_000 if forward else 0}
            return {"tx_hash": "0xburn" + str(state["nonce"]), "forward": forward,
                    "fee_ceiling_micro": 1_000_000, "gas_route": route}
        if not self.external["minted"]:
            raise Pending("awaiting the Circle forwarder's Base mint")
        return {"tx_hash": "0xforwarder", "forwarded": True, "chain_key": "base",
                "cctp_fee_micro": 200_000, "fee_ceiling_micro": 200_000}

    def send(self, step, reference):
        self.external["sends"].append((step, deepcopy(reference)))

    def poll(self, step, state):
        if step == "withdraw_burn":
            if ("withdraw_burn", state["reference"]) not in self.external["sends"]:
                return None  # the venue has no row for a withdrawal that never reached it
            return {"confirmed": True, "received_micro": 9_000_000, "fee_micro": 1_000_000,
                    "wallet_fee_micro": 1_000_000, "principal_moved": True,
                    "route_data": {"burn": {"tx_hash": "0xsystem", "forwarded": True}},
                    "evidence": state["reference"]}
        return {"confirmed": True, "received_micro": 8_800_000, "fee_micro": 200_000,
                "wallet_fee_micro": 200_000, "principal_moved": True, "chain_key": "base",
                "evidence": {"tx_hash": "0xforwarder", "forwarded": True}}


def setup(**kwargs):
    ledger = Ledger(clock_ns=lambda: 0)
    records = []
    append = ledger.append

    def record(item):
        records.append(deepcopy(item))
        return append(item)

    ledger.append = record
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    rail = GasRail()
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000, **kwargs)
    wallet.bind_pots(treasury.pots)
    return treasury, rail, wallet, records


def manifest_base() -> dict:
    return {"name": "x", "initial_balance_usd": "10",
            "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
            "assemblies": [{"id": "a", "model_id": "m"}],
            "novelty": {"share": 0.1, "window": "1h"}}


def test_r3_gas_route_manifest_keys_default_validate_and_preserve_world_identity():
    default = manifest_from_dict(manifest_base())
    spec = default.treasury
    assert spec.cctp_forwarding == "on_empty_gas"
    assert spec.max_forward_fee_micro == 200_000
    assert spec.max_forward_fees_per_window == 1_000_000
    explicit = manifest_from_dict({**manifest_base(), "treasury": {
        "cctp_forwarding": "on_empty_gas", "max_forward_fee_usd": "0.20",
        "max_forward_fees_per_window": "1"}})
    assert explicit.manifest_hash() == default.manifest_hash()
    assert "cctp_forwarding" not in json.loads(default.canonical_json())["treasury"]
    chosen = manifest_from_dict({**manifest_base(), "treasury": {
        "cctp_forwarding": "always", "max_forward_fee_usd": "0.25",
        "max_forward_fees_per_window": 2}})
    assert chosen.treasury.cctp_forwarding == "always"
    assert chosen.treasury.max_forward_fee_micro == 250_000
    assert chosen.treasury.max_forward_fees_per_window == 2_000_000
    assert chosen.manifest_hash() != default.manifest_hash()
    assert json.loads(chosen.canonical_json())["treasury"]["cctp_forwarding"] == "always"
    for bad in ({"cctp_forwarding": "sometimes"}, {"cctp_forwarding": 1},
                {"max_forward_fee_usd": "-0.01"}, {"max_forward_fees_per_window": 1.5}):
        with pytest.raises(ValueError, match="treasury"):
            manifest_from_dict({**manifest_base(), "treasury": bad})
    with pytest.raises(ValueError):  # exact micro-USD only, as for every treasury amount
        manifest_from_dict({**manifest_base(),
                            "treasury": {"max_forward_fees_per_window": "0.0000001"}})
    for world in ("scripted", "testnet"):
        loaded = load_manifest(world).treasury
        assert (loaded.cctp_forwarding, loaded.max_forward_fee_micro) == ("on_empty_gas", 200_000)


def test_r3_pots_view_carries_the_gas_block_the_population_reads():
    treasury, rail, wallet, records = setup()
    view = treasury.refresh_pots()
    assert view["gas"]["route"] == "forwarded" and view["gas"]["refill_ready"] is True
    assert view["gas"]["base_gas_remaining_wei"] == 10**15
    assert wallet.pots()["gas"] == view["gas"]
    assert records[-1]["kind"] == "treasury.pots" and records[-1]["pots"]["gas"] == view["gas"]
    assert view["total_micro"] == 100_000_000  # the gas position is never counted as money
    rail.external["view_fails"] = True
    assert treasury.refresh_pots()["gas"] == {
        "refill_ready": False, "blocked_by": "gas position unavailable"}
    ledger = Ledger(clock_ns=lambda: 0)
    fake = FakeTreasury(ledger, Wallet(100_000_000, ledger, clock_ns=lambda: 0))
    assert "gas" not in fake.refresh_pots() and "gas" not in fake.pots()


def test_r3_gas_route_is_ledgered_before_submission_and_forward_fees_are_window_capped():
    treasury, rail, wallet, records = setup(max_forward_fees_per_window=300_000)
    treasury.open_window(1)
    assert records[-1]["kind"] == "treasury.venice_window"
    assert records[-1]["forward_fees_micro"] == 0 and records[-1]["spent_micro"] == 0
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result["status"] == "submitted"
    kinds = [i["kind"] for i in records]
    route = next(i for i in records if i["kind"] == "treasury.gas_route")
    assert kinds.index("treasury.gas_route") < kinds.index("treasury.submitted")
    assert route == {"kind": "treasury.gas_route", "transfer_id": "treasury-0",
                     "direction": "to_reserve", **ROUTE}
    assert treasury.forward_spent == 200_000
    treasury.tick(2)  # the burn confirms; the forwarder has not minted yet
    assert treasury.state["index"] == 1 and treasury.state["reference"] is None
    treasury.tick(3)
    rail.external["minted"] = True
    treasury.tick(4)
    assert treasury.tick(5)[0]["status"] == "confirmed"
    assert treasury.state["fees_micro"] == 1_200_000
    assert wallet.balance == 98_800_000 and wallet.check_conservation()
    assert treasury.gas_spent == {} and "treasury.gas" not in [i["kind"] for i in records]
    refused = treasury.transfer("to_reserve", "10", handle="b", now_ns=6)
    assert refused == {"status": "refused",
                       "error": "treasury.max_forward_fees_per_window exhausted"}
    assert sum(i["kind"] == "treasury.gas_route" for i in records) == 1
    rail.external["forward"] = False
    assert treasury.transfer("to_reserve", "10", handle="c", now_ns=7)["status"] == "submitted"
    assert treasury.forward_spent == 200_000  # a self-mint pays no forwarding fee
    routes = [i for i in records if i["kind"] == "treasury.gas_route"]
    assert len(routes) == 2 and routes[1]["forward"] is False
    assert routes[1]["transfer_id"] == "treasury-1" and routes[1]["cctp_max_fee_micro"] == 0
    for tick in range(8, 12):
        treasury.tick(tick)
    rail.external["forward"] = True
    treasury.open_window(2)
    assert treasury.forward_spent == 0
    assert treasury.transfer("to_reserve", "10", handle="d", now_ns=12)["status"] == "submitted"


def test_r3_forward_unavailable_is_public_with_the_quote_and_moves_nothing():
    treasury, rail, wallet, records = setup()
    rail.external["unavailable"] = True
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result == {"status": "refused",
                      "error": "CoreDepositWallet cannot currently forward the destination mint"}
    assert records[-1] == {"kind": "treasury.refused", "direction": "to_reserve", "handle": "a",
                           "reason": result["error"]}
    assert not any(i["kind"] == "treasury.gas_route" for i in records)
    assert wallet.available == wallet.balance == 100_000_000 and rail.external["sends"] == []


def test_r3_snapshot_carries_the_forward_window_and_old_checkpoints_restore():
    treasury, rail, wallet, records = setup()
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    saved = treasury.snapshot()
    assert saved["forward_spent"] == 200_000
    restored, _, restored_wallet, _ = setup()
    restored_wallet._restore_state(wallet.state())
    restored.restore(saved)
    assert restored.forward_spent == 200_000
    del saved["forward_spent"]
    restored.restore(saved)
    assert restored.forward_spent == 0


CONFIG = {"name": "gas-acceptance", "max_transfer_fee_micro": 2_000_000,
          "max_forward_fees_per_window": 1_000_000}
TRANSFER = {"kind": "transfer", "direction": "to_reserve", "usd": "10"}


@pytest.mark.parametrize("cut", ["treasury.gas_route", "treasury.submitted",
                                "treasury.step_confirmed", "treasury.advance"])
def test_r3_resume_replays_the_gas_route_and_never_resigns_the_withdrawal(tmp_path, cut):
    path = tmp_path / "acceptance.jsonl"
    rail = GasRail()
    first = AcceptanceSession(path, rail, CONFIG)
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == cut:
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    with pytest.raises(Crash):
        first.execute(TRANSFER, 1_000_000_000)
        first.execute({"kind": "advance"}, 2_000_000_000)
    second = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    items = second.journal.ledger._recovery_items()
    routes = [i for i in items if i.get("kind") == "treasury.gas_route"]
    assert len(routes) == 1 and routes[0]["transfer_id"] == "treasury-0"
    assert routes[0]["forward"] is True and routes[0]["forward_fee_micro"] == 200_000
    assert second.treasury.forward_spent == 200_000
    assert second.status()["status"] == "submitted"
    # One retry interval passes: a lost submission is re-sent with the same reference,
    # the burn confirms, and the mint step waits for the forwarder.
    for now_ns in (100_000_000_000, 200_000_000_000):
        second.execute({"kind": "advance"}, now_ns)
    withdrawals = [ref for step, ref in rail.external["sends"] if step == "withdraw_burn"]
    assert len(withdrawals) == 1
    assert second.treasury.state["index"] == 1
    assert second.treasury.state["reference"] is None  # the forwarder has not minted
    rail.external["minted"] = True
    second.execute({"kind": "advance"}, 300_000_000_000)
    third = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    result = third.execute({"kind": "advance"}, 400_000_000_000)
    assert result["status"] == "confirmed"
    assert third.treasury.state["fees_micro"] == 1_200_000
    assert third.wallet.balance == third.wallet.available == 98_800_000
    assert third.wallet.check_conservation()
    assert len([ref for step, ref in rail.external["sends"] if step == "withdraw_burn"]) == 1
    assert sum(i.get("kind") == "treasury.gas_route"
               for i in third.journal.ledger._recovery_items()) == 1


def test_r3_mechanics_disclose_the_hype_and_forwarding_rule_of_the_exit_route():
    rt = runtime()
    rt._derive_regions()
    mechanics = rt._world_block()["mechanics"]["treasury"]
    assert mechanics["max_forward_fees_per_window_micro"] == 1_000_000
    assert mechanics["max_forward_fee_micro"] == 200_000
    route = mechanics["exit_route"]
    assert "HYPE" in route and "HYPE/USDC" in route and "forward" in route
    assert "pots.gas" in route
    assert "to_venue" in mechanics["return_route"] and "ETH" in mechanics["return_route"]
    spec = rt.tool_specs["treasury.transfer"]["description"]
    assert "HYPE" in spec and "gas" in spec
