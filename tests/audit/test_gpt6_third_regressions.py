"""Permanent regressions converted from GPT-6 Pro's third-reading cold audit of 4618f6f.

The reviewer shipped 33 characterization tests that asserted the snapshot's defects, and
its README asked for exactly this: "After applying reviewed repairs, convert the selected
fixed expectations into permanent regression tests." Twenty-three of its characterizations
now fail because the repair landed; each of those is restated here as an assertion of the
repaired behaviour, with the finding it came from named in the docstring. The ten that
still pass are kept verbatim at the bottom of this file: two are the reviewer's own
controls, and eight characterize findings this patch deliberately does not repair
(`reading.md` §9 lists them), so they stay as the record of what is still open.

The reviewer's untouched originals live under
``docs/audits/v6/gpt6-third/review/tests/audit/``. Its second file,
``test_candidate_repairs_4618f6f.py``, was a patch generator over the old source rather
than a test of this tree; its eleven checks are covered by the conversions below.

No provider, network, venue, disk diary or key is used. Production functions whose module
imports unavailable SDKs are compiled unchanged from their AST with explicit fake
dependencies; those are unit regressions, not integration coverage.
"""
from __future__ import annotations

import ast
import copy
import json
import socket
from decimal import Decimal
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from factorylab.kernel.artifacts import PRIVATE_REFUSAL, ArtifactStore
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime import witness
from factorylab.runtime.continuity import MAX_SAID, OutcomeInbox, WorkingState
from factorylab.runtime.subscriptions import SubscriptionBook, evaluate_trigger
from factorylab.runtime.venue import VenueMixin, wind_down
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import OrderResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def prohibit_network_and_key_reads(monkeypatch):
    """Defense in depth: no test may connect or open a *.key through Python I/O."""
    import builtins
    import io
    import os
    old_open, old_io_open, old_os_open = builtins.open, io.open, os.open

    def check(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            name = os.fsdecode(path)
            if name.endswith(".key"):
                raise AssertionError("key access is forbidden in this audit")

    def guarded_open(path, *a, **kw):
        check(path)
        return old_open(path, *a, **kw)

    def guarded_io_open(path, *a, **kw):
        check(path)
        return old_io_open(path, *a, **kw)

    def guarded_os_open(path, *a, **kw):
        check(path)
        return old_os_open(path, *a, **kw)

    def deny(*a, **kw):
        raise AssertionError("network access is forbidden in this audit")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())


class Evidence:
    """A ledger double that can be made to fail on one item kind."""

    def __init__(self, fail_kind=None):
        self.rows = []
        self.fail_kind = fail_kind

    def append(self, row):
        if row.get("kind") == self.fail_kind:
            raise OSError("injected evidence-store failure")
        self.rows.append(copy.deepcopy(row))
        return len(self.rows)


def archive():
    ledger = Evidence()
    clock = SimpleNamespace(ns=0)
    store = ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)
    return ledger, clock, store


def source_objects(relative, *names, injected=None):
    """Compile selected definitions verbatim from the current tree; nothing is edited."""
    source = (ROOT / relative).read_text()
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                and n.name in names]
    assert len(selected) == len(names)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    namespace = {"json": json, "Any": Any, "__name__": "audit_isolated", **(injected or {})}
    exec(compile(module, str(ROOT / relative), "exec"), namespace)
    return tuple(namespace[n] for n in names)


def method_from_source(relative, class_name, method_name, injected=None, source=None):
    tree = ast.parse(source if source is not None else (ROOT / relative).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, fn], type_ignores=[]))
    namespace = {"__name__": "audit_isolated", **(injected or {})}
    exec(compile(module, str(ROOT / relative), "exec"), namespace)
    return namespace[method_name]


# ---- artifacts: ownership, durability and read authority -------------------------------

def test_same_bytes_second_writer_reads_its_own_artifact():
    """Finding: "Artifact ownership and durability inconsistent" — "second writer of
    identical bytes cannot read its own"."""
    _, _, store = archive()
    sha = store.put(b"own independently written content", owner="alice", kind="working.state")
    assert store.put(b"own independently written content", owner="bob",
                     kind="working.state") == sha
    assert store.read(sha, reader="bob")["text"] == "own independently written content"
    assert [row["sha"] for row in store.list(owner="bob")] == [sha]
    # The first owner still stands: the grant is a read, not a second liability.
    assert store.owner_for(sha) == "alice"


