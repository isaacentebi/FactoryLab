from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.runtime.venue import VenueMixin
from factorylab.world.evm import Pending
from factorylab.world.exchange import AccountState
from factorylab.world.treasury import (
    FakeRail,
    FakeTreasury,
    Treasury,
    UnconfiguredRail,
    provider_pots,
)


def setup():
    ledger = Ledger(clock_ns=lambda: 0)
    records = []
    append = ledger.append

    def record(item):
        records.append(deepcopy(item))
        return append(item)

    ledger.append = record
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    return ledger, wallet, records


def test_scripted_transfer_is_pending_until_next_tick_and_conserves_principal():
    ledger, wallet, records = setup()
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    before = wallet.pots()
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result["status"] == "submitted"
    assert wallet.balance == 100_000_000
    assert wallet.available == 89_990_000
    assert wallet.pots()["reserve"] == before["reserve"] == 0
    assert wallet.pots()["pending"] and not wallet.pots()["complete"]
    assert treasury.transfer("to_reserve", "10", handle="b", now_ns=2)["status"] == "refused"
    confirmed = treasury.tick(2)
    assert confirmed[0]["status"] == "confirmed"
    assert wallet.pots()["reserve"] == 9_990_000
    assert wallet.pots()["venue"] == 90_000_000
    assert wallet.balance == wallet.available == 99_990_000
    assert wallet.check_conservation()
    assert treasury.tick(3) == []
    assert wallet.balance == 99_990_000
    assert sum(i["kind"] == "treasury.confirmed" for i in records) == 1
    treasury.transfer("to_venue", "5", handle="c", now_ns=3)
    treasury.tick(4)
    assert wallet.pots()["reserve"] == 4_990_000
    assert wallet.pots()["venue"] == 94_990_000
    assert wallet.balance == 99_980_000


@pytest.mark.parametrize("usd", [True, 5.0, "NaN", "Infinity", "-1", "0", "0.0000001", "4", "101"])
def test_invalid_amounts_never_submit_or_reserve(usd):
    ledger, wallet, records = setup()
    treasury = FakeTreasury(ledger, wallet)
    assert treasury.transfer("to_reserve", usd, handle="a", now_ns=1)["status"] == "refused"
    assert wallet.available == wallet.balance == 100_000_000
    assert not any(i["kind"] == "treasury.submitted" for i in records)


def test_source_pot_shortage_cannot_be_hidden_by_other_pots():
    ledger, wallet, _ = setup()
    treasury = FakeTreasury(ledger, wallet)
    result = treasury.transfer("to_venue", "5", handle="a", now_ns=1)
    assert result["status"] == "refused" and "source pot" in result["error"]


class DelayedRail(FakeRail):
    def __init__(self, wallet, records):
        super().__init__(wallet)
        self.records, self.sent = records, []
        self.ready = False

    def send(self, step, reference):
        assert any(
            i["kind"] in {"treasury.submitted", "treasury.step_submitted"}
            and reference in i["tx_refs"]
            for i in self.records
        )
        self.sent.append(deepcopy(reference))
        raise Pending("simulated transport timeout")

    def poll(self, step, state):
        return super().poll(step, state) if self.ready else None


def test_timeout_holds_principal_and_reconciles_original_reference_once():
    ledger, wallet, records = setup()
    rail = DelayedRail(wallet, records)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=10_000)
    result = treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert result["status"] == "submitted"
    assert treasury.tick(2) == []
    assert wallet.available == 89_990_000
    original = rail.sent[0]
    rail.ready = True
    treasury.tick(3)
    assert treasury.state["reference"] == original
    assert wallet.balance == 99_990_000
    treasury.tick(4)
    assert wallet.balance == 99_990_000


def test_ledger_failure_prevents_broadcast_and_releases_unsubmitted_holds():
    ledger, wallet, records = setup()
    rail = DelayedRail(wallet, records)
    treasury = Treasury(ledger, wallet, rail)
    append = ledger.append

    def fail_submission(item):
        if item["kind"] == "treasury.submitted":
            raise OSError("disk full")
        return append(item)

    ledger.append = fail_submission
    with pytest.raises(OSError):
        treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    assert rail.sent == []
    assert wallet.available == wallet.balance


