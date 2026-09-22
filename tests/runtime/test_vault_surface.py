"""The venue's vaults as a surface: journeys, refusals, identity and income, offline.

A vault is published only where the manifest opts in, says what each call does
and costs, and nothing else. Its writes carry an order's discipline: a durable
intent before submission, one submission per identity through a retry or a
resume, a refusal the author can read, and a batch that writes whole or not at
all. Money a vault moves stays in the factory's custody; a leader's commission
from an outside depositor is income, never financing and never P&L.
"""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import canonical
from factorylab.runtime.propensity import action_class, effect_label
from factorylab.runtime.resume import RecoveryJournal, encode, restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_produce,
    _consequence_runtime,
)

OUTSIDE = "0x" + "0b5e".rjust(40, "0")
VAULT_TOOLS = ("venue.vault_create", "venue.vault_deposit", "venue.vault_details",
               "venue.vault_positions", "venue.vault_withdraw")


class Scripted:
    """Replies in order, one per provider call."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, req):
        return ModelResponse(req.model_id, json.dumps(self.replies.pop(0)), 300, 40, "end_turn")


class LosesAcknowledgements(FakeExchange):
    """Applies the next vault write, then loses its acknowledgement."""

    lose_next = False
    submissions = 0

    def vault_transfer(self, *args, **kwargs):
        LosesAcknowledgements.submissions += 1
        result = super().vault_transfer(*args, **kwargs)
        if LosesAcknowledgements.lose_next:
            LosesAcknowledgements.lose_next = False
            raise ConnectionError("acknowledgement lost")
        return result


class LiveLike(FakeExchange):
    """A fake venue whose vault writes answer as a live one can.

    ``mode`` "drop": the write never reaches the venue and the call fails; "lose":
    the write lands and its answer is lost; "bare": the write lands and the answer
    is the live venue's bare ``ok``, with no transaction or amounts.
    """

    mode = None

    def vault_transfer(self, vault, is_deposit, usd, *, client_id=None):
        if self.mode == "drop":
            raise ConnectionError("never sent")
        result = super().vault_transfer(vault, is_deposit, usd, client_id=client_id)
        if self.mode == "lose":
            raise ConnectionError("acknowledgement lost")
        if self.mode == "bare" and result["status"] == "ok":
            return {"status": "ok", "vault": result["vault"], "usd": result["usd"]}
        return result


def _manifest(on=True):
    manifest = load_manifest("scripted")
    return replace(manifest, exchange=replace(manifest.exchange, vault_tools=on))


def _exchange(cls=FakeExchange, cash="20000"):
    return cls(coins=("BTC", "ETH"), start_cash_usd=Decimal(cash), vault_lockup_ns=0,
               start_prices={"BTC": Decimal("100"), "ETH": Decimal("10")})


def _runtime(exchange=None, provider=None, on=True):
    return _consequence_runtime(manifest=_manifest(on), exchange=exchange or _exchange(),
                                provider=provider)


def _decision(rt, seat="seed-decider"):
    handle = _consequence_decision(rt, seat, "verdict")
    rt.consequences.start(handle, rt.n)
    rt.handle_to_assembly[handle] = seat
    return handle


def _call(rt, handle, tool, slot="tool:0", **args):
    return rt._run_tool("seed-decider", handle, {"tool": tool, "args": args}, slot=slot)[0]


def _create(rt, usd="1000"):
    handle = _decision(rt)
    result = _call(rt, handle, "venue.vault_create", name="vault", description="long enough",
                   usd=usd)
    assert result["status"] == "ok", result
    return handle, result["vault"]


def _items(rt, kind):
    """The diary's items of one kind, read without ending the world."""
    return [i for i in rt.ledger._iter_items() if i["kind"] == kind]


def test_the_surface_exists_only_where_the_manifest_opts_in_and_states_no_strategy():
    assert not any(tool in _runtime(on=False).tool_specs for tool in VAULT_TOOLS)
    rt = _runtime()
    assert all(tool in rt.tool_specs for tool in VAULT_TOOLS)
    for tool in VAULT_TOOLS:
        spec = rt.tool_specs[tool]
        assert spec["kind"] == "venue" and spec["args_schema"]["examples"]
        text = spec["description"].lower()
        assert not any(word in text for word in ("should", "recommend", "income", "earn",
                                                 "opportunit", "strategy", "profitable"))
    assert "10000 USDC creation fee" in rt.tool_specs["venue.vault_create"]["description"]
    assert rt.treasury.vault_custody


