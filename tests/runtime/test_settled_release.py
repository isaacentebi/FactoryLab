"""Settled decisions are released (wave 17b): the world-level proofs.

Chapter II §I.b: the return channel keeps "a managed queue of outstanding decisions
awaiting their reward"; §IV.c: a verdict "is consumed as a reward signal ... and then
discarded". A decision stays addressable exactly while a score is still owed to it
(``SettledMixin._score_owed``). Two worlds are run once for the whole module, one
with release and one without (``runs``), and every test reads them or a restored
twin of them:

* release changes nothing any reader sees: the two diaries and summaries are equal
  (regression caught: a release that drops something a reader still reads);
* the committee-eligibility tally equals the scan over every decision the world ever
  opened, on every event (regression caught: a settlement path the tally misses);
* each clause of the predicate is violated once and the release refused by name
  (regression caught: a release of a decision still owed a score);
* a judge naming a released handle is refused factually;
* a crash inside a release pass resumes to the uninterrupted run (regression caught:
  a release a replay does not repeat).

The unit-level rules (venue-confirmed orders, venue books, seat deliveries, legacy
inbox items) are in ``test_settled_release_units.py``, in the check tier.
"""

import json
from collections import Counter
from dataclasses import replace
from fractions import Fraction

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import Released, SettleStatus
from factorylab.runtime import settled
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import (
    checkpoint_state,
    decode,
    restore_runtime,
    resume_world,
    runtime_state,
)
from factorylab.runtime.settled import RELEASED_REFUSAL
from factorylab.runtime.shared import DEF_EVALUATION
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.lots import Lot, LotOrder

pytestmark = pytest.mark.gate

#: The smallest world whose decisions pass the release horizon and the charter's ten
#: margin windows, whose tombstones pass their compaction horizon, and whose committee
#: seats qualify (at 100 events the margin windows still hold nearly everything).
EVENTS = 150


def _world(events=EVENTS, path=None, seed=1):
    return Runtime(load_manifest("scripted"), events=events, seed=seed,
                   initial_balance_micro=None,
                   ledger_path=None if path is None else str(path), router_gamma=.1)


def _plain(summary):
    return {k: v for k, v in json.loads(json.dumps(summary, default=str)).items()
            if k not in ("ledger_path", "ledger")}


def _diary(rt):
    return [{k: v for k, v in i.items() if k not in ("hash", "prev_hash")}
            for i in rt.ledger._recovery_items() if i["kind"] != "snapshot"]


def _twin(rt):
    """A runtime restored from ``rt``'s state: mutable without touching the shared run."""
    state = runtime_state(rt)
    twin = Runtime(rt.m, ledger_path=None, **state["config"])
    restore_runtime(twin, state)
    return twin


# -- the scan the tally replaced, over every decision the world ever opened ---------------


class Shadow:
    """What each released decision held for the eligibility scan, kept aside by the
    test only, so the scan can still see every decision the world opened."""

    def __init__(self):
        self.decisions, self.histories, self.accounts, self.authors = {}, {}, {}, {}

    def keep(self, rt, handle):
        self.decisions[handle] = rt.queue.get(handle)
        self.histories[handle] = rt.queue.history(handle)
        try:
            self.accounts[handle] = rt.consequences.table.account(handle)
        except KeyError:
            pass
        if handle in rt.handle_to_assembly:
            self.authors[handle] = rt.handle_to_assembly[handle]