class MultiStepRail(FakeRail):
    def __init__(self, wallet):
        super().__init__(wallet)
        self.fail_mint = False
        self.attestation_ready = False

    def plan(self, direction):
        return ("burn_arb", "mint_base")

    def prepare(self, step, state, gas_spent):
        if step == "mint_base" and not self.attestation_ready:
            raise Pending("await attestation")
        return {"network": "scripted", "tx_hash": step}

    def poll(self, step, state):
        return {
            "confirmed": not (step == "mint_base" and self.fail_mint),
            "principal_moved": step == "burn_arb",
            "fee_micro": 10_000,
            "received_micro": state["received_micro"],
            "evidence": state["reference"],
        }


def test_attestation_wait_does_not_recharge_burn_and_failure_quarantines_principal():
    ledger, wallet, records = setup()
    rail = MultiStepRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=30_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    assert wallet.balance == 99_990_000
    assert treasury.state["reference"] is None
    for tick in range(3, 8):
        treasury.tick(tick)
    assert wallet.balance == 99_990_000
    rail.attestation_ready = rail.fail_mint = True
    treasury.tick(8)
    treasury.tick(9)
    assert treasury.state["status"] == "stranded"
    assert wallet.balance == 99_980_000 and wallet.available == 89_980_000
    assert treasury.pots()["pending"]
    assert treasury.transfer("to_reserve", "10", handle="b", now_ns=10)["status"] == "refused"
    assert any(
        i["kind"] == "treasury.failed" and i["stranded_micro"] == 10_000_000 for i in records
    )


def test_native_gas_is_a_booked_fee_without_destroying_unspent_usdc():
    ledger, wallet, records = setup()
    rail = MultiStepRail(wallet)
    poll = rail.poll
    rail.poll = lambda step, state: {
        **poll(step, state), "fee_micro": 10_000, "wallet_fee_micro": 0,
        "gas_fee_wei": 123, "chain_key": "hyper",
    }
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=30_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    assert treasury.state["fees_micro"] == 10_000
    assert treasury.gas_spent == {"hyper": 123}
    assert wallet.balance == 100_000_000 and wallet.check_conservation()
    assert wallet.available == 89_980_000
    assert any(i["kind"] == "treasury.step_confirmed" and
               i["outcome"]["wallet_fee_micro"] == 0 for i in records)
    rail.attestation_ready = True
    treasury.tick(3)
    treasury.tick(4)
    assert wallet.balance == wallet.available == 100_000_000
    assert treasury.pots()["total_micro"] == wallet.balance
    assert treasury.state["fees_micro"] == 20_000 and treasury.gas_spent["hyper"] == 246


def test_pots_are_read_only_and_never_create_kernel_money():
    ledger, wallet, _ = setup()
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    result = wallet.pots()
    result["venue"] = 10**20
    assert wallet.pots()["venue"] == wallet.balance == 100_000_000
    with pytest.raises(ValueError):
        wallet.bind_pots(lambda: {})


def test_reconciler_sums_the_pots_and_never_ledgers_authority_as_drift():
    """The wallet is authority, not one of the pots: a snapshot reports both, compares
    neither with the other, and an incomplete view sums to nothing."""
    ledger, wallet, records = setup()
    pots = {
        "venue": 60_000_000,
        "reserve": 20_000_000,
        "seed": 10_000_000,
        "sellers": {"venice": 9_500_000},
        "pending": False,
    }
    snap = Reconciler.snapshot(wallet.balance, None, None, pots_view=pots, ledger=ledger)
    assert snap["pots_micro"] == 99_500_000 and snap["wallet_micro"] == 100_000_000
    pots["sellers"]["venice"] -= 70_000_000  # authority far above the money: no alarm
    Reconciler.snapshot(wallet.balance, None, None, pots_view=pots, ledger=ledger)
    assert not any(i["kind"] == "reconcile.drift" for i in records)
    assert wallet.balance == 100_000_000
    pots["reserve"] = None
    assert Reconciler.snapshot(wallet.balance, None, None, pots_view=pots)["pots_micro"] is None
    pots["reserve"], pots["pending"] = 20_000_000, True
    assert Reconciler.snapshot(wallet.balance, None, None, pots_view=pots)["pots_micro"] is None


