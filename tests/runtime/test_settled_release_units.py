"""Settled decisions are released (wave 17b): the rules that live in one function.

Each test names its contract and the regression it catches; each runs no world (the
world-level proofs are in ``test_settled_release.py``):

* an order's account is released only once the venue's own order status confirms it
  terminal, never on a cancel acknowledgement or a horizon (regression: a fill after
  the cancel moves money nobody books);
* terminal vault and Polymarket writes pin nothing, and leave with their decision
  (regression: those decisions pinned forever);
* a Polymarket decision is released once resolved, confirmed and claimed;
* a seat not balloted within the published retention has its returns released
  unread (regression: a rarely balloted seat pins its decisions);
* an older checkpoint's inbox items are held a full horizon from the restore
  (regression: every legacy item released at the first boundary).
"""

from fractions import Fraction

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime import settled


def test_a_cancelled_order_is_released_only_once_the_venue_reads_it_back_terminal():
    """A resting order is cancelled and the cancel acknowledged: that is not the venue's
    word that it can fill no more. The next reconciliation reads the order back; only the
    venue's own status (``cancelled``) confirms it, and only then may its account go."""
    from tests.helpers import collateral_decision
    from tests.runtime.test_loop import _consequence_runtime

    rt = _consequence_runtime()
    handle = collateral_decision(rt)
    placed = rt._run_tool("seed-decider", handle, {"tool": "venue.place_limit", "args": {
        "coin": "BTC", "side": "buy", "size": "0.001", "price": "1"}}, slot="tool:0")[0]
    assert placed["status"] == "resting"
    order_id = str(placed["order_id"])
    cancelled = rt._run_tool("seed-decider", handle, {"tool": "venue.cancel", "args": {
        "coin": "BTC", "order_id": order_id}}, slot="tool:1")[0]
    assert cancelled["status"] == "cancelled"
    rt.consequences.finish(handle, 0)
    rt.ticks_consumed += 10**6  # any horizon, however long, has passed
    rt.consequences.table = rt.consequences.table.resolve(rt.n, 1, {}, tick=rt.ticks_consumed)
    [order] = [o for o in rt.consequences.table.orders if o.order_id == order_id]
    assert order.remaining == 0 and order.confirmed is None
    assert not rt.consequences.releasable(handle)
    rt._reconcile_orders()
    [row] = [i for i in rt.ledger._recovery_items() if i["kind"] == "consequence.terminal"]
    assert (row["order_id"], row["status"], row["handle"]) == (order_id, "cancelled", handle)
    assert rt.consequences.releasable(handle)


def test_terminal_venue_writes_pin_nothing_and_leave_with_their_decision():
    """Vault and Polymarket writes name their decision while in flight only, and a
    Polymarket decision while its realised money is unclaimed; a released decision's
    writes leave with it, and a vault write's transaction stays claimed."""
    from types import SimpleNamespace

    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.vault_intents = {
        "h1:v": {"handle": "h1", "result": {"status": "ok", "hash": "0xa"}, "settled": True},
        "h2:v": {"handle": "h2", "result": {"status": "ok", "hash": "0xb"}},
        "h3:v": {"handle": "h3", "result": {"status": "uncertain"}},
        "h4:v": {"handle": "h4", "result": {"status": "uncertain"}, "unresolved": True},
    }
    rt.polymarket = SimpleNamespace(
        intents={"h5": {"handle": "h5", "result": {"status": "filled", "order_id": "pm-1"}},
                 "h6": {"handle": "h6", "result": {"status": "uncertain"}}},
        order_ids={"pm-1": "h5"},
        realized={"h5": Fraction(3_000_001, 2), "h7": Fraction(7)},
        claimed={"h5": 1_500_000, "h7": 3})
    named = settled._names_in(rt._live_venue_books(),
                              {"h1", "h2", "h3", "h4", "h5", "h6", "h7"})
    assert set(named) == {"h2", "h3", "h6", "h7"}
    rt._drop_released(["h1", "h5"])
    assert set(rt.vault_intents) == {"h2:v", "h3:v", "h4:v"}
    assert "0xa" in rt._vault_claimed("h2:v")
    assert set(rt.polymarket.intents) == {"h6"} and rt.polymarket.order_ids == {}
    assert "h5" not in rt.polymarket.realized and "h5" not in rt.polymarket.claimed


