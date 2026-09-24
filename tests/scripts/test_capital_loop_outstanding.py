"""The capital loop's keyless chain reads: the launch check and the outstanding report.

Nothing here touches a network or a signing key: Base is a fake JSON-RPC transport and
each run's diary is a real encrypted ledger written with its own sealing key file, the
way a rehearsal writes one and ``factorylab postmortem`` reads one.
"""

import json
from dataclasses import replace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.capital_loop import (
    CapitalLoopRefused,
    journaled_references,
    keyless_base,
    launch_check,
    outstanding,
    read_items,
)
from factorylab.runtime.worlds import load_manifest
from factorylab.world.evm import BASE
from factorylab.world.x402 import HTTPResponse

RESERVE = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
LIVE_NONCE = "0x" + "11" * 32
OLD_NONCE = "0x" + "22" * 32
SETTLED_NONCE = "0x" + "33" * 32


class Rpc:
    """Base mainnet as the public JSON-RPC answers it, and a record of every request."""

    def __init__(self):
        self.balance = 10_000_000
        self.final_number, self.final_ts = 900, 2_000
        self.lag_s = 960  # latest less finalized, as measured on Base mainnet
        self.used = {SETTLED_NONCE}
        self.requests = []

    @property
    def latest_ts(self):
        return self.final_ts + self.lag_s

    def __call__(self, method, url, payload, headers):
        assert url == BASE.rpc, url  # nothing else is asked, Venice's quote included
        self.requests.append(payload)
        name, params = payload["method"], payload["params"]
        if name == "eth_chainId":
            result = hex(BASE.id)
        elif name == "eth_getBlockByNumber":
            result = ({"number": hex(self.final_number), "timestamp": hex(self.final_ts)}
                      if params[0] == "finalized" else
                      {"number": hex(self.final_number + self.lag_s // 2),
                       "timestamp": hex(self.final_ts + self.lag_s)}
                      if params[0] == "latest" else {"hash": "0x" + "ab" * 32})
        elif name == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x70a08231"):  # balanceOf(address)
                result = hex(self.balance)
            else:  # authorizationState(address,bytes32)
                result = hex(int("0x" + data[-64:] in self.used))
        elif name == "eth_getLogs":
            nonce = params[0]["topics"][2]
            result = ([{"address": BASE.usdc, "topics": params[0]["topics"],
                        "transactionHash": "0x" + "cd" * 32, "blockNumber": hex(850),
                        "blockHash": "0x" + "ab" * 32}]
                      if nonce in self.used else [])
        else:
            raise AssertionError(f"unexpected RPC {name}")
        return HTTPResponse(200, {"jsonrpc": "2.0", "id": 1, "result": result})


def reference(nonce, valid_before):
    return {"authorization": {"nonce": nonce, "validBefore": valid_before, "to": "0x" + "9" * 40},
            "start_block": 800, "network": "eip155:8453"}


def write_run(run_dir, *, crash=True):
    """A capital-loop diary: a settled conversion, a superseded authorization, a current
    one still inside its validity window, and a shadow send left unconfirmed."""
    run_dir.mkdir(parents=True)
    path = run_dir / "ledger.jsonl"
    ledger = Ledger(str(path), manifest={"name": "edition5-capital-loop"},
                    clock_ns=lambda: 0, key_path=str(path) + ".key")
    steps = ["shadow_send", "venice_top_up"]
    settled = {"id": "treasury-0", "steps": steps, "index": 1, "status": "submitted",
               "reference": reference(SETTLED_NONCE, 1_000), "route_data": {}}
    ledger.append({"kind": "treasury.step_submitted", "state": settled})
    ledger.append({"kind": "treasury.confirmed", "state": {**settled, "status": "confirmed"}})
    current = {"id": "treasury-1", "steps": steps, "index": 1, "status": "submitted",
               "reference": reference(LIVE_NONCE, 3_000),
               "route_data": {"superseded_references": [reference(OLD_NONCE, 1_500)]}}
    ledger.append({"kind": "treasury.step_submitted", "state": current})
    prepared_only = reference("0x" + "44" * 32, 3_000)  # never journaled toward a signature
    ledger.append({"kind": "io.result", "call": 1, "result": json.dumps(prepared_only)})
    shadow = {"id": "treasury-2", "steps": steps, "index": 0, "status": "submitted",
              "amount_micro": 5_000_000,
              "reference": {"nonce": 1_700_000_000_000, "destination": "0x" + "de" * 20}}
    ledger.append({"kind": "treasury.submitted", "state": shadow})
    if crash:
        with path.open("ab") as stream:
            stream.write(b'{"item": "gAAAAA-torn')  # the process died mid-append
    return run_dir


def test_a_crashed_run_is_read_with_its_own_ledger_key_and_nothing_else(tmp_path):
    items = read_items(write_run(tmp_path / "run"))
    top_ups, shadows = journaled_references(items)
    assert sorted(r["authorization"]["nonce"] for r in top_ups) == sorted(
        [SETTLED_NONCE, LIVE_NONCE, OLD_NONCE])  # the merely prepared one is not listed
    assert shadows == [{"transfer_id": "treasury-2", "nonce": 1_700_000_000_000,
                        "sink": "0x" + "de" * 20, "amount_micro": 5_000_000}]
    (tmp_path / "run" / "ledger.jsonl.key").unlink()
    with pytest.raises(CapitalLoopRefused, match="run_ledger_key_missing"):
        read_items(tmp_path / "run")


def test_outstanding_reports_each_authorization_from_the_chain(tmp_path):
    rpc = Rpc()
    report = outstanding(write_run(tmp_path / "run"), reserve_address=RESERVE,
                         base=keyless_base(transport=rpc))
    rows = {row["nonce"]: row for row in report["top_ups"]}
    assert rows[SETTLED_NONCE]["authorization_used"] and rows[SETTLED_NONCE]["debits"]
    assert rows[LIVE_NONCE]["live"] and not rows[LIVE_NONCE]["expired"]
    assert rows[LIVE_NONCE]["finalized_timestamp"] == 2_000
    assert rows[OLD_NONCE]["expired"] and not rows[OLD_NONCE]["live"]
    assert len(report["shadow_sends"]) == 1
    methods = {r["method"] for r in rpc.requests}
    assert methods <= {"eth_chainId", "eth_getBlockByNumber", "eth_call", "eth_getLogs"}


def test_the_script_prints_every_authorization_and_flags_what_may_still_settle(
        tmp_path, capsys):
    from scripts import capital_loop_outstanding

    code = capital_loop_outstanding.main([str(write_run(tmp_path / "run"))], transport=Rpc())
    out = capsys.readouterr().out
    assert code == 1  # a live authorization and a pending shadow send
    assert f"nonce {LIVE_NONCE}" in out and "LIVE: may still settle" in out
    assert "authorizationState True" in out and "settled (debited)" in out
    assert "expired unused" in out and "shadow send pending treasury-2" in out
    assert "validBefore 3000" in out and "finalized_ts 2000" in out


def capital_loop_world():
    return load_manifest("worlds/edition6-capital-loop.toml")


def test_the_launch_check_needs_the_chain_to_bound_what_a_fresh_run_can_spend():
    world = capital_loop_world()
    world = replace(world, treasury=replace(world.treasury, venice_reserve_floor_micro=5_000_000))
    rpc = Rpc()
    rpc.balance = 15_000_000  # $10 above a $5 floor: exactly the total cap
    numbers = launch_check(world, transport=rpc)
    assert numbers["reserve_usdc_micro"] == 15_000_000
    assert numbers["spendable_above_floor_micro"] == 10_000_000
    rpc.balance = 15_000_001
    with pytest.raises(CapitalLoopRefused, match="reserve_floor_leaves_more") as refused:
        launch_check(world, transport=rpc)
    assert refused.value.detail["reserve_usdc_micro"] == 15_000_001
    # The review's case: a $1 floor on a $15 reserve would have allowed $14.
    low = replace(world, treasury=replace(world.treasury, venice_reserve_floor_micro=1_000_000))
    rpc.balance = 15_000_000
    with pytest.raises(CapitalLoopRefused, match="reserve_floor_leaves_more"):
        launch_check(low, transport=rpc)


def test_the_launch_check_refuses_while_an_earlier_run_can_still_settle(tmp_path):
    world = capital_loop_world()
    rpc = Rpc()
    run = write_run(tmp_path / "old")
    with pytest.raises(CapitalLoopRefused, match="previous_run_authorization") as refused:
        launch_check(world, previous_runs=(run,), transport=rpc)
    assert [row["nonce"] for row in refused.value.detail["authorizations"]] == [LIVE_NONCE]
    rpc.final_ts = 3_001  # finalized Base is now past its validBefore, still unused
    assert launch_check(world, previous_runs=(run,), transport=rpc)["previous_runs"]


def rehearse(out, rpc, tmp_path, **kwargs):
    """A capital-loop launch against fakes: the lock lives in the test's own directory."""
    from factorylab.runtime.worlds import duration_ns
    from scripts import edition4_rehearsal as rehearsal

    kwargs.setdefault("duration_ns", duration_ns("3h"))
    kwargs.setdefault("source_root", tmp_path)
    kwargs.setdefault("provider", object())
    kwargs.setdefault("world", "worlds/edition6-capital-loop.toml")
    # The host clock reads Base's latest block: no lead to add to the bound.
    kwargs.setdefault("now_ns", lambda: rpc.latest_ts * 1_000_000_000)
    return rehearsal.run_rehearsal(out=out, capital_loop=True, capital_loop_transport=rpc,
                                   capital_loop_lock_dir=tmp_path / "locks", **kwargs)


def test_the_rehearsal_runner_runs_the_launch_check_before_anything_is_built(
        tmp_path, capsys):
    write_run(tmp_path / "runs" / "earlier")
    rpc = Rpc()
    report = rehearse(tmp_path / "runs" / "now", rpc, tmp_path)
    assert report["status"] == "failed"
    assert report["refusal"]["reason"] == "previous_run_authorization_may_still_settle"
    rpc.final_ts = 3_001
    report = rehearse(tmp_path / "runs" / "later", rpc, tmp_path)
    # The chain checks passed; the next refusal is the frozen-source check's.
    assert report["refusal"]["reason"] == "source_root_mismatch"
    printed = capsys.readouterr().out
    assert '"reserve_usdc_micro": 10000000' in printed
    floor = load_manifest("worlds/edition6-capital-loop.toml").treasury.venice_reserve_floor_micro
    assert f'"venice_reserve_floor_micro": {floor}' in printed


def test_the_kill_witness_beside_a_run_is_not_a_sibling_run(tmp_path):
    # The second live capital-loop launch (23 September 2026) refused with
    # run_ledger_key_missing on work/capital-loop/.witness, the kill witness the first
    # run wrote beside itself.
    from factorylab.runtime.witness import WITNESS_DIR
    from scripts import edition4_rehearsal as rehearsal

    write_run(tmp_path / "earlier")
    witness = tmp_path / WITNESS_DIR
    witness.mkdir()
    (witness / "ledger.jsonl").write_text("{}\n")
    assert rehearsal._sibling_runs(tmp_path / "now") == (tmp_path / "earlier",)


def test_a_run_named_like_the_witness_is_still_read_and_the_name_is_refused(tmp_path):
    # The #142 review: a run directory named .witness that holds a diary and its key must
    # not be hidden from the launch check, and no run may take that name.
    from factorylab.runtime.witness import WITNESS_DIR
    from scripts import edition4_rehearsal as rehearsal

    write_run(tmp_path / WITNESS_DIR)
    assert rehearsal._sibling_runs(tmp_path / "now") == (tmp_path / WITNESS_DIR,)
    with pytest.raises(rehearsal.RehearsalRefused, match="output_dir_reserved_for_witness"):
        rehearsal._refuse_reserved_out(tmp_path / WITNESS_DIR)


def test_a_launch_named_like_the_witness_is_refused_before_anything_is_created(tmp_path):
    # The #142 review: the refusal must precede mkdir, or it leaves a 0755 .witness.
    from factorylab.runtime.witness import WITNESS_DIR
    from scripts import edition4_rehearsal as rehearsal

    with pytest.raises(rehearsal.RehearsalRefused, match="output_dir_reserved_for_witness"):
        rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                out=tmp_path / "runs" / WITNESS_DIR, capital_loop=True,
                                provider=object())
    assert not (tmp_path / "runs").exists()


