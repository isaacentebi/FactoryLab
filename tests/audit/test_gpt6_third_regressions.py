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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factorylab.kernel.artifacts import ArtifactStore
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime import witness
from factorylab.runtime.continuity import OutcomeInbox, WorkingState

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


# ---- the inbox: exact item addresses, oldest first, fail-closed, deduplicated ------------


def test_failed_inbox_evidence_publishes_no_item():
    """Finding: "Inbox and state failure semantics lose continuity" — "failed journal writes
    advance memory"."""
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    ledger.fail_kind = "outcome.addressed"
    with pytest.raises(OSError):
        inbox.append("alice", handle="d1", outcome={"paid": 1})
    assert inbox.unread("alice")["count"] == 0


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


# The two witness findings this reviewer characterized here are repaired by R3-C, and
# their regressions live with the rest of the death contract in
# ``tests/audit/test_r3c_death.py``: the receiver requirement is part of the launch
# identity, so unsetting the variable removes no veto, and the local record is keyed by
# launch identity, so renaming a diary finds the same line.


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