def test_a_polymarket_decision_is_released_once_resolved_confirmed_and_claimed():
    from tests.helpers import collateral_decision
    from tests.runtime.test_polymarket_surface import advance, buy, still_fake, world

    rt = world(fake=still_fake(resolutions={"fake-1": (10**12, 0)}))
    handle = collateral_decision(rt)
    assert buy(rt, handle)["status"] == "filled"
    rt.consequences.finish(handle, 0)
    rt.clock.now_ns = 10**12
    advance(rt, rt.ev.consequence_horizon_ticks + 1)
    rt.queue.settle(handle, channel="verdict", score=0.5, status=SettleStatus.SETTLED,
                    definition_version="probe", sampling_ref=None)
    rt._release_read_deliveries()
    assert any(i["kind"] == "consequence.terminal" for i in rt.ledger._recovery_items())
    assert rt.polymarket.realized[handle] and rt._live_venue_books()[-1] == []
    assert handle in rt._release_settled()
    assert not any(i["handle"] == handle for i in rt.polymarket.intents.values())
    assert handle not in rt.polymarket.realized


def test_a_seat_not_balloted_within_the_retention_has_its_returns_released_unread():
    from factorylab.kernel.queue import PropensityRecord
    from tests.conftest import make_runtime

    rt = make_runtime()
    lid = "assembly:seed-decider"
    handle = rt.queue.queue.open(
        actor=lid, event_id="probe", propensity=PropensityRecord(
            ("seed-decider",), (1.,), "seed-decider", 0, lid, "probe"),
        channel="policy", deadline_ns=rt.clock.now_ns + 10**12, parent_handle=None,
        cost_ceiling=0)
    rt.queue.queue.settle(handle, channel="policy", score=1.0, status=SettleStatus.SETTLED,
                          definition_version="probe", sampling_ref=None)
    retention = rt._inbox_retention_ticks()
    rt._release_read_deliveries()
    assert rt.queue.owed(handle) == "a delivered return was not read by its consumer"
    rt.ticks_consumed += retention
    rt._release_read_deliveries()
    assert rt.queue.owed(handle) is not None  # held the whole retention
    rt.ticks_consumed += 1
    rt._release_read_deliveries()
    assert rt.queue.owed(handle) is None and rt.policy_seen[lid] == 1
    assert rt.queue.returns_since(lid, rt.policy_seen[lid]) == ((), 1)  # a ballot reads on
    assert lid not in rt.policy_marks


def test_an_older_checkpoint_s_inbox_items_are_held_a_full_horizon_from_the_restore():
    """Items addressed before item ticks existed carry none: a restore stamps them with
    the restore tick, so they are held the whole retention from there, never released
    at once as though addressed at tick 0."""
    from factorylab.runtime.resume import restore_runtime, runtime_state
    from tests.conftest import make_runtime

    rt = make_runtime()
    for n in range(3):
        rt.outcomes.append("seed-decider", handle=f"decision-{n}",
                           outcome={"kind": "verdict", "score": n / 10}, evidence=f"e{n}")
    rows = [row for rows in rt.outcomes.items.values() for row in rows]
    for row in rows:
        row.pop("tick")  # as a checkpoint older than item ticks holds them
    rt.ticks_consumed = 7
    state = runtime_state(rt)
    twin = make_runtime()  # the same manifest and configuration, no world run
    restore_runtime(twin, state)
    held = [row for rows in twin.outcomes.items.values() for row in rows]
    assert {row["tick"] for row in held} == {twin.ticks_consumed}
    before = len(held)
    twin.ticks_consumed += twin._inbox_retention_ticks()
    for seat in twin.outcomes.cursors:
        twin.outcomes.cursors[seat] = 0  # nothing acknowledged: only age could release
    twin._release_inbox()
    assert sum(len(r) for r in twin.outcomes.items.values()) == before
    twin.ticks_consumed += 1
    twin._release_inbox()
    assert sum(len(r) for r in twin.outcomes.items.values()) == 0