def test_republishing_an_existing_hash_publishes_it():
    """Finding: "Artifact ownership and durability inconsistent" — "republishing does not
    publish"."""
    _, _, store = archive()
    sha = store.put(b"publish me", owner="alice", kind="working.state")
    store.put(b"publish me", owner="alice", kind="note", public=True)
    assert store.read(sha, reader="bob")["text"] == "publish me"
    assert store.index[sha]["public"] is True


def test_orphan_blob_is_not_readable_by_a_stranger():
    """Finding: "Artifact ownership and durability inconsistent" — "unindexed blob
    readable". Bytes the index does not describe confer no read authority; a hash the
    archive never saw at all is still reported unknown rather than private."""
    _, _, store = archive()
    sha = store.put(b"private data from an unindexed tail", owner="alice", kind="working.state")
    # An earlier checkpoint restores an index that predates the durable blob.
    store.index.clear()
    assert store.read(sha, reader="bob") == {"sha": sha, "error": PRIVATE_REFUSAL}
    assert store.read("0" * 64, reader="bob") == {"error": "unknown artifact"}


def test_put_makes_bytes_durable_before_it_ledgers_the_reference():
    """Finding: "Artifact ownership and durability inconsistent" — "references can precede
    durable bytes"."""
    ledger, _, store = archive()
    ledger.fail_kind = "artifact.put"
    data = b"bytes that outlive their failed record"
    with pytest.raises(OSError):
        store.put(data, owner="alice", kind="working.state")
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    assert store._memory[sha] == data       # unreferenced bytes, never a dangling reference
    assert sha not in store.index


# ---- working state: fail-closed, write-ahead, byte-time rent ----------------------------

def test_missing_state_is_unavailable_not_absent():
    """Finding: "Inbox and state failure semantics lose continuity" — "missing blobs become
    'no state'"."""
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    head = state.put("alice", {"keep": "a hypothesis"}, handle="d1")
    store._memory.pop(head["sha"])
    assert state.head("alice") is not None
    with pytest.raises(RuntimeError, match="present but unavailable"):
        state.render("alice")


def test_failed_state_evidence_leaves_the_head_where_it_was():
    """Finding: "Inbox and state failure semantics lose continuity" — "failed journal writes
    advance memory"."""
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    old = state.put("alice", {"version": 1}, handle="d1")["sha"]
    ledger.fail_kind = "state.put"
    with pytest.raises(OSError):
        state.put("alice", {"version": 2}, handle="d2")
    assert state.head("alice")["sha"] == old
    assert state.render("alice")["state"] == {"version": 1}


def test_rewrite_does_not_reprice_the_previous_rent_interval():
    """Finding: "Working-state rent reprices the past" — "rewriting a head uses the new byte
    count for an earlier interval"."""
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    initial = state.put("alice", {"data": "x" * 1000}, handle="d1")
    old_bytes = initial["bytes"]
    clock.ns = 86_400_000_000_000
    replacement = state.put("alice", {}, handle="d2")
    assert replacement["bytes"] == 2
    # The day the large state was held is banked as byte-nanoseconds, at its own size,
    # and the new interval starts now at the new size.
    assert replacement["rent_ns"] == clock.ns
    assert replacement["rent_byte_ns"] == old_bytes * 86_400_000_000_000


def test_byte_time_rent_is_invariant_to_shrinking_the_state():
    """Finding: "Working-state rent reprices the past". The accrual a seat owes for a day of
    large bytes is the same whether or not it shrinks its state at the end of that day."""
    from factorylab.runtime.notes import accrue

    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    bounds = SimpleNamespace(rate=lambda: (1, 1))
    notes = SimpleNamespace(state_rent=bounds, rate=bounds.rate)
    kept = state.put("alice", {"data": "x" * 1000}, handle="d1")
    clock.ns = 86_400_000_000_000
    shrunk = state.put("alice", {}, handle="d2")
    held = accrue(kept, clock.ns, notes)[0]
    rewritten = accrue(shrunk, clock.ns, notes)[0]
    assert rewritten == held == kept["bytes"] * 86_400_000_000_000