def test_the_manifest_key_is_hash_neutral_off_and_names_a_new_world_on():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict

    off, on = _manifest(False), _manifest(True)
    assert "vault_tools" not in off.canonical_json()
    assert off.manifest_hash() == load_manifest("scripted").manifest_hash()
    assert on.manifest_hash() != off.manifest_hash()
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw["venue"]["vault_tools"] = True
    assert manifest_from_dict(raw).manifest_hash() == on.manifest_hash()
    raw["venue"]["vault_tools"] = False
    assert manifest_from_dict(raw).manifest_hash() == off.manifest_hash()
    raw["venue"]["vault_tools"] = "yes"
    with pytest.raises(ValueError, match="vault_tools"):
        manifest_from_dict(raw)


def test_create_deposit_and_withdraw_move_money_between_custodians_and_book_its_effects():
    rt = _runtime()
    pots_before = rt.treasury.pots()["total_micro"]
    creator, vault = _create(rt)
    assert rt.vault_book[vault]["leader"] and rt.vault_book[vault]["seat"] == "seed-decider"
    fee = [i for i in _items(rt, "venue.settled") if i["reference"].startswith("vault_fee")]
    assert [(i["amount"], i["custody"], i["handle"]) for i in fee] == [
        (-10_000_000_000, "venue_perps", creator)]
    handle = _decision(rt)
    assert _call(rt, handle, "venue.vault_deposit", vault=vault, usd="500")["status"] == "ok"
    rt.treasury.forget_observations()
    pots = rt.treasury.pots()
    assert pots["vaults"] == 1_500_000_000
    assert pots["total_micro"] == pots_before - 10_000_000_000  # only the fee left
    positions = _call(rt, handle, "venue.vault_positions", slot="tool:1")
    assert positions["positions"][0]["equity_usd"] == "1500"
    detail = _call(rt, handle, "venue.vault_details", slot="tool:2", vault=vault)
    assert detail["is_leader"] and detail["own_equity_usd"] == "1500"
    rt.exchange.mark_vaults(Decimal(1000))  # the vault's own equity rises 10%
    out = _decision(rt)
    result = _call(rt, out, "venue.vault_withdraw", vault=vault, usd="165")
    assert result["status"] == "ok" and result["commission"] == result["commission_rebate"]
    pnl = [i for i in _items(rt, "venue.settled") if i["reference"].startswith("vault_withdraw")]
    assert [(i["amount"], i["custody"], i["handle"]) for i in pnl] == [
        (15_000_000, "venue_vaults", out)]
    moves = [(i["operation"], i["from"], i["to"]) for i in _items(rt, "vault.custody")]
    assert moves == [("venue.vault_create", "venue_perps", "venue_vaults"),
                     ("venue.vault_deposit", "venue_perps", "venue_vaults"),
                     ("venue.vault_withdraw", "venue_vaults", "venue_perps")]
    # A judge sees the vault writes the decision executed, as it sees orders.
    assert [w["operation"] for w in rt.tool_writes(out)] == ["venue.vault_withdraw"]


def test_a_deposit_leaves_perps_collateral_and_order_collateral_sees_it():
    rt = _runtime()
    handle = _decision(rt)
    assert rt._order_collateral(handle, "BTC", Decimal(40), True) is None
    _create(rt, usd="9000")
    reason = rt._order_collateral(_decision(rt), "BTC", Decimal(40), True)
    assert reason == "order collateral exceeds venue free collateral"


@pytest.mark.parametrize(("tool", "args", "why"), [
    ("venue.vault_create", {"name": "vault", "description": "long enough", "usd": "50"},
     "below minimum"),
    ("venue.vault_create", {"name": "vault", "description": "long enough", "usd": "15000"},
     "insufficient collateral"),
    ("venue.vault_deposit", {"usd": "9500"}, "insufficient collateral"),
    ("venue.vault_withdraw", {"usd": "1001"}, "exceeds this account's equity"),
    ("venue.vault_deposit", {"vault": "0x" + "1" * 40, "usd": "1"}, "vault record unavailable"),
    # Codex review of #124: a seventh decimal was truncated on the wire and rounded in
    # the books, so the venue row never matched its intent.
    ("venue.vault_deposit", {"usd": "1.0000009"}, "finer than one micro-USD"),
])
def test_a_refused_vault_write_is_explained_and_never_submitted(tool, args, why):
    rt = _runtime()
    _, vault = _create(rt)
    handle = _decision(rt)
    result = _call(rt, handle, tool, **{"vault": vault, **args}
                   if tool != "venue.vault_create" else args)
    assert result["status"] == "rejected" and why in result["error"]
    assert [i for i in rt.vault_intents.values() if i["handle"] == handle] == []
    refused = [i for i in _items(rt, "vault.refused") if i["handle"] == handle]
    assert len(refused) == 1 and why in refused[0]["reason"]
    # The reason reaches the ordering seat's own inbox under this handle (C5).
    told = [rt.outcomes.body(r["sha"])["outcome"]
            for r in rt.outcomes.items.get("seed-decider", ()) if r["handle"] == handle]
    assert any(why in item["reason"] for item in told)