# ---- Wave 10: a diary line that cannot be read is never read as "nothing outstanding"


def diary_lines(run):
    return (run / "ledger.jsonl").read_bytes().splitlines(keepends=True)


def rewrite(run, lines):
    (run / "ledger.jsonl").write_bytes(b"".join(lines))


def test_a_run_folder_with_another_key_refuses_instead_of_hiding_its_live_top_up(tmp_path):
    from cryptography.fernet import Fernet

    world, rpc = capital_loop_world(), Rpc()
    run = write_run(tmp_path / "restored")
    # A copied or restored run folder whose key file is not the one its diary was sealed
    # with: every line fails to decrypt. Read line by line and skipped, the live
    # authorization vanished and the launch check passed.
    (run / "ledger.jsonl.key").write_bytes(Fernet.generate_key())
    with pytest.raises(CapitalLoopRefused, match="run_ledger_unreadable") as refused:
        read_items(run)
    assert refused.value.detail["line"] == 2  # the first record after the header
    with pytest.raises(CapitalLoopRefused, match="run_ledger_unreadable"):
        launch_check(world, previous_runs=(run,), transport=rpc)
    (run / "ledger.jsonl.key").write_bytes(b"not a key")
    with pytest.raises(CapitalLoopRefused, match="run_ledger_key_invalid"):
        read_items(run)


