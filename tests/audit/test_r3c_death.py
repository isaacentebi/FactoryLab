"""R3-C, death and identity: the acceptance of `docs/plans/edition3-r3.md`, as tests.

    "partial fills, resting closes, unavailable mids, ledger failures, dropped acks,
    repeated kills and restarts: production stays dead, residual exposure is reported
    under the executor's authority, and neither renaming files nor unsetting a
    variable revives an identity."

Four guarantees, in that order: two states ledgered separately with a restart-safe
wind-down executor (`factorylab/runtime/winddown.py`); a witness bound to the launch
identity and fail-closed; a transactional restore; retirement final at the budget layer.

Nothing here reaches a network: every venue is a double, and the one receiver is a
hash of a string that is never contacted.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.kernel.budget import BudgetBook
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime import witness
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import (
    _RUNTIME_FIELDS,
    ResumeError,
    restore_runtime,
    resume_runtime,
    resume_world,
    runtime_state,
)
from factorylab.runtime.venue import VenueMixin, wind_down
from factorylab.runtime.wake import _Observatory
from factorylab.runtime.winddown import (
    DUST,
    FLAT,
    PENDING,
    UNKNOWN,
    WindDownExecutor,
    operation_id,
)
from factorylab.runtime.worlds import KillSpec, load_manifest
from factorylab.world.exchange import AccountState, OrderResult, Position, SpotBalance

NONCE = "a" * 32


@pytest.fixture(autouse=True)
def _fresh_witness(monkeypatch):
    """No receiver, no inherited note, no remembered kill: each test witnesses its own."""
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())
    witness.note_wind_down(wind_down=False, orders=0)


class Diary:
    """A ledger double that keeps what it took and can be made to refuse one kind."""

    def __init__(self, fail_kind: str | None = None, rows: list | None = None) -> None:
        self.rows: list[dict] = list(rows or [])
        self.fail_kind = fail_kind

    def append(self, row: dict) -> int:
        if row.get("kind") == self.fail_kind:
            raise OSError("injected diary failure")
        self.rows.append(json.loads(json.dumps(row, default=str)))
        return len(self.rows)

    def items(self):
        return list(self.rows)

    def kinds(self, kind: str) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == kind]


class Venue:
    """A venue double: every call is counted, and none of it leaves this process.

    ``place`` is present and raises, because the executor's authority does not
    include opening a position and a test must see it if that ever changes.
    """

    name = "double"

    def __init__(self, *, positions=(), balances=(), resting=(),
                 close_result=None, mids=None, mids_error=None, lookup_result=None) -> None:
        self.positions = list(positions)
        self.balances = list(balances)
        self.resting = list(resting)
        self.close_result = close_result or OrderResult("o1", "filled", Decimal("1"), None)
        self._mids = mids if mids is not None else {"BTC": Decimal("100"),
                                                    "PURR/USDC": Decimal("4.6")}
        self.mids_error = mids_error
        self.lookup_result = lookup_result
        self.calls: list[tuple] = []

    def open_orders(self):
        self.calls.append(("open_orders",))
        return list(self.resting)

    def account(self):
        self.calls.append(("account",))
        return AccountState(equity_usd=Decimal("100"), cash_usd=Decimal("100"),
                            positions=tuple(self.positions), margin_used_usd=Decimal("0"),
                            spot_balances=tuple(self.balances))

    def mids(self):
        self.calls.append(("mids",))
        if self.mids_error is not None:
            raise self.mids_error
        return dict(self._mids)

    def cancel(self, order_id, *, coin=None, client_id=None):
        self.calls.append(("cancel", order_id, coin, client_id))
        return {"status": "cancelled", "order_id": order_id}

    def close(self, coin, size=None, *, client_id=None, market="perp"):
        self.calls.append(("close", coin, size, client_id, market))
        return self.close_result

    def lookup(self, client_id, *, order_id=None):
        self.calls.append(("lookup", client_id))
        if self.lookup_result is None:
            return OrderResult(None, "uncertain", Decimal("0"), None)
        return self.lookup_result

    def place(self, order):  # pragma: no cover - reached only by a bug
        raise AssertionError("the wind-down executor may never open a position")

    def sent(self, name: str) -> list[tuple]:
        return [call for call in self.calls if call[0] == name]


def _runtime_double(venue, diary, *, wind=True, final=False):
    """The smallest thing ``VenueMixin.kill`` needs: a manifest's kill spec and a death."""
    term = SimpleNamespace(final=final, reason=None)

    def terminate(reason):
        term.final, term.reason = True, reason

    term.kill = terminate
    return SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=wind,
                                                                  dust_micro=1_000_000)),
                           exchange=venue, ledger=diary, termination=term,
                           launch_nonce=NONCE)