def test_seller_seed_and_reserve_are_not_double_counted():
    provider = SimpleNamespace(
        openrouter=SimpleNamespace(balance_micro=lambda: 10),
        venice=SimpleNamespace(balance_micro=lambda: 20),
    )
    assert provider_pots(provider) == (10, {"venice": 20})
    venice = SimpleNamespace(name="venice", balance_micro=lambda: 20)
    assert provider_pots(venice) == (0, {"venice": 20})
    assert Decimal("0.000020") * 1_000_000 == 20


class StalledRail(FakeRail):
    """A poll that cannot complete: the transfer waits with no receipt and no refusal."""

    def __init__(self, wallet):
        super().__init__(wallet)
        self.failure = Pending("RPC call rejected or unavailable")

    def poll(self, step, state):
        if self.failure is None:
            return super().poll(step, state)
        raise self.failure


def test_a_stalled_poll_is_ledgered_bounded_and_public_until_evidence_arrives():
    ledger, wallet, records = setup()
    rail = StalledRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=10_000)
    wallet.bind_pots(treasury.pots)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    view = wallet.pots()
    assert view["pending"] and view["pending_reason"] is None and view["pending_since"] is None
    for now_ns in range(2, 27):
        assert treasury.tick(now_ns) == []
    stalls = [i for i in records if i["kind"] == "treasury.pending" and "attempts" in i]
    assert [i["attempts"] for i in stalls] == [1, 10, 20]
    assert stalls[0] == {"kind": "treasury.pending", "transfer_id": "treasury-0",
                         "step": "to_reserve", "phase": "poll",
                         "reason": "RPC call rejected or unavailable", "attempts": 1,
                         "since_ns": 2, "since_window": 0, "since_tick": 0, "reference": None}
    assert all(i["since_ns"] == 2 for i in stalls)
    assert treasury.state["pending"]["attempts"] == 25
    view = wallet.pots()
    assert view["pending"] and view["pending_reason"] == "RPC call rejected or unavailable"
    assert view["pending_since"] == 2 and not view["complete"]
    # A changed reason is public at once; a transport error is named by class only,
    # so a URL or a body it carries never reaches the journal.
    rail.failure = ConnectionError("https://rpc.example/?token=secret")
    treasury.tick(27)
    assert records[-1]["kind"] == "treasury.pending" and records[-1]["attempts"] == 26
    assert records[-1]["reason"] == "ConnectionError" and "secret" not in str(records)
    rail.failure = Pending("RPC call rejected or unavailable")
    treasury.tick(28)
    assert records[-1]["attempts"] == 27 and records[-1]["since_ns"] == 2
    written = len(records)
    treasury.tick(29)  # the same reason again, off the tenth-attempt beat: nothing written
    assert len(records) == written and treasury.state["pending"]["attempts"] == 28
    assert wallet.available == 89_990_000  # the principal and fee holds never moved
    rail.failure = None
    treasury.tick(30)
    assert treasury.state["status"] == "confirmed" and "pending" not in treasury.state
    view = wallet.pots()
    assert view["pending_reason"] is None and view["pending_since"] is None
    assert not view["pending"] and wallet.balance == 99_990_000