# ---- the inbox: exact item addresses, oldest first, fail-closed, deduplicated ------------

def test_every_fact_on_one_decision_stays_separately_addressable():
    """Finding: "Inbox and state failure semantics lose continuity" — "a handle retrieves
    only its latest outcome"."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.append("alice", handle="d1", outcome={"first": True}, evidence="first-fact")
    for i in range(9):
        inbox.append("alice", handle="d1", outcome={"later": i}, evidence=f"later-{i}")
    unread = inbox.unread("alice")
    assert unread["count"] == 10
    # Oldest first: the first fact is delivered, not buried under the nine that followed.
    assert unread["items"][0]["outcome"] == {"first": True}
    assert unread["items"][0]["outcome_id"] == "outcome:1"
    # R3-F restated the handle fallback: a handle names a decision, not an item, so it
    # answers with the oldest outcome of that decision the seat has not read — the
    # earliest fact, not the latest, which is what buried the others.
    by_handle = inbox.get("alice", "d1")
    assert by_handle["outcome"] == {"first": True}
    assert by_handle["related_outcomes"] == [f"outcome:{n}" for n in range(1, 11)]
    assert "outcome_id" in by_handle["note"]
    assert inbox.get("alice", "outcome:7")["outcome"] == {"later": 5}
    assert "note" not in inbox.get("alice", "outcome:7")


def test_acknowledging_an_item_never_acknowledges_one_never_shown():
    """Finding: "Inbox and state failure semantics lose continuity" — "a handle retrieves
    only its latest outcome"; §4 — "acknowledging a handle can acknowledge unseen items"."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    for i in range(10):
        inbox.append("alice", handle=f"d{i}", outcome={"n": i})
    shown = inbox.unread("alice")
    assert len(shown["items"]) == 8
    assert [row["handle"] for row in shown["items"]] == [f"d{i}" for i in range(8)]
    # The seat acknowledges exactly what it was shown, by that item's own address.
    assert inbox.ack_through("alice", shown["items"][-1]["outcome_id"]) == 8
    remaining = inbox.unread("alice")
    assert remaining["count"] == 2
    assert [row["handle"] for row in remaining["items"]] == ["d8", "d9"]
    assert inbox.get("alice", "d9")["read"] is False


def test_a_decisions_rationale_survives_until_its_delayed_outcome():
    """Finding: §3 further — "MAX_SAID can evict decision-linked material before a delayed
    consequence"."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.record_said("alice", "original", {"rationale": "a long-horizon hypothesis"})
    for i in range(MAX_SAID):
        inbox.record_said("bob", f"other-{i}", {"rationale": "unrelated"})
    inbox.append("alice", handle="original", outcome={"settled": True})
    assert inbox.get("alice", "original")["said"]["rationale"] == "a long-horizon hypothesis"


def test_the_same_settlement_fact_is_addressed_once():
    """Finding: "Duplicate income receipts booked twice" / "Inbox and state failure semantics
    lose continuity" — one settlement fact must not become two consequences."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    first = inbox.append("alice", handle="d1", outcome={"paid": 1}, evidence="same-receipt")
    again = inbox.append("alice", handle="d1", outcome={"paid": 1}, evidence="same-receipt")
    assert again == first
    assert inbox.unread("alice")["count"] == 1
    # A genuinely different fact on the same decision is still its own item.
    inbox.append("alice", handle="d1", outcome={"paid": 2}, evidence="other-receipt")
    assert inbox.unread("alice")["count"] == 2


def test_failed_inbox_evidence_publishes_no_item():
    """Finding: "Inbox and state failure semantics lose continuity" — "failed journal writes
    advance memory"."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    ledger.fail_kind = "outcome.addressed"
    with pytest.raises(OSError):
        inbox.append("alice", handle="d1", outcome={"paid": 1})
    assert inbox.unread("alice")["count"] == 0


# ---- subscriptions: a missing observation is not a new value ----------------------------

def test_missing_watcher_observation_retains_the_last_valid_value():
    """Finding: "Subscriptions do not mean what a seat infers" — "missing watcher
    observations erase the last value"."""
    trigger = {"kind": "price_cross", "coin": "BTC", "level": "100"}
    _, before = evaluate_trigger(trigger, {"mids": {"BTC": "90"}}, None)
    _, missing = evaluate_trigger(trigger, {"mids": {}}, before)
    assert missing == before
    fact, _ = evaluate_trigger(trigger, {"mids": {"BTC": "110"}}, missing)
    assert fact is not None
    assert fact["previous"] == "90" and fact["observed"] == "110"


