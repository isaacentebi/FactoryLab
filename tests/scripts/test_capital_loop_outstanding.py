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
from factorylab.world.x402 import VENICE_URL, HTTPResponse
from tests.world.test_x402 import quote as quote_fixture

RESERVE = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
LIVE_NONCE = "0x" + "11" * 32
OLD_NONCE = "0x" + "22" * 32
SETTLED_NONCE = "0x" + "33" * 32


class Rpc:
    """Base mainnet as the public JSON-RPC answers it, Venice's unpaid quote beside it, and
    a record of every request."""

    def __init__(self):
        self.balance = 10_000_000
        self.final_number, self.final_ts = 900, 2_000
        self.lag_s = 960  # latest less finalized, as measured on Base mainnet
        self.used = {SETTLED_NONCE}
        self.requests = []
        self.quotes = []
        self.max_timeout_s = 300

    def __call__(self, method, url, payload, headers):
        if url.startswith(VENICE_URL):
            # The unpaid 402 quote: no payment header, no credential, nothing signed.
            assert (method, url, payload, headers) == (
                "POST", VENICE_URL + "/x402/top-up", {}, {})
            self.quotes.append(url)
            body = quote_fixture.__wrapped__()
            body["accepts"][0]["maxTimeoutSeconds"] = self.max_timeout_s
            return HTTPResponse(402, body)
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

    kwargs.setdefault("duration_ns", duration_ns("2h"))
    return rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml", out=out,
                                   capital_loop=True, capital_loop_transport=rpc,
                                   source_root=tmp_path, provider=object(),
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
    assert rpc.requests == [] and rpc.quotes == []  # refused before the chain was read
    in_flight.close()
    report = rehearse(tmp_path / "runs" / "third", rpc, tmp_path)
    assert report["refusal"]["reason"] == "source_root_mismatch"  # every capital check passed
    ReserveLock(reserve, lock_dir=tmp_path / "locks").close()  # the runner released it


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
    horizon = (300 + 960) * S + tick  # quote window + Base finality lag + one tick
    numbers = settlement_bound(3 * horizon, tick, transport=rpc)
    assert (numbers["validity_window_s"], numbers["validity_source"],
            numbers["finality_lag_s"], numbers["minimum_run_ns"]) == (300, "quote", 960,
                                                                      3 * horizon)
    assert rpc.quotes == [VENICE_URL + "/x402/top-up"]  # the unpaid quote, nothing signed
    with pytest.raises(CapitalLoopRefused,
                       match="capital_loop_duration_below_settlement_bound") as refused:
        settlement_bound(3 * horizon - 1, tick, transport=rpc)
    assert refused.value.detail["minimum_run_ns"] == 3 * horizon
    rpc.max_timeout_s = 3_600  # the signer caps every window at 600 s; so does the bound
    assert settlement_bound(3 * ((600 + 960) * S + tick), tick,
                            transport=rpc)["validity_window_s"] == 600

    def venice_down(method, url, payload, headers):
        if url.startswith(VENICE_URL):
            raise TimeoutError
        return rpc(method, url, payload, headers)

    # An unread quote is replaced by the cap, which only lengthens the bound.
    rpc.max_timeout_s = 300
    with pytest.raises(CapitalLoopRefused, match="below_settlement_bound"):
        settlement_bound(3 * horizon, tick, transport=venice_down)
    assert settlement_bound(3 * ((600 + 960) * S + tick), tick,
                            transport=venice_down)["validity_source"] == "cap"
    rpc.lag_s = -1  # a latest block older than the finalized one is no measurement
    with pytest.raises(CapitalLoopRefused, match="finality_lag_unreadable"):
        settlement_bound(10**15, tick, transport=rpc)

    def base_down(method, url, payload, headers):
        if url.startswith(VENICE_URL):
            return rpc(method, url, payload, headers)
        raise TimeoutError

    with pytest.raises(CapitalLoopRefused, match="finality_lag_unreadable"):
        settlement_bound(10**15, tick, transport=base_down)


def test_the_runner_refuses_the_old_thirty_minute_run_and_a_ticks_bound_one(tmp_path):
    from factorylab.runtime.worlds import duration_ns

    write_run(tmp_path / "runs" / "earlier")  # live: it would refuse too, but later
    rpc = Rpc()
    report = rehearse(tmp_path / "runs" / "short", rpc, tmp_path,
                      duration_ns=duration_ns("30m"))
    assert report["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert report["refusal"]["minimum_run_ns"] == 3 * ((300 + 960) * S + 10 * S)
    report = rehearse(tmp_path / "runs" / "few-ticks", rpc, tmp_path,
                      duration_ns=duration_ns("2h"), target_ticks=360)  # one hour of ticks
    assert report["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert report["refusal"]["run_ns"] == 360 * 10 * S


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
    assert loud["top_ups_submitted"] == [{"transfer_id": "treasury-1", "nonce": LIVE_NONCE,
                                          "valid_before": 3_000,
                                          "last_reason": "submission outcome unknown"}]
    assert loud["next_step"] == f"uv run python scripts/capital_loop_outstanding.py {run}"
    assert report["capital_loop"]["outstanding_at_end"] is loud
    printed = capsys.readouterr()
    assert "CAPITAL LOOP OUTSTANDING" in printed.err and loud["next_step"] in printed.err
    assert '"capital_loop_outstanding"' in printed.out
    ledger.append({"kind": "treasury.confirmed", "state": {**live, "status": "confirmed"}})
    quiet = {"capital_loop": {}}
    rehearsal._report_outstanding(quiet, SimpleNamespace(ledger=ledger), run)
    assert "capital_loop_outstanding" not in quiet
    assert quiet["capital_loop"]["outstanding_at_end"]["top_ups_submitted"] == []
    assert capsys.readouterr().err == ""

    class Unreadable:
        def _recovery_items(self):
            raise LedgerIntegrityError("ledger verification failed")

    unread = {"capital_loop": {}}
    rehearsal._report_outstanding(unread, SimpleNamespace(ledger=Unreadable()), run)
    assert unread["capital_loop_outstanding"]["diary_unreadable"] == "LedgerIntegrityError"
    assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err


def test_the_cli_summary_carries_the_outstanding_warning(tmp_path, monkeypatch, capsys):
    from scripts import edition4_rehearsal as rehearsal

    loud = {"warning": "unbooked",
            "next_step": "uv run python scripts/capital_loop_outstanding.py x"}
    monkeypatch.setattr(rehearsal, "run_rehearsal", lambda *a, **k: {
        "status": "completed", "cost": {}, "capital_loop_outstanding": loud})
    assert rehearsal.main(["--out", str(tmp_path / "x"), "--capital-loop"]) == 0
    assert json.loads(capsys.readouterr().out)["capital_loop_outstanding"] == loud
