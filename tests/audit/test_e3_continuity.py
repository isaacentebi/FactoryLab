"""Edition 3 C1: the acceptance for working state and the outcome inbox.

The plan's acceptance, verbatim: a seat forms a hypothesis in its state at wake
one, returns three more times, the runtime is checkpointed and restored from the
diary in a fresh process-equivalent, the consequence settles, and the seat's next
request carries both the hypothesis (head unchanged) and the outcome addressed to
that exact handle. The matched control, with state disabled, shows neither.

Three returns is the number that mattered: the deque this replaces held exactly
three, so the fourth return used to evict the first and the settlement had
nowhere to land.

Around it: the size limits, the acknowledgement cursor, the refusal, the
scoping of ``artifact.get`` and ``outcome.get``. Nothing here reaches a network,
a credential or a venue beyond the fake.
"""

import json
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import PRIVATE_REFUSAL
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.continuity import (
    HARD_STATE_BYTES,
    INLINE_OUTCOMES,
    SOFT_STATE_BYTES,
    OutcomeInbox,
    WorkingState,
)
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_connectors import decision
from tests.runtime.test_loop import (
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)

SEAT = "seed-decider"
HYPOTHESIS = {"hypothesis": "funding flips sign before the mid does",
              "checked_at_wake": 1, "method": "watch funding, not price"}


class Continuous(ScriptedProvider):
    """A producer that writes its hypothesis on its first wake and holds afterwards.

    ``keeps_state`` is the control switch: the matched run answers exactly the
    same way and simply never carries the field, so the comparison is the
    contract and not a different policy.
    """

    def __init__(self, *, keeps_state: bool = True) -> None:
        super().__init__()
        self.keeps_state = keeps_state
        self.wakes = 0
        self.seen_state: list[object] = []
        self.seen_outcomes: list[dict] = []

    def _produce(self, desc, inputs):
        self.wakes += 1
        self.seen_state.append(inputs.get("your_state"))
        self.seen_outcomes.append(inputs.get("unread_outcomes"))
        reply = {"action": "hold", "rationale": f"wake {self.wakes}", "payoff": 0.1}
        if self.wakes == 1 and self.keeps_state:
            reply["working_state"] = dict(HYPOTHESIS)
        return reply


def _world(path, provider):
    return Runtime(load_manifest("scripted"), events=0, seed=1,
                   initial_balance_micro=100_000_000, ledger_path=str(path), drip=False,
                   router_gamma=0.2, provider=provider, exchange=FakeExchange())


def _wake(rt, seat=SEAT):
    """One producer return for ``seat``, exactly as the router's dispatch makes one."""
    from tests.runtime.test_loop import _consequence_decision

    rt.n += 1
    handle = _consequence_decision(rt, seat, "verdict")
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=seat), rt.queue.get(handle).deadline_ns)
    event = next(e for e in rt.internal if e.kind == EventKind.PRODUCER_RETURN
                 and e.payload["about_handle"] == handle)
    rt._settle_due_forecasts()
    return handle, event


def _twin(rt, root):
    """Restore this runtime's checkpoint into a fresh process-equivalent Runtime.

    The twin's own diary starts empty, so its decision counter would hand out
    handles the restored queue already holds. Continuing it past them is a
    harness detail of restoring into a new diary, not part of the contract.
    """
    state = runtime_state(rt)
    twin = Runtime(load_manifest("scripted"), ledger_path=None, provider=rt.provider.target,
                   exchange=FakeExchange(), **state["config"])
    restore_runtime(twin, state)
    twin.artifacts.root = root
    issued = [int(h.split("-")[-1]) for h in twin.queue.state()["decisions"]
              if h.startswith("decision-") and h.split("-")[-1].isdigit()]
    twin.ledger.ledger._Ledger__decision_count = max(issued, default=-1) + 1
    return twin