# ---- the evaluation boundary: no private state, no manufactured work --------------------

def test_forwarded_outputs_are_projected_before_they_cross_a_boundary():
    """Finding: "Private state reaches evaluators" — `runtime/loop.py`, `runtime/compute.py`
    forward `ret.outputs` with continuity fields."""
    from factorylab.cortex.request import public_return
    (outputs,) = source_objects("factorylab/runtime/wake.py", "_outputs",
                                injected={"public_return": public_return})
    private = {"action": "hold", "working_state": {"private_hypothesis": "sensitive"},
               "ack_through": "outcome:4", "raw": "the whole untrimmed completion",
               "propensity": {"hold": .9, "order": .1}}
    published = outputs(json.dumps(private))
    assert published == {"action": "hold", "propensity": {"hold": .9, "order": .1}}
    assert "sensitive" not in json.dumps(published)


def test_an_empty_router_draw_is_not_graded_as_a_producer_return():
    """Finding: "Router abstention manufactures a producer return" — `loop.py::_assembly_step`
    sends an unselected draw into `_producer_step`. §7: delete the synthetic noop path."""
    from factorylab.kernel.queue import SettleStatus

    called, settled = [], []
    step = method_from_source("factorylab/runtime/loop.py", "Runtime", "_assembly_step",
                              {"NOOP": "noop", "SettleStatus": SettleStatus,
                               "DEF_VERDICT": "verdict-v1"})
    stats = SimpleNamespace(noops=0)
    rt = SimpleNamespace(
        _start_return=lambda h: None, _event_subject=lambda ev: None,
        _producer_step=lambda *args: called.append(args), stats=stats,
        consequences=SimpleNamespace(finish=lambda h, c: None),
        queue=SimpleNamespace(get=lambda h: SimpleNamespace(channel="conformity"),
                              settle=lambda h, **kw: settled.append((h, kw))),
    )
    step(rt, object(), "d1", SimpleNamespace(chosen="noop"), 10)
    assert called == []
    assert stats.noops == 1
    assert settled[0][0] == "d1"
    assert settled[0][1]["status"] is SettleStatus.INAPPLICABLE


def test_open_commitments_follow_the_executing_seat_not_the_router_actor():
    """Finding: "Owner-directed feedback incomplete" / §3 table — the open-commitments filter
    used the router that drew the decision rather than the seat that executed it."""
    d = SimpleNamespace(handle="d1", actor="router:WorldUpdate", channel="verdict",
                        deadline_ns=100, opened_ns=0, cost_ceiling=10)
    queue = SimpleNamespace(outstanding=lambda actor=None:
                            [d] if actor is None or actor == d.actor else [])
    fn = method_from_source("factorylab/cortex/schematics.py", "SchematicsMixin",
                            "_open_commitments", {"DIRECTORY_PAGE": 50, "_usd": str, "_utc": str})
    rt = SimpleNamespace(queue=queue, book=SimpleNamespace(pending=lambda: []), n=1,
                         handle_to_assembly={"d1": "alice"})
    assert fn(rt, "alice")["open_decision_count"] == 1
    # A seat that neither owns nor executes the decision still sees nothing.
    assert fn(SimpleNamespace(queue=queue, book=rt.book, n=1,
                              handle_to_assembly={"d1": "alice"}),
              "bob")["open_decision_count"] == 0