def test_a_shared_archived_rationale_outlives_the_release_of_one_of_its_decisions(
        monkeypatch):
    """Two decisions of one seat with byte-identical rationales share one archived
    artifact and one reference. Releasing one decision keeps the reference the other
    still reads (regression: the survivor's ``what_was_said`` raised after collection)."""
    from factorylab.runtime import continuity
    from tests.conftest import make_runtime

    rt = make_runtime()
    monkeypatch.setattr(continuity, "MAX_SAID", 0)
    rt.outcomes.consequences_open = lambda handle: False
    for handle in ("decision-a", "decision-b"):
        rt.outcomes.record_said("seed-decider", handle, {"rationale": "the same words"})
    sha = rt.outcomes.archived_said["decision-a"]
    assert rt.outcomes.archived_said["decision-b"] == sha
    rt.outcomes.forget_said(["decision-a"])
    rt.artifacts.seal_released()
    rt.artifacts.collect()
    assert sha in rt.artifacts.index
    assert rt.outcomes.what_was_said("decision-b")["rationale"] == "the same words"
    rt.outcomes.forget_said(["decision-b"])  # the last one takes the reference with it
    assert rt.artifacts.index[sha].get("released")


def test_a_retired_seat_versioned_again_ballots_past_what_was_discarded():
    """A retired seat has no reader, so its policy returns are discarded as delivered;
    its cursor moves past them, so when the id is versioned again and inherits its
    records, its next ballot reads on from there, across a checkpoint, and sees no
    discarded return (regression: a cursor below the released prefix raised in the
    ballot and aborted governance)."""
    from factorylab.kernel.queue import PropensityRecord
    from factorylab.runtime.resume import restore_runtime, runtime_state
    from tests.conftest import make_runtime

    rt = make_runtime()
    seat, lid = "seed-decider", "assembly:seed-decider"

    def policy(runtime, value):
        handle = runtime.queue.queue.open(
            actor=lid, event_id=f"probe-{value}", propensity=PropensityRecord(
                (seat,), (1.,), seat, 0, lid, "probe"),
            channel="policy", deadline_ns=runtime.clock.now_ns + 10**12, parent_handle=None,
            cost_ceiling=0)
        runtime.queue.queue.settle(handle, channel="policy", score=value,
                                   status=SettleStatus.SETTLED, definition_version="probe",
                                   sampling_ref=None)
        return handle

    policy(rt, 0.1)
    rt.retired_assemblies.add(seat)
    policy(rt, 0.2)  # settles while the seat is retired
    rt._release_read_deliveries()
    assert rt.policy_seen[lid] == rt.queue.delivered_count(lid) == 2
    twin = make_runtime()
    restore_runtime(twin, runtime_state(rt))
    twin.retired_assemblies.discard(seat)  # the id is versioned again
    assert twin.queue.returns_since(lid, twin.policy_seen[lid]) == ((), 2)
    later = policy(twin, 0.3)
    returns, delivered = twin.queue.returns_since(lid, twin.policy_seen[lid])
    assert [r.handle for r in returns] == [later] and delivered == 3


def test_a_lookup_that_omits_the_filled_quantity_confirms_nothing(monkeypatch):
    """A venue answer ``{"status": "cancelled"}`` without its filled quantity is not the
    venue's word on what filled: the order stays unconfirmed and its account pinned,
    and the next read confirms it (regression: 0 filled was assumed, and the account
    released while fills were still arriving)."""
    from types import SimpleNamespace

    from tests.helpers import collateral_decision
    from tests.runtime.test_loop import _consequence_runtime

    rt = _consequence_runtime()
    handle = collateral_decision(rt)
    placed = rt._run_tool("seed-decider", handle, {"tool": "venue.place_limit", "args": {
        "coin": "BTC", "side": "buy", "size": "0.001", "price": "1"}}, slot="tool:0")[0]
    rt._run_tool("seed-decider", handle, {"tool": "venue.cancel", "args": {
        "coin": "BTC", "order_id": str(placed["order_id"])}}, slot="tool:1")
    lookup = rt.exchange.target.lookup
    monkeypatch.setattr(rt.exchange.target, "lookup",
                        lambda *_a, **_k: SimpleNamespace(status="cancelled"))
    rt._reconcile_orders()
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "consequence.terminal"]
    assert all(o.confirmed is None for o in rt.consequences.table.orders)
    monkeypatch.setattr(rt.exchange.target, "lookup", lookup)
    rt._reconcile_orders()
    assert [i["status"] for i in rt.ledger._recovery_items()
            if i["kind"] == "consequence.terminal"] == ["cancelled"]