def test_a_leader_is_refused_a_withdrawal_below_its_minimum_share():
    rt = _runtime()
    _, vault = _create(rt, usd="100")
    assert rt.exchange.simulate_deposit(vault, OUTSIDE, Decimal(1900))["status"] == "ok"
    result = _call(rt, _decision(rt), "venue.vault_withdraw", vault=vault, usd="1")
    assert result["status"] == "rejected" and "below 5%" in result["error"]


def test_a_withdrawal_inside_the_lockup_is_refused_with_its_end():
    exchange = _exchange()
    exchange.vault_lockup_ns = 10**15
    rt = _runtime(exchange)
    _, vault = _create(rt)
    result = _call(rt, _decision(rt), "venue.vault_withdraw", vault=vault, usd="1")
    assert result["status"] == "rejected" and "locked until" in result["error"]


def test_a_lost_acknowledgement_is_resolved_by_reading_never_by_resending():
    rt = _runtime(_exchange(LosesAcknowledgements))
    _, vault = _create(rt)
    LosesAcknowledgements.submissions, LosesAcknowledgements.lose_next = 0, True
    handle = _decision(rt)
    first = _call(rt, handle, "venue.vault_deposit", vault=vault, usd="500")
    assert first["status"] == "ok" and LosesAcknowledgements.submissions == 1
    kinds = [i["kind"] for i in rt.ledger._iter_items() if i.get("client_id") == f"{handle}:tool:0"]
    assert kinds[:3] == ["vault.intent", "vault.uncertain", "vault.acknowledged"]
    again = _call(rt, handle, "venue.vault_deposit", vault=vault, usd="500")
    assert again == first and LosesAcknowledgements.submissions == 1
    assert rt.exchange._vault_equity() == Decimal(1500)


def test_a_resumed_world_never_submits_a_vault_write_twice():
    rt = _runtime()
    _, vault = _create(rt)
    handle = _decision(rt)
    first = _call(rt, handle, "venue.vault_deposit", vault=vault, usd="500")
    saved = runtime_state(rt)
    restored = _runtime()
    restore_runtime(restored, saved)
    assert restored.vault_intents == rt.vault_intents and restored.vault_book == rt.vault_book
    cash = restored.exchange._cash
    again = restored._vault_write(handle, "venue.vault_deposit",
                                  {"vault": vault, "usd": "500"}, slot="tool:0")
    assert again == first
    assert restored.exchange._cash == cash
    assert restored.exchange._vault_equity() == Decimal(1500)


def test_a_depositors_commission_is_income_never_financing_or_pnl():
    rt = _runtime()
    _, vault = _create(rt)
    wallet_before = rt.wallet.balance
    budget_before = rt.budget.entitlement("seed-decider")
    assert rt.exchange.simulate_deposit(vault, OUTSIDE, Decimal(5000))["status"] == "ok"
    rt.exchange.mark_vaults(Decimal(1000))
    assert rt.exchange.simulate_withdraw(vault, OUTSIDE, Decimal(5500))["commission"] == (
        "50.000000")
    settled_before = len(_items(rt, "venue.settled"))
    rt._collect_income()
    rt._collect_income()  # a second read books nothing new
    assert rt.treasury.income["earned_micro"] == 50_000_000
    earned = _items(rt, "income.earned")
    assert [(i["service"], i["micro"], i["custody"], i["chain"]) for i in earned] == [
        ("vault.leader_commission", 50_000_000, "venue_perps", "hypercore")]
    custody = _items(rt, "income.custody")
    assert [(i["custody"], i["micro"]) for i in custody] == [("venue_perps", 50_000_000)]
    assert rt.treasury.rail.reserve == 0  # the venue already holds it; no pot is told twice
    assert rt.wallet.balance == wallet_before + 50_000_000
    assert rt.budget.entitlement("seed-decider") == budget_before + 50_000_000
    assert len(_items(rt, "venue.settled")) == settled_before
    assert rt.treasury.collect_financing() == [] and not _items(rt, "financing.classified")
    assert rt.treasury.pots()["converted_from_principal_micro"] == 0


