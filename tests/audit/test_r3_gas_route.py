"""Round three: the exit route is self-serve, bounded, disclosed and replayed.

The treasury chooses the Base mint from its own observed gas position, ledgers the
choice in ``treasury.gas_route`` before signing, caps forwarding fees per reserve
window, publishes the position in the pots view every request reads, and replays
all of it on resume. Every case is offline with a recorded rail.
"""

from copy import deepcopy

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.treasury_cli import AcceptanceSession
from factorylab.world.evm import Pending, RailError
from factorylab.world.treasury import Treasury

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