def test_a_pending_preparation_is_ledgered_with_its_step_and_carries_the_rail_cursor():
    ledger, wallet, records = setup()
    rail = MultiStepRail(wallet)
    cursors = []

    def prepare(step, state, gas_spent):
        if step == "mint_base" and not rail.attestation_ready:
            cursors.append((state.get("pending") or {}).get("reference"))
            raise Pending("awaiting the Circle forwarder's Base mint",
                          carry={"scanned_to": 100 + len(cursors)})
        return {"network": "scripted", "tx_hash": step}

    rail.prepare = prepare
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=30_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)  # the burn confirms and the mint is prepared: it waits
    stalls = [i for i in records if i["kind"] == "treasury.pending"]
    assert stalls == [{"kind": "treasury.pending", "transfer_id": "treasury-0",
                       "step": "mint_base", "phase": "prepare",
                       "reason": "awaiting the Circle forwarder's Base mint", "attempts": 1,
                       "since_ns": 2, "since_window": 0, "since_tick": 0,
                       "reference": {"scanned_to": 101}}]
    for tick in range(3, 12):
        treasury.tick(tick)
    # Each wait resumes from the cursor the previous wait carried back.
    assert cursors == [None] + [{"scanned_to": 100 + n} for n in range(1, 10)]
    assert treasury.state["pending"]["reference"] == {"scanned_to": 110}
    assert [i["attempts"] for i in records if i["kind"] == "treasury.pending"] == [1, 10]
    assert treasury.pots()["pending_reason"] == "awaiting the Circle forwarder's Base mint"
    assert treasury.pots()["pending_since"] == 2
    # An outage without a cursor keeps the one already carried.
    rail.prepare = lambda step, state, spent: (_ for _ in ()).throw(
        Pending("Circle attestation service unavailable"))
    treasury.tick(12)
    assert treasury.state["pending"]["reference"] == {"scanned_to": 110}
    assert records[-1]["reason"] == "Circle attestation service unavailable"
    rail.prepare = prepare
    rail.attestation_ready = True
    treasury.tick(13)
    assert treasury.state["reference"] == {"network": "scripted", "tx_hash": "mint_base"}
    assert "pending" not in treasury.state and treasury.pots()["pending_reason"] is None
    treasury.tick(14)
    assert treasury.state["status"] == "confirmed"


def test_an_old_checkpoint_without_a_pending_record_restores():
    ledger, wallet, records = setup()
    rail = StalledRail(wallet)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=10_000)
    treasury.transfer("to_reserve", "10", handle="a", now_ns=1)
    treasury.tick(2)
    saved = treasury.snapshot()
    assert saved["state"]["pending"]["attempts"] == 1
    del saved["state"]["pending"]
    restored = Treasury(ledger, wallet, StalledRail(wallet), fee_ceiling_micro=10_000)
    restored.restore(saved)
    assert restored.pots()["pending_reason"] is None
    restored.tick(3)
    assert restored.state["pending"] == {
        "step": "to_reserve", "phase": "poll", "reason": "RPC call rejected or unavailable",
        "attempts": 1, "since_ns": 3, "since_window": 0, "since_tick": 0,
        "reference": None}


@pytest.mark.parametrize("step", ["withdraw_burn", "shadow_send"])
def test_expired_withdrawal_retains_hold_for_late_ledger_debit(step):
    """The wallet moves only when money moves. A nonce past the venue's window proves the
    signed action can no longer execute, not that it never did: a venue ledger that
    shows its debit late still books the fee and carries the transfer forward."""
    from factorylab.world.treasury_rails import HybridRail

    day_ns = 86_400 * 10**9
    ledger, wallet, _ = setup()

    class LateLedger(FakeRail):
        """A venue that executed the withdrawal at once but whose history shows it late."""

        revealed = False
        polls = 0

        def plan(self, direction):
            return (step, "mint_base")

        def prepare(self, current, state, gas_spent):
            return {"network": "scripted", "nonce": state["nonce"]}

        def poll(self, current, state):
            self.polls += 1
            if current == step and not self.revealed:
                return None  # the venue's history is empty, for now
            return super().poll(current, state)

        def expired(self, current, state, now_ns):
            return HybridRail.expired(HybridRail.__new__(HybridRail), current, state, now_ns)

    rail = LateLedger(wallet, fee_micro=1_000_000)
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000)
    assert treasury.transfer("to_reserve", "5", handle="a", now_ns=1)["status"] == "submitted"
    held = wallet.available
    assert held == 100_000_000 - 5_000_000 - 2_000_000
    treasury.tick(4 * day_ns)  # past the venue's nonce window, history still empty
    assert treasury.state["status"] == "submitted" and treasury.state["index"] == 0
    assert wallet.available == held  # the hold stays: nothing proved the money stayed
    rail.revealed = True
    polls = rail.polls
    treasury.tick(4 * day_ns + 1)
    assert rail.polls > polls
    assert treasury.state["index"] == 1 and treasury.state["fees_micro"] == 1_000_000
    assert wallet.balance == 100_000_000 - 1_000_000 and wallet.check_conservation()


