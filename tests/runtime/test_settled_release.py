"""Settled decisions are released (wave 17b).

Chapter II §I.b: the return channel keeps "a managed queue of outstanding decisions
awaiting their reward"; §IV.c: a verdict "is consumed as a reward signal ... and then
discarded". A decision stays addressable exactly while a score is still owed to it
(``SettledMixin._score_owed``). Here:

* a world run with and without release writes the same diary and the same summary,
  and releases most of what it opened;
* the committee-eligibility tally equals the scan it replaced on every event;
* each clause of the predicate is tested by a violation attempt: a decision a score is
  still owed to is never released, and ``_release_one`` refuses it by name;
* a judge naming a released handle is refused, factually;
* a crash in the middle of a release pass resumes to the uninterrupted run.
"""

import json
from dataclasses import replace
from fractions import Fraction

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import Released, SettleStatus
from factorylab.runtime import settled
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import checkpoint_state, decode, resume_world
from factorylab.runtime.settled import RELEASED_REFUSAL
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.lots import Lot, LotOrder

pytestmark = pytest.mark.gate

EVENTS = 150


def _world(events=EVENTS, path=None, seed=1, **kwargs):
    return Runtime(load_manifest("scripted"), events=events, seed=seed,
                   initial_balance_micro=None,
                   ledger_path=None if path is None else str(path), router_gamma=.1, **kwargs)


def _no_release(monkeypatch):
    """Release off: the tally still reads every settlement, nothing is released."""
    monkeypatch.setattr(settled.SettledMixin, "_release_settled",
                        lambda self: (self._drain_finalized(), [])[1])


def _diary(rt):
    return [{k: v for k, v in i.items() if k not in ("hash", "prev_hash")}
            for i in rt.ledger._recovery_items() if i["kind"] != "snapshot"]


def _plain(summary):
    return {k: v for k, v in json.loads(json.dumps(summary, default=str)).items()
            if k not in ("ledger_path", "ledger")}


def _released(rt):
    return sum(rt.queue.released_counts().values())


def test_release_changes_nothing_any_reader_sees(monkeypatch):
    """Every reader a released decision had is gone before it is: with and without
    release, one manifest and seed write the same diary, snapshots aside."""
    released = _world()
    expected = released.run()
    _no_release(monkeypatch)
    kept = _world()
    summary = kept.run()
    assert _released(released) > len(released.queue.retained())
    assert len(kept.queue.retained()) > 3 * len(released.queue.retained())
    assert _diary(released) == _diary(kept)
    assert _plain(summary) == _plain(expected)
    last = [i for i in released.ledger._recovery_items() if i["kind"] == "snapshot"][-1]
    heavy = [i for i in kept.ledger._recovery_items() if i["kind"] == "snapshot"][-1]
    assert last["bytes"] < heavy["bytes"] / 2
    # Every order a released account placed was confirmed terminal by the venue's own
    # order status before its release.
    confirmed = {i["order_id"] for i in released.ledger._recovery_items()
                 if i["kind"] == "consequence.terminal"}
    orders = {row[0] for row in released.consequences.table.released_orders}
    assert orders and orders <= confirmed


def _record_eligibility(monkeypatch, read):
    rows = []
    process = Runtime._process_event

    def recorded(self, ev):
        result = process(self, ev)
        rows.append(read(self))
        return result

    monkeypatch.setattr(Runtime, "_process_event", recorded)
    return rows


def _tally_equals_scan(monkeypatch, events, seed):
    tallied = _record_eligibility(monkeypatch, lambda rt: rt._committee_eligible())
    live = _world(events, seed=seed)
    live.run()
    monkeypatch.undo()
    assert _released(live) > 0
    _no_release(monkeypatch)
    scanned = _record_eligibility(
        monkeypatch, lambda rt: (rt._committee_eligible(), rt._committee_eligible_scan()))
    kept = _world(events, seed=seed)
    kept.run()
    monkeypatch.undo()
    assert len(tallied) == len(scanned) == kept.n
    for n, (tally, (kept_tally, scan)) in enumerate(zip(tallied, scanned, strict=True), 1):
        assert tally == kept_tally == scan, (seed, n)
    assert any(tallied), "the world never qualified a seat: the comparison is empty"