@pytest.mark.parametrize("damage", ["corrupted", "removed", "reordered", "complete_last"])
def test_a_damaged_middle_or_complete_last_line_refuses_the_whole_read(tmp_path, damage):
    world, rpc = capital_loop_world(), Rpc()
    run = write_run(tmp_path / "run")
    lines = diary_lines(run)
    live = 3  # the header, the settled step, its confirmation, then the live step_submitted
    assert LIVE_NONCE in json.dumps(read_items(run)[live - 1])
    if damage == "corrupted":
        record = json.loads(lines[live])
        record["item"] = record["item"][:40] + ("A" if record["item"][40] != "A" else "B") + (
            record["item"][41:])
        lines[live] = json.dumps(record).encode() + b"\n"
    elif damage == "removed":
        del lines[live]
    elif damage == "reordered":
        lines[live], lines[live + 1] = lines[live + 1], lines[live]
    else:  # a complete last line is no torn append: it must decrypt like any other
        lines[-1] = b'{"item": "gAAAAA-complete-but-garbage"}\n'
    rewrite(run, lines)
    with pytest.raises(CapitalLoopRefused, match="run_ledger_unreadable"):
        read_items(run)
    with pytest.raises(CapitalLoopRefused, match="run_ledger_unreadable"):
        launch_check(world, previous_runs=(run,), transport=rpc)


def test_only_a_torn_last_line_is_skipped(tmp_path):
    torn = read_items(write_run(tmp_path / "torn"))  # ends mid-append
    whole = read_items(write_run(tmp_path / "whole", crash=False))
    assert [i["kind"] for i in torn] == [i["kind"] for i in whole] and len(whole) == 5
    lines = diary_lines(tmp_path / "whole")
    rewrite(tmp_path / "whole", [*lines[:2], lines[2][:-1], *lines[3:]])  # torn, not last
    with pytest.raises(CapitalLoopRefused, match="run_ledger_unreadable"):
        read_items(tmp_path / "whole")


def test_a_last_line_whose_newline_was_cut_is_still_read(tmp_path):
    # Cutting the final newline off a complete record must not turn the newest item (here
    # the live authorization) into a "torn append" that is skipped.
    run = write_run(tmp_path / "cut", crash=False)
    lines = diary_lines(run)
    live = lines[3]
    rewrite(run, [*lines[:4]])
    assert LIVE_NONCE in json.dumps(read_items(run)[-1])
    rewrite(run, [*lines[:3], live[:-1]])
    assert len(read_items(run)) == 3 and LIVE_NONCE in json.dumps(read_items(run)[-1])
    rewrite(run, [*lines[:3], live[:-40]])  # a real torn append: cut inside the record
    assert len(read_items(run)) == 2


# ---- Wave 10: one capital-loop run per reserve, held for the run's life


def test_one_run_holds_a_reserve_and_any_spelling_of_it_is_refused(tmp_path):
    import fcntl

    from factorylab.runtime.capital_loop import ReserveLock

    held = ReserveLock(RESERVE, lock_dir=tmp_path)
    assert fcntl.fcntl(held.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC  # no child inherits it
    for spelling in (RESERVE, RESERVE.lower()):
        with pytest.raises(CapitalLoopRefused, match="capital_loop_reserve_locked"):
            ReserveLock(spelling, lock_dir=tmp_path)
    ReserveLock("0x" + "12" * 20, lock_dir=tmp_path).close()  # another reserve is free
    held.close()
    ReserveLock(RESERVE, lock_dir=tmp_path).close()
    assert (tmp_path / f"{RESERVE.lower()}.lock").exists()  # never unlinked


def test_a_killed_runs_lock_is_released_by_its_death(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    from factorylab.runtime.capital_loop import ReserveLock

    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import time\n"
         "from factorylab.runtime.capital_loop import ReserveLock\n"
         f"lock = ReserveLock({RESERVE!r}, lock_dir={str(tmp_path)!r})\n"
         "print('held', flush=True)\n"
         "time.sleep(60)\n"],
        stdout=subprocess.PIPE, cwd=Path(__file__).resolve().parents[2])
    try:
        assert holder.stdout.readline() == b"held\n"
        with pytest.raises(CapitalLoopRefused, match="capital_loop_reserve_locked"):
            ReserveLock(RESERVE, lock_dir=tmp_path)
    finally:
        holder.kill()  # SIGKILL: no finally, no close, no cleanup ran in the holder
        holder.wait()
        holder.stdout.close()
    ReserveLock(RESERVE, lock_dir=tmp_path).close()  # the OS released it with the process