def test_a_leaders_own_commission_repaid_to_itself_is_not_income():
    rt = _runtime()
    _, vault = _create(rt)
    rt.exchange.mark_vaults(Decimal(1000))
    result = _call(rt, _decision(rt), "venue.vault_withdraw", vault=vault, usd="1100")
    assert result["commission_rebate"] == "10.000000"
    rt._collect_income()
    assert rt.treasury.income["earned_micro"] == 0 and not _items(rt, "income.earned")
    assert [i["micro"] for i in _items(rt, "vault.commission_returned")] == [10_000_000]
    pnl = [i for i in _items(rt, "venue.settled") if i["reference"].startswith("vault_withdraw")]
    assert [i["amount"] for i in pnl] == [100_000_000]  # the whole gain, commission repaid


def test_a_vault_write_is_named_as_a_venue_act():
    assert effect_label("venue.vault_deposit", {"vault": "0x", "usd": "1"}) == "vault:deposit"
    assert action_class("vault:create", {}) == "order"


def _vault_batch(calls_for):
    """One decision writing ``calls_for(vault)`` in one batch, to a vault that exists."""
    provider = Scripted()
    rt = _runtime(provider=provider)
    vault = rt.exchange.vault_create("vault", "long enough", Decimal(1000))["vault"]
    provider.replies = [{"action": "investigate", "tool_calls": calls_for(vault)},
                        {"action": "hold"}]
    handle, _ = _consequence_produce(rt)
    return rt, handle


def test_a_vault_batch_whose_second_write_would_be_refused_submits_neither():
    rt, handle = _vault_batch(lambda vault: [
        {"tool": "venue.vault_deposit", "args": {"vault": vault, "usd": "100"}},
        {"tool": "venue.vault_withdraw", "args": {"vault": vault, "usd": "5000"}}])
    assert rt.vault_intents == {}
    refused = _items(rt, "order.batch_refused")
    assert len(refused) == 1 and refused[0]["handle"] == handle
    assert "exceeds this account's equity" in refused[0]["reason"]
    assert rt.exchange._vault_equity() == Decimal(1000)


def test_two_deposits_that_only_fit_one_at_a_time_are_refused_together():
    rt, _ = _vault_batch(lambda vault: [
        {"tool": "venue.vault_deposit", "args": {"vault": vault, "usd": "5000"}},
        {"tool": "venue.vault_deposit", "args": {"vault": vault, "usd": "5000"}}])
    assert rt.vault_intents == {}
    assert "insufficient collateral" in _items(rt, "order.batch_refused")[0]["reason"]
    assert rt.exchange._vault_equity() == Decimal(1000)


@pytest.mark.parametrize("name", ["exchange.vault_create", "exchange.vault_transfer"])
def test_a_vault_write_interrupted_before_its_answer_replays_as_uncertain_never_resent(name):
    """A process death between a vault write's ``io.call`` and its ``io.result`` resumes
    with the write uncertain, for its intent's owner to resolve from the venue's ledger;
    the replay never calls the venue again."""
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = journal.recovering = True
    fingerprint = hashlib.sha256(canonical(encode(((), {})))).hexdigest()
    journal.tail = [{"kind": "io.call", "name": name, "input_hash": fingerprint,
                     "seq": 0, "ts": 0}]
    result = journal.call(name, lambda: pytest.fail("vault write sent twice"), (), {})
    assert result == {"status": "uncertain"}
    assert recorded[-1] == {"kind": "io.result", "call": 0,
                            "result": encode({"status": "uncertain"})}


# ---- cold review: row binding, wind-down, attribution, mixed batches ----------------


def _pnl(rt):
    return [i for i in _items(rt, "venue.settled") if i["reference"].startswith("vault_withdraw")]


def test_a_lost_withdrawal_never_borrows_an_acknowledged_ones_row():
    rt = _runtime(_exchange(LiveLike))
    _, vault = _create(rt)
    rt.exchange.mark_vaults(Decimal(1000))
    first = _decision(rt)
    assert _call(rt, first, "venue.vault_withdraw", vault=vault, usd="50")["status"] == "ok"
    rt.exchange.mode = "drop"  # the second withdrawal of 50 never reaches the venue
    second = _decision(rt)
    result = _call(rt, second, "venue.vault_withdraw", vault=vault, usd="50")
    assert result["status"] == "uncertain"
    for _ in range(6):
        rt._reconcile_orders()
    assert len(_pnl(rt)) == 1 and _pnl(rt)[0]["handle"] == first
    assert rt.vault_intents[f"{second}:tool:0"]["unresolved"]
    assert [i["client_id"] for i in _items(rt, "vault.unresolved")] == [f"{second}:tool:0"]