def full_scan(rt, shadow):
    """``_committee_eligible_scan`` as it was before the tally, over retained and
    released decisions alike."""
    from factorylab.charter.committee import experienced

    authors = {**shadow.authors, **rt.handle_to_assembly}
    decisions = {**shadow.decisions, **{h: rt.queue.get(h) for h in rt.queue.retained()}}

    def history(handle):
        return shadow.histories.get(handle) or rt.queue.history(handle)

    def independent(handle, assembly):
        original = decisions.get(handle)
        return (original is not None and original.parent_handle is None
                and original.propensity.source == "sampled"
                and original.propensity.chosen == assembly
                and original.propensity.learner_id == original.actor
                and original.channel != "policy")

    accounts = [*shadow.accounts.values(), *rt.consequences.table.returns]
    evidence = {(r.handle, authors.get(r.handle)) for r in accounts
                if r.payoff is not None and r.payoff.censored is None
                and decisions[r.handle].channel in ("verdict", "exposure")}
    for handle, decision in decisions.items():
        if any(r.status is SettleStatus.SETTLED and r.definition_version == DEF_EVALUATION
               for r in history(handle)):
            evidence.add((handle, authors.get(handle)))
        if (decision.channel == "consequence" and decision.status is SettleStatus.SETTLED
                and decision.parent_handle):
            evidence.add((decision.parent_handle,
                          authors.get(decision.parent_handle, decision.actor)))
    counted = Counter(a for h, a in evidence if a is not None and independent(h, a))
    return experienced({a.spec.id: a.spec.role for a in rt.assemblies.values()
                        if a.spec.id not in rt.retired_assemblies},
                       counted, rt.m.committee.min_settled)


def tally_against_scan(monkeypatch, rt):
    """Run ``rt`` with release live, recording (tally, full scan) after every event."""
    shadow, rows = Shadow(), []
    release_one = settled.SettledMixin._release_one
    process = Runtime._process_event

    def keeping(self, handle, live):
        shadow.keep(self, handle)
        return release_one(self, handle, live)

    def recorded(self, ev):
        result = process(self, ev)
        rows.append((self._committee_eligible(), full_scan(self, shadow)))
        return result

    monkeypatch.setattr(settled.SettledMixin, "_release_one", keeping)
    monkeypatch.setattr(Runtime, "_process_event", recorded)
    try:
        summary = rt.run()
    finally:
        monkeypatch.undo()
    return summary, rows


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """The module's two worlds, each run once: release live (on a diary file, with the
    eligibility tally and the full scan recorded after every event), and release off.

    Over 10 s (about 75 s, once per xdist worker that runs this module): the
    diary-equality and tally-equals-scan contracts need a world long enough to release
    (``EVENTS``), once with release and once without, the full scan beside every event
    of the first; every test of this module reads these two runs or a restored twin
    of them, and the crash probe compares against the first, so nothing reruns them.
    """
    patch = pytest.MonkeyPatch()
    path = tmp_path_factory.mktemp("released") / "w.jsonl"
    released = _world(path=path)
    summary, rows = tally_against_scan(patch, released)
    patch.setattr(settled.SettledMixin, "_release_settled",
                  lambda self: (self._drain_finalized(), [])[1])
    try:
        kept = _world()
        kept_summary = kept.run()
    finally:
        patch.undo()
    return {"released": released, "summary": summary, "rows": rows, "path": path,
            "kept": kept, "kept_summary": kept_summary}


def test_release_changes_nothing_any_reader_sees(runs):
    """With and without release, one manifest and seed write the same diary."""
    released, kept = runs["released"], runs["kept"]
    # Wave 16 keeps a decision until its outcome is fixed at the horizon on the venue's
    # clock and its margin window is read (D2; 40 of this world's ticks with the verdict
    # window), a longer tail of a 150-event run than before: release still frees a
    # large share, and the kept run retains every decision.
    total = sum(released.queue.released_counts().values())
    assert total > 0.8 * len(released.queue.retained())
    assert len(kept.queue.retained()) == total + len(released.queue.retained())
    assert _diary(released) == _diary(kept)
    assert _plain(runs["summary"]) == _plain(runs["kept_summary"])
    # Every order a released account placed was confirmed terminal by the venue's own
    # order status before its release.
    confirmed = {i["order_id"] for i in released.ledger._recovery_items()
                 if i["kind"] == "consequence.terminal"}
    orders = {row[0] for row in released.consequences.table.released_orders}
    assert orders and orders <= confirmed