def test_the_eligibility_tally_equals_the_scan_on_every_event(monkeypatch):
    """Committee eligibility is a running tally kept at settlement (wave 17b). Side by
    side over a scripted world: the tally with release live equals, on every event,
    the scan over every decision and account of the same world run without release."""
    _tally_equals_scan(monkeypatch, EVENTS, 1)


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_eligibility_tally_equals_the_scan_over_three_seeds(monkeypatch, seed):
    """The same side-by-side proof, 500 world events on each of three seeds."""
    _tally_equals_scan(monkeypatch, 500, seed)


def test_an_older_checkpoint_rebuilds_the_tally_from_the_scan(monkeypatch):
    rt = _world(40)
    rt.run()
    expected = rt._committee_eligible_scan()
    rt.eligibility_tally, rt.eligibility_evidence = {"stale": 99}, set()
    rt._rebuild_eligibility_tally()
    assert rt._committee_eligible() == expected


@pytest.fixture(scope="module")
def unreleased():
    """A world run with release off: plenty of decisions a release would now take."""
    patch = pytest.MonkeyPatch()
    _no_release(patch)
    try:
        rt = _world()
        rt.run()
    finally:
        patch.undo()
    return rt


def _candidates(rt, *, account=False):
    live = rt._live_references()
    out = []
    for handle in rt.queue.retained():
        if rt._score_owed(handle, live) is not None:
            continue
        try:
            has = not rt.consequences.table.account(handle).voided
        except KeyError:
            has = False
        if has or not account:
            out.append(handle)
    return out


def test_invariant_a_release_never_drops_a_decision_still_owed_a_score(unreleased):
    """Violation attempts, one per clause of ``_score_owed``: each decision below is
    made to owe something, then released; every release is refused by name, and the
    boundary's own pass releases every other settled decision and none of these."""
    rt = unreleased
    plain = _candidates(rt)
    owned = [h for h in _candidates(rt, account=True) if h not in plain[:6]]
    assert len(plain) > 12 and len(owned) > 6
    pending = next(d.handle for d in rt.queue.outstanding())
    parent = plain[0]
    rt.decision_subjects[pending] = parent  # a retained, pending decision judges it
    noop, routed, balloted = plain[2], plain[3], plain[4]
    rt.noop_credits[noop] = {"router": "router:Tick", "due_tick": 10**9, "p": None,
                             "executed": None}
    rt.internal.append(Event("probe", EventKind.VERDICT, 0,
                             {"about_handle": routed, "evaluator_handle": routed}, "kernel"))
    rt.pending_votes.append({"handle": balloted})
    unanswered, holding, young, rejected = owned[:4]
    rt.consequences.unresolved_orders["probe"] = {"handle": unanswered, "coin": "BTC",
                                                  "reason": "external_unobservable"}
    table = rt.consequences.table
    lot = Lot(holding, "BTC", True, Fraction(1), Fraction(100), Fraction(0))
    accounts = tuple(replace(r, opened_at_tick=rt.ticks_consumed) if r.handle == young else r
                     for r in table.returns)
    order = LotOrder("probe-order", rejected, Fraction(0), Fraction(1), Fraction(1),
                     confirmed=Fraction(1))
    rt.consequences.table = replace(table, lots=(*table.lots, lot), returns=accounts,
                                    orders=(*table.orders, order))
    rt.events_log.append({"kind": str(EventKind.ORDER_REJECTED),
                          "payload": {"order_id": "probe-order"}})
    owed = {
        pending: "pending",
        parent: "parent or judged subject",
        noop: "live book", routed: "live book", balloted: "live book",
        unanswered: "consequence account is open",
        holding: "consequence account is open",
        young: "horizon has not passed",
        rejected: "forecast window reads an order",
    }
    live = rt._live_references()
    before = rt.queue.state()
    for handle, debt in owed.items():
        assert debt in (rt._score_owed(handle, live) or ""), (handle, debt)
        with pytest.raises(ValueError, match=debt):
            rt._release_one(handle, live)
    assert rt.queue.state() == before  # every refused release changed nothing
    released = rt._release_settled()
    assert released and not set(owed) & set(released)
    for handle in owed:
        assert rt.queue.get(handle) is not None
    for handle in released:
        with pytest.raises(Released):
            rt.queue.get(handle)