def test_a_lost_deposit_is_not_confirmed_by_an_earlier_deposits_row():
    rt = _runtime(_exchange(LiveLike))
    _, vault = _create(rt)
    assert _call(rt, _decision(rt), "venue.vault_deposit", vault=vault, usd="500")[
        "status"] == "ok"
    rt.exchange.mode = "drop"
    lost = _decision(rt)
    assert _call(rt, lost, "venue.vault_deposit", vault=vault, usd="500")[
        "status"] == "uncertain"
    rt._reconcile_orders()
    assert rt.vault_intents[f"{lost}:tool:0"]["result"]["status"] == "uncertain"
    assert rt.exchange._vault_equity() == Decimal(1500)


def test_two_real_writes_of_one_amount_each_bind_their_own_row_and_settle():
    rt = _runtime(_exchange(LiveLike))
    _, vault = _create(rt)
    rt.exchange.mark_vaults(Decimal(1000))
    rt.exchange.mode = "bare"  # a live acknowledgement: no transaction, no amounts
    handles = [_decision(rt), _decision(rt)]
    for handle in handles:
        assert _call(rt, handle, "venue.vault_withdraw", vault=vault, usd="100")[
            "status"] == "ok"
    assert _pnl(rt) == []  # nothing is booked before the rows are bound
    rt._reconcile_orders()
    hashes = [rt.vault_intents[f"{h}:tool:0"]["result"]["hash"] for h in handles]
    assert len(set(hashes)) == 2
    assert sorted(i["handle"] for i in _pnl(rt)) == sorted(handles)
    arrived = [i["micro"] for i in _items(rt, "vault.custody") if i["to"] == "venue_perps"]
    assert arrived == [100_000_000, 100_000_000]  # net plus the leader's own repaid share
    restored = _runtime(_exchange(LiveLike))
    restore_runtime(restored, runtime_state(rt))
    assert [restored.vault_intents[f"{h}:tool:0"]["result"]["hash"] for h in handles] == hashes
    restored._reconcile_orders()
    assert _pnl(restored) == []  # a resumed world books nothing again


def test_an_acknowledged_write_whose_row_never_arrives_is_named_unbooked():
    rt = _runtime(_exchange(LiveLike))
    _, vault = _create(rt)
    rt.exchange.mode = "bare"
    handle = _decision(rt)
    assert _call(rt, handle, "venue.vault_withdraw", vault=vault, usd="100")["status"] == "ok"
    rt.exchange._vault_rows.clear()  # the venue never shows the row
    for _ in range(6):
        rt._reconcile_orders()
    assert [i["client_id"] for i in _items(rt, "vault.unbooked")] == [f"{handle}:tool:0"]


def test_money_in_a_vault_is_pending_exposure_at_a_kill_never_flat():
    from factorylab.runtime.winddown import PENDING, UNKNOWN, execute
    from tests.runtime.test_winddown_retry import Diary

    rt = _runtime()
    _create(rt)
    report = execute(rt.exchange.target, Diary())
    assert report["exposure_state"] == PENDING
    assert [v["equity_usd"] for v in report["residual"]["vaults"]] == ["1000"]
    summary = rt._summary()
    assert summary["vault_equity_usd"] == "1000"
    assert Decimal(summary["exchange_equity_usd"]) == Decimal(20000 - 11000)

    class Unreadable(FakeExchange):
        def vault_equities(self):
            raise ConnectionError("no answer")

    assert execute(_exchange(Unreadable), Diary())["exposure_state"] == UNKNOWN


def test_commission_is_income_only_when_every_led_vault_is_this_worlds():
    rt = _runtime(_exchange(cash="40000"))
    _, vault = _create(rt)
    # A vault the operator created on the same account, outside this world.
    rt.exchange.vault_create("operator", "created by the operator", Decimal(1000))
    rt.exchange.simulate_deposit(vault, OUTSIDE, Decimal(5000))
    rt.exchange.mark_vaults(Decimal(1000))
    rt.exchange.simulate_withdraw(vault, OUTSIDE, Decimal(5500))
    rt._collect_income()
    assert rt.treasury.income["earned_micro"] == 0
    skipped = _items(rt, "vault.commission_skipped")
    assert len(skipped) == 1 and "did not create" in skipped[0]["reason"]