class _UnresolvedVenueAction(FakeRail):
    """A venue whose first-step action shows no evidence until ``revealed`` names its
    transfer; its expiry and lapse rules are the live hybrid rail's own."""

    def __init__(self, wallet, first_step):
        super().__init__(wallet, fee_micro=1_000_000)
        self.first_step, self.revealed, self.sent = first_step, set(), []

    def plan(self, direction):
        return (self.first_step, "mint_base")

    def prepare(self, step, state, gas_spent):
        return {"network": "scripted", "nonce": state["nonce"], "transfer": state["id"]}

    def send(self, step, reference):
        self.sent.append((step, reference["transfer"]))

    def poll(self, step, state):
        if step == self.first_step and state["id"] not in self.revealed:
            return None
        return super().poll(step, state)

    def expired(self, step, state, now_ns):
        from factorylab.world.treasury_rails import HybridRail

        return HybridRail.expired(HybridRail.__new__(HybridRail), step, state, now_ns)

    def lapsed(self, step, state, now_ns):
        from factorylab.world.treasury_rails import HybridRail

        return HybridRail.lapsed(HybridRail.__new__(HybridRail), step, state, now_ns)


def test_an_unresolved_withdrawal_holds_its_own_money_and_blocks_nothing_else():
    """A withdrawal the venue never shows keeps its own principal and fee held, forever
    if need be; once its nonce can no longer execute it is parked, public, and every
    transfer that touches other money runs. Its late debit is still booked."""
    day_ns = 86_400 * 10**9
    ledger, wallet, _ = setup()
    rail = _UnresolvedVenueAction(wallet, "withdraw_burn")
    rail.reserve = 50_000_000
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000)
    assert treasury.transfer("to_reserve", "5", handle="a", now_ns=1)["status"] == "submitted"
    stuck = treasury.state["id"]
    held = wallet.available
    assert held == 100_000_000 - 7_000_000
    treasury.tick(day_ns)
    assert treasury.transfer("to_reserve", "5", handle="b", now_ns=day_ns)["status"] == (
        "refused")  # inside its nonce window it could still execute: it keeps the slot
    treasury.tick(10_000 * day_ns)
    pots = treasury.pots()
    assert not pots["pending"] and not pots["complete"]
    [parked] = pots["parked"]
    assert (parked["transfer_id"], parked["step"], parked["held_micro"]) == (
        stuck, "withdraw_burn", 7_000_000)
    assert parked["parked_ns"] == 10_000 * day_ns and parked["reason"]
    assert wallet.available == held  # its own money stays held: nothing is abandoned
    # A restart keeps it parked, with its holds.
    restored = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000)
    restored.restore(treasury.snapshot())
    assert restored.pots()["parked"] == pots["parked"]
    sends = len(rail.sent)
    restored.tick(10_000 * day_ns + 10**12)
    assert len(rail.sent) == sends  # a lapsed action is never sent again
    # Other money moves: every later transfer runs and confirms beside it.
    for n, direction in enumerate(("to_reserve", "to_venue", "to_reserve")):
        now = 10_001 * day_ns + n
        result = restored.transfer(direction, "5", handle=f"later-{n}", now_ns=now)
        assert result["status"] == "submitted", result
        rail.revealed.add(result["transfer_id"])
        restored.tick(now + 1)
        restored.tick(now + 2)
        assert restored.state["status"] == "confirmed"
    assert [p["transfer_id"] for p in restored.pots()["parked"]] == [stuck]
    # The venue finally shows the stuck debit: it is booked and carried forward.
    rail.revealed.add(stuck)
    restored.tick(10_002 * day_ns)
    assert restored.pots()["parked"] == []
    assert restored.state["id"] == stuck and restored.state["index"] == 1
    assert restored.state["fees_micro"] == 1_000_000 and wallet.check_conservation()