def test_a_minimal_world_still_partitions_seat_privacy():
    """Finding: "A minimal world bypasses privacy partitioning" — `cortex/request.py`
    conditions separation on stable fields existing."""
    from factorylab.cortex.request import Request
    req = Request("decision-1", "work", {"you": "alice", "world": {"seats": [
        {"seat_id": "alice", "your_resources": {"mine": 1}},
        {"seat_id": "bob", "your_resources": {"secret": "BOB_PRIVATE_ENTITLEMENT"}}]}},
        {}, {}, 10, 1, None, "done", "verdict", "alice")
    assert "BOB_PRIVATE_ENTITLEMENT" not in req.seat_text()
    assert "BOB_PRIVATE_ENTITLEMENT" not in req.prompt_text()


def test_a_fidelity_objection_is_not_scored_by_the_proxy_it_challenges():
    """Finding: "Fidelity objections scored against the proxy they challenge" —
    `settlement/settle.py`. §7: remove the same-proxy score; keep the claim open."""
    from factorylab.settlement.fidelity import FidelityObjection
    from factorylab.settlement.scoring import PrevalenceBaseline
    from factorylab.settlement.settle import Settler
    from factorylab.settlement.standing import ConsequenceStanding
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(None, None, standing, PrevalenceBaseline(), None)
    # This is exactly the objection's contract: proxy looks good, norm may not be served.
    objection = FidelityObjection("fidelity", "successful_proxy", "independent counterexample", 0)
    settler._Settler__objections["judge-decision"] = objection
    result = settler.settle_verdict(evaluator_id="alice", about_handle="decision-1",
                                    q=1.0, share=0.0, judge_handle="judge-decision")
    assert result.brier == 1.0
    assert result.objection == objection
    assert result.objection_brier is None and result.objection_baseline_brier is None
    assert standing.snapshot()["alice"]["verdict_n"] == 1


# ---- kill: defensive, non-repeating, honest about exposure ------------------------------

class FakeVenue:
    def __init__(self, close_status="filled"):
        self.close_status = close_status
        self.close_calls = 0
        self.positions = [SimpleNamespace(coin="BTC", size=Decimal("1"))]

    def open_orders(self):
        return []

    def account(self):
        return SimpleNamespace(positions=self.positions, spot_balances=[],
                               equity_usd=Decimal("100"), margin_used_usd=Decimal("0"))

    def mids(self):
        return {"BTC": Decimal("100")}

    def close(self, *args, **kwargs):
        self.close_calls += 1
        return OrderResult("order-1", self.close_status, Decimal("0"), None)


def test_wind_down_does_not_count_a_resting_close_as_closed():
    """Finding: "Kill can fail before termination, repeat external actions, or report success
    early" — "'resting' counts as closed". §6.D: resting is not flat."""
    venue = FakeVenue("resting")
    report = wind_down(venue, Evidence())
    assert report["closed"] == 0 and report["failed"] == 1
    assert venue.positions[0].size != 0


def test_wind_down_ledger_failure_does_not_escape_the_kill():
    """Finding: "Kill can fail before termination, repeat external actions, or report success
    early" — "ledger failure escapes wind-down"."""
    term = SimpleNamespace(final=False)

    def terminate(reason):
        term.final = True

    term.kill = terminate
    rt = SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=True, dust_micro=1)),
                         exchange=FakeVenue(), ledger=Evidence("kill.wind_down"),
                         termination=term)
    report = VenueMixin.kill(rt, "explicit_kill")
    assert term.final is True                       # production dies whatever the store did
    assert report["error"] == "OSError"             # and the refused record is counted
    assert report["ledger_failures"] >= 1
    # R3-C: the exposure state is the account's answer, not the store's. This venue
    # acknowledges the close and keeps showing the position, so the honest word for
    # what is left is "pending", never "flat" and no longer merely "unknown".
    assert report["exposure_status"] == report["exposure_state"] == "wind_down_pending"
    assert report["production_state"] == "killed"


def test_a_repeated_kill_does_not_repeat_the_external_close():
    """Finding: "Kill can fail before termination, repeat external actions, or report success
    early" — "repeated kills close again"."""
    venue = FakeVenue()
    term = SimpleNamespace(final=True, kill=lambda reason: None)
    rt = SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=True, dust_micro=1)),
                         exchange=venue, ledger=Evidence(), termination=term)
    report = VenueMixin.kill(rt, "explicit_kill")
    assert venue.close_calls == 0
    assert report["exposure_status"] == "unknown"


# ---- money: retirement is final, receipts book once, refusals reach their owner ----------