# ---- 1. two states, ledgered separately ---------------------------------------------------


def test_production_dies_before_the_first_operation_and_the_seal_comes_after_the_last():
    """Kill sets production killed first and irrevocably; the executor runs after it."""
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))])
    diary = Diary()
    rt = _runtime_double(venue, diary)
    report = VenueMixin.kill(rt, "explicit_kill:operator")

    kinds = [row["kind"] for row in diary.rows]
    assert kinds[0] == "kill.production"
    assert kinds.index("kill.production") < kinds.index("winddown.op")
    assert rt.production_state == "killed" and report["production_state"] == "killed"
    assert rt.termination.final and rt.termination.reason == "explicit_kill:operator"
    # The executor's own authority: reads, cancels and closes. Never a new position.
    assert not venue.sent("place")


def test_a_resting_close_is_not_flat_and_a_partial_fill_is_not_flat():
    """§6.D verbatim: "'resting' is not flat, partial fill is not flat"."""
    resting = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))],
                    close_result=OrderResult("o1", "resting", Decimal("0"), None))
    report = wind_down(resting, Diary(), launch_nonce=NONCE)
    assert report["closed"] == 0 and report["failed"] == 1
    assert report["exposure_state"] == PENDING

    # A fill for half the size, and half the position still at the venue.
    partial = Venue(positions=[Position("BTC", Decimal("0.5"), Decimal("100"))],
                    close_result=OrderResult("o1", "filled", Decimal("0.5"), Decimal("100")))
    report = wind_down(partial, Diary(), launch_nonce=NONCE)
    assert report["closed"] == 1                      # the venue did acknowledge a fill
    assert report["exposure_state"] == PENDING        # and the account is not flat
    assert report["residual"]["positions"] == [{"coin": "BTC", "size": "0.5"}]


def test_an_unavailable_mid_is_unknown_exposure_and_never_an_empty_account():
    """A price that cannot be read prices nothing: the state is unknown, not flat."""
    venue = Venue(balances=[SpotBalance("PURR", Decimal("100"), Decimal("100"))],
                  mids_error=OSError("no prices"))
    diary = Diary()
    report = wind_down(venue, diary, launch_nonce=NONCE)

    assert report["exposure_state"] == UNKNOWN
    assert {row["read"] for row in diary.rows if row.get("step") == "read_failed"} == {"mids"}
    # An unpriceable balance is exposure of unknown size, never dust.
    assert report["residual"]["dust"] == []
    assert [row["coin"] for row in report["residual"]["balances"]] == ["PURR/USDC"]


def test_dust_within_the_precommitted_bound_is_its_own_state():
    venue = Venue(balances=[SpotBalance("PURR", Decimal("0.1"), Decimal("0.1"))])
    report = wind_down(venue, Diary(), launch_nonce=NONCE)
    assert report["sold"] == 0 and report["exposure_state"] == DUST


def test_an_empty_account_after_the_passes_is_flat():
    venue = Venue()
    report = wind_down(venue, Diary(), launch_nonce=NONCE)
    assert report["exposure_state"] == FLAT and report["operations"] == 0