# ---- the acceptance ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def accepted(tmp_path_factory):
    """The whole acceptance, run once: state kept, and the matched control beside it."""
    out = {}
    for name, keeps in (("kept", True), ("control", False)):
        path = tmp_path_factory.mktemp(name) / "world.jsonl"
        provider = Continuous(keeps_state=keeps)
        rt = _world(path, provider)
        first, _ = _wake(rt)                       # wake 1: the hypothesis is formed
        head_after_first = dict(rt.working_state.heads.get(SEAT) or {})
        for _ in range(3):                         # three more returns: the deque's capacity
            _wake(rt)
        twin = _twin(rt, path.with_suffix(".artifacts"))
        # The consequence of the first decision settles in the restored world.
        judge = _consequence_judge(twin, next(
            e for e in twin.internal if e.kind == EventKind.PRODUCER_RETURN
            and e.payload["about_handle"] == first), "eval-a")
        twin._settle_due_forecasts()
        before = provider.wakes
        _wake(twin)                                # the next request of that seat
        out[name] = {
            "first": first, "judge": judge, "provider": provider,
            "head_after_first": head_after_first,
            "head_now": dict(twin.working_state.heads.get(SEAT) or {}),
            "state_on_next_request": provider.seen_state[before],
            "outcomes_on_next_request": provider.seen_outcomes[before],
            "wakes": provider.wakes,
        }
    return out


def test_acceptance_the_hypothesis_survives_three_returns_and_a_restore(accepted):
    kept = accepted["kept"]
    assert kept["wakes"] == 5                      # wake 1, three more, one after restore
    rendered = kept["state_on_next_request"]
    assert rendered["state"] == HYPOTHESIS         # verbatim, the head unchanged
    assert rendered["sha"] == kept["head_after_first"]["sha"] == kept["head_now"]["sha"]
    assert rendered["bytes"] == len(json.dumps(HYPOTHESIS, sort_keys=True).encode())


def test_acceptance_the_outcome_is_addressed_to_that_exact_handle(accepted):
    kept = accepted["kept"]
    unread = kept["outcomes_on_next_request"]
    assert unread["count"] >= 1
    addressed = [i for i in unread["items"] if i["handle"] == kept["first"]]
    assert addressed, "the settlement reached the seat at the handle it decided on"
    assert all(i["said"]["rationale"] == "wake 1" for i in addressed)  # what it said then
    assert all(i["evidence"] is not None for i in addressed)
    facts = {key for i in addressed for key in i["outcome"]}
    # Both consequences of that one decision land on it: the verdict its judge gave,
    # and whether it paid off, with the money attached.
    assert "verdict" in facts and "return_paid_off" in facts
    assert any(i["outcome"].get("net_micro") is not None for i in addressed)


def test_acceptance_the_matched_control_shows_neither_state_nor_hypothesis(accepted):
    control = accepted["control"]
    assert control["wakes"] == 5
    assert control["head_now"] == {} and control["state_on_next_request"] is None
    # The control is matched, not crippled: its inbox still works, and only the
    # hypothesis is missing. The difference the acceptance names is the state.
    assert control["outcomes_on_next_request"]["count"] >= 1


# ---- the size limits --------------------------------------------------------------------

def test_a_state_over_the_soft_allowance_is_kept_and_the_rent_is_what_it_is():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    big = {"notes": "x" * (SOFT_STATE_BYTES * 2)}
    head = rt.working_state.put(SEAT, big)
    assert head["bytes"] > SOFT_STATE_BYTES
    assert rt.working_state.render(SEAT)["state"] == big
    put = [i for i in rt.ledger._recovery_items() if i["kind"] == "state.put"][-1]
    assert put["over_soft"] is True


def test_a_state_over_the_hard_limit_is_refused_and_the_head_is_unchanged():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    rt.working_state.put(SEAT, {"keep": "this"})
    before = dict(rt.working_state.heads[SEAT])
    with pytest.raises(ValueError):
        rt.working_state.put(SEAT, {"notes": "x" * (HARD_STATE_BYTES + 1)})
    assert rt.working_state.heads[SEAT] == before
    assert rt.working_state.render(SEAT)["state"] == {"keep": "this"}