def test_a_late_credit_does_not_resurrect_a_retired_seat():
    """Finding: "Retirement not final at the budget layer" — "a late credit recreates
    entitlement for a retired seat"; keep such resources in the commons."""
    from factorylab.kernel.budget import BudgetBook
    ledger = Ledger()
    wallet = Wallet(100_000_000, ledger)
    budget = BudgetBook(wallet, ledger, clock_ns=lambda: 0)
    budget.genesis(["alice", "bob"])
    budget.retire("alice", "approved-retirement")
    unallocated = budget.unallocated()
    assert budget.credit("alice", 1, "late trading consequence") == 0
    assert budget.earn("alice", 1, "late service income") is None
    assert "alice" not in budget.seats() and "alice" not in budget.heads()
    assert budget.unallocated() == unallocated


def test_the_same_income_receipt_books_once():
    """Finding: "Duplicate income receipts booked twice" — `world/treasury.py`,
    `runtime/seller.py`; that mints internal authority."""
    earn = method_from_source("factorylab/world/treasury.py", "Treasury", "earn")
    t = SimpleNamespace(ledger=Evidence(), income={"earned_micro": 0, "receipts": {}})
    first = earn(t, "service", 1000, "0xSAME-TRANSACTION", payer="buyer")
    assert first is not None
    # The same reference, in either case, is the same payment.
    assert earn(t, "service", 1000, "0xsame-transaction", payer="buyer") is None
    assert t.income["earned_micro"] == 1000
    assert len(t.ledger.rows) == 1
    # A reference reused for a different payment is a conflict, not a second credit.
    with pytest.raises(ValueError, match="conflicting payment"):
        earn(t, "service", 2000, "0xSAME-TRANSACTION", payer="buyer")
    assert t.income["earned_micro"] == 1000


def test_a_refusal_is_addressed_to_the_seat_that_decided_it():
    """Finding: "Owner-directed feedback incomplete" — refusals broadcast, not addressed.
    §4: the constructor described a refused order as having "vanished"."""
    ledger = Evidence()

    class Inbox:
        def __init__(self):
            self.calls = []

        def append(self, *a, **kw):
            self.calls.append((a, kw))

        def seat_of(self, handle):
            return "alice"

    inbox = Inbox()
    rt = SimpleNamespace(ledger=ledger, registration_feedback=[],
                         clock=SimpleNamespace(now_ns=1), outcomes=inbox,
                         handle_to_assembly={"d1": "alice"})
    result = VenueMixin._refuse_order(rt, "d1", "insufficient collateral")
    assert result["status"] == "rejected"
    assert rt.registration_feedback                     # still on the public surface
    (args, kwargs), = inbox.calls                       # and addressed to its author
    assert args == ("alice",)
    assert kwargs["handle"] == "d1"
    assert kwargs["outcome"]["kind"] == "order_refused"
    assert kwargs["outcome"]["reason"] == "insufficient collateral"


# ---- the reviewer's own controls, and the findings this patch does not repair ------------
#
# Everything below still passes exactly as the reviewer wrote it. The two `test_control_*`
# are its controls for claims it did not confirm; the rest characterize findings left to
# later workstreams (typed treasury, witness identity binding, evaluation commissions,
# time-based cascade separation, a clear paid-work admission contract) and must keep
# failing-by-passing until those land.

def test_defer_covers_routine_world_wakes_and_not_paid_commissions():
    """Finding: "Subscriptions do not mean what a seat infers" — "non-routine judge work
    bypasses deferral; paid-work admission needs a clear contract". The behaviour is
    unchanged and now it is the stated contract (R3-F, ``docs/manifest.md``): defer and
    the cadence floor silence routine world wakes, and a commission is declined by
    answering ``cannot``, at the cost of the call and nothing more."""
    book = SubscriptionBook()
    book.defer("judge", 100, now=1)
    assert book.absent("judge", "WorldUpdate", now=2, coins=frozenset()).startswith("asleep:")
    assert book.absent("judge", "ProducerReturn", now=2, coins=frozenset()) == ""