def test_a_judge_naming_a_released_handle_is_refused():
    rt = _world()
    rt.run()
    gone = next(h for h in (t.handle for t in rt.queue.tombstones()))
    judge, subject = rt.queue.retained()[-2:]
    with pytest.raises(Released) as released:
        rt.queue.get(gone)
    assert released.value.tombstone.status is not SettleStatus.PENDING
    event = Event("probe", EventKind.PRODUCER_RETURN, rt.clock.now_ns,
                  {"about_handle": subject}, "kernel")
    ret = Return(judge, {"about_handle": gone, "verdict": 0.5}, 0, "ok")
    assert rt._judged_event(event, judge, ret, predicts=True) is None
    refused = [i for i in rt.ledger._recovery_items() if i["kind"] == "return.refused"][-1]
    assert refused == {**refused, "handle": judge, "reason": RELEASED_REFUSAL,
                       "about_handle": gone}


class Crash(BaseException):
    """The process dies here: nothing after it runs, nothing catches it."""


def _final_queue(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    ledger = Ledger.open_read_only(path, manifest=manifest)
    snapshot = [i for i in ledger.items() if i["kind"] == "snapshot"][-1]
    queue = decode(checkpoint_state(ledger, snapshot)["kernel"]["queue"])
    return sorted(queue["decisions"]), sorted(queue["tombstones"]), queue["compacted"]


def _settlements(path):
    manifest = json.loads(load_manifest("scripted").canonical_json())
    return [json.dumps(i["return"], sort_keys=True, default=str)
            for i in Ledger.open_read_only(path, manifest=manifest).items()
            if i["kind"] == "decision.settle"]


@pytest.fixture(scope="module")
def whole(tmp_path_factory):
    path = tmp_path_factory.mktemp("whole") / "w.jsonl"
    summary = _world(path=path).run()
    return _plain(summary), _final_queue(path), _settlements(path)


@pytest.mark.parametrize("where,n", [("release", 1), ("release", 40), ("release", 400),
                                     ("inbox", 3)])
def test_a_crash_in_a_release_pass_resumes_to_the_uninterrupted_run(whole, tmp_path,
                                                                     monkeypatch, where, n):
    """Die in the middle of a boundary's release pass (after the Nth decision released)
    or between that pass and the inbox's, before the checkpoint names either: the
    resume replays from the previous checkpoint and releases exactly what the
    uninterrupted run released."""
    seen = [0]
    target = "_release_one" if where == "release" else "_release_inbox"
    original = getattr(settled.SettledMixin, target)

    def dying(self, *args, **kwargs):
        seen[0] += 1
        if seen[0] == n:
            raise Crash
        return original(self, *args, **kwargs)

    monkeypatch.setattr(settled.SettledMixin, target, dying)
    path = tmp_path / "w.jsonl"
    with pytest.raises(Crash):
        _world(path=path).run()
    monkeypatch.undo()
    summary = resume_world(load_manifest("scripted"), str(path))
    expected_summary, expected_queue, expected_settlements = whole
    summary = _plain(summary)
    summary["stats"]["resumes"] = expected_summary["stats"]["resumes"]
    assert summary == expected_summary
    assert _final_queue(path) == expected_queue
    assert _settlements(path) == expected_settlements


# ---- venue-confirmed terminal orders, venue books, seat deliveries, legacy inbox ----------


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

    rt = _world(40)
    rt.run()
    rows = [row for rows in rt.outcomes.items.values() for row in rows]
    assert rows
    for row in rows:
        row.pop("tick")
    state = runtime_state(rt)
    twin = Runtime(rt.m, ledger_path=None, **state["config"])
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
