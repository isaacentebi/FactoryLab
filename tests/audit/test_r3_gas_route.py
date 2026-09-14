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
            "view_fails": False, "stalled": False, "head": 100, "scans": []}

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
            cursor = ((state.get("pending") or {}).get("reference") or {}).get("scanned_to")
            self.external["scans"].append(77 if cursor is None else cursor + 1)
            raise Pending("awaiting the Circle forwarder's Base mint",
                          carry={"scanned_to": self.external["head"]})
        return {"tx_hash": "0xforwarder", "forwarded": True, "chain_key": "base",
                "cctp_fee_micro": 200_000, "fee_ceiling_micro": 200_000}

    def send(self, step, reference):
        self.external["sends"].append((step, deepcopy(reference)))

    def poll(self, step, state):
        if step == "withdraw_burn":
            if self.external["stalled"]:
                raise Pending("RPC call rejected or unavailable")
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
    assert spec.max_forward_fee_micro == 300_000  # $0.10 of headroom over the $0.20 quote
    assert spec.max_forward_fees_per_window == 1_000_000
    explicit = manifest_from_dict({**manifest_base(), "treasury": {
        "cctp_forwarding": "on_empty_gas", "max_forward_fee_usd": "0.30",
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
        assert (loaded.cctp_forwarding, loaded.max_forward_fee_micro) == ("on_empty_gas", 300_000)


def test_r3_c_the_forwarded_ceiling_is_inside_the_total_fee_validation():
    # withdrawal $1 + CCTP cap $0.10 + forwarding cap $0.30 must fit the transfer fee cap,
    # or a forwarded exit's mint step would be refused after the principal burned.
    for fee in ("1.39", "1"):
        with pytest.raises(ValueError, match="forwarding"):
            manifest_from_dict({**manifest_base(), "treasury": {"max_transfer_fee_usd": fee}})
    manifest_from_dict({**manifest_base(), "treasury": {"max_transfer_fee_usd": "1.40"}})
    with pytest.raises(ValueError, match="forwarding"):
        manifest_from_dict({**manifest_base(), "treasury": {"max_forward_fee_usd": "0.91"}})
    manifest_from_dict({**manifest_base(), "treasury": {"max_forward_fee_usd": "0.90"}})


def test_r3_forward_wait_windows_is_bounded_validated_and_hash_neutral_by_default():
    default = manifest_from_dict(manifest_base())
    assert default.treasury.forward_wait_windows == 2
    assert "forward_wait_windows" not in json.loads(default.canonical_json())["treasury"]
    explicit = manifest_from_dict({**manifest_base(), "treasury": {"forward_wait_windows": 2}})
    assert explicit.manifest_hash() == default.manifest_hash()
    chosen = manifest_from_dict({**manifest_base(), "treasury": {"forward_wait_windows": 5}})
    assert chosen.treasury.forward_wait_windows == 5
    assert chosen.manifest_hash() != default.manifest_hash()
    assert json.loads(chosen.canonical_json())["treasury"]["forward_wait_windows"] == 5
    for bad in (0, -1, True, 1.5, "2"):
        with pytest.raises(ValueError, match="treasury.forward_wait_windows"):
            manifest_from_dict({**manifest_base(), "treasury": {"forward_wait_windows": bad}})
    ledger = Ledger(clock_ns=lambda: 0)
    with pytest.raises(ValueError, match="forward_wait_windows"):
        Treasury(ledger, Wallet(100_000_000, ledger, clock_ns=lambda: 0), GasRail(),
                 forward_wait_windows=0)


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


def test_r3_b_the_gas_block_stays_fresh_while_a_transfer_is_pending():
    treasury, rail, wallet, records = setup()
    treasury.refresh_pots()
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    written = len(records)
    view = treasury.refresh_pots()
    # Money pots stay the cached observation, labelled incomplete; the gas block is
    # re-read and names the transfer in flight as the blocker of the next exit.
    assert view["pending"] and not view["complete"] and view["venue"] == 100_000_000
    assert view["gas"]["refill_ready"] is False
    assert view["gas"]["blocked_by"] == "a previous transfer is still pending or stranded"
    assert view["gas"]["route"] == "forwarded" and view["gas"]["forward_fee_micro"] == 200_000
    assert records[-1]["kind"] == "treasury.pots" and records[-1]["pending"] is True
    assert records[-1]["pots"]["gas"] == view["gas"] and len(records) == written + 1
    rail.external["view_fails"] = True
    assert treasury.refresh_pots()["gas"] == {
        "refill_ready": False, "blocked_by": "gas position unavailable"}
    rail.external["view_fails"] = False
    treasury.tick(2)  # the burn confirms; the mint waits on the forwarder
    view = treasury.refresh_pots()
    assert view["pending_reason"] == "awaiting the Circle forwarder's Base mint"
    assert view["pending_since"] == 2 and wallet.pots()["gas"] == view["gas"]
    assert view["gas"]["blocked_by"] == "a previous transfer is still pending or stranded"
    rail.external["minted"] = True
    treasury.tick(3)
    treasury.tick(4)
    view = treasury.refresh_pots()
    assert not view["pending"] and view["complete"] and view["gas"]["refill_ready"] is True
    assert view["gas"]["blocked_by"] is None and view["pending_reason"] is None


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


STRAND = "forwarded mint not delivered within treasury.forward_wait_windows"


def test_r3_a_forward_never_delivered_strands_after_the_bound_stays_recoverable_and_unblocks():
    treasury, rail, wallet, records = setup(forward_wait_windows=2)
    treasury.open_window(1)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)  # the burn confirms; the forwarder has not minted
    assert treasury.state["index"] == 1 and treasury.state["pending"]["since_window"] == 1
    treasury.open_window(2)
    for now_ns in range(3, 8):
        treasury.tick(now_ns)
    # One window boundary is not the bound: the wait goes on and the slot stays taken.
    assert treasury.state["status"] == "submitted"
    assert treasury.transfer("to_reserve", "10", handle="b", now_ns=8) == {
        "status": "refused", "error": "a previous transfer is still pending or stranded"}
    treasury.open_window(3)
    treasury.tick(9)
    assert treasury.state["status"] == "stranded" and treasury.state["recoverable"] is True
    assert "pending" not in treasury.state
    failed = next(i for i in records if i["kind"] == "treasury.failed")
    assert failed["stranded_micro"] == 9_000_000
    assert failed["reason"] == STRAND and failed["state"]["recoverable"] is True
    assert failed["waited"] == {"step": "mint_base", "phase": "prepare", "attempts": 7,
                                "reason": "awaiting the Circle forwarder's Base mint",
                                "since_ns": 2, "since_window": 1,
                                "reference": {"scanned_to": 100}}
    # The burned principal is still held and public; the money pots observe again.
    assert wallet.balance == 99_000_000 and wallet.available == 89_000_000
    view = treasury.refresh_pots()
    assert not view["pending"] and view["complete"] and wallet.pots() == view
    assert records[-1]["kind"] == "treasury.pots" and "pending" not in records[-1]
    assert view["stranded"] == [{"transfer_id": "treasury-0", "stranded_micro": 9_000_000,
                                 "reason": STRAND, "since_ns": 9}]  # stranded since the bound
    # The strand does not block a transfer on the new window, which completes first.
    rail.external["minted"] = True
    assert treasury.transfer("to_reserve", "10", handle="c", now_ns=10)["status"] == "submitted"
    assert treasury.state["id"] == "treasury-1"
    treasury.tick(11)  # c's burn confirms and its forwarded mint is observed at once
    assert treasury.tick(12)[0]["transfer_id"] == "treasury-1"
    assert not any(i["kind"] == "treasury.recovered" for i in records)  # never mid-flight
    # A later delivery is still recognised: the free slot re-checks the strand, which confirms.
    assert treasury.tick(13) == []
    recovered = next(i for i in records if i["kind"] == "treasury.recovered")
    assert recovered["state"]["id"] == "treasury-0" and recovered["ts"] == 13
    assert recovered["state"]["status"] == "submitted" and "pending" not in recovered["state"]
    assert treasury.state["id"] == "treasury-0" and treasury.state["reference"]["forwarded"]
    assert treasury.stranded == [] and wallet.pots()["stranded"] == []
    result = treasury.tick(14)
    assert result[0]["status"] == "confirmed" and result[0]["transfer_id"] == "treasury-0"
    assert result[0]["received_micro"] == 8_800_000
    assert wallet.balance == wallet.available == 97_600_000 and wallet.check_conservation()
    assert [i["state"]["id"] for i in records if i["kind"] == "treasury.confirmed"] == [
        "treasury-1", "treasury-0"]
    assert len([r for s, r in rail.external["sends"] if s == "withdraw_burn"]) == 2


def test_r3_a_parked_strand_is_checkpointed_with_its_hold_and_old_checkpoints_restore():
    treasury, rail, wallet, records = setup(forward_wait_windows=1)
    treasury.open_window(1)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    treasury.open_window(2)
    treasury.tick(3)
    assert treasury.state["status"] == "stranded" and treasury.state["recoverable"]
    saved = treasury.snapshot()
    assert saved["principal_hold_id"] is None and saved["fee_hold_id"] is None
    assert saved["stranded"][0]["state"] == treasury.state
    hold = saved["stranded"][0]["principal_hold_id"]
    assert isinstance(hold, str)
    restored, restored_rail, restored_wallet, _ = setup(forward_wait_windows=1)
    restored_wallet._restore_state(wallet.state())
    restored.restore(saved)
    assert restored.stranded[0]["principal_hold"].id == hold
    assert restored.pots()["stranded"] == treasury.pots()["stranded"]
    assert restored.transfer("to_reserve", "10", handle="b", now_ns=4)["status"] == "submitted"
    restored_rail.external["minted"] = True
    for now_ns in (5, 6, 7):
        restored.tick(now_ns)
    assert restored.tick(8)[0]["transfer_id"] == "treasury-0"
    assert restored.stranded == [] and restored_wallet.check_conservation()
    assert restored_wallet.balance == restored_wallet.available == 97_600_000
    del saved["stranded"]  # checkpoints predate parked strands
    older, _, older_wallet, _ = setup()
    older_wallet._restore_state(wallet.state())
    older.restore(saved)
    assert older.stranded == [] and older.pots()["stranded"] == []


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
    assert mechanics["max_forward_fee_micro"] == 300_000
    assert mechanics["forward_wait_windows"] == 2
    route = mechanics["exit_route"]
    assert "HYPE" in route and "HYPE/USDC" in route and "forward" in route
    assert "pots.gas" in route
    assert "to_venue" in mechanics["return_route"] and "ETH" in mechanics["return_route"]
    spec = rt.tool_specs["treasury.transfer"]["description"]
    assert "HYPE" in spec and "gas" in spec


def test_r3_a_stalled_poll_replays_its_ledgered_reason_and_keeps_counting(tmp_path):
    path = tmp_path / "acceptance.jsonl"
    rail = GasRail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000_000)
    rail.external["stalled"] = True
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == "treasury.pending":
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    with pytest.raises(Crash):
        first.execute({"kind": "advance"}, 2_000_000_000)
    second = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    items = second.journal.ledger._recovery_items()
    stalls = [i for i in items if i.get("kind") == "treasury.pending"]
    assert len(stalls) == 1 and stalls[0]["attempts"] == 1
    assert stalls[0]["reason"] == "RPC call rejected or unavailable"
    assert stalls[0]["step"] == "withdraw_burn" and stalls[0]["since_ns"] == 2_000_000_000
    status = second.status()
    assert status["status"] == "submitted"
    assert status["pots"]["pending_reason"] == "RPC call rejected or unavailable"
    assert status["pots"]["pending_since"] == 2_000_000_000
    for n in range(2, 12):
        second.execute({"kind": "advance"}, (n + 1) * 1_000_000_000)
    third = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    assert third.treasury.state["pending"]["attempts"] == 11
    for n in range(12, 21):
        third.execute({"kind": "advance"}, (n + 1) * 1_000_000_000)
    stalls = [i for i in third.journal.ledger._recovery_items()
              if i.get("kind") == "treasury.pending"]
    assert [i["attempts"] for i in stalls] == [1, 10, 20]
    assert len(rail.external["sends"]) == 1  # the stall never re-signed the withdrawal
    rail.external["stalled"] = False
    third.execute({"kind": "advance"}, 22_000_000_000)
    # The burn confirms and the poll stall is over; the mint step begins its own wait.
    assert third.treasury.state["index"] == 1
    assert third.treasury.state["pending"] == {
        "step": "mint_base", "phase": "prepare", "attempts": 1, "since_ns": 22_000_000_000,
        "since_window": 0, "reason": "awaiting the Circle forwarder's Base mint",
        "reference": {"scanned_to": 100}}
    assert third.status()["pots"]["pending_since"] == 22_000_000_000
    rail.external["minted"] = True
    third.execute({"kind": "advance"}, 23_000_000_000)
    assert "pending" not in third.treasury.state
    assert third.status()["pots"]["pending_reason"] is None
    result = third.execute({"kind": "advance"}, 24_000_000_000)
    assert result["status"] == "confirmed" and third.wallet.check_conservation()


def test_r3_a_waiting_forward_resumes_from_the_journaled_scan_cursor(tmp_path):
    path = tmp_path / "acceptance.jsonl"
    rail = GasRail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000_000)
    first.execute({"kind": "advance"}, 2_000_000_000)  # the burn confirms; the mint waits
    assert rail.external["scans"] == [77]
    assert first.treasury.state["pending"]["reference"] == {"scanned_to": 100}
    rail.external["head"] = 400
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == "io.result" and item.get("error") == "Pending":
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    with pytest.raises(Crash):
        first.execute({"kind": "advance"}, 3_000_000_000)
    assert rail.external["scans"] == [77, 101]
    second = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    # The replayed wait is served from the journal: no scan, and the cursor it carried.
    assert rail.external["scans"] == [77, 101]
    assert second.treasury.state["pending"]["reference"] == {"scanned_to": 400}
    assert second.treasury.state["pending"]["attempts"] == 2
    rail.external["head"] = 700
    second.execute({"kind": "advance"}, 4_000_000_000)
    assert rail.external["scans"] == [77, 101, 401]
    third = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    assert third.treasury.state["pending"]["reference"] == {"scanned_to": 700}
    rail.external["minted"] = True
    third.execute({"kind": "advance"}, 5_000_000_000)
    assert third.treasury.state["reference"]["tx_hash"] == "0xforwarder"
    assert "pending" not in third.treasury.state
    result = third.execute({"kind": "advance"}, 6_000_000_000)
    assert result["status"] == "confirmed" and third.wallet.check_conservation()