def test_a_refused_state_field_is_ledgered_and_costs_the_seat_its_head_nothing():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    rt.working_state.put(SEAT, {"keep": "this"})
    handle = decision(rt, SEAT)
    from factorylab.cortex.request import Return

    rt._apply_continuity(SEAT, handle, Return(
        handle, {"action": "hold", "working_state": {"n": "x" * (HARD_STATE_BYTES + 1)}}, 0, "ok"))
    refusal = [i for i in rt.ledger._recovery_items() if i["kind"] == "state.refused"]
    assert refusal and refusal[-1]["assembly_id"] == SEAT
    assert rt.working_state.render(SEAT)["state"] == {"keep": "this"}


def test_a_working_state_that_is_not_a_json_object_is_refused():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    for bad in ([1, 2], "text", None, {"x": float("nan")}):
        with pytest.raises(ValueError):
            rt.working_state.put(SEAT, bad)
    assert rt.working_state.head(SEAT) is None


def test_a_manifest_may_seed_a_seat_its_first_head(tmp_path):
    from dataclasses import replace

    manifest = load_manifest("scripted")
    seeded = replace(manifest, assemblies=tuple(
        replace(a, initial_state={"lens": "mechanism"}) if a.id == SEAT else a
        for a in manifest.assemblies))
    assert seeded.manifest_hash() != manifest.manifest_hash()
    rt = Runtime(seeded, events=0, seed=1, initial_balance_micro=100_000_000,
                 ledger_path=None, drip=False, router_gamma=0.2,
                 provider=ScriptedProvider(), exchange=FakeExchange())
    assert rt.working_state.render(SEAT)["state"] == {"lens": "mechanism"}
    assert rt.working_state.head("eval-a") is None


def test_the_initial_state_field_preserves_the_hash_of_every_manifest_without_one():
    manifest = load_manifest("scripted")
    payload = json.loads(manifest.canonical_json())
    assert all("initial_state" not in a for a in payload["assemblies"])


# ---- the inbox: cursor, bound, outcome.get ----------------------------------------------

def _inbox(rt):
    return rt.outcomes


def test_the_cursor_advances_only_to_a_handle_the_seat_was_addressed_on():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    inbox = _inbox(rt)
    for n in range(3):
        inbox.record_said(SEAT, f"h{n}", {"rationale": f"r{n}"})
        inbox.append(SEAT, handle=f"h{n}", outcome={"verdict": 1.0})
    assert inbox.unread(SEAT)["count"] == 3
    assert inbox.ack_through(SEAT, "nobody-said-that") is None
    assert inbox.unread(SEAT)["count"] == 3
    inbox.ack_through(SEAT, "h1")
    assert inbox.unread(SEAT)["count"] == 1
    assert [i["handle"] for i in inbox.unread(SEAT)["items"]] == ["h2"]
    # The cursor only advances: acknowledging an older item again un-reads nothing.
    inbox.ack_through(SEAT, "h0")
    assert inbox.unread(SEAT)["count"] == 1


def test_an_unacknowledged_item_stays_however_many_arrive_after_it():
    """Restated for the GPT-6 third reading (§3, §4: "a handle retrieves only its latest
    outcome", "acknowledging a handle can acknowledge unseen items").

    This test used to pin newest-first delivery. Newest-first plus a cursor-based
    acknowledgement is how an unread item is acknowledged without ever being shown: the
    seat sees the newest eight, acknowledges the newest, and the cursor passes over the
    older ones it never read. Delivery is now oldest-first, and every delivered item
    carries an exact ``outcome_id`` the seat can acknowledge by itself.
    """
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    inbox = _inbox(rt)
    for n in range(INLINE_OUTCOMES + 5):
        inbox.append(SEAT, handle=f"h{n}", outcome={"verdict": 0.5})
    unread = inbox.unread(SEAT)
    assert unread["count"] == INLINE_OUTCOMES + 5          # every one counted
    assert len(unread["items"]) == INLINE_OUTCOMES         # the oldest eight inline
    assert unread["items"][0]["handle"] == "h0"            # oldest first
    assert unread["items"][0]["outcome_id"] == "outcome:1"
    # The newest is not inline and is not lost: outcome.get still returns it.
    assert inbox.get(SEAT, f"h{INLINE_OUTCOMES + 4}")["handle"] == f"h{INLINE_OUTCOMES + 4}"


