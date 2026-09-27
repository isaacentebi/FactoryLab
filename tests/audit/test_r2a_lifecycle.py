"""Second reading, repair R2-A: the lifecycle seams the reviewer found (P1-02, P1-04, the
facilitator pin). P1-01, the witness at the restore boundary, is in
``tests/runtime/test_witness.py``.

P1-02: program memory across backup and restore. A checkpoint names every artifact by
hash; the bytes live beside the ledger. A resume whose index names bytes that are
missing refuses (``artifact_missing``, naming the sha and its owner) rather than
running a program with no state that reports ok.

P1-04: the ability to act versus death. When every live seat's entitlement is below its
call and the unallocated pool still holds money, the pool is released once at the window
boundary, one share per live lineage to its head as a tranche is split
(``budget op="commons_release"``); when the pool is empty
too, the condition is unaffordability: dormant while a release is still due, terminal
``insolvency:entitlement`` otherwise. One exhausted seat among feasible ones changes
nothing.

Facilitator: the x402 facilitator is read from the environment once, at launch,
ledgered in the ``Launch`` event, refused on a resume under a different one, and read
by the seller from the ledger rather than the environment.

Scripted world only: no network, no credentials.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime import seller as seller_module
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import ResumeError, resume_runtime
from factorylab.runtime.worlds import EndowmentSpec, load_manifest
from factorylab.world.models import ModelRequest
from tests.runtime.test_connectors import ledger_items

BASE = load_manifest("scripted")
TICK = BASE.tick_interval_ns


def world(path, events, *, initial=None, endowment=None, provider=None):
    manifest = BASE if endowment is None else replace(BASE, endowment=endowment)
    # The manifest's own adapters (the deterministic venue, the scripted provider), so a
    # world on disk resumes under the same adapter names resume_runtime constructs.
    return Runtime(manifest, events=events, seed=1, initial_balance_micro=initial,
                   ledger_path=None if path is None else str(path), router_gamma=.1,
                   provider=provider)


def diary(path):
    return Ledger.reopen(path, manifest=json.loads(BASE.canonical_json()))._recovery_items()


def kinds(items, *names):
    return [i for i in items if i.get("kind") in names]


def budget_ops(items, op):
    return [i for i in items if i.get("kind") == "budget" and i.get("op") == op]


def model_commits(items):
    return [i for i in items if i.get("kind") == "wallet.commit"
            and str(i.get("reason", "")).startswith("model:")]


# ---- P1-02: program memory across backup and restore ---------------------------------


def put_on_first_event(rt, data: bytes, owner: str) -> list[str]:
    """Archive one artifact for ``owner`` during the first event, before its snapshot."""
    shas: list[str] = []
    original = rt._process_event

    def hooked(event):
        if not shas:
            shas.append(rt.artifacts.put(data, owner=owner, kind="program.state"))
        return original(event)

    rt._process_event = hooked
    return shas


def test_a_resume_whose_archive_index_names_missing_bytes_refuses_by_sha_and_owner(tmp_path):
    path = tmp_path / "runs" / "w.jsonl"
    path.parent.mkdir()
    rt = world(path, 6)
    shas = put_on_first_event(rt, b'{"n": 3}', "prog-x")
    assert not rt.run()["terminated"]
    (sha,) = shas
    archived = path.with_suffix(".artifacts") / sha
    assert archived.is_file() and rt.artifacts.index[sha]["owner"] == "prog-x"
    # A backup that carried the diary but not the artifact directory (P1-02).
    archived.unlink()
    before = path.read_bytes()
    with pytest.raises(ResumeError) as refused:
        resume_runtime(BASE, str(path))
    assert refused.value.code == "artifact_missing"
    assert refused.value.details == {"sha": sha, "owner": "prog-x"}
    failed = kinds(diary(path), "failed_resume")
    assert [(f["reason"], f["sha"], f["owner"]) for f in failed] == [
        ("artifact_missing", sha, "prog-x")]
    assert path.read_bytes().startswith(before)  # the refusal is the only new record
    # Corrupt bytes under the right name are missing bytes too.
    archived.write_bytes(b'{"n": 4}')
    with pytest.raises(ResumeError) as corrupt:
        resume_runtime(BASE, str(path))
    assert corrupt.value.code == "artifact_missing" and corrupt.value.details["sha"] == sha
    # The bytes restored: the same resume proceeds and reads the same memory.
    archived.write_bytes(b'{"n": 3}')
    restored = resume_runtime(BASE, str(path))
    assert restored.artifacts.get(sha) == b'{"n": 3}'
    assert [f["reason"] for f in kinds(diary(path), "failed_resume")] == [
        "artifact_missing", "artifact_missing"]


# ---- P1-04: the ability to act versus death ------------------------------------------


def need(rt, seat) -> int:
    """What routing's probe requires of a seat before its first call (routing._is_feasible)."""
    asm = rt.assemblies[seat]
    probe = ModelRequest(asm.spec.model_id, asm.spec.system_prompt,
                        ({"role": "user", "content": ""},), asm.spec.max_tokens)
    return asm.model.ceiling(probe) * (1 if asm.spec.model_id.startswith("x402:") else 2)