def test_a_parked_shadow_send_blocks_only_another_venice_conversion():
    """A shadow send matches rows naming no nonce on sender, sink and amount, so a second
    conversion beside an unresolved one could be mistaken for it: that, and only that,
    waits."""
    from factorylab.world.treasury import FakeHybridRail

    day_ns = 86_400 * 10**9
    ledger, wallet, _ = setup()
    rail = _UnresolvedVenueAction(wallet, "shadow_send")
    rail.plan = lambda direction: (("shadow_send", "venice_top_up") if direction == "to_venice"
                                   else ("withdraw_burn", "mint_base"))
    rail.reserve = 50_000_000
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000)
    assert treasury.transfer("to_venice", "5", handle="a", now_ns=1)["status"] == "submitted"
    treasury.tick(4 * day_ns)
    assert [p["step"] for p in treasury.pots()["parked"]] == ["shadow_send"]
    refused = treasury.transfer("to_venice", "5", handle="b", now_ns=4 * day_ns)
    assert refused["status"] == "refused" and "shadow" in refused["error"]
    assert treasury.transfer("to_reserve", "5", handle="c",
                             now_ns=4 * day_ns)["status"] == "submitted"
    assert not hasattr(FakeHybridRail, "lapsed")  # a scripted rail never parks


def test_a_parked_transfer_is_published_in_the_wake():
    """A transfer waiting past the venue's nonce window is visible where the population
    and the liveness check read the pots: the wake's public window item."""
    from factorylab.runtime.wake import public_window_item
    from tests.conftest import make_runtime

    rt = make_runtime()
    assert public_window_item(rt, window=1, event=rt.n)["pots"]["parked_transfers"] == []
    row = {"transfer_id": "treasury-0", "direction": "to_reserve", "step": "withdraw_burn",
           "held_micro": 7, "nonce": 1, "parked_ns": 5, "reason": "outcome unknown"}
    pots = rt.wallet.pots
    rt.wallet.pots = lambda: {**pots(), "parked": [row]}
    assert public_window_item(rt, window=1, event=rt.n)["pots"]["parked_transfers"] == [row]


@pytest.mark.parametrize("step", ["withdraw_burn", "shadow_send"])
def test_late_evidence_for_a_parked_action_is_booked_from_that_read(step):
    """The read that found a parked action's late evidence is the evidence: it is booked
    from it, so an outage on the next read neither takes the slot nor resends the
    lapsed nonce. A transfer that was ever parked is never sent again at that step."""
    day_ns, minute_ns = 86_400 * 10**9, 60 * 10**9
    ledger, wallet, _ = setup()
    rail = _UnresolvedVenueAction(wallet, step)
    rail.reserve = 50_000_000
    direction = "to_venice" if step == "shadow_send" else "to_reserve"
    treasury = Treasury(ledger, wallet, rail, fee_ceiling_micro=2_000_000)
    assert treasury.transfer(direction, "5", handle="a", now_ns=1)["status"] == "submitted"
    stuck = treasury.state["id"]
    treasury.tick(4 * day_ns)
    assert [p["transfer_id"] for p in treasury.pots()["parked"]] == [stuck]
    reads = {"n": 0}
    poll = rail.poll

    def late_then_outage(current, state):
        if current == step:
            reads["n"] += 1
            if reads["n"] > 1:
                raise ConnectionError("venue unavailable")
            return FakeRail.poll(rail, current, state)
        return poll(current, state)

    rail.poll = late_then_outage
    now = 5 * day_ns
    for k in range(4):
        treasury.tick(now + k * minute_ns + k)
    assert [s for s in rail.sent if s == (step, stuck)] == [(step, stuck)]  # sent once
    assert treasury.pots()["parked"] == []
    assert not treasury.pots()["pending"]  # the transfer finished; nothing holds the slot
    assert treasury.state["id"] == stuck and treasury.state["status"] == "confirmed"