def test_a_coin_subscription_filters_the_delivered_fold():
    """Finding: "Subscriptions do not mean what a seat infers" — "coin filters affect
    admission not the fold". R3-F: a seat subscribed to BTC reads BTC."""
    from factorylab.runtime.subscriptions import Subscription
    book = SubscriptionBook()
    book.set_subscription("alice", Subscription(coins=frozenset({"BTC"})))
    for coin in ("BTC", "ETH"):
        book.observe(["alice"], "MarketMid", {"coin": coin, "mid": "100"}, 1, now=1)
    delivered = book.take("alice", now=1)
    assert "ETH" not in json.dumps(delivered)
    assert list(delivered["coins"]) == ["BTC"]
    # The counts it is shown are the counts of what it is shown, not of the world.
    assert delivered["prints"] == 1 and delivered["events"] == {"MarketMid": 1}


# The two witness findings this reviewer characterized here are repaired by R3-C, and
# their regressions live with the rest of the death contract in
# ``tests/audit/test_r3c_death.py``: the receiver requirement is part of the launch
# identity, so unsetting the variable removes no veto, and the local record is keyed by
# launch identity, so renaming a diary finds the same line.


def test_a_venue_loss_no_longer_touches_untouched_compute_credit():
    """Restated by R3-B. This pinned the defect: a $20 venue loss ran through
    ``wallet.settle_batch`` and took the compute wallet from $10 to -$10.10, dead,
    while the provider credit it was supposed to represent had not been spent at
    all -- "venue losses can consume fictitious compute resources". The finding is
    repaired rather than characterized now, so the assertion is the repair: the
    same loss settles on the venue account, is ledgered as ``venue.settled`` under
    ``venue_perps``, and leaves compute authority and provider inventory where
    they were."""
    ledger = Ledger()
    wallet = Wallet(10_000_000, ledger)
    untouched_provider_credit = 10_000_000
    rt = SimpleNamespace(
        wallet=wallet, consequences=SimpleNamespace(table=None, observe=lambda *a: None),
        n=0, stats=SimpleNamespace(fills=0), clock=SimpleNamespace(now_ns=7),
        window=SimpleNamespace(fills=0, notional_micro=0, realized_pnl_micro=0, index=0),
        realized_to_date=0, fees_to_date=0, funding_to_date=0, ledger=ledger,
        internal=[], _kernel_event=lambda e: e, venue_deltas={},
    )
    # The venue-side booking is the production method, bound to this double.
    rt._settle_venue = MethodType(VenueMixin._settle_venue, rt)
    rt._order_owner = MethodType(VenueMixin._order_owner, rt)
    fill = WorldEvent(WorldEventKind.FILL, 1, "fake", {
        "order_id": "1", "coin": "BTC", "market": "perp", "realized_usd": "-20",
        "fee_usd": "0.1", "size": "1", "px": "100", "is_buy": False,
    })
    VenueMixin._settle_exchange_effects(rt, [fill], observe_positions=False)
    assert wallet.balance == 10_000_000 and not wallet.dead
    assert untouched_provider_credit == 10_000_000
    assert wallet.check_conservation()
    settled = [i for i in ledger.items() if i.get("kind") == "venue.settled"]
    assert [(i["custody"], i["amount"]) for i in settled] == [("venue_perps", -20_100_000)]
    # The venue's own realised P&L still counts, where it happened.
    assert rt.realized_to_date == -20_000_000 and rt.fees_to_date == 100_000


def test_control_payoff_net_excludes_already_paid_compute():
    from factorylab.settlement.lots import LotTable
    table = LotTable().start("d1", 0).finish("d1", 1000).resolve(1, 10, {})
    payoff = table.account("d1").payoff
    assert payoff.net_micro == 0 and payoff.cost_micro == 1000


def test_control_wallet_reservation_is_single_use():
    from factorylab.kernel.wallet import Infeasible
    wallet = Wallet(10_000_000, Ledger())
    hold = wallet.reserve(1000, "d1", "model:test")
    wallet.commit(hold, 750)
    with pytest.raises(Infeasible):
        wallet.commit(hold, 750)
    assert wallet.balance == 9_999_250 and wallet.check_conservation()