def test_a_diary_failure_during_the_wind_down_never_prevents_death():
    """Caught, counted, printed on stderr, carried to the witness line; the world dies."""
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))])
    diary = Diary("winddown.op")  # the record before each submission cannot be written
    rt = _runtime_double(venue, diary)
    report = VenueMixin.kill(rt, "explicit_kill:operator")

    assert rt.termination.final                       # first, and whatever the store did
    assert report["ledger_failures"] >= 1 and report["error"] == "OSError"
    assert venue.sent("close")                        # the operation still happened
    assert report["exposure_state"] == PENDING        # and the account still holds it
    assert witness._pending_wind_down["ledger_failures"] >= 1


def test_every_operation_has_a_durable_id_derived_from_the_launch_identity():
    """(launch_nonce, coin, market, side, sequence), and nothing that varies with time."""
    first = operation_id(NONCE, "BTC", "perp", "sell", 0)
    assert first == operation_id(NONCE, "BTC", "perp", "sell", 0)
    assert first != operation_id("b" * 32, "BTC", "perp", "sell", 0)
    assert first != operation_id(NONCE, "ETH", "perp", "sell", 0)
    assert first != operation_id(NONCE, "BTC", "spot", "sell", 0)
    assert first != operation_id(NONCE, "BTC", "perp", "buy", 0)
    assert first != operation_id(NONCE, "BTC", "perp", "sell", 1)

    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))])
    diary = Diary()
    wind_down(venue, diary, launch_nonce=NONCE)
    assert [row["op_id"] for row in diary.kinds("winddown.op")] == [first]
    # The identity is what the venue is told, so the adapter deduplicates it too.
    assert venue.sent("close")[0][3] == first


def test_a_repeated_kill_repeats_no_operation():
    """The same diary, a second kill: the result is known, so nothing is sent again."""
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))])
    diary = Diary()
    wind_down(venue, diary, launch_nonce=NONCE)
    assert len(venue.sent("close")) == 1

    again = wind_down(venue, diary, launch_nonce=NONCE)
    assert len(venue.sent("close")) == 1              # not twice
    assert again["orders"] == 0 and again["reconciled"] == 1
    assert [row["disposition"] for row in diary.kinds("winddown.op_result")][-1] == "known"
    # The position is still there, and the second kill says so rather than claiming flat.
    assert again["exposure_state"] == PENDING


def test_a_dropped_acknowledgement_is_reconciled_by_reading_and_never_resent():
    """A submitted operation with no recorded result: read the venue, do not act again."""
    op_id = operation_id(NONCE, "BTC", "perp", "sell", 0)
    submitted = [{"kind": "winddown.op", "op": "close", "op_id": op_id, "coin": "BTC",
                  "market": "perp", "side": "sell", "sequence": "0"}]
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))],
                  lookup_result=OrderResult("o1", "filled", Decimal("1"), Decimal("100")))
    diary = Diary(rows=submitted)

    report = wind_down(venue, diary, launch_nonce=NONCE)
    assert venue.sent("close") == []                  # never resubmitted
    assert [call[1] for call in venue.sent("lookup")] == [op_id]
    assert report["reconciled"] == 1 and report["orders"] == 0
    result = diary.kinds("winddown.op_result")[0]
    assert result["disposition"] == "reconciled" and result["result"]["status"] == "filled"


def test_a_restart_mid_wind_down_reconciles_the_diary_it_finds():
    """The process died between the two records; the next kill continues from them."""
    op_id = operation_id(NONCE, "BTC", "perp", "sell", 0)
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100")),
                             Position("ETH", Decimal("1"), Decimal("10"))])
    # First pass: the diary refuses the result records, exactly as a crash between
    # the submission and its answer would leave them.
    crashed = Diary("winddown.op_result")
    wind_down(venue, crashed, launch_nonce=NONCE)
    assert len(venue.sent("close")) == 2
    assert [row["op_id"] for row in crashed.kinds("winddown.op")][0] == op_id
    assert crashed.kinds("winddown.op_result") == []

    # Second pass over the same diary, with a store that works again.
    restarted = Diary(rows=crashed.rows)
    report = wind_down(venue, restarted, launch_nonce=NONCE)
    assert len(venue.sent("close")) == 2               # no operation repeated
    assert report["reconciled"] == 2 and report["orders"] == 0
    assert len(venue.sent("lookup")) == 2
    assert report["exposure_state"] == PENDING


