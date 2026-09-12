from copy import deepcopy

import pytest

from factorylab.runtime.treasury_cli import AcceptanceSession
from factorylab.world.evm import RailError


class Crash(BaseException):
    pass


class Rail:
    name = "test-receipted-rail"

    def __init__(self, external=None):
        self.external = external if external is not None else {"moves": [], "sends": []}
        self.crash_after_send = False

    def balances(self):
        return {"venue": 90_000_000 if self.external["moves"] else 100_000_000,
                "reserve": 9_990_000 if self.external["moves"] else 0}

    def preflight(self, *args):
        pass

    def plan(self, direction):
        return ("settle",)

    def prepare(self, step, state, spent):
        return {"tx_hash": "0xnonce" + str(state["nonce"]), "fee_ceiling_micro": 10_000}

    def send(self, step, reference):
        self.external["sends"].append(deepcopy(reference))
        if reference in self.external["moves"]:
            raise RailError("venue rejected withdrawal")  # a nonce-already-used response
        self.external["moves"].append(deepcopy(reference))
        if self.crash_after_send:
            raise Crash()

    def poll(self, step, state):
        return {"confirmed": True, "received_micro": 9_990_000, "fee_micro": 10_000,
                "principal_moved": True, "evidence": state["reference"]}


CONFIG = {"name": "fake-acceptance", "max_transfer_fee_micro": 30_000}
TRANSFER = {"kind": "transfer", "direction": "to_reserve", "usd": "10"}


def test_pending_checkpoint_restores_actual_holds_and_confirms_once(tmp_path):
    path = tmp_path / "acceptance.jsonl"
    rail = Rail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000)
    before = first.wallet.available
    second = AcceptanceSession(path, Rail(rail.external), CONFIG)
    assert second.wallet.available == before == 89_970_000
    assert len(rail.external["sends"]) == 1  # restore alone did not broadcast
    result = second.execute({"kind": "advance"}, 2_000_000)
    assert result["status"] == "confirmed"
    assert second.wallet.balance == second.wallet.available == 99_990_000
    third = AcceptanceSession(path, Rail(rail.external), CONFIG)
    third.execute({"kind": "advance"}, 3_000_000)
    assert len(rail.external["moves"]) == 1
    assert third.wallet.balance == 99_990_000
    assert third.wallet.pots()["reserve"] == 9_990_000


@pytest.mark.parametrize("kind", ["treasury.step_confirmed", "wallet.commit",
                                 "treasury.confirmed", "wallet.release"])
def test_crash_during_confirmation_replays_bookkeeping_once(tmp_path, kind):
    path = tmp_path / "acceptance.jsonl"
    rail = Rail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000)
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == kind:
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    with pytest.raises(Crash):
        first.execute({"kind": "advance"}, 2_000_000)
    second = AcceptanceSession(path, Rail(rail.external), CONFIG)
    assert second.status()["status"] == "confirmed"
    assert second.wallet.available == second.wallet.balance == 99_990_000
    assert second.wallet.check_conservation()
    assert len(rail.external["moves"]) == len(rail.external["sends"]) == 1


def test_lost_acknowledgement_retries_same_reference_and_cannot_release_principal(tmp_path):
    path = tmp_path / "acceptance.jsonl"
    rail = Rail()
    rail.crash_after_send = True
    first = AcceptanceSession(path, rail, CONFIG)
    with pytest.raises(Crash):
        first.execute(TRANSFER, 1_000_000)
    assert len(rail.external["moves"]) == 1
    second = AcceptanceSession(path, Rail(rail.external), CONFIG)
    assert len(rail.external["sends"]) == 2
    assert rail.external["sends"][0] == rail.external["sends"][1]
    assert second.status()["status"] == "submitted"
    assert second.wallet.available == 89_970_000
    second.execute({"kind": "advance"}, 2_000_000)
    assert second.wallet.balance == second.wallet.available == 99_990_000
    assert len(rail.external["moves"]) == 1


def test_different_route_cannot_restore_an_existing_transfer(tmp_path):
    path = tmp_path / "acceptance.jsonl"
    first = AcceptanceSession(path, Rail(), CONFIG)
    first.execute(TRANSFER, 1_000_000)
    changed = Rail()
    changed.name = "another-chain"
    with pytest.raises(RailError, match="differs"):
        AcceptanceSession(path, changed, CONFIG)
    assert changed.external["sends"] == []


