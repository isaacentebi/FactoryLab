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
import os
import shutil
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime import seller as seller_module
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import ResumeError, resume_runtime
from factorylab.runtime.seller import Seller, configured_facilitator, facilitator_from_items
from factorylab.runtime.wake import _Observatory
from factorylab.runtime.worlds import EndowmentSpec, load_manifest
from factorylab.world.models import ModelRequest
from tests.runtime.test_connectors import ledger_items

BASE = load_manifest("scripted")
TICK = BASE.tick_interval_ns
WINDOW_EVENTS = BASE.novelty.window_ns // TICK


def world(path, events, *, initial=None, endowment=None, provider=None):
    manifest = BASE if endowment is None else replace(BASE, endowment=endowment)
    # The manifest's own adapters (the deterministic venue, the scripted provider), so a
    # world on disk resumes under the same adapter names resume_runtime constructs.
    return Runtime(manifest, events=events, seed=1, initial_balance_micro=initial,
                   ledger_path=None if path is None else str(path), drip=False,
                   router_gamma=.1, provider=provider)


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


@pytest.mark.slow
def test_a_program_seat_never_runs_with_lost_state_and_reports_ok(tmp_path):
    from tests.audit.test_e2_programs import Proposer, items, stop_after
    from tests.audit.test_e2_programs import world as program_world
    from tests.cortex.test_jail import require_jail

    require_jail()
    path = tmp_path / "crash.jsonl"
    rt = program_world(path, 400)
    # The process dies in the second reserve window, after the program has kept state:
    # the checkpoint at that window's boundary indexes the state it had by then.
    stop_after(rt, lambda r, e: r.stats.reserve_windows >= 2
               and r.stats.invocations_by_assembly.get("prog-a", 0) >= 3
               and str(e.kind) == "Tick")
    calls = [i for i in items(path) if i["kind"] == "program.call"
             and i["assembly_id"] == "prog-a" and i["status"] == "ok"]
    first_sha, last_sha = calls[0]["state_out"], calls[-1]["state_out"]
    archive = path.with_suffix(".artifacts")
    assert (archive / first_sha).is_file() and (archive / last_sha).is_file()
    assert json.loads((archive / last_sha).read_bytes())["n"] >= 3
    # The reviewer's run: the diary restored without its artifact directory. The
    # refusal names the first archived state and its owner; nothing is restored.
    saved = tmp_path / "artifacts.bak"
    shutil.copytree(archive, saved)
    shutil.rmtree(archive)
    with pytest.raises(ResumeError) as refused:
        resume_runtime(load_manifest("scripted"), str(path), provider=Proposer())
    assert refused.value.code == "artifact_missing"
    assert refused.value.details == {"sha": first_sha, "owner": "prog-a"}
    failed = [i for i in items(path) if i["kind"] == "failed_resume"]
    assert failed[-1]["reason"] == "artifact_missing" and failed[-1]["owner"] == "prog-a"
    assert failed[-1]["sha"] == first_sha
    # With the directory back the world resumes and the program continues from its state.
    shutil.copytree(saved, archive)
    restored = resume_runtime(load_manifest("scripted"), str(path), provider=Proposer())
    assert restored.assemblies["prog-a"].state_sha == last_sha
    assert json.loads(restored.artifacts.get(last_sha))["n"] >= 3
    # Bytes lost while the world runs: the call fails naming why, is not billed, and the
    # seat keeps its last good hash rather than continuing from an empty memory.
    (archive / last_sha).unlink()
    restored.run()
    calls = [i for i in items(path) if i["kind"] == "program.call"
             and i["assembly_id"] == "prog-a"]
    after = [c for c in calls if c.get("state_error")]
    assert after and all(c["status"] == "failed" and c["cost"] == 0 for c in after)
    assert all(c["state_in"] == c["state_out"] == last_sha for c in after)
    assert restored.assemblies["prog-a"].state_sha == last_sha
    assert not [c for c in calls[calls.index(after[0]):] if c["status"] == "ok"]