def test_a_second_launch_while_a_run_holds_the_reserve_is_refused_before_any_read(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock

    rpc = Rpc()
    reserve = capital_loop_world().treasury.reserve_address
    # Two launches close together, before either journals an authorization: each floor
    # check would read the same balance. The first holds the reserve; the second stops.
    in_flight = ReserveLock(reserve, lock_dir=tmp_path / "locks")
    report = rehearse(tmp_path / "runs" / "second", rpc, tmp_path)
    assert report["refusal"]["reason"] == "capital_loop_reserve_locked"
    assert rpc.requests == []  # refused before the chain was read
    in_flight.close()
    report = rehearse(tmp_path / "runs" / "third", rpc, tmp_path)
    assert report["refusal"]["reason"] == "source_root_mismatch"  # every capital check passed
    ReserveLock(reserve, lock_dir=tmp_path / "locks").close()  # the runner released it


def test_a_removed_last_run_record_refuses_until_the_deliberate_reset(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock

    rpc = Rpc()
    reserve = capital_loop_world().treasury.reserve_address
    locks = tmp_path / "locks"
    with ReserveLock(reserve, lock_dir=locks) as lock:
        assert lock.last_run() is None  # the first lock records that no run held it
        lock.record_run(write_run(tmp_path / "elsewhere" / "earlier"))
        record = lock.record_path
    record.unlink()  # switching the last-run check off by deleting its record
    with ReserveLock(reserve, lock_dir=locks) as lock:
        with pytest.raises(CapitalLoopRefused, match="capital_loop_last_run_missing"):
            lock.last_run()
    report = rehearse(tmp_path / "runs" / "after-removal", rpc, tmp_path)
    assert report["refusal"]["reason"] == "capital_loop_last_run_missing"
    # Deleting the write-ahead authorization record cannot switch its check off either.
    authorizations = locks / f"{reserve.lower()}.authorizations.jsonl"
    record.write_text(json.dumps({"reserve_address": reserve, "run_dir": None}))
    authorizations.unlink()
    with ReserveLock(reserve, lock_dir=locks) as lock:
        with pytest.raises(CapitalLoopRefused,
                           match="capital_loop_authorization_record_missing"):
            lock.authorizations()
    report = rehearse(tmp_path / "runs" / "after-record-removal", rpc, tmp_path)
    assert report["refusal"]["reason"] == "capital_loop_authorization_record_missing"
    # The runbook's manual reset removes all three files while no run holds the reserve.
    (locks / f"{reserve.lower()}.lock").unlink()
    record.unlink()
    with ReserveLock(reserve, lock_dir=locks) as lock:
        assert lock.last_run() is None and lock.authorizations() == []
    # Removing only the lock file, as a run still holding it would never notice, refuses
    # and recreates nothing: a fresh lock file would be a second, free lock.
    with ReserveLock(reserve, lock_dir=locks) as lock:
        lock.record_run(tmp_path / "elsewhere" / "earlier")
    (locks / f"{reserve.lower()}.lock").unlink()
    for _ in range(2):
        with pytest.raises(CapitalLoopRefused, match="capital_loop_lock_file_missing"):
            ReserveLock(reserve, lock_dir=locks)
    assert not (locks / f"{reserve.lower()}.lock").exists()


def test_a_lock_file_replaced_under_a_waiting_launch_is_not_the_lock(tmp_path, monkeypatch):
    from factorylab.runtime import capital_loop
    from factorylab.runtime.capital_loop import ReserveLock

    ReserveLock(RESERVE, lock_dir=tmp_path).close()  # the files exist, no run holds them
    path = tmp_path / f"{RESERVE.lower()}.lock"
    flock = capital_loop.fcntl.flock

    def replaced_first(fd, operation):
        # Between this launch's open and its flock, the path is removed and recreated:
        # the descriptor now names an unlinked inode anyone else could lock afresh.
        path.unlink()
        path.touch()
        return flock(fd, operation)

    monkeypatch.setattr(capital_loop.fcntl, "flock", replaced_first)
    with pytest.raises(CapitalLoopRefused, match="capital_loop_lock_replaced"):
        ReserveLock(RESERVE, lock_dir=tmp_path)

    def removed_first(fd, operation):
        path.unlink()
        return flock(fd, operation)

    monkeypatch.setattr(capital_loop.fcntl, "flock", removed_first)
    with pytest.raises(CapitalLoopRefused, match="capital_loop_lock_replaced"):
        ReserveLock(RESERVE, lock_dir=tmp_path)


def test_a_recorded_run_whose_diary_is_empty_refuses(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock

    rpc = Rpc()
    reserve = capital_loop_world().treasury.reserve_address
    recorded = write_run(tmp_path / "elsewhere" / "recorded", crash=False)
    with ReserveLock(reserve, lock_dir=tmp_path / "locks") as lock:
        lock.record_run(recorded)
    rpc.final_ts = 3_001  # nothing it holds is live any more: only its length matters
    lines = diary_lines(recorded)
    for truncated in ([lines[0]], [lines[0], lines[1][:-30]], [lines[0][:-1]]):
        rewrite(recorded, truncated)  # header only; torn at its first record; torn header
        report = rehearse(tmp_path / "runs" / f"after-{len(b''.join(truncated))}", rpc,
                          tmp_path)
        assert report["refusal"]["reason"] == "recorded_run_ledger_empty"
    # The same emptiness in a run that is merely a sibling is still read, as before: only
    # the recorded run is known to have written its launch items.
    assert read_items(recorded) == []
    rewrite(recorded, lines)
    assert rehearse(tmp_path / "runs" / "whole", rpc, tmp_path)["refusal"]["reason"] == (
        "source_root_mismatch")


def test_the_lock_directory_is_the_accounts_not_home(monkeypatch, tmp_path, operator_lock_dir):
    import os
    import pwd
    from pathlib import Path

    account = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".factorylab" / "capital-loop"
    monkeypatch.setenv("HOME", str(tmp_path))  # a fresh, empty home for this launch
    assert operator_lock_dir() == account and tmp_path not in operator_lock_dir().parents


def test_a_recorded_run_is_durable_with_its_rename(monkeypatch, tmp_path):
    import os
    import stat

    from factorylab.runtime import capital_loop
    from factorylab.runtime.capital_loop import ReserveLock

    synced, durable, replace = [], capital_loop._durable, capital_loop.os.replace

    def spy(fd):
        synced.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        durable(fd)

    def renamed(*args):
        replace(*args)
        synced.append("rename")

    monkeypatch.setattr(capital_loop, "_durable", spy)
    monkeypatch.setattr(capital_loop.os, "replace", renamed)
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        # The first lock writes its record naming no run and its empty authorization
        # record: each file's bytes, then the directory holding its name.
        assert synced == ["file", "directory", "file", "directory"]
        del synced[:]
        lock.record_run(tmp_path / "run")
        # The record's bytes, the rename, and only then the directory holding the new
        # name: without that last flush a power loss could bring the old record back.
        assert synced == ["file", "rename", "directory"]
        assert lock.last_run() == (tmp_path / "run").resolve()


def test_durable_asks_the_drive_to_flush_where_the_os_can(monkeypatch, tmp_path):
    import os

    from factorylab.runtime import capital_loop

    asked, fsynced = [], []
    monkeypatch.setattr(capital_loop.fcntl, "F_FULLFSYNC", 51, raising=False)
    monkeypatch.setattr(capital_loop.fcntl, "fcntl", lambda fd, op: asked.append(op))
    monkeypatch.setattr(capital_loop.os, "fsync", lambda fd: fsynced.append(fd))
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        capital_loop._durable(fd)
        assert asked == [51] and fsynced == []  # F_FULLFSYNC, not a plain fsync

        def refused(fd, op):
            raise OSError("not supported here")

        monkeypatch.setattr(capital_loop.fcntl, "fcntl", refused)
        capital_loop._durable(fd)
        assert fsynced == [fd]  # a filesystem that refuses it still gets fsync
        monkeypatch.delattr(capital_loop.fcntl, "F_FULLFSYNC")
        capital_loop._durable(fd)
        assert fsynced == [fd, fd]
    finally:
        os.close(fd)


def test_the_reserves_last_run_is_checked_wherever_its_directory_is(tmp_path):
    import shutil

    from factorylab.runtime.capital_loop import ReserveLock

    rpc = Rpc()
    reserve = capital_loop_world().treasury.reserve_address
    earlier = write_run(tmp_path / "elsewhere" / "earlier")  # not beside the next --out
    with ReserveLock(reserve, lock_dir=tmp_path / "locks") as lock:
        lock.record_run(earlier)
    report = rehearse(tmp_path / "runs" / "now", rpc, tmp_path)
    assert report["refusal"]["reason"] == "previous_run_authorization_may_still_settle"
    assert report["refusal"]["run_dir"] == str(earlier.resolve())
    with ReserveLock(reserve, lock_dir=tmp_path / "locks") as lock:
        assert lock.last_run() == earlier.resolve()  # a refused launch recorded nothing
    rpc.final_ts = 3_001  # finalized Base is past the live authorization's validBefore
    assert rehearse(tmp_path / "runs" / "later", rpc, tmp_path)["refusal"]["reason"] == (
        "source_root_mismatch")
    shutil.rmtree(earlier)  # a recorded run that vanished may have left anything live
    report = rehearse(tmp_path / "runs" / "gone", rpc, tmp_path)
    assert report["refusal"]["reason"] == "run_ledger_missing"
    with ReserveLock(reserve, lock_dir=tmp_path / "locks") as lock:
        lock.record_path.write_text("{torn")
        with pytest.raises(CapitalLoopRefused, match="capital_loop_last_run_unreadable"):
            lock.last_run()


# ---- Wave 10: a run long enough for its conversions to settle, and loud when one did not

S = 1_000_000_000


def test_a_run_shorter_than_three_settlement_horizons_is_refused():
    from factorylab.runtime.capital_loop import settlement_bound

    rpc, tick = Rpc(), 10 * S
    host = rpc.final_ts + 960  # this host's clock: Base's real head, 16 minutes past final

    def bound(run_ns, lead_s=0):
        return settlement_bound(run_ns, tick, transport=rpc, now_s=lambda: host + lead_s)

    # The 600 s cap the signer obeys, whatever a quote says; twice how far finalized Base
    # is behind this host's clock (the 960 s lag, no lead); a tick.
    horizon = (600 + 2 * 960) * S + tick
    numbers = bound(3 * horizon)
    assert (numbers["validity_window_s"], numbers["finalized_behind_s"],
            numbers["minimum_run_ns"]) == (600, 960, 3 * horizon)
    blocks = [r["params"][0] for r in rpc.requests if r["method"] == "eth_getBlockByNumber"]
    assert blocks == ["finalized", "latest"]
    with pytest.raises(CapitalLoopRefused,
                       match="capital_loop_duration_below_settlement_bound") as refused:
        bound(3 * horizon - 1)
    assert refused.value.detail["minimum_run_ns"] == 3 * horizon
    # A stale "latest" answer (a node 958 s behind the head, reporting a 2 s lag) cannot
    # shorten it; latest-less-finalized would have allowed a run of 3 x 614 s.
    rpc.lag_s = 2
    assert bound(3 * horizon)["minimum_run_ns"] == 3 * horizon
    with pytest.raises(CapitalLoopRefused, match="below_settlement_bound"):
        bound(3 * (600 + 2 * 2 + 10) * S)
    rpc.lag_s = 960
    # A slow host clock cannot shrink it either: the latest block's time stands in.
    assert bound(3 * horizon, lead_s=-500)["finalized_behind_s"] == 960
    # A host clock ahead of Base stamps validBefore later in chain time: it lengthens it.
    with pytest.raises(CapitalLoopRefused, match="below_settlement_bound") as refused:
        bound(3 * horizon, lead_s=120)
    assert refused.value.detail["minimum_run_ns"] == 3 * (horizon + 2 * 120 * S)
    # A stale finalized block only lengthens it.
    rpc.final_ts -= 600
    with pytest.raises(CapitalLoopRefused, match="below_settlement_bound") as refused:
        bound(3 * horizon)
    assert refused.value.detail["finalized_behind_s"] == 1_560
    rpc.final_ts += 600
    # A provider answering "finalized" with its latest block is refused outright.
    rpc.lag_s = 0
    with pytest.raises(CapitalLoopRefused, match="finalized_tag_not_behind_latest"):
        bound(10**15)
    rpc.lag_s = 960

    def base_down(method, url, payload, headers):
        raise TimeoutError

    with pytest.raises(CapitalLoopRefused, match="finality_lag_unreadable"):
        settlement_bound(10**15, tick, transport=base_down, now_s=lambda: 0)


def test_the_runner_refuses_the_old_thirty_minute_run_and_a_ticks_bound_one(tmp_path):
    from factorylab.runtime.worlds import duration_ns

    write_run(tmp_path / "runs" / "earlier")  # live: it would refuse too, but later
    rpc = Rpc()
    report = rehearse(tmp_path / "runs" / "short", rpc, tmp_path,
                      duration_ns=duration_ns("30m"))
    assert report["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert report["refusal"]["minimum_run_ns"] == 3 * ((600 + 2 * 960) * S + 10 * S)
    report = rehearse(tmp_path / "runs" / "few-ticks", rpc, tmp_path,
                      duration_ns=duration_ns("3h"), target_ticks=361)  # one hour of ticks
    assert report["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert report["refusal"]["run_ns"] == 360 * 10 * S  # 361 ticks span 360 intervals


def test_n_ticks_are_credited_n_minus_one_intervals_exactly(tmp_path):
    # The clock's first tick comes at once: N ticks run for N - 1 intervals. The bound is
    # 7,590 s, 759 intervals of 10 s, so 760 ticks are just enough and 759 are not
    # (crediting 759 x 10 s used to admit the 759-tick run 10 s short).
    from factorylab.runtime.worlds import duration_ns

    rpc = Rpc()
    short = rehearse(tmp_path / "runs" / "759", rpc, tmp_path,
                     duration_ns=duration_ns("3h"), target_ticks=759)
    assert short["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert short["refusal"]["run_ns"] == 758 * 10 * S
    assert short["refusal"]["minimum_run_ns"] == 759 * 10 * S
    exact = rehearse(tmp_path / "runs" / "760", rpc, tmp_path,
                     duration_ns=duration_ns("3h"), target_ticks=760)
    assert exact["refusal"]["reason"] == "source_root_mismatch"  # the bound admitted it


def test_a_run_that_ends_with_a_top_up_submitted_says_so_loudly(tmp_path, capsys):
    from types import SimpleNamespace

    from factorylab.kernel.ledger import LedgerIntegrityError
    from scripts import edition4_rehearsal as rehearsal

    ledger = Ledger(clock_ns=lambda: 0)
    steps = ["shadow_send", "venice_top_up"]
    live = {"id": "treasury-1", "steps": steps, "index": 1, "status": "submitted",
            "reference": reference(LIVE_NONCE, 3_000), "route_data": {}}
    ledger.append({"kind": "treasury.step_submitted", "state": live})
    ledger.append({"kind": "treasury.pending", "transfer_id": "treasury-1",
                   "reason": "submission outcome unknown"})
    run = tmp_path / "runs" / "ended"
    report = {"capital_loop": {}}
    rehearsal._report_outstanding(report, SimpleNamespace(ledger=ledger), run)
    loud = report["capital_loop_outstanding"]
    assert loud["top_ups_submitted"] == [{"transfer_id": "treasury-1", "status": "submitted",
                                          "nonce": LIVE_NONCE, "valid_before": 3_000,
                                          "last_reason": "submission outcome unknown"}]
    assert loud["next_step"] == f"uv run python scripts/capital_loop_outstanding.py {run}"
    assert loud["next_step_argv"] == ["uv", "run", "python",
                                      "scripts/capital_loop_outstanding.py", str(run)]
    assert report["capital_loop"]["outstanding_at_end"] is loud
    assert capsys.readouterr().err == ""  # computed only: printed once report.json is on disk
    rehearsal._announce_outstanding(report)
    printed = capsys.readouterr()
    assert "CAPITAL LOOP OUTSTANDING" in printed.err and loud["next_step"] in printed.err
    assert '"capital_loop_outstanding"' in printed.out
    confirmed = {**live, "status": "confirmed", "receipts": [{"nonce": LIVE_NONCE}]}
    ledger.append({"kind": "treasury.confirmed", "state": confirmed})
    # A stop between treasury.confirmed and treasury.financing: the debit happened, the
    # booking did not. It stays unresolved, loudly, until the financing exists.
    stopped = {"capital_loop": {}}
    rehearsal._report_outstanding(stopped, SimpleNamespace(ledger=ledger), run)
    assert [(r["transfer_id"], r["status"]) for r in stopped["capital_loop_outstanding"][
        "top_ups_submitted"]] == [("treasury-1", "confirmed")]
    assert rehearsal.exit_code({"status": "stopped", **stopped}) == 3
    capsys.readouterr()
    ledger.append({"kind": "treasury.financing", "transfer_id": "treasury-1"})
    quiet = {"capital_loop": {}}
    rehearsal._report_outstanding(quiet, SimpleNamespace(ledger=ledger), run)
    rehearsal._announce_outstanding(quiet)
    assert "capital_loop_outstanding" not in quiet
    assert quiet["capital_loop"]["outstanding_at_end"]["top_ups_submitted"] == []
    assert capsys.readouterr().err == ""

    class Unreadable:
        def _recovery_items(self):
            raise LedgerIntegrityError("ledger verification failed")

    unread = {"capital_loop": {}}
    rehearsal._report_outstanding(unread, SimpleNamespace(ledger=Unreadable()), run)
    rehearsal._announce_outstanding(unread)
    assert unread["capital_loop_outstanding"]["diary_unreadable"] == "LedgerIntegrityError"
    assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err


def test_the_recovery_command_survives_a_run_directory_a_shell_would_split(tmp_path,
                                                                          capsys):
    # Codex on PR #144: an unquoted --out with a space or a ";" split the advertised
    # recovery command, or ran the rest as shell syntax, while a top-up might be live.
    import shlex
    import subprocess
    from types import SimpleNamespace

    from scripts import edition4_rehearsal as rehearsal

    ledger = Ledger(clock_ns=lambda: 0)
    ledger.append({"kind": "treasury.step_submitted", "state": {
        "id": "treasury-1", "steps": ["shadow_send", "venice_top_up"], "index": 1,
        "status": "submitted", "reference": reference(LIVE_NONCE, 3_000), "route_data": {}}})
    marker = tmp_path / "ran"
    run = tmp_path / f"my runs/first run; touch {marker} #"
    report = {"capital_loop": {}}
    rehearsal._report_outstanding(report, SimpleNamespace(ledger=ledger), run)
    loud = report["capital_loop_outstanding"]
    assert loud["next_step_argv"][-1] == str(run)
    assert shlex.split(loud["next_step"]) == loud["next_step_argv"]
    # A real shell reads the printed command as exactly those words and runs nothing
    # else: each word is echoed, not executed.
    words = subprocess.run(["sh", "-c", f'set -- {loud["next_step"]}; printf "%s\\n" "$@"'],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    assert words == loud["next_step_argv"] and not marker.exists()
    rehearsal._announce_outstanding(report)
    printed = capsys.readouterr()
    assert loud["next_step"] in printed.err
    assert json.loads(printed.out)["capital_loop_outstanding"]["next_step_argv"][-1] == (
        str(run))


def test_the_cli_summary_carries_the_outstanding_warning(tmp_path, monkeypatch, capsys):
    from scripts import edition4_rehearsal as rehearsal

    loud = {"warning": "unbooked", "top_ups_submitted": [{"transfer_id": "treasury-1"}],
            "next_step": "uv run python scripts/capital_loop_outstanding.py x"}
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *a, **k: {
        "status": "completed", "cost": {}, "capital_loop_outstanding": loud})
    # A completed run that left a top-up submitted is not a success to a shell.
    assert rehearsal.main(["--out", str(tmp_path / "x"), "--capital-loop"]) == 3
    assert json.loads(capsys.readouterr().out)["capital_loop_outstanding"] == loud
    assert rehearsal.exit_code({"status": "failed", "capital_loop_outstanding": {
        "diary_unreadable": "LedgerIntegrityError"}}) == 3
    shadow_only = {"top_ups_submitted": [], "shadow_sends_pending": [{"nonce": 1}]}
    assert rehearsal.exit_code({"status": "completed",
                                "capital_loop_outstanding": shadow_only}) == 0
    assert rehearsal.exit_code({"status": "failed"}) == 1
    assert rehearsal.exit_code({"status": "completed", "report_write_failed": "OSError"}) == 1

    class Gone:  # the terminal closed: every write to stdout fails
        def write(self, _text):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

    import sys

    monkeypatch.setattr(sys, "stdout", Gone())
    assert rehearsal.main(["--out", str(tmp_path / "y"), "--capital-loop"]) == 3


def repo_root():
    from pathlib import Path

    import factorylab

    return Path(factorylab.__file__).resolve().parents[1]


def test_an_exception_a_library_caller_catches_still_releases_the_reserve(tmp_path,
                                                                          monkeypatch):
    from factorylab.runtime.capital_loop import ReserveLock
    from scripts import edition4_rehearsal as rehearsal

    def broken(*_):  # anything that raises after the launch checks passed
        raise RuntimeError("roster unreadable")

    monkeypatch.setattr(rehearsal, "roster_hash", broken)
    with pytest.raises(RuntimeError, match="roster unreadable") as caught:
        rehearse(tmp_path / "runs" / "raises", Rpc(), tmp_path, source_root=repo_root())
    # The caller still holds the traceback, and with it every frame the lock lived in:
    # only an explicit release, not garbage collection, can have freed the reserve.
    assert caught.tb is not None
    reserve = capital_loop_world().treasury.reserve_address
    ReserveLock(reserve, lock_dir=tmp_path / "locks").close()  # free in this same process


@pytest.mark.gate
@pytest.mark.parametrize("write_fails", [False, True])
def test_a_real_launch_records_itself_before_its_world_runs_and_reports_its_end(
        tmp_path, monkeypatch, capsys, write_fails):
    """run_rehearsal itself, as the operator launches it, up to the world's first move:
    the real manifest loading, lock, checks, Runtime and HybridRail, faked only where
    bytes leave the process (Base's JSON-RPC, the SDK's HTTP post, and an offline
    urllib for everything else). The move itself is a stand-in: it journals a top-up the
    way the treasury does and is stopped by Ctrl-C, so the runner's own end is what is
    under test (the treasury's real top-up is in the next test)."""
    from pathlib import Path
    from urllib import error

    from eth_account import Account
    from hyperliquid.api import API

    from factorylab.runtime.capital_loop import ReserveLock
    from factorylab.world import x402
    from factorylab.world.exchange import HyperliquidExchange
    from factorylab.world.models import ModelResponse
    from scripts import edition4_rehearsal as rehearsal
    from tests.audit.test_edition4_rehearsal import StubProvider
    from tests.world.test_venice_hybrid import SINK, VenueWire

    main, reserve = Account.create(), Account.create()  # throwaway keys, never funded
    world = tmp_path / "edition6-capital-loop.toml"  # a manifest's name is its file stem
    world.write_text(Path("worlds/edition6-capital-loop.toml").read_text().replace(
        RESERVE, reserve.address))
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("HL_PRIVATE_KEY", main.key.hex())
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", reserve.key.hex())
    venue = VenueWire(main.address, SINK)
    monkeypatch.setattr(API, "post", lambda api, path, payload=None: venue.post(
        api, path, payload))

    class Offline:
        def open(self, request, timeout=None):
            raise error.URLError("offline")

    monkeypatch.setattr(x402.request, "build_opener", lambda *handlers: Offline())
    out, locks = tmp_path / "runs" / "live", tmp_path / "locks"
    seen = {}

    def first_move(runtime):
        # What the lock says the moment the world could first sign anything.
        seen["record"] = json.loads(
            (locks / f"{reserve.address.lower()}.last-run.json").read_text())
        seen["rail"] = runtime.treasury.rail.target.name
        steps = ["shadow_send", "venice_top_up"]
        runtime.ledger.append({"kind": "treasury.step_submitted", "state": {
            "id": "treasury-0", "steps": steps, "index": 1, "status": "submitted",
            "reference": reference(LIVE_NONCE, 3_000), "route_data": {}}})
        if write_fails:
            out.chmod(0o500)  # report.json can no longer be written
        raise KeyboardInterrupt  # the operator's Ctrl-C, mid-run

    monkeypatch.setattr(rehearsal.Runtime, "run", first_move)
    provider = StubProvider(ModelResponse("openai/gpt-6-luna", "{}", 1, 1, "stop",
                                          cost_micro=1))  # the roster's catalogue, no network
    rpc = Rpc()

    def launch():
        return rehearse(out, rpc, tmp_path, world=str(world), source_root=repo_root(),
                        provider=provider, exchange=HyperliquidExchange(mainnet=False))

    if write_fails:
        try:
            ended = launch()
        finally:
            out.chmod(0o700)
        assert not (out / "report.json").exists()
        # The write failed; the warning was printed anyway, and the exit code is still
        # the outstanding top-up's, not a failure's.
        assert ended["report_write_failed"] == "PermissionError"
        assert rehearsal.exit_code(ended) == 3
        assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err
        ReserveLock(reserve.address, lock_dir=locks).close()
        return
    ended = launch()
    assert ended["status"] == "stopped" and ended["stopped_by"] == "SIGINT", ended
    assert seen["record"] == {"reserve_address": reserve.address,
                              "run_dir": str(out.resolve())}
    assert seen["rail"] == "hypercore-testnet-venice-base-mainnet-hybrid"
    report = json.loads((out / "report.json").read_text())
    loud = report["capital_loop_outstanding"]
    assert [t["nonce"] for t in loud["top_ups_submitted"]] == [LIVE_NONCE]
    assert loud["next_step"] == f"uv run python scripts/capital_loop_outstanding.py {out}"
    assert rehearsal.exit_code(report) == 3
    assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err
    ReserveLock(reserve.address, lock_dir=locks).close()  # released on the way out


# ---- Wave 10, Codex on PR #144: a write-ahead authorization record outside the diary


def signed(nonce, valid_before):
    """A journal reference as the rail holds it when it is about to sign."""
    return {"authorization": {"nonce": nonce, "from": RESERVE, "to": "0x" + "9" * 40,
                              "value": 5_000_000, "validAfter": 0,
                              "validBefore": valid_before},
            "start_block": 800, "network": "eip155:8453"}


def test_a_diary_cut_at_a_line_boundary_cannot_hide_a_recorded_authorization(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations

    rpc = Rpc()
    locks = tmp_path / "locks"
    run = write_run(tmp_path / "elsewhere" / "cut", crash=False)
    with ReserveLock(RESERVE, lock_dir=locks) as lock:
        lock.record_run(run)
        lock.authorization_log(run)(signed(LIVE_NONCE, 3_000))  # before its signature
    # Cut the diary at a complete-line boundary just before the live step_submitted:
    # what is left is a valid, readable, non-empty prefix that shows nothing live.
    rewrite(run, diary_lines(run)[:3])
    assert LIVE_NONCE not in json.dumps(read_items(run))
    report = rehearse(tmp_path / "runs" / "after-cut", rpc, tmp_path)
    assert report["refusal"]["reason"] == "recorded_authorization_may_still_settle"
    assert [a["nonce"] for a in report["refusal"]["authorizations"]] == [LIVE_NONCE]
    rpc.final_ts = 3_001  # finalized Base is past its validBefore, and it is unused
    report = rehearse(tmp_path / "runs" / "after-expiry", rpc, tmp_path)
    assert report["refusal"]["reason"] == "source_root_mismatch"  # every check passed
    assert {"kind": "resolved", "nonce": LIVE_NONCE, "how": "expired"} in (
        read_authorizations(locks / f"{RESERVE.lower()}.authorizations.jsonl"))


def test_a_deleted_diary_cannot_hide_a_recorded_authorization(tmp_path, capsys):
    import shutil

    from factorylab.runtime.capital_loop import ReserveLock, acknowledge
    from scripts import edition4_rehearsal as rehearsal

    rpc = Rpc()
    locks = tmp_path / "locks"
    run = write_run(tmp_path / "elsewhere" / "deleted", crash=False)
    with ReserveLock(RESERVE, lock_dir=locks) as lock:
        lock.authorization_log(run)(signed(LIVE_NONCE, 3_000))
    shutil.rmtree(run)  # the diary is gone; the last-run record never named it
    report = rehearse(tmp_path / "runs" / "live", rpc, tmp_path)
    assert report["refusal"]["reason"] == "recorded_authorization_may_still_settle"
    # It settled after all, and no diary can show it was booked: a recovery.
    rpc.used.add(LIVE_NONCE)
    report = rehearse(tmp_path / "runs" / "settled", rpc, tmp_path)
    assert report["refusal"]["reason"] == "recorded_authorization_settled_unbooked"
    assert rehearsal.exit_code(report) == 3
    printed = capsys.readouterr().err
    assert "CAPITAL LOOP RECOVERY" in printed and f"--acknowledge {LIVE_NONCE}" in printed
    # Once the books are settled by hand, the operator acknowledges it, and only then.
    with ReserveLock(RESERVE, lock_dir=locks) as lock:
        with pytest.raises(CapitalLoopRefused, match="acknowledge_refused"):
            acknowledge(lock, OLD_NONCE, transport=rpc)  # not in the record
        assert acknowledge(lock, LIVE_NONCE, transport=rpc)["acknowledged"]
    assert rehearse(tmp_path / "runs" / "after", rpc, tmp_path)["refusal"]["reason"] == (
        "source_root_mismatch")


def test_a_recorded_authorization_the_diary_booked_resolves_once(tmp_path):
    from factorylab.runtime.capital_loop import (
        ReserveLock,
        check_authorization_record,
        read_authorizations,
    )

    rpc = Rpc()
    run = tmp_path / "booked"
    run.mkdir()
    path = run / "ledger.jsonl"
    ledger = Ledger(str(path), manifest={"name": "edition6-capital-loop"},
                    clock_ns=lambda: 0, key_path=str(path) + ".key")
    state = {"id": "treasury-0", "steps": ["shadow_send", "venice_top_up"], "index": 1,
             "status": "confirmed", "reference": signed(SETTLED_NONCE, 1_000),
             "receipts": [{"nonce": 1_700_000_000_000}, {"nonce": SETTLED_NONCE}]}
    ledger.append({"kind": "treasury.confirmed", "state": state})
    ledger.append({"kind": "treasury.financing", "transfer_id": "treasury-0"})
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(run)(signed(SETTLED_NONCE, 1_000))
        summary = check_authorization_record(lock, transport=rpc)
        assert [(r["nonce"], r["how"]) for r in summary["resolved_now"]] == [
            (SETTLED_NONCE, "financed")]
        # Resolved for good: the diary is no longer needed to prove it.
        path.unlink()
        assert check_authorization_record(lock, transport=rpc)["open"] == 0
    entries = read_authorizations(tmp_path / f"{RESERVE.lower()}.authorizations.jsonl")
    assert [e["kind"] for e in entries] == ["authorization", "resolved"]
    assert entries[0]["run_dir"] == str(run.resolve()) and entries[0]["validBefore"] == "1000"


def test_the_authorization_record_refuses_damage_and_tolerates_only_a_torn_append(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        log = lock.authorization_log(tmp_path / "run")
        log(signed(LIVE_NONCE, 3_000))
        with pytest.raises(ValueError, match="payer"):
            log({**signed(OLD_NONCE, 3_000), "authorization": {
                **signed(OLD_NONCE, 3_000)["authorization"], "from": "0x" + "12" * 20}})
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    whole = path.read_bytes()
    path.write_bytes(whole + b'{"kind": "authoriz')  # a crash mid-append: never signed
    assert len(read_authorizations(path)) == 1
    path.write_bytes(b"not json\n" + whole)
    with pytest.raises(CapitalLoopRefused, match="authorization_record_unreadable"):
        read_authorizations(path)


def test_the_script_acknowledges_only_what_finalized_base_shows_used(tmp_path, capsys):
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations
    from scripts import capital_loop_outstanding

    rpc = Rpc()
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(tmp_path / "run")(signed(LIVE_NONCE, 3_000))
    argv = ["--acknowledge", LIVE_NONCE, "--lock-dir", str(tmp_path)]
    assert capital_loop_outstanding.main(argv, transport=rpc) == 2  # unused: refused
    assert "acknowledge_refused" in capsys.readouterr().err
    rpc.used.add(LIVE_NONCE)
    with ReserveLock(RESERVE, lock_dir=tmp_path):  # a run is alive: it waits for it
        assert capital_loop_outstanding.main(argv, transport=rpc) == 2
    assert "capital_loop_reserve_locked" in capsys.readouterr().err
    assert capital_loop_outstanding.main(argv, transport=rpc) == 0
    entries = read_authorizations(tmp_path / f"{RESERVE.lower()}.authorizations.jsonl")
    assert entries[-1] == {"kind": "resolved", "nonce": LIVE_NONCE, "how": "acknowledged"}
