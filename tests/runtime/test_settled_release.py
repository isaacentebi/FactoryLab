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


def _world(events=EVENTS, path=None, **kwargs):
    return Runtime(load_manifest("scripted"), events=events, seed=1, initial_balance_micro=None,
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


def _record_eligibility(monkeypatch, read):
    rows = []
    process = Runtime._process_event

    def recorded(self, ev):
        result = process(self, ev)
        rows.append(read(self))
        return result

    monkeypatch.setattr(Runtime, "_process_event", recorded)
    return rows


def test_the_eligibility_tally_equals_the_scan_on_every_event(monkeypatch):
    """Committee eligibility is a running tally kept at settlement (wave 17b). Side by
    side over a scripted world: the tally with release live equals, on every event,
    the scan over every decision and account of the same world run without release."""
    tallied = _record_eligibility(monkeypatch, lambda rt: rt._committee_eligible())
    live = _world()
    live.run()
    monkeypatch.undo()
    assert _released(live) > 0
    _no_release(monkeypatch)
    scanned = _record_eligibility(
        monkeypatch, lambda rt: (rt._committee_eligible(), rt._committee_eligible_scan()))
    kept = _world()
    kept.run()
    assert len(tallied) == len(scanned) == kept.n
    for n, (tally, (kept_tally, scan)) in enumerate(zip(tallied, scanned, strict=True), 1):
        assert tally == kept_tally == scan, n
    assert any(tallied), "the world never qualified a seat: the comparison is empty"


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
    order = LotOrder("probe-order", rejected, Fraction(0), Fraction(1), Fraction(1))
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