def test_outcome_get_is_a_free_ledgered_kernel_read_of_the_seats_own_inbox():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    rt.outcomes.record_said(SEAT, "h0", {"rationale": "because funding flipped"})
    rt.outcomes.append(SEAT, handle="h0", outcome={"return_paid_off": 1}, delta_micro=125,
                       evidence=7)
    handle = decision(rt, SEAT)
    before = rt.wallet.balance
    result, cost = rt._run_tool(SEAT, handle, {"tool": "outcome.get", "args": {"handle": "h0"}})
    assert cost == 0 and rt.wallet.balance == before
    assert result["said"]["rationale"] == "because funding flipped"
    assert result["delta_micro"] == 125 and result["evidence"] == 7
    assert result["read"] is False
    row = [i for i in rt.ledger._recovery_items() if i["kind"] == "outcome.get"][-1]
    assert row["assembly_id"] == SEAT and row["found"] is True


def test_one_seat_cannot_read_another_seats_outcome():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    rt.outcomes.append(SEAT, handle="h0", outcome={"verdict": 1.0})
    handle = decision(rt, "eval-a")
    result, _ = rt._run_tool("eval-a", handle, {"tool": "outcome.get", "args": {"handle": "h0"}})
    assert "error" in result and "handle" not in result


def test_an_answers_ack_through_advances_the_cursor():
    """R3-F tightened this: an ack never reaches past what the seat was delivered, so the
    item is rendered onto a request (``unread``) first, exactly as the loop renders it."""
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    from factorylab.cortex.request import Return

    rt.outcomes.append(SEAT, handle="h0", outcome={"verdict": 1.0})
    assert rt.outcomes.unread(SEAT)["count"] == 1  # the request carried it
    handle = decision(rt, SEAT)
    rt._apply_continuity(SEAT, handle, Return(
        handle, {"action": "hold", "ack_through": "h0"}, 0, "ok"))
    assert rt.outcomes.unread(SEAT)["count"] == 0
    assert rt.outcomes.get(SEAT, "h0")["read"] is True


# ---- scoping ----------------------------------------------------------------------------

def test_a_seat_reads_its_own_state_and_a_stranger_is_refused_artifact_private():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    sha = rt.working_state.put(SEAT, {"mine": True})["sha"]
    own = rt._run_tool(SEAT, decision(rt, SEAT), {"tool": "artifact.get", "args": {"sha": sha}})[0]
    assert json.loads(own["text"]) == {"mine": True}
    other = rt._run_tool("eval-a", decision(rt, "eval-a"),
                         {"tool": "artifact.get", "args": {"sha": sha}})[0]
    assert other == {"sha": sha, "error": PRIVATE_REFUSAL}
    row = [i for i in rt.ledger._recovery_items() if i["kind"] == "artifact.get"][-1]
    assert row["found"] is False and row["reason"] == PRIVATE_REFUSAL


def test_a_published_artifact_is_readable_by_anyone():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    sha = rt.artifacts.put(b"shared finding", owner=SEAT, kind="note", public=True)
    result = rt._run_tool("eval-a", decision(rt, "eval-a"),
                          {"tool": "artifact.get", "args": {"sha": sha}})[0]
    assert result["text"] == "shared finding"


def test_the_archive_lists_its_rows_for_the_directory_w4_builds():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    sha = rt.artifacts.put(b"row", owner=SEAT, kind="note", public=True)
    rows = {row[0]: row for row in rt.artifacts.entries()}
    assert rows[sha] == (sha, SEAT, True, 3, rows[sha][4])