def test_backup_archives_the_artifact_directory_with_the_diary():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[2] / "deploy" / "backup.sh").read_text()
    assert "runs/funded.artifacts" in script
    assert "artifact bytes do not match their hash" in script
    assert "record['artifacts'] = {'count': count, 'bytes': size}" in script
    readme = (Path(__file__).resolve().parents[2] / "deploy" / "README.md").read_text()
    assert "artifact_missing" in readme and "funded.artifacts/" in readme


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


def test_all_seats_exhausted_and_pool_positive_releases_the_commons_once_then_seats_act():
    rt = world(None, 2 * WINDOW_EVENTS + 4, initial=100_000_000)
    rt._manage_reserve_window()  # the first boundary comes a window later, not at launch
    starve(rt, pool=True)
    pool_before = rt.budget.unallocated()
    seats = rt.budget.seats()  # the nine seeds: nobody can propose a child before the release
    assert pool_before > 0 and len(seats) == 9
    summary = rt.run()
    items = ledger_items(rt)
    releases = budget_ops(items, "commons_release")
    assert len(releases) == 1
    release = releases[0]
    assert release["amount"] == pool_before and release["reason"] == "nobody can act"
    assert release["grants"] == {seat: pool_before // len(seats) for seat in seats}
    assert release["unallocated_after"] == pool_before - sum(release["grants"].values())
    at = items.index(release)
    # Before the release: routed, nobody chosen, and never counted as insolvency.
    routed_before = [i for i in kinds(items, "compute.route") if items.index(i) < at]
    assert routed_before and not any(r["unaffordable"] for r in routed_before)
    assert not any(items.index(c) < at for c in model_commits(items))
    assert not any(items.index(i) < at for i in kinds(items, "treasury.insolvency"))
    # After it: the seats think again, on their own entitlements, and nobody died.
    assert any(items.index(c) > at for c in model_commits(items))
    assert not summary["terminated"] and rt.dormancy is None
    assert not kinds(items, "dormant")
    assert rt.budget.check_invariant()


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


def test_one_exhausted_seat_among_feasible_ones_is_the_seats_own_state():
    rt = world(None, 0, initial=100_000_000)
    rt._manage_reserve_window()
    rt._unhistoried = lambda _seat: False
    seat = rt.budget.seats()[0]
    rt.budget.debit(seat, rt.budget.entitlement(seat), "exhausted")
    assert not rt._is_feasible(seat)[0]
    assert any(rt._is_feasible(other)[0] for other in rt.budget.seats() if other != seat)
    assert rt._commons_check() is False
    assert not budget_ops(ledger_items(rt), "commons_release")
    assert rt.budget.entitlement(seat) == 0  # nothing was released to it
    # A wallet-level shortfall is the insolvency streak's business, not the commons'.
    rt.budget.credit(seat, 1, "back")
    rt._is_feasible = lambda _seat: (False, "compute: provider balance 0 below ceiling 1")
    assert rt._commons_check() is False
    assert not budget_ops(ledger_items(rt), "commons_release")


def test_the_commons_release_is_a_classification_and_the_wake_shows_it():
    rt = world(None, 0, initial=100_000_000)
    balance = rt.wallet.balance
    book = rt.budget
    pool = book.unallocated()
    grants = book.commons_release("nobody can act")
    # One share per live lineage, to its head (the nine seeds are nine roots), as a
    # tranche is split: replication buys no larger share of the commons either.
    assert sum(grants.values()) <= pool and set(grants) == set(book.heads())
    assert book.unallocated() == pool - sum(grants.values()) < len(book.heads())
    logged = budget_ops(ledger_items(rt), "commons_release")[0]
    assert logged["lineages"] == {head: head for head in book.heads()}
    assert rt.wallet.balance == balance and book.check_invariant()
    assert book.commons_release("again") == {}  # nothing left: nothing moves, nothing logged
    releases = budget_ops(ledger_items(rt), "commons_release")
    assert len(releases) == 1 and releases[0]["amount"] == pool
    observatory = _Observatory()
    for item in ledger_items(rt):
        observatory.feed(item)
    shown = observatory.result(rt.m)["pots"]["commons_releases"]
    assert shown == [{"ts_ns": releases[0]["ts"], "amount_micro": pool,
                      "lineages": len(grants), "per_seat_micro": pool // len(grants)}]


# ---- The facilitator pin --------------------------------------------------------------


def launch_payload(path):
    return next(i for i in diary(path) if i["kind"] == "event"
                and i["event"]["kind"] == "Launch")["event"]["payload"]


def test_launch_ledgers_the_facilitator_beside_the_release_digest(tmp_path, monkeypatch):
    monkeypatch.delenv(seller_module.FACILITATOR_ENV, raising=False)
    default = tmp_path / "default.jsonl"
    rt = world(default, 2)
    rt.run()
    payload = launch_payload(default)
    assert payload["facilitator_url"] == seller_module.FACILITATOR_URL
    assert payload["release_digest"] == rt.release_digest
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://facilitator.test/x402")
    pinned = tmp_path / "pinned.jsonl"
    world(pinned, 2).run()
    assert launch_payload(pinned)["facilitator_url"] == "https://facilitator.test/x402"
    ledger = Ledger.reopen(pinned, manifest=json.loads(BASE.canonical_json()))
    assert ledger.identity()["facilitator_url"] == "https://facilitator.test/x402"
    frozen = Ledger.open_read_only(pinned, manifest=json.loads(BASE.canonical_json()))
    assert facilitator_from_items(frozen.items()) == "https://facilitator.test/x402"
    assert configured_facilitator() == "https://facilitator.test/x402"
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "ftp://nope")
    with pytest.raises(ValueError):
        configured_facilitator()


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


def test_the_cli_names_the_facilitator_mismatch(tmp_path, monkeypatch, capsys):
    from factorylab.runtime.cli import _cmd_resume, build_parser

    monkeypatch.delenv(seller_module.FACILITATOR_ENV, raising=False)
    path = tmp_path / "w.jsonl"
    world(path, 2).run()
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://other.test/x402")
    monkeypatch.setenv("RUNTIME_DIRECTORY", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    args = build_parser().parse_args(["resume", "--world", "scripted", "--ledger", str(path)])
    assert _cmd_resume(args) == 1
    assert capsys.readouterr().err == "factorylab resume: facilitator_mismatch\n"
    assert (tmp_path / "run" / "reason").read_text() == "facilitator_mismatch\n"


def test_the_seller_reads_the_ledgered_facilitator_never_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://facilitator.test/x402")
    path = tmp_path / "w.jsonl"
    world(path, 2).run()
    # After launch the environment says something else; the seller does not listen.
    monkeypatch.setenv(seller_module.FACILITATOR_ENV, "https://evil.test/x402")
    unbound = Seller({}, pay_to="0x" + "1" * 40, runner=None, earn=lambda *a: None)
    assert unbound.facilitator == seller_module.FACILITATOR_URL
    assert "evil" not in unbound.facilitator
    import deploy.serve as serve

    services, _pay_to, facilitator = serve.load_catalogue(path)
    assert services == {} and facilitator == "https://facilitator.test/x402"
    bound = Seller(services, pay_to="0x" + "1" * 40, runner=None, earn=lambda *a: None,
                   facilitator=facilitator)
    assert bound.facilitator == "https://facilitator.test/x402"
    assert os.environ[seller_module.FACILITATOR_ENV] == "https://evil.test/x402"
    # No source of the seller reads the variable after launch.
    from pathlib import Path

    source = Path(seller_module.__file__).read_text()
    assert source.count("os.environ") == 1  # configured_facilitator, at launch only
    assert "FACTORYLAB_FACILITATOR_URL" not in Path(serve.__file__).read_text()