def starve(rt, *, pool: bool):
    """Every seat one micro short of its own call; the pool keeps the rest, or nothing."""
    rt._unhistoried = lambda _seat: False  # careers, not trials: no protected share
    targets = {seat: need(rt, seat) - 1 for seat in rt.budget.seats()}
    for seat, target in targets.items():  # debits first: they refill the pool the grants draw
        if rt.budget.entitlement(seat) > target:
            rt.budget.debit(seat, rt.budget.entitlement(seat) - target, "starve")
    for seat, target in targets.items():
        if rt.budget.entitlement(seat) < target:
            rt.budget.grant(seat, target - rt.budget.entitlement(seat), "starve")
    if not pool:
        assert rt.budget.unallocated() == 0
    for seat in rt.budget.seats():
        feasible, why = rt._is_feasible(seat)
        assert not feasible and why.startswith("entitlement:")
    assert rt.budget.check_invariant()


def all_seats_one_short(initial=None):
    """A balance that leaves every seat exactly one micro short once the pool is granted out."""
    probe = world(None, 0, initial=initial)
    return sum(need(probe, seat) - 1 for seat in probe.budget.seats())


def test_all_seats_exhausted_and_pool_empty_with_a_release_due_is_dormant_until_it_lands():
    locked = 100_000_000
    offset = 3 * TICK + TICK // 2
    unlocked = all_seats_one_short()
    rt = world(None, 8, initial=unlocked + locked,
               endowment=EndowmentSpec(locked, ((offset, locked),)))
    starve(rt, pool=False)
    summary = rt.run()
    items = ledger_items(rt)
    dormant = kinds(items, "dormant")
    assert [(d["state"], d.get("trigger")) for d in dormant] == [("entered", "entitlement"),
                                                                  ("exited", None)]
    assert not budget_ops(items, "commons_release")
    entered, exited = (items.index(d) for d in dormant)
    # Dormant: no paid cognition, no route, while the mandatory maintenance ran.
    assert not any(entered < items.index(r) < exited for r in kinds(items, "compute.route"))
    assert not any(entered < items.index(c) < exited for c in model_commits(items))
    assert sum(entered < items.index(d) < exited for d in kinds(items, "runtime.event_done"))
    # The tranche landed, was split to the seats, and the seats act again.
    tranche = kinds(items, "release")
    assert len(tranche) == 1 and items.index(tranche[0]) < exited
    split = budget_ops(items, "release")
    assert len(split) == 1 and all(share > 0 for share in split[0]["grants"].values())
    assert any(items.index(c) > exited for c in model_commits(items))
    assert not summary["terminated"] and rt.dormancy is None


def test_all_seats_exhausted_and_pool_empty_with_no_release_due_is_terminal():
    rt = world(None, 8, initial=all_seats_one_short())
    starve(rt, pool=False)
    summary = rt.run()
    items = ledger_items(rt)
    assert summary["terminated"] and summary["termination_reason"] == "insolvency:entitlement"
    assert rt.termination.reason == "insolvency:entitlement"
    assert not budget_ops(items, "commons_release") and not kinds(items, "dormant")
    assert not model_commits(items)
    terminated = [i for i in kinds(items, "event") if i["event"]["kind"] == "Terminated"]
    assert terminated[-1]["event"]["payload"] == {"reason": "insolvency:entitlement"}


# ---- The facilitator pin --------------------------------------------------------------


def launch_payload(path):
    return next(i for i in diary(path) if i["kind"] == "event"
                and i["event"]["kind"] == "Launch")["event"]["payload"]


def test_a_resume_under_a_different_facilitator_is_refused_and_ledgered(tmp_path, monkeypatch):
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://facilitator.test/x402")
    path = tmp_path / "w.jsonl"
    world(path, 4).run()
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://other.test/x402")
    before = path.read_bytes()
    with pytest.raises(ResumeError) as refused:
        resume_runtime(BASE, str(path))
    assert refused.value.code == "facilitator_mismatch"
    failed = kinds(diary(path), "failed_resume")
    assert len(failed) == 1 and failed[0]["reason"] == "facilitator_mismatch"
    assert failed[0]["ledgered_facilitator_url"] == "https://facilitator.test/x402"
    assert failed[0]["running_facilitator_url"] == "https://other.test/x402"
    assert path.read_bytes().startswith(before)
    # The same facilitator resumes past the refusal; the CLI names the code.
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://facilitator.test/x402")
    restored = resume_runtime(BASE, str(path))
    assert restored.facilitator_url == "https://facilitator.test/x402"
    assert launch_payload(path)["facilitator_url"] == restored.facilitator_url