def test_the_executor_can_only_reduce():
    """Its whole authority: cancel, reduce, close, reconcile. A short is bought back."""
    venue = Venue(positions=[Position("BTC", Decimal("-1"), Decimal("100"))],
                  resting=[{"order_id": "r1", "coin": "BTC", "side": "buy"}])
    diary = Diary()
    wind_down(venue, diary, launch_nonce=NONCE)
    closes = [row for row in diary.kinds("winddown.op") if row["op"] == "close"]
    assert [row["side"] for row in closes] == ["buy"]   # reduces a short, never adds to it
    assert [row["op"] for row in diary.kinds("winddown.op")] == ["cancel", "close"]
    assert not venue.sent("place")


def test_the_executor_cannot_reach_the_population():
    """It is constructed from an exchange and a diary, and imports neither runtime."""
    source = Path("factorylab/runtime/winddown.py").read_text()
    for forbidden in ("runtime.loop", "runtime.bootstrap", "cortex.assembly", "Runtime("):
        assert forbidden not in source
    executor = WindDownExecutor(Venue(), Diary(), launch_nonce=NONCE)
    assert set(vars(executor)) == {"exchange", "ledger", "launch_nonce", "dust_micro",
                                   "reader", "report", "_known", "_submitted",
                                   "_requested", "_owed", "_minimums"}


def test_the_witness_line_carries_both_states_and_the_operation_count(tmp_path):
    """A world that owed a wind-down: two lines, the second with what the account held."""
    manifest = replace(load_manifest("scripted"), kill=KillSpec(wind_down=True))
    path = tmp_path / "runs" / "w.jsonl"
    path.parent.mkdir()
    run_world(manifest, events=1, seed=1, ledger_path=str(path), kill_at_end=True)

    lines = [json.loads(raw) for raw in witness.witness_path(path).read_text().splitlines()]
    assert [line["stage"] for line in lines] == ["production_kill", "kill"]
    assert all(line["production_state"] == "killed" for line in lines)
    assert lines[0]["exposure_state"] == UNKNOWN      # nothing wound down yet
    assert lines[-1]["exposure_state"] == FLAT
    assert lines[-1]["wind_down"] is True
    assert lines[-1]["wind_down_operations"] == lines[-1]["wind_down_orders"]
    assert lines[-1]["wind_down_ledger_failures"] == 0


def test_a_second_kill_reads_the_real_diary_it_cannot_iterate(tmp_path):
    """A writable diary refuses to be iterated, so ``factorylab kill`` reopens it read-only."""
    from factorylab.runtime.cli import _winddown_reader

    m, path = _world(tmp_path)
    op = {"kind": "winddown.op", "op": "close",
          "op_id": operation_id(NONCE, "BTC", "perp", "sell", 0), "coin": "BTC",
          "market": "perp", "side": "sell", "sequence": "0"}
    writable = Ledger.reopen(path, manifest=json.loads(m.canonical_json()))
    writable.append(op)
    with pytest.raises(PermissionError):
        list(writable.items())               # the boundary moves under a reader

    # The reader the kill supplies sees it, so the executor knows the operation
    # was submitted and reconciles it instead of sending it again.
    read = _winddown_reader(m, str(path))()
    assert [row["op_id"] for row in read] == [op["op_id"]]
    venue = Venue(positions=[Position("BTC", Decimal("1"), Decimal("100"))],
                  lookup_result=OrderResult("o1", "filled", Decimal("1"), Decimal("100")))
    report = wind_down(venue, Diary(), launch_nonce=NONCE,
                       reader=_winddown_reader(m, str(path)))
    assert venue.sent("close") == [] and report["reconciled"] == 1


# ---- 2. the witness is identity-bound and fail-closed --------------------------------------