def test_a_released_polymarket_order_s_late_proceeds_are_claimed_on_the_pot():
    """A released Polymarket order fills (a venue error) and the lot it opens resolves:
    the proceeds are the released decision's owner's, booked late, and claimed on the
    Polymarket pot, never as a Hyperliquid venue claim (regression: they bypassed the
    pot's claim book and landed on the venue claim)."""
    from factorylab.runtime import polymarket
    from tests.helpers import collateral_decision
    from tests.runtime.test_polymarket_surface import advance, buy, token, world

    rt = world()
    handle = collateral_decision(rt)
    placed = buy(rt, handle, price="0.30")  # resting
    oid = placed["order_id"]
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": oid}}, slot="tool:1")
    rt.consequences.finish(handle, 0)
    advance(rt, rt.ev.consequence_horizon_ticks + 1)
    rt.queue.settle(handle, channel="verdict", score=0.5, status=SettleStatus.SETTLED,
                    definition_version="probe", sampling_ref=None)
    rt._release_read_deliveries()
    assert handle in rt._release_settled()
    tok = token(rt)
    venue_claims = dict(rt.budget.venue_claims())
    polymarket.settle(rt, [{"kind": "fill", "order_id": oid, "token_id": tok,
                            "market_id": "fake-1", "is_buy": True, "size": "10",
                            "px": "0.30", "fee_usd": "0", "realized_usd": "0",
                            "ts_ns": rt.clock.now_ns}])
    assert any(lot.handle == handle for lot in rt.consequences.table.lots)
    polymarket.settle(rt, [{"kind": "resolution", "market_id": "fake-1",
                            "condition_id": "c", "token_id": tok, "outcome_index": 0,
                            "outcome_name": "YES", "payout": "1", "size": "10",
                            "realized_usd": "7", "ts_ns": rt.clock.now_ns}])
    rt._settle_late()
    assert rt.polymarket.claims.get("seed-decider") == 7_000_000
    assert dict(rt.budget.venue_claims()) == venue_claims
    rt._release_settled()
    assert handle not in rt.polymarket.realized  # claimed in full, then forgotten


def test_released_vault_transactions_stay_bound_only_within_the_lookup_window():
    """Every released vault write keeps its transaction bound while a lookup can still
    return it, and no longer (regression: the set grew with every write ever made)."""
    from factorylab.runtime.vault import LOOKUP_SKEW_NS
    from tests.conftest import make_runtime

    rt = make_runtime()
    for n in range(500):
        rt.clock.now_ns = n * 10 * LOOKUP_SKEW_NS
        rt.vault_intents = {f"h{n}:v": {"handle": f"h{n}", "since_ns": rt.clock.now_ns,
                                        "result": {"status": "ok", "hash": f"0x{n}"},
                                        "settled": True}}
        rt._drop_released([f"h{n}"])
        rt._prune_vault_released()
        assert len(rt.vault_released_hashes) <= 1
    assert "0x499" in rt._vault_claimed("other")  # inside the window: never rebound
    # A write still being looked up holds the window open for what it could be offered.
    rt.vault_intents = {"late:v": {"handle": "late", "since_ns": 0,
                                   "result": {"status": "uncertain"}}}
    rt.vault_released_hashes = [["0xold", LOOKUP_SKEW_NS]]
    rt._prune_vault_released()
    assert rt.vault_released_hashes == [["0xold", LOOKUP_SKEW_NS]]


def test_late_money_no_live_seat_can_take_is_ledgered_with_its_amount():
    """A released decision's seat and its lineage's root are both retired: the late
    money stays booked in custody, unattributed, and is on the record with its amount."""
    from dataclasses import replace

    from tests.conftest import make_runtime

    rt = make_runtime()
    table = rt.consequences.table
    rt.consequences.table = replace(
        table, released_orders=(("o", "decision-9", 0, "seed-decider"),),
        released_late=(("decision-9", Fraction(5_000_000), 0),))
    rt.retired_assemblies.add("seed-decider")
    claims = dict(rt.budget.venue_claims())
    rt._settle_late()
    [row] = [i for i in rt.ledger._recovery_items()
             if i["kind"] == "consequence.late_undeliverable"]
    assert (row["handle"], row["micro"]) == ("decision-9", 5_000_000)
    assert dict(rt.budget.venue_claims()) == claims