# ---- the units, in isolation ------------------------------------------------------------

class _FakeLedger:
    def __init__(self):
        self.items = []

    def append(self, item):
        self.items.append(item)
        return len(self.items)


def _bare():
    from factorylab.kernel.artifacts import ArtifactStore

    ledger, tick = _FakeLedger(), iter(range(1, 10_000))
    store = ArtifactStore(ledger, root=None, clock_ns=lambda: next(tick))
    return store, ledger


def test_working_state_is_content_addressed_and_idempotent_by_content():
    store, _ledger = _bare()
    state = WorkingState(store, _FakeLedger(), lambda: 1)
    first = state.put(SEAT, {"a": 1})
    second = state.put(SEAT, {"a": 1})
    assert first["sha"] == second["sha"]
    assert state.render(SEAT) == {"sha": first["sha"], "bytes": first["bytes"], "state": {"a": 1}}


def test_an_inbox_body_that_lost_its_bytes_fails_closed_rather_than_reporting_no_facts():
    """Restated for the GPT-6 third reading (§3: "missing blobs become 'no state'").

    This test used to pin the fail-open reading: an item whose bytes were gone was
    reported as absent, so an inbox that had lost its storage was indistinguishable from
    an inbox with nothing in it, and the seat learned a falsehood about its own world.
    The repair raises instead: an addressed outcome that exists and cannot be read is an
    unavailable fact, not the absence of one.
    """
    store, _ledger = _bare()
    inbox = OutcomeInbox(store, _FakeLedger(), lambda: 1)
    record = inbox.append(SEAT, handle="h0", outcome={"verdict": 1.0})
    store._memory.pop(record["sha"])
    with pytest.raises(RuntimeError, match="unavailable"):
        inbox.body(record["sha"])
    with pytest.raises(RuntimeError, match="unavailable"):
        inbox.unread(SEAT)
    with pytest.raises(RuntimeError, match="unavailable"):
        inbox.get(SEAT, "h0")
    # The item itself is still counted and addressed: only its body is unreadable.
    assert inbox.items[SEAT][0]["handle"] == "h0"


def test_the_said_record_keeps_every_handle_a_delayed_consequence_can_reference():
    """Restated for the GPT-6 third reading (§3: "MAX_SAID can evict decision-linked
    material before a delayed consequence").

    This test used to pin the bound: the oldest ``said`` entry was dropped once the cap
    was reached. Unrelated traffic could therefore evict the rationale of a decision
    whose consequence had not settled yet, and the seat was then told the outcome of a
    decision it could no longer be shown the reasons for. Eviction is gone until an
    archive-backed collector can prove no open consequence references a handle.
    """
    from factorylab.runtime.continuity import MAX_SAID

    inbox = OutcomeInbox(None, _FakeLedger(), lambda: 1)
    for n in range(MAX_SAID + 10):
        inbox.record_said(SEAT, f"h{n}", {"rationale": str(n)})
    assert len(inbox.said) == MAX_SAID + 10
    assert inbox.seat_of("h0") == SEAT and inbox.seat_of(f"h{MAX_SAID + 9}") == SEAT


# ---- the deque is gone ------------------------------------------------------------------

def test_no_runtime_carries_the_three_entry_memory_deque_any_more():
    rt = _consequence_runtime(provider=ScriptedProvider(), exchange=FakeExchange())
    assert not hasattr(rt, "memory")
    about, event = _consequence_produce(rt, "seed-decider")
    _consequence_judge(rt, event, "eval-a")
    # What the deque used to carry now arrives addressed, and outlives three returns.
    def verdicts():
        return [i for i in rt.outcomes.unread("seed-decider")["items"]
                if i["handle"] == about and "verdict" in i["outcome"]]

    assert verdicts()
    for _ in range(4):
        _consequence_produce(rt, "seed-decider")
    assert verdicts(), "the fourth return no longer evicts the first"
    assert rt.outcomes.get("seed-decider", about)["handle"] == about