def _world(tmp_path, name="w"):
    m = load_manifest("scripted")
    path = tmp_path / "runs" / f"{name}.jsonl"
    path.parent.mkdir(exist_ok=True)
    run_world(m, events=1, seed=1, ledger_path=str(path))
    return m, path


def test_renaming_the_diary_does_not_revive_a_killed_identity(tmp_path, monkeypatch):
    """The local record is keyed by launch identity, not by a filename anyone can change."""
    m, path = _world(tmp_path)
    shutil.copytree(path.parent, tmp_path / "earlier")
    from factorylab.kernel.events import Bus
    from factorylab.kernel.ledger import LedgerLock
    from factorylab.kernel.termination import Termination

    with LedgerLock(str(path)):
        ledger = Ledger.reopen(path, manifest=json.loads(m.canonical_json()))
        Termination(ledger=ledger, bus=Bus(ledger)).kill("explicit_kill:operator")
        nonce = ledger.identity()["launch_nonce"]
    monkeypatch.setattr(witness, "_killed_here", set())  # only the files may answer

    renamed = tmp_path / "earlier" / "not-w.jsonl"
    (tmp_path / "earlier" / path.name).rename(renamed)
    Path(str(tmp_path / "earlier" / path.name) + ".key").rename(str(renamed) + ".key")
    # The file named from the diary's stem is not where this diary now looks...
    assert not witness.witness_path(renamed).exists()
    # ...and the identity-keyed file is, because nothing in its name came from the diary.
    assert witness.identity_path(renamed, m.name, nonce).exists()
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(renamed))
    assert refused.value.code == "identity_killed"


def test_unsetting_the_receiver_cannot_remove_its_veto(tmp_path, monkeypatch):
    """The requirement is in ``Launch`` and in the checkpoint: the variable is not the veto."""
    monkeypatch.setenv(witness.URL_ENV, "https://receiver.invalid/witness")
    monkeypatch.setattr(witness, "_post", lambda *a, **k: {"killed": False})
    m, path = _world(tmp_path)
    launch = next(i for i in Ledger.open_read_only(
        path, manifest=json.loads(m.canonical_json())).items()
        if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert launch["event"]["payload"]["witness_required"] is True
    assert launch["event"]["payload"]["witness_receiver"] == witness.receiver_identity()
    assert "receiver.invalid" not in json.dumps(launch)   # the address never enters the diary

    # With the same receiver, the world continues.
    assert resume_world(m, str(path))["ledger_verify"]

    # With none, it refuses: the identity launched under a witness keeps it.
    monkeypatch.delenv(witness.URL_ENV)
    with pytest.raises(ResumeError) as refused:
        resume_runtime(m, str(path))
    assert refused.value.code == "witness_required"

    # With a different one, it refuses too: another receiver never heard of this world.
    monkeypatch.setenv(witness.URL_ENV, "https://other.invalid/witness")
    with pytest.raises(ResumeError) as mismatched:
        resume_runtime(m, str(path))
    assert mismatched.value.code == "witness_mismatch"

    reasons = [i["reason"] for i in Ledger.open_read_only(
        path, manifest=json.loads(m.canonical_json())).items()
        if i["kind"] == "failed_resume"]
    assert reasons == ["witness_required", "witness_mismatch"]


def test_a_world_launched_without_a_receiver_keeps_the_weaker_guarantee(tmp_path):
    """No receiver at launch, no requirement afterwards: the local file alone decides."""
    m, path = _world(tmp_path)
    launch = next(i for i in Ledger.open_read_only(
        path, manifest=json.loads(m.canonical_json())).items()
        if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert "witness_required" not in launch["event"]["payload"]
    assert resume_world(m, str(path))["ledger_verify"]


# ---- 3. restore is transactional -----------------------------------------------------------


def _twin(m, state):
    return Runtime(m, ledger_path=None, **state["config"])


def _fields(rt) -> dict:
    return {name: getattr(rt, name) for name in _RUNTIME_FIELDS}


def _unchanged(rt, before: dict) -> None:
    """Every runtime field is the object it was: a refused restore assigned nothing.

    The few fields that are properties are compared by value, because reading one
    builds a fresh mapping from the component it belongs to.
    """
    for name, value in before.items():
        current = getattr(rt, name)
        if isinstance(getattr(type(rt), name, None), property):
            assert current == value, name
        else:
            assert current is value, name


def test_a_refused_restore_leaves_every_runtime_field_exactly_as_it_was(monkeypatch):
    """Every identity constraint is checked against the saved state before one assignment."""
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1)
    rt.run()
    state = runtime_state(rt)
    rt.termination.kill("explicit_kill:operator")      # the checkpoint names a dead identity

    twin = _twin(m, state)
    before = _fields(twin)
    with pytest.raises(ResumeError) as killed:
        restore_runtime(twin, state)
    assert killed.value.code == "identity_killed"
    _unchanged(twin, before)

    # A release that is not the one the world launched under: refused just as early.
    monkeypatch.setattr(witness, "_killed_here", set())
    other = _twin(m, state)
    other.release_digest = "0" * 64
    before = _fields(other)
    with pytest.raises(ResumeError) as release:
        restore_runtime(other, state)
    assert release.value.code == "release_mismatch"
    _unchanged(other, before)


def test_a_restore_refused_on_the_witness_requirement_changes_nothing(monkeypatch):
    monkeypatch.setenv(witness.URL_ENV, "https://receiver.invalid/witness")
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1)
    rt.run()
    state = runtime_state(rt)
    twin = _twin(m, state)
    monkeypatch.delenv(witness.URL_ENV)
    before = _fields(twin)
    with pytest.raises(ResumeError) as refused:
        restore_runtime(twin, state)
    assert refused.value.code == "witness_required"
    _unchanged(twin, before)