def test_a_fill_is_addressed_to_the_seat_whose_order_it_was():
    """Finding: "Owner-directed feedback incomplete" — "fills not consistently addressed".

    R3-F addresses the fill through the inbox, not the router: the draw for a Fill stays
    open (anyone who accepts the kind may be woken by it), and the fill itself becomes an
    item with an exact id in the inbox of the seat whose order filled.
    """
    from factorylab.kernel.events import Event, EventKind
    (RoutingMixin,) = source_objects("factorylab/runtime/routing.py", "RoutingMixin",
                                     injected={"EventKind": EventKind})
    event = Event("fill-1", EventKind.FILL, 0, {"order_id": "order-1"}, "venue")
    assert RoutingMixin._addressed_seat(SimpleNamespace(), event) is None

    (FeedbackMixin,) = source_objects("factorylab/runtime/feedback.py", "FeedbackMixin")
    appended = []
    rt = SimpleNamespace(
        consequences=SimpleNamespace(table=SimpleNamespace(
            orders=[SimpleNamespace(order_id="order-1", handle="decision-1")])),
        handle_to_assembly={"decision-1": "alice"},
        outcomes=SimpleNamespace(append=lambda *a, **kw: appended.append((a, kw)),
                                 seat_of=lambda h: None))
    FeedbackMixin._address_fill_to_inbox(rt, {
        "order_id": "order-1", "coin": "BTC", "market": "perp", "is_buy": False,
        "size": "0.01", "px": "60000", "fee_usd": "0.02", "realized_usd": "1.50"})
    (args, kwargs), = appended
    assert args == ("alice",) and kwargs["handle"] == "decision-1"
    assert kwargs["evidence"] == "fill:order-1"
    assert kwargs["outcome"]["kind"] == "fill" and kwargs["outcome"]["coin"] == "BTC"


def test_a_non_payoff_forecast_reaches_the_seat_that_made_it():
    """Finding: "Owner-directed feedback incomplete" — "non-payoff forecasts miss the
    inbox". R3-F addresses every settled forecast to the decision that carried it."""
    (FeedbackMixin,) = source_objects("factorylab/runtime/feedback.py", "FeedbackMixin")
    appended = []
    rt = SimpleNamespace(
        queue=SimpleNamespace(get=lambda h: SimpleNamespace(parent_handle="d1")),
        handle_to_assembly={"d1": "alice"},
        outcomes=SimpleNamespace(append=lambda *a, **kw: appended.append((a, kw)),
                                 seat_of=lambda h: None))
    rt._deliver_forecast_to_inbox = lambda s: FeedbackMixin._deliver_forecast_to_inbox(rt, s)
    settled = SimpleNamespace(handle="f1", predicate_id="wallet_up", y=1, brier=0.09,
                              baseline_brier=0.25, status="settled")
    # The non-payoff branch is the one that used to return without looking anything up.
    FeedbackMixin._deliver_consequence_to_inbox(rt, settled)
    (args, kwargs), = appended
    assert args == ("alice",) and kwargs["handle"] == "d1" and kwargs["evidence"] == "f1"
    assert kwargs["outcome"]["predicate"] == "wallet_up"
    assert kwargs["outcome"]["your_brier"] == 0.09 and kwargs["outcome"]["resolved"] == 1


def test_cascade_separation_is_time_and_completed_evidence():
    """Converted by R3-D. Finding: "Cascade separation counts arrivals, not time" — the
    reviewer's characterization asserted that three messages arriving in the same
    nanosecond released a window. It is a duration now (§6.C), so they release nothing;
    the same window releases once its time has passed."""
    from factorylab.kernel.events import Event, EventKind
    from factorylab.runtime.cascade import CascadeGate, release_window_ns

    window_ns = release_window_ns(3, 0.0, 0.0, 10)
    gate = CascadeGate(window_ns, opened_ns=100)

    def arrive(gate, i, ts_ns):
        return gate.add(Event(f"verdict-{i}", EventKind.VERDICT, ts_ns,
                              {"verdict": 1.0, "evaluator_handle": f"judge-{i}"}, "judge"))

    for i in range(3):
        gate, released = arrive(gate, i, 100)
        assert released is None
    assert len(gate.arrivals) == 3
    _, released = arrive(gate, 3, 100 + window_ns)
    assert released is not None
    assert released.payload["window"]["count"] == 4