@pytest.mark.parametrize("kind", ["treasury.step_confirmed", "wallet.release"])
def test_crash_while_booking_native_gas_does_not_charge_usdc(tmp_path, kind):
    class GasRail(Rail):
        def balances(self):
            return {"venue": 90_000_000 if self.external["moves"] else 100_000_000,
                    "reserve": 10_000_000 if self.external["moves"] else 0}

        def poll(self, step, state):
            return {**super().poll(step, state), "wallet_fee_micro": 0,
                    "received_micro": 10_000_000, "gas_fee_wei": 123, "chain_key": "hyper"}

    path = tmp_path / "acceptance.jsonl"
    rail = GasRail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000)
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == kind:
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    with pytest.raises(Crash):
        first.execute({"kind": "advance"}, 2_000_000)
    second = AcceptanceSession(path, GasRail(rail.external), CONFIG)
    assert second.wallet.balance == second.wallet.available == 100_000_000
    assert second.wallet.pots()["total_micro"] == 100_000_000
    assert second.treasury.state["fees_micro"] == 10_000
    assert second.treasury.gas_spent == {"hyper": 123}
    assert len(rail.external["moves"]) == 1


@pytest.mark.parametrize("kind", ["treasury.step_confirmed", "treasury.advance",
                                 "treasury.step_submitted", "burn.sent"])
def test_crash_between_provisional_approval_and_burn_preserves_pair_and_gas(tmp_path, kind):
    class PairedRail(Rail):
        def balances(self):
            return {"venue": 100_000_000, "reserve": 0}

        def plan(self, direction):
            return ("approve", "burn")

        def prepare(self, step, state, spent):
            if step == "approve":
                return {"tx_hash": "0xapproval", "nonce": 7, "fee_ceiling_micro": 0}
            return deepcopy(state["route_data"]["prepared_burn"])

        def send(self, step, reference):
            self.external["sends"].append(deepcopy(reference))
            if step == "burn":
                if reference not in self.external["moves"]:
                    self.external["moves"].append(deepcopy(reference))
                if self.crash_after_send:
                    raise Crash()

        def poll(self, step, state):
            if step == "approve":
                return {"confirmed": True, "received_micro": 10_000_000,
                        "fee_micro": 0, "wallet_fee_micro": 0, "principal_moved": False,
                        "evidence": {"confirmation": "provisional"},
                        "route_data": {"prepared_burn": {
                            "tx_hash": "0xburn", "nonce": 8, "fee_ceiling_micro": 10_000,
                            "pending_approval": deepcopy(state["reference"]),
                        }}}
            if state["reference"] not in self.external["moves"]:
                return None
            return {"confirmed": True, "received_micro": 10_000_000,
                    "fee_micro": 10_000, "wallet_fee_micro": 0,
                    "gas_fee_wei": 300, "chain_key": "base", "principal_moved": True,
                    "evidence": {"confirmation": "approval and burn finalized"}}

    path = tmp_path / "acceptance.jsonl"
    rail = PairedRail()
    first = AcceptanceSession(path, rail, CONFIG)
    first.execute(TRANSFER, 1_000_000)
    append = first.journal.ledger.append

    def crash(item):
        seq = append(item)
        if item["kind"] == kind:
            raise Crash()
        return seq

    first.journal.ledger.append = crash
    rail.crash_after_send = kind == "burn.sent"
    with pytest.raises(Crash):
        first.execute({"kind": "advance"}, 2_000_000)
    second = AcceptanceSession(path, PairedRail(rail.external), CONFIG)
    assert second.treasury.state["reference"]["nonce"] == 8
    assert second.treasury.state["reference"]["pending_approval"]["nonce"] == 7
    assert second.wallet.available == 89_970_000
    assert second.treasury.gas_spent == {}
    second.execute({"kind": "advance"}, 100_000_000_000)
    second.execute({"kind": "advance"}, 101_000_000_000)
    assert second.status()["status"] == "confirmed"
    assert second.wallet.balance == second.wallet.available == 100_000_000
    assert second.treasury.gas_spent == {"base": 300}
    assert second.treasury.state["fees_micro"] == 10_000
    assert len(rail.external["moves"]) == 1
    assert second.wallet.check_conservation()