def test_a_restore_refused_on_a_missing_artifact_changes_nothing(tmp_path):
    """The archive is checked from the saved state, so the refusal precedes every write."""
    m = load_manifest("scripted")
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1)
    rt.run()
    rt.working_state.heads["ghost"] = {"sha": "f" * 64, "bytes": 1, "ts_ns": 0}
    state = runtime_state(rt)
    twin = _twin(m, state)
    twin.artifacts.root = tmp_path / "artifacts"       # a memory-only twin checks nothing
    twin.artifacts.root.mkdir()
    before = _fields(twin)
    with pytest.raises(ResumeError) as refused:
        restore_runtime(twin, state)
    assert refused.value.code == "artifact_missing"
    assert refused.value.details["owner"] == "ghost"
    _unchanged(twin, before)


# ---- 4. retirement is final at the budget layer ---------------------------------------------


def test_a_late_credit_to_a_retired_seat_goes_to_the_commons_and_is_ledgered_as_such():
    ledger = Ledger()
    wallet = Wallet(100_000_000, ledger)
    budget = BudgetBook(wallet, ledger, clock_ns=lambda: 7)
    budget.genesis(["alice", "bob"])
    budget.retire("alice", "approved-retirement")
    unallocated = budget.unallocated()

    assert budget.credit("alice", 1_000, "late trading consequence") == 0
    assert budget.earn("alice", 2_000, "late service income") is None
    assert budget.unallocated() == unallocated
    assert "alice" not in budget.seats() and "alice" not in budget.heads()
    assert budget.entitlement("alice") == 0

    rows = [i for i in ledger.items()
            if i.get("kind") == "budget" and i.get("op") == "retired_credit_to_commons"]
    assert [(r["assembly_id"], r["amount"], r["source"]) for r in rows] == [
        ("alice", 1_000, "credit"), ("alice", 2_000, "income")]

    # And the wake shows it, with the amount and not the seat.
    observatory = _Observatory()
    for item in ledger.items():
        observatory.feed(item)
    shown = observatory.result(load_manifest("scripted"))["liveness"]
    assert shown["retired_credits_to_commons"] == [
        {"ts_ns": 7, "amount_micro": 1_000, "source": "credit"},
        {"ts_ns": 7, "amount_micro": 2_000, "source": "income"}]
    assert "alice" not in json.dumps(shown)