class _RetryExecutedClassRail(UnconfiguredRail):
    """An offline class-transfer rail whose first POST never completes and whose retry
    executes: the venue stamps the execution 70 s after the signed nonce."""

    def __init__(self, executes=True):
        self.rows, self.sends, self.executes = [], 0, executes
        account = AccountState(Decimal(500), Decimal(500), (), Decimal(0))
        super().__init__(SimpleNamespace(
            name="hyperliquid", _address="offline-account",
            _exchange=SimpleNamespace(wallet=SimpleNamespace(address="offline-account"),
                                      vault_address=None),
            _info=SimpleNamespace(user_non_funding_ledger_updates=lambda *_: self.rows),
            account=lambda: account))

    def send(self, step, reference):
        self.sends += 1
        if self.sends == 1:
            raise Pending("offline first POST did not complete")
        if self.executes and not self.rows:
            self.rows.append({
                "time": reference["nonce"] + 70_000, "hash": "executed-class-move",
                "delta": {"type": "accountClassTransfer", "toPerp": False, "usdc": "5"}})


def test_class_transfer_executed_on_retry_must_confirm_and_unblock_venue():
    """A class move retried at its own nonce may execute on any ledgered attempt, so
    its receipt window spans every attempt, not the first send's alone. A retry that
    executed past the first send's window confirmed nothing, held the principal and
    the slot forever, and refused every venue write but a cancel, closes included."""
    ledger, wallet, _ = setup()
    rail = _RetryExecutedClassRail()
    treasury = Treasury(ledger, wallet, rail)
    start_ns = 1_700_000_000_000_000_000
    assert treasury.transfer("perps_to_spot", "5", handle="h",
                             now_ns=start_ns)["status"] == "submitted"
    treasury.tick(start_ns + 70_000_000_000)  # the retry executes at nonce + 70 s
    assert rail.sends == 2 and len(rail.rows) == 1
    treasury.tick(start_ns + 80_000_000_000)
    assert treasury.state["status"] == "confirmed"
    assert wallet.available == wallet.balance  # the principal hold is released
    rt = SimpleNamespace(treasury=treasury, order_intents={}, vault_intents={},
                         _spot_shortfall=lambda *_: None)
    rt._class_transfer_pending = lambda: VenueMixin._class_transfer_pending(rt)
    assert VenueMixin.venue_batch_refusal(
        rt, "seat", "close", [("0", "venue.close", {"coin": "BTC"})]) is None
    # The unique-row rule still holds across the widened window: a second candidate
    # row inside it confirms nothing.
    ledger2, wallet2, _ = setup()
    rail2 = _RetryExecutedClassRail()
    treasury2 = Treasury(ledger2, wallet2, rail2)
    treasury2.transfer("perps_to_spot", "5", handle="h", now_ns=start_ns)
    treasury2.tick(start_ns + 70_000_000_000)
    rail2.rows.append({**rail2.rows[0], "hash": "another", "time": rail2.rows[0]["time"] + 1})
    treasury2.tick(start_ns + 80_000_000_000)
    assert treasury2.state["status"] == "submitted"


def _unrelated_class_row(nonce: int, after_ms: int, name: str) -> dict:
    return {"time": nonce + after_ms, "hash": name,
            "delta": {"type": "accountClassTransfer", "toPerp": False, "usdc": "5"}}


def test_an_unrelated_class_transfer_between_attempts_confirms_nothing():
    """Sol 6.1 on #193: a ledger row binds to no signed action (an accountClassTransfer
    row carries no nonce, the submit answers no hash), so the evidence window is the
    union of each ledgered send's own window. Neither send executed; another $5 move
    the same way executed at nonce + 65 s, between the windows: it confirms nothing."""
    start_ns = 1_700_000_000_000_000_000
    ledger, wallet, _ = setup()
    rail = _RetryExecutedClassRail(executes=False)
    treasury = Treasury(ledger, wallet, rail)
    treasury.transfer("perps_to_spot", "5", handle="h", now_ns=start_ns)
    nonce = treasury.state["nonce"]
    treasury.tick(start_ns + 70_000_000_000)  # the retry, which does not execute either
    assert rail.sends == 2
    rail.rows.append(_unrelated_class_row(nonce, 65_000, "someone-else"))
    treasury.tick(start_ns + 80_000_000_000)
    assert treasury.state["status"] == "submitted"
    assert wallet.available < wallet.balance  # the principal stays held
    # Ours executing on the retry beside it is ambiguous: two candidates, still pending.
    rail.rows.append(_unrelated_class_row(nonce, 72_000, "ours"))
    treasury.tick(start_ns + 90_000_000_000)
    assert treasury.state["status"] == "submitted"