def test_a_commission_in_an_own_withdrawal_or_beside_an_unread_row_is_never_income():
    rt = _runtime()
    _, vault = _create(rt)
    rt.exchange.mark_vaults(Decimal(1000))
    # The account's own withdrawal, with a repayment of a different amount.
    tx = "0x" + "ab" * 32
    rows = rt.exchange._vault_rows
    rows.append({"ts_ns": 1, "hash": tx, "type": "vaultWithdraw", "vault": vault,
                 "user": None, "requested": Decimal(10), "commission": Decimal(1),
                 "closing_cost": Decimal(0), "basis": Decimal(9), "net": Decimal(9)})
    rows.append({"ts_ns": 1, "hash": tx, "type": "vaultLeaderCommission", "vault": None,
                 "user": None, "usd": Decimal("1.5")})
    # An outside commission on a page that carries a row this surface could not read.
    rows.append({"ts_ns": 2, "hash": "0x" + "cd" * 32, "type": "unparsed",
                 "kind": "vaultWithdraw", "vault": None, "user": None})
    rows.append({"ts_ns": 2, "hash": "0x" + "cd" * 32, "type": "vaultLeaderCommission",
                 "vault": None, "user": None, "usd": Decimal(3)})
    rt._collect_income()
    assert rt.treasury.income["earned_micro"] == 0 and not _items(rt, "income.earned")
    assert [i["micro"] for i in _items(rt, "vault.commission_returned")] == [1_500_000]
    skipped = _items(rt, "vault.commission_skipped")
    assert [i["micro"] for i in skipped] == [3_000_000]
    assert "could not be read" in skipped[0]["reason"]


def test_an_unread_vault_row_is_kept_as_unparsed_not_dropped():
    from factorylab.world.vaults import UNPARSED, ledger_rows, own_withdraw_hashes

    rows = ledger_rows([{"time": 5, "hash": "0xab", "delta": {
        "type": "vaultWithdraw", "vault": "0xv", "requestedUsd": "not a number"}}])
    assert [(r["type"], r["hash"]) for r in rows] == [(UNPARSED, "0xab")]
    assert own_withdraw_hashes(rows, "0xme") == set()


def test_a_deposit_and_an_order_that_each_fit_alone_are_refused_together():
    rt, _ = _vault_batch(lambda vault: [
        {"tool": "venue.vault_deposit", "args": {"vault": vault, "usd": "8000"}},
        {"tool": "venue.place_market", "args": {"coin": "BTC", "side": "buy", "size": "40"}}])
    assert rt.vault_intents == {} and rt.order_intents == {}
    assert _items(rt, "order.batch_refused")
    # Each fits alone: 9000 of perps collateral is free after the vault's creation.
    alone = _decision(rt)
    assert rt._order_collateral(alone, "BTC", Decimal(40), True) is None


def test_a_commission_indexed_late_in_the_newest_millisecond_is_still_booked_once():
    """Codex review of #124: a cursor advanced past the newest row's millisecond lost
    a commission the venue indexed after the poll, at that same millisecond."""
    rt = _runtime()
    _create(rt)
    at = rt.vault_ledger_cursor_ns + 5_000_000
    rows = rt.exchange._vault_rows
    rows.append({"ts_ns": at, "hash": "0x" + "a1" * 32, "type": "vaultLeaderCommission",
                 "vault": None, "user": OUTSIDE, "usd": Decimal(2)})
    rt._collect_income()
    rows.append({"ts_ns": at, "hash": "0x" + "a2" * 32, "type": "vaultLeaderCommission",
                 "vault": None, "user": OUTSIDE, "usd": Decimal(3)})
    rt._collect_income()
    rt._collect_income()
    assert rt.treasury.income["earned_micro"] == 5_000_000
    assert sorted(i["micro"] for i in _items(rt, "income.earned")) == [2_000_000, 3_000_000]


def test_a_vault_amount_finer_than_a_micro_usd_is_refused_by_the_venue_adapter_too():
    from factorylab.world.vaults import exact_micro

    assert exact_micro("1.000001") == 1_000_001 and exact_micro("1.0000009") is None
    ex = _exchange()
    assert "finer" in ex.vault_transfer("0x" + "2" * 40, True, Decimal("1.0000009"))["error"]