def test_the_eligibility_tally_equals_the_scan_on_every_event(runs):
    """The tally, with release live, equals on every event the scan over every decision
    and account the world ever opened, the released ones included."""
    rows = runs["rows"]
    assert len(rows) == runs["released"].n
    for n, (tally, scan) in enumerate(rows, 1):
        assert tally == scan, n
    assert any(tally for tally, _scan in rows), "no seat ever qualified: nothing compared"


def test_an_older_checkpoint_rebuilds_the_tally_from_the_scan(runs):
    twin = _twin(runs["kept"])
    expected = twin._committee_eligible_scan()
    twin.eligibility_tally, twin.eligibility_evidence = {"stale": 99}, set()
    twin._rebuild_eligibility_tally()
    assert twin._committee_eligible() == expected


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


def test_invariant_a_release_never_drops_a_decision_still_owed_a_score(runs):
    """Violation attempts, one per clause of ``_score_owed``: each decision below is
    made to owe something, then released; every release is refused by name, and the
    boundary's own pass releases every other settled decision and none of these."""
    rt = _twin(runs["kept"])
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
    unanswered, holding, young, rejected, unconfirmed = owned[:5]
    rt.consequences.unresolved_orders["probe"] = {"handle": unanswered, "coin": "BTC",
                                                  "reason": "external_unobservable"}
    table = rt.consequences.table
    lot = Lot(holding, "BTC", True, Fraction(1), Fraction(100), Fraction(0))
    # Young on the venue's clock, where the horizon is counted (wave 16, D2).
    accounts = tuple(replace(r, opened_at_tick=rt.ticks_consumed, opened_at_ns=rt.clock.now_ns)
                     if r.handle == young else r
                     for r in table.returns)
    confirmed = LotOrder("probe-order", rejected, Fraction(0), Fraction(1), Fraction(1),
                         confirmed=Fraction(1))
    # Cancelled with nothing left, and any horizon passed, but never read back terminal.
    cancelled = LotOrder("probe-cancel", unconfirmed, Fraction(0), Fraction(1), Fraction(0))
    rt.consequences.table = replace(table, lots=(*table.lots, lot), returns=accounts,
                                    orders=(*table.orders, confirmed, cancelled))
    rt.events_log.append({"kind": str(EventKind.ORDER_REJECTED),
                          "payload": {"order_id": "probe-order"}})
    owed = {
        pending: "pending",
        parent: "parent or judged subject",
        noop: "live book", routed: "live book", balloted: "live book",
        unanswered: "consequence account is open",
        holding: "consequence account is open",
        unconfirmed: "consequence account is open",
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


def test_a_judge_naming_a_released_handle_is_refused(runs):
    rt = _twin(runs["released"])
    gone = rt.queue.tombstones()[0].handle
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


def test_a_crash_in_a_release_pass_resumes_to_the_uninterrupted_run(runs, tmp_path,
                                                                     monkeypatch):
    """Die in the middle of a boundary's release pass (after the 40th decision released),
    before the checkpoint names it: the resume replays from the previous checkpoint and
    releases exactly what the uninterrupted run (the module's released world) released.
    The other crash points around a boundary (the checkpoint's own write protocol, the
    archive's collection) are ``test_retained_state_crash.py``'s, whose worlds release.

    Over 10 s (about 45 s): a crash probe must run its world to the crash and resume it
    to the end; the uninterrupted reference is the module's shared run.
    """
    seen, n = [0], 40
    target = "_release_one"
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
    summary = _plain(resume_world(load_manifest("scripted"), str(path)))
    expected = _plain(runs["summary"])
    summary["stats"]["resumes"] = expected["stats"]["resumes"]
    assert summary == expected
    assert _final_queue(path) == _final_queue(runs["path"])
    assert _settlements(path) == _settlements(runs["path"])