def test_a_long_retry_run_ledgers_each_send_once_not_a_growing_list():
    """Every ledgered send widens the evidence window by its own window, so the sends
    are kept; a transfer retried every minute for days must not copy them all into
    each row it writes (the diary would grow quadratically)."""
    start_ns = 1_700_000_000_000_000_000
    ledger, wallet, records = setup()
    rail = _RetryExecutedClassRail(executes=False)
    treasury = Treasury(ledger, wallet, rail)
    treasury.transfer("perps_to_spot", "5", handle="h", now_ns=start_ns)
    for minute in range(1, 6):
        treasury.tick(start_ns + minute * 61_000_000_000)
    assert len(treasury.state["sent_ns"]) == rail.sends == 6
    rows = [r for r in records if r["kind"].startswith("treasury.")]
    assert not any("sent_ns" in r.get("state", {}) for r in rows)
    assert [r["sent_ns"] for r in rows if r["kind"] == "treasury.broadcast"] == (
        treasury.state["sent_ns"])


def test_a_send_resent_by_recovery_is_recorded_at_its_own_time():
    """Sol 6.1 on 5154f86c: a send interrupted by a crash at t0 is resent by the
    recovery at t120, the replayed event's clock still reading t0. The resend is
    recorded at its own time (the journal's io.result, and the treasury's sends), so
    the venue's t120 execution confirms instead of falling outside every window."""
    import hashlib

    from factorylab.runtime.resume import JournalProxy, RecoveryJournal, canonical, encode

    start_ns, wall_ns = 1_700_000_000_000_000_000, 1_700_000_120_000_000_000
    rail = _RetryExecutedClassRail(executes=False)
    plain = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(plain, lambda: start_ns)
    journal.wall_ns = lambda: wall_ns
    wallet = Wallet(100_000_000, journal, clock_ns=lambda: 0)
    treasury = Treasury(journal, wallet, JournalProxy(rail, journal, "treasury.rail"))
    journal.active = True
    # The process died after the send's io.call and before its result: the recovery
    # finds the call in the tail and resends it, now.
    real_call = journal.call

    def interrupted(name, function, args, kwargs, **kw):
        if name != "treasury.rail.send":
            return real_call(name, function, args, kwargs, **kw)
        fingerprint = hashlib.sha256(canonical(encode((args, kwargs)))).hexdigest()
        plain.append({"kind": "io.call", "name": name, "input_hash": fingerprint,
                      "ts": start_ns})
        journal.tail = [plain._recovery_items()[-1]]
        journal.position, journal._next = 0, None
        journal.call = real_call
        return real_call(name, function, args, kwargs, **kw)

    journal.call = interrupted
    treasury.transfer("perps_to_spot", "5", handle="h", now_ns=start_ns)
    assert rail.sends == 1  # the recovery's resend
    assert [r["resent_ns"] for r in plain._recovery_items()
            if r["kind"] == "io.result" and "resent_ns" in r] == [wall_ns]
    assert treasury.state["sent_ns"][-1] == treasury.state["last_send_ns"] == wall_ns
    nonce = treasury.state["nonce"]
    rail.rows.append(_unrelated_class_row(nonce, 120_000, "executed-on-recovery"))
    journal.active = False
    treasury.tick(wall_ns + 1_000_000_000)  # no retry yet: the resend was a second ago
    assert rail.sends == 1 and treasury.state["status"] == "confirmed"
    # A later replay reads the resend's time from the recorded result, sending nothing.
    items = plain._recovery_items()
    replay = RecoveryJournal(Ledger(clock_ns=lambda: 0), lambda: start_ns)
    replay.active = True
    replay.tail = [next(i for i in items if i["kind"] == "io.call"
                        and i["name"] == "treasury.rail.send"),
                   next(i for i in items if "resent_ns" in i)]
    with pytest.raises(Pending):  # the resend's recorded outcome: unknown
        replay.call("treasury.rail.send", rail.send,
                    ("perps_to_spot", treasury.state["reference"]), {})
    assert replay.resent_ns == wall_ns and rail.sends == 1
