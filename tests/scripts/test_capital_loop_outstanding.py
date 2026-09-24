"""The capital loop's keyless chain reads: the launch check and the outstanding report.

Nothing here touches a network or a signing key: Base is a fake JSON-RPC transport and
each run's diary is a real encrypted ledger written with its own sealing key file, the
way a rehearsal writes one and ``factorylab postmortem`` reads one.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.capital_loop import (
    AUTHORIZATION_CANCELED,
    AUTHORIZATION_USED,
    TRANSFER,
    CapitalLoopRefused,
    journaled_references,
    keyless_base,
    launch_check,
    outstanding,
    read_items,
)
from factorylab.runtime.worlds import load_manifest
from factorylab.world.evm import BASE, event_topic
from factorylab.world.x402 import HTTPResponse

RESERVE = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
LIVE_NONCE = "0x" + "11" * 32
OLD_NONCE = "0x" + "22" * 32
SETTLED_NONCE = "0x" + "33" * 32


class Rpc:
    """Base mainnet as its public JSON-RPC answers it: per block tag and per authorizer.

    Block ``n`` has timestamp ``stamp(n)``: one second apart by default, or at the
    irregular ``gaps`` a test sets. The finalized block is the last at or before
    ``final_ts``; the latest is ``lag_s`` seconds later. ``use`` makes one authorizer's
    nonce used at a moment, and every answer comes from that one fact at the block asked
    for: ``authorizationState`` is true only for that authorizer and only at a block at or
    after it (so a read at ``latest`` sees what finalized Base does not yet), and
    ``eth_getLogs`` returns its ``AuthorizationUsed`` and ``Transfer`` only for matching
    topics inside the asked range. ``drift`` moves the finalized tag forward that many
    blocks after each time it is read, as a provider's does between two calls.
    ``cancel`` makes a nonce canceled (``AuthorizationCanceled``, state true, no debit);
    ``consume`` moves an account's nonce at a moment (``eth_getTransactionCount`` at the
    asked block); ``by_hash`` answers ``eth_getTransactionByHash``; a block in ``missing``
    cannot be read; a URL in ``others`` is another chain's node. Every request is
    recorded.
    """

    def __init__(self, *, gaps=(1,)):
        self.balance = 10_000_000
        self.gaps = tuple(gaps)
        self.lag_s = 960  # latest less finalized, as measured on Base mainnet
        self.drift = 0
        self.extra_logs = []
        self.canceled: dict[str, tuple[str, int]] = {}
        self.nonces: dict[str, list[tuple[int, int]]] = {}
        self.by_hash: dict[str, dict] = {}
        self.missing: set[int] = set()
        self.others = {}
        self.used: dict[str, tuple[str, int, str]] = {}
        self.requests = []
        self._final_number = 0
        self.final_ts = 12_000
        self.use(SETTLED_NONCE, at_ts=8_600)  # settled well before the cooling-off window

    def stamp(self, number):
        cycle, rest = divmod(number, len(self.gaps))
        return cycle * sum(self.gaps) + sum(self.gaps[:rest])

    def number_at(self, timestamp):
        """The last block at or before ``timestamp``."""
        low, high = 0, max(1, timestamp) * 4
        while low < high:
            middle = (low + high + 1) // 2
            if self.stamp(middle) <= timestamp:
                low = middle
            else:
                high = middle - 1
        return low

    @property
    def final_ts(self):
        return self.stamp(self._final_number)

    @final_ts.setter
    def final_ts(self, timestamp):
        self._final_number = self.number_at(timestamp)

    @property
    def final_number(self):
        return self._final_number

    @property
    def latest_number(self):
        return self.number_at(self.final_ts + self.lag_s)

    @property
    def latest_ts(self):
        return self.final_ts + self.lag_s

    def use(self, nonce, *, at_ts, authorizer=RESERVE, payee="0x" + "9" * 40):
        self.used[nonce.lower()] = (authorizer.lower(), at_ts, payee.lower())

    def cancel(self, nonce, *, at_ts, authorizer=RESERVE):
        self.canceled[nonce.lower()] = (authorizer.lower(), at_ts)

    def consume(self, account, count, *, at_ts):
        self.nonces.setdefault(account.lower(), []).append((at_ts, count))

    def account_nonce(self, account, number):
        moves = self.nonces.get(account.lower(), [])
        return max([c for ts, c in moves if ts <= self.stamp(number)] or [0])

    def block(self, number):
        if not 0 <= number <= self.latest_number or number in self.missing:
            return None
        return {"number": hex(number), "hash": "0x" + number.to_bytes(32).hex(),
                "timestamp": hex(self.stamp(number))}

    def at(self, tag):
        if tag == "finalized":
            number = self._final_number
            self._final_number += self.drift
            return number
        return self.latest_number if tag == "latest" else int(tag, 16)

    def logs(self, query):
        low, high = int(query["fromBlock"], 16), int(query["toBlock"], 16)
        rows = list(self.extra_logs)
        for nonce, (authorizer, at_ts, payee) in self.used.items():
            number = self.number_at(at_ts)
            where = {"address": BASE.usdc, "blockNumber": hex(number),
                     "blockHash": "0x" + number.to_bytes(32).hex(),
                     "transactionHash": "0x" + nonce[2:][::-1]}
            word = "0x" + "0" * 24 + authorizer[2:]
            rows += [{**where, "topics": [event_topic(AUTHORIZATION_USED), word, nonce],
                      "data": "0x"},
                     {**where, "topics": [event_topic(TRANSFER), word,
                                          "0x" + "0" * 24 + payee[2:]],
                      "data": hex(5_000_000)}]
        for nonce, (authorizer, at_ts) in self.canceled.items():
            number = self.number_at(at_ts)
            rows.append({"address": BASE.usdc, "blockNumber": hex(number),
                         "blockHash": "0x" + number.to_bytes(32).hex(),
                         "transactionHash": "0x" + nonce[2:][::-1], "data": "0x",
                         "topics": [event_topic(AUTHORIZATION_CANCELED),
                                    "0x" + "0" * 24 + authorizer[2:], nonce]})
        wanted = query["topics"]
        return [r for r in rows if low <= int(r["blockNumber"], 16) <= high
                and query["address"].lower() == r["address"].lower()
                and all(w is None or w.lower() == g.lower()
                        for w, g in zip(wanted, r["topics"], strict=False))]

    def __call__(self, method, url, payload, headers):
        if url in self.others:
            return self.others[url](method, url, payload, headers)
        assert url == BASE.rpc, url  # nothing else is asked, Venice's quote included
        self.requests.append(payload)
        name, params = payload["method"], payload["params"]
        if name == "eth_chainId":
            result = hex(BASE.id)
        elif name == "eth_blockNumber":
            result = hex(self.latest_number)
        elif name == "eth_getBlockByNumber":
            result = self.block(self.at(params[0]))
        elif name == "eth_call":
            data, tag = params[0]["data"], params[1]
            if data.startswith("0x70a08231"):  # balanceOf(address)
                result = hex(self.balance)
            else:  # authorizationState(address,bytes32), as of the asked block
                authorizer, nonce = "0x" + data[34:74].lower(), "0x" + data[74:138].lower()
                fact = self.used.get(nonce) or self.canceled.get(nonce)
                result = hex(int(fact is not None and fact[0] == authorizer
                                 and self.number_at(fact[1]) <= self.at(tag)))
        elif name == "eth_getLogs":
            result = self.logs(params[0])
        elif name == "eth_getTransactionCount":
            result = hex(self.account_nonce(params[0], self.at(params[1])))
        elif name == "eth_getTransactionByHash":
            result = self.by_hash.get(params[0].lower())
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
               "reference": reference(SETTLED_NONCE, 9_000), "route_data": {}}
    ledger.append({"kind": "treasury.step_submitted", "state": settled})
    ledger.append({"kind": "treasury.confirmed", "state": {**settled, "status": "confirmed"}})
    current = {"id": "treasury-1", "steps": steps, "index": 1, "status": "submitted",
               "reference": reference(LIVE_NONCE, 13_000),
               "route_data": {"superseded_references": [reference(OLD_NONCE, 11_500)]}}
    ledger.append({"kind": "treasury.step_submitted", "state": current})
    prepared_only = reference("0x" + "44" * 32, 13_000)  # never journaled toward a signature
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
    assert rows[LIVE_NONCE]["finalized_timestamp"] == 12_000
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
    assert "validBefore 13000" in out and "finalized_ts 12000" in out


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
    rpc.final_ts = 13_001  # finalized Base is now past its validBefore, still unused
    assert launch_check(world, previous_runs=(run,), transport=rpc)["previous_runs"]


def rehearse(out, rpc, tmp_path, **kwargs):
    """A capital-loop launch against fakes: the lock lives in the test's own directory.

    A funded run takes neither a transport nor a lock directory from its caller, so the
    fakes are swapped in where the runner itself reads them (its HTTP transport and the
    operator's lock directory) for this launch, and put back.
    """
    from factorylab.runtime import capital_loop
    from factorylab.runtime.worlds import duration_ns
    from scripts import edition4_rehearsal as rehearsal

    kwargs.setdefault("duration_ns", duration_ns("3h"))
    kwargs.setdefault("source_root", tmp_path)
    kwargs.setdefault("provider", object())
    kwargs.setdefault("world", "worlds/edition6-capital-loop.toml")
    # A capital-loop run reads only the wall clock; here the wall reads Base's latest
    # block (no lead to add to the bound). Swapped in for this launch and put back.
    wall, http, locks = (rehearsal._wall_ns, rehearsal._http_request,
                         capital_loop.default_lock_dir)
    rehearsal._wall_ns = lambda: rpc.latest_ts * 1_000_000_000
    rehearsal._http_request = lambda: rpc
    capital_loop.default_lock_dir = lambda: tmp_path / "locks"
    try:
        return rehearsal.run_rehearsal(out=out, capital_loop=True, **kwargs)
    finally:
        rehearsal._wall_ns, rehearsal._http_request = wall, http
        capital_loop.default_lock_dir = locks


def test_the_rehearsal_runner_runs_the_launch_check_before_anything_is_built(
        tmp_path, capsys):
    write_run(tmp_path / "runs" / "earlier")
    rpc = Rpc()
    report = rehearse(tmp_path / "runs" / "now", rpc, tmp_path)
    assert report["status"] == "failed"
    assert report["refusal"]["reason"] == "previous_run_authorization_may_still_settle"
    rpc.final_ts = 13_001
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
    rpc.final_ts = 13_001  # nothing it holds is live any more: only its length matters
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
    rpc.final_ts = 13_001  # finalized Base is past the live authorization's validBefore
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
        lock.authorization_log(run)(signed(LIVE_NONCE, 13_000), 13_000 - 700)  # before signing
    # Cut the diary at a complete-line boundary just before the live step_submitted:
    # what is left is a valid, readable, non-empty prefix that shows nothing live.
    rewrite(run, diary_lines(run)[:3])
    assert LIVE_NONCE not in json.dumps(read_items(run))
    report = rehearse(tmp_path / "runs" / "after-cut", rpc, tmp_path)
    assert report["refusal"]["reason"] == "recorded_authorization_may_still_settle"
    assert [a["nonce"] for a in report["refusal"]["authorizations"]] == [LIVE_NONCE]
    rpc.final_ts = 13_001  # finalized Base is past its validBefore, and it is unused
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
        lock.authorization_log(run)(signed(LIVE_NONCE, 13_000), 13_000 - 700)
    shutil.rmtree(run)  # the diary is gone; the last-run record never named it
    report = rehearse(tmp_path / "runs" / "live", rpc, tmp_path)
    assert report["refusal"]["reason"] == "recorded_authorization_may_still_settle"
    # It settled after all, and no diary can show it was booked: a recovery.
    rpc.use(LIVE_NONCE, at_ts=12_700)
    rpc.final_ts = 13_001
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
             "status": "confirmed", "reference": signed(SETTLED_NONCE, 9_000),
             "receipts": [{"nonce": 1_700_000_000_000}, {"nonce": SETTLED_NONCE}]}
    ledger.append({"kind": "treasury.confirmed", "state": state})
    ledger.append({"kind": "treasury.financing", "transfer_id": "treasury-0"})
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(run)(signed(SETTLED_NONCE, 9_000), 9_000 - 700)
        summary = check_authorization_record(lock, transport=rpc)
        assert [(r["nonce"], r["how"]) for r in summary["resolved_now"]] == [
            (SETTLED_NONCE, "financed")]
        # Resolved for good: the diary is no longer needed to prove it.
        path.unlink()
        assert check_authorization_record(lock, transport=rpc)["open"] == 0
    entries = read_authorizations(tmp_path / f"{RESERVE.lower()}.authorizations.jsonl")
    assert [e["kind"] for e in entries] == ["authorization", "resolved"]
    assert entries[0]["run_dir"] == str(run.resolve()) and entries[0]["validBefore"] == "9000"


def test_the_authorization_record_refuses_damage_and_a_torn_last_line(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        log = lock.authorization_log(tmp_path / "run")
        log(signed(LIVE_NONCE, 13_000), 13_000 - 700)
        with pytest.raises(ValueError, match="payer"):
            log({**signed(OLD_NONCE, 13_000), "authorization": {
                **signed(OLD_NONCE, 13_000)["authorization"], "from": "0x" + "12" * 20}},
                12_300)
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    whole = path.read_bytes()
    path.write_bytes(whole + b'{"kind": "authoriz')  # a crash mid-append
    with pytest.raises(CapitalLoopRefused, match="authorization_record_torn") as refused:
        read_authorizations(path)
    assert "--repair-torn" in refused.value.detail["repair"]
    path.write_bytes(b"not json\n" + whole)
    with pytest.raises(CapitalLoopRefused, match="authorization_record_unreadable"):
        read_authorizations(path)


def test_an_append_never_lands_on_a_torn_fragment(tmp_path):
    # The cold review: after a crash mid-append the next append wrote onto the fragment,
    # "{frag}{new}\n", and every launch after refused the record forever.
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations

    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        log = lock.authorization_log(tmp_path / "run")
        log(signed(LIVE_NONCE, 13_000), 13_000 - 700)
        whole = path.read_bytes()
        path.write_bytes(whole + b'{"kind": "authorization", "nonce": "0x' + b"ab" * 20)
        with pytest.raises(CapitalLoopRefused, match="authorization_record_torn"):
            log(signed(OLD_NONCE, 13_000), 13_000 - 700)
        assert path.read_bytes().endswith(b"ab" * 20)  # nothing was appended onto it
        # A whole entry that lost only its newline counts, and is completed first.
        path.write_bytes(whole[:-1])
        assert [e["nonce"] for e in read_authorizations(path)] == [LIVE_NONCE]
        log(signed(OLD_NONCE, 13_000), 13_000 - 700)
    assert path.read_bytes().count(b"\n") == 2
    assert [e["nonce"] for e in read_authorizations(path)] == [LIVE_NONCE, OLD_NONCE]


def test_a_torn_fragment_is_set_aside_and_its_nonce_resolved_like_any_other(
        tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace

    from factorylab.runtime import capital_loop
    from factorylab.runtime.capital_loop import (
        ReserveLock,
        check_authorization_record,
        read_authorizations,
    )
    from scripts import capital_loop_outstanding

    # The script repairs at the host's time: here chain time 12_400 (its bound 13_000).
    monkeypatch.setattr(capital_loop, "time", SimpleNamespace(time_ns=lambda: 12_400 * 10**9))
    rpc = Rpc()
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(tmp_path / "run")(signed(OLD_NONCE, 11_500), 11_500 - 700)
    fragment = (b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "validBefore": "13000", "va')
    path.write_bytes(path.read_bytes() + fragment)
    argv = ["--repair-torn", "--lock-dir", str(tmp_path)]
    assert capital_loop_outstanding.main(argv, transport=rpc) == 0
    repaired = json.loads(capsys.readouterr().out)
    sidecar = tmp_path / repaired["sidecar"].rsplit("/", 1)[1]
    assert sidecar.read_bytes() == fragment  # nothing deleted, only moved aside
    torn = read_authorizations(path)[-1]
    assert torn["kind"] == "torn" and torn["nonces"] == [LIVE_NONCE]
    assert bytes.fromhex(torn["fragment_hex"]) == fragment and torn["validBefore"] == "13000"
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        # The fragment's nonce is open, and resolved against the chain like any other.
        with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
            check_authorization_record(lock, transport=rpc)
        rpc.final_ts = 13_001
        resolved = check_authorization_record(lock, transport=rpc)
        # (the expired one was resolved by the refused check already; now the other)
        assert {(r["nonce"], r["how"]) for r in resolved["resolved_now"]} == {
            (LIVE_NONCE, "expired")}
        lock.authorization_log(tmp_path / "run")(signed("0x" + "55" * 32, 14_000), 14_000 - 700)
    assert capital_loop_outstanding.main(argv, transport=rpc) == 0  # nothing torn: a no-op
    assert json.loads(capsys.readouterr().out)["repaired"] is False


def test_the_script_acknowledges_only_what_finalized_base_shows_used(tmp_path, capsys):
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations
    from scripts import capital_loop_outstanding

    rpc = Rpc()
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(tmp_path / "run")(signed(LIVE_NONCE, 13_000), 13_000 - 700)
    argv = ["--acknowledge", LIVE_NONCE, "--lock-dir", str(tmp_path)]
    assert capital_loop_outstanding.main(argv, transport=rpc) == 2  # unused: refused
    assert "acknowledge_refused" in capsys.readouterr().err
    rpc.use(LIVE_NONCE, at_ts=12_700)
    rpc.final_ts = 13_001
    with ReserveLock(RESERVE, lock_dir=tmp_path):  # a run is alive: it waits for it
        assert capital_loop_outstanding.main(argv, transport=rpc) == 2
    assert "capital_loop_reserve_locked" in capsys.readouterr().err
    assert capital_loop_outstanding.main(argv, transport=rpc) == 0
    entries = read_authorizations(tmp_path / f"{RESERVE.lower()}.authorizations.jsonl")
    assert entries[-1] == {"kind": "resolved", "nonce": LIVE_NONCE, "how": "acknowledged"}


# ---- Wave 10, the cold review of 634d29a


def test_a_rolled_back_record_cannot_hide_a_settled_authorization(tmp_path):
    # A backup restored with its directory: the record forgets an authorization that
    # already settled. Finalized Base remembers it, inside one settlement window.
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check
    from scripts import edition4_rehearsal as rehearsal

    rpc = Rpc()
    rpc.use(LIVE_NONCE, at_ts=11_700)  # settled five minutes before the finalized head
    report = rehearse(tmp_path / "runs" / "rolled-back", rpc, tmp_path)
    assert report["refusal"]["reason"] == "unrecorded_reserve_authorization"
    assert report["refusal"]["nonces"] == [LIVE_NONCE]
    assert rehearsal.exit_code(report) == 3  # real money moved and nothing knows it
    with ReserveLock(RESERVE, lock_dir=tmp_path / "locks") as lock:
        # Recorded, the same chain is clean; and a window that has passed is too.
        lock.authorization_log(tmp_path / "run")(signed(LIVE_NONCE, 12_000), 12_000 - 700)
        assert cooling_off_check(lock, window_s=2_520, transport=rpc)[
            "authorizations_seen"] == 1
    rpc.final_ts = 16_000  # the settlement is now far older than any window
    fresh = tmp_path / "fresh-locks"
    with ReserveLock(RESERVE, lock_dir=fresh) as lock:
        assert cooling_off_check(lock, window_s=2_520, transport=rpc)[
            "authorizations_seen"] == 0


def test_money_leaving_the_reserve_without_a_recorded_authorization_is_refused(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check

    rpc = Rpc()
    # A plain Transfer out of the reserve, not an EIP-3009 authorization.
    rpc.extra_logs.append({
        "address": BASE.usdc, "blockNumber": hex(11_950),
        "blockHash": "0x" + (11_950).to_bytes(32).hex(), "transactionHash": "0x" + "ee" * 32,
        "data": hex(1), "topics": [event_topic(TRANSFER), "0x" + "0" * 24 + RESERVE.lower()[2:],
                                   "0x" + "0" * 24 + "12" * 20]})
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        with pytest.raises(CapitalLoopRefused, match="unrecorded_reserve_transfer") as refused:
            cooling_off_check(lock, window_s=2_520, transport=rpc)
    assert refused.value.detail["transactions"] == ["0x" + "ee" * 32]


def test_a_7599_second_duration_is_credited_7580_seconds_and_refused(tmp_path):
    # The clock delivers floor(7599 / 10) = 759 ticks, which span 758 intervals.
    rpc = Rpc()
    report = rehearse(tmp_path / "runs" / "7599", rpc, tmp_path, duration_ns=7_599 * S)
    assert report["refusal"]["reason"] == "capital_loop_duration_below_settlement_bound"
    assert (report["refusal"]["run_ns"], report["refusal"]["minimum_run_ns"]) == (
        7_580 * S, 7_590 * S)
    assert rehearse(tmp_path / "runs" / "7600", rpc, tmp_path, duration_ns=7_600 * S)[
        "refusal"]["reason"] == "source_root_mismatch"
    # --ticks with a shorter deadline is credited the lesser of the two.
    report = rehearse(tmp_path / "runs" / "both", rpc, tmp_path, duration_ns=7_599 * S,
                      target_ticks=10_000)
    assert report["refusal"]["run_ns"] == 7_580 * S


def test_the_capital_loop_refuses_an_injected_clock(tmp_path):
    from scripts import edition4_rehearsal as rehearsal

    with pytest.raises(rehearsal.RehearsalRefused, match="capital_loop_requires_the_wall_clock"):
        rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                out=tmp_path / "runs" / "virtual", capital_loop=True,
                                provider=object(), now_ns=lambda: 1_000_000_000)


def test_nothing_is_resolved_from_one_read_or_from_the_wrong_block_or_address(tmp_path):
    from factorylab.runtime.capital_loop import (
        ReserveLock,
        acknowledge,
        check_authorization_record,
    )

    rpc = Rpc()
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(tmp_path / "run")(signed(LIVE_NONCE, 13_000), 13_000 - 700)
        # Used after the finalized head: a read at "latest" would call it used.
        rpc.use(LIVE_NONCE, at_ts=12_400)
        with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
            check_authorization_record(lock, transport=rpc)
        # Used, but by another authorizer with the same nonce: not ours.
        rpc.use(LIVE_NONCE, at_ts=12_400, authorizer="0x" + "12" * 20)
        rpc.final_ts = 13_001
        assert [r["how"] for r in check_authorization_record(lock, transport=rpc)[
            "resolved_now"]] == ["expired"]
        lock.authorization_log(tmp_path / "run")(signed(OLD_NONCE, 13_000), 13_000 - 700)
        rpc.use(OLD_NONCE, at_ts=12_700)
        # The two reads disagree: the state says used, the logs say nothing.
        honest = rpc.logs
        rpc.logs = lambda query: []
        for resolve in (lambda: check_authorization_record(lock, transport=rpc),
                        lambda: acknowledge(lock, OLD_NONCE, transport=rpc)):
            with pytest.raises(CapitalLoopRefused) as refused:
                resolve()
            assert "recorded_authorization_reads_disagree" in (
                refused.value.reason, refused.value.detail.get("why"))
        rpc.logs = honest
        # A finalized tag aliased to latest refuses before anything is read or written.
        rpc.lag_s = 0
        with pytest.raises(CapitalLoopRefused, match="finalized_tag_not_behind_latest"):
            check_authorization_record(lock, transport=rpc)
        rpc.lag_s = 960
        entries = lock.authorizations()
        assert [e["nonce"] for e in entries if e["kind"] == "resolved"] == [LIVE_NONCE]
    requests = [r for r in rpc.requests if r["method"] == "eth_call"]
    assert all(r["params"][1] != "latest" for r in requests)  # never read at latest


def test_a_lock_that_predates_the_record_names_the_three_file_reset(tmp_path):
    from factorylab.runtime.capital_loop import ReserveLock

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorizations_path.unlink()  # as a lock made before the record existed
        with pytest.raises(CapitalLoopRefused,
                           match="capital_loop_authorization_record_missing") as refused:
            lock.authorizations()
    reset = refused.value.detail["reset"]
    for name in (".lock", ".last-run.json", ".authorizations.jsonl"):
        assert f"{RESERVE.lower()}{name}" in reset
    assert "copy" in reset and "aside" in reset
# ---- Wave 10, the reviews of 0b5b487


def record(tmp_path, *entries):
    """A reserve's write-ahead record holding exactly ``entries``, as raw JSON lines (a
    legacy entry is written as an older signer wrote it, without the newer fields)."""
    from factorylab.runtime.capital_loop import ReserveLock

    ReserveLock(RESERVE, lock_dir=tmp_path).close()  # creates the lock's three files
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    path.write_bytes(b"".join(json.dumps(e, sort_keys=True).encode() + b"\n"
                              for e in entries))
    return path


def entry(nonce, valid_before, **fields):
    """A recorded authorization; ``fields`` adds (or, set to None, drops) a field."""
    row = {"kind": "authorization", "nonce": nonce, "from": RESERVE, "to": "0x" + "9" * 40,
           "value": "5000000", "validAfter": "0", "validBefore": str(valid_before)}
    row.update(fields)
    return {k: v for k, v in row.items() if v is not None}


def get_logs(rpc):
    return [(int(r["params"][0]["fromBlock"], 16), int(r["params"][0]["toBlock"], 16))
            for r in rpc.requests if r["method"] == "eth_getLogs"]


def resolve(tmp_path, rpc, **kwargs):
    from factorylab.runtime.capital_loop import ReserveLock, check_authorization_record

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        return check_authorization_record(lock, transport=rpc, **kwargs)


def test_a_recorded_start_block_finds_a_use_a_corrected_host_clock_would_miss(tmp_path):
    # Item 1: the host clock led Base by an hour when it signed (validBefore is the
    # host's now + 600) and has been corrected since. Only the recorded head finds it.
    rpc = Rpc()
    signed_at = 11_000  # chain time of the head read just before the record was written
    valid_before = signed_at + 3_600 + 600
    rpc.use(LIVE_NONCE, at_ts=11_100)
    record(tmp_path, entry(LIVE_NONCE, valid_before, origin="reserve_topup",
                           start_block=rpc.number_at(signed_at)))
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)  # no lead now
    assert [(r["nonce"], r["how"]) for r in summary["resolved_now"]] == [(LIVE_NONCE, "spent")]
    # START_BLOCK_MARGIN blocks before the recorded head: an unsafe head may reorg.
    assert get_logs(rpc)[0][0] == rpc.number_at(signed_at) - 300


def test_a_legacy_entry_scans_from_validbefore_less_the_stated_margin(tmp_path):
    # Item 1, legacy: no start_block. The scan starts at the block at validBefore - 600
    # - the host's lead now - LEGACY_SKEW_CUSHION_S (300) - the finality lag.
    from factorylab.runtime.capital_loop import LEGACY_SKEW_CUSHION_S, MAX_AUTHORIZATION_S

    assert (MAX_AUTHORIZATION_S, LEGACY_SKEW_CUSHION_S) == (600, 300)
    rpc = Rpc()
    lead = 120
    valid_before = 11_500
    # Signed with a host clock 250 s ahead of the chain: used 830 s before validBefore.
    rpc.use(LIVE_NONCE, at_ts=valid_before - 600 - 250 + 20)
    record(tmp_path, entry(LIVE_NONCE, valid_before, origin="reserve_topup"))
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts + lead)
    assert [r["how"] for r in summary["resolved_now"]] == ["spent"]
    scans = get_logs(rpc)
    first, last = scans[0][0], scans[-1][1]
    assert first == rpc.number_at(valid_before - 600 - lead - 300 - rpc.lag_s) > 0
    assert rpc.stamp(last) > valid_before >= rpc.stamp(last - 1)


def test_nothing_is_signed_when_the_chain_head_cannot_be_read(tmp_path):
    # Item 1: the head is read before the record is written; unread, nothing is signed.
    from factorylab.runtime.capital_loop import ReserveGuard, read_authorizations
    from factorylab.world.x402 import AuthorizationNotRecorded, sign_transfer_authorization
    from tests.world.test_signing_chokepoint import ACCOUNT, typed

    guard = ReserveGuard("test", lock_dir=tmp_path)

    def unreadable():
        raise OSError("rpc down")

    for head in (unreadable, None, lambda: -1):
        with pytest.raises(AuthorizationNotRecorded):
            sign_transfer_authorization(ACCOUNT, typed(), guard=guard, head=head)
    path = tmp_path / f"{ACCOUNT.address.lower()}.authorizations.jsonl"
    assert not path.exists() or read_authorizations(path) == []
    sign_transfer_authorization(ACCOUNT, typed(), guard=guard, head=lambda: 77)
    assert [e["start_block"] for e in read_authorizations(path)] == [77]


def test_an_old_entrys_scan_ends_at_the_first_block_past_validbefore(tmp_path):
    # Item 2: an entry signed long ago costs the same as a fresh one: the scan stops at
    # the first block past validBefore, not at the finalized head a million blocks on.
    from factorylab.world.evm import LOG_PAGE_BLOCKS

    rpc = Rpc()
    rpc.final_ts = 1_000_000
    record(tmp_path, entry(LIVE_NONCE, 1_600, origin="reserve_topup", start_block=1_000))
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert [r["how"] for r in summary["resolved_now"]] == ["expired"]
    scans = get_logs(rpc)
    assert scans[0][0] == 700 and scans[-1][1] == rpc.number_at(1_600) + 1
    # Two scans (AuthorizationUsed, AuthorizationCanceled) of 300 + 601 + 1 blocks each.
    assert len(scans) <= 2 * -(-(300 + 601 + 1) // LOG_PAGE_BLOCKS)
    assert len(rpc.requests) < 80  # a binary search, not a walk


def test_an_entry_is_not_resolvable_until_finalized_base_passes_its_end(tmp_path):
    # Item 2: finalized Base at validBefore (not past it): the end block is not final.
    rpc = Rpc()
    rpc.final_ts = 11_500
    record(tmp_path, entry(LIVE_NONCE, 11_500, origin="reserve_topup", start_block=10_900))
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    rpc.final_ts = 11_501
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
        "resolved_now"]] == ["expired"]


@pytest.mark.parametrize("gaps", [(1, 3, 2), (2,), (1, 1, 1, 9)])
def test_irregular_block_times_bound_the_scan_by_the_blocks_own_timestamps(tmp_path, gaps):
    # Item 9: the scan's first and last blocks come from block timestamps, not an
    # assumed block rate.
    rpc = Rpc(gaps=gaps)
    rpc.use(LIVE_NONCE, at_ts=11_000)
    record(tmp_path, entry(LIVE_NONCE, 11_400, origin="reserve_topup"))
    now = rpc.latest_ts + 45  # and a host clock that leads the chain
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: now)[
        "resolved_now"]] == ["spent"]
    scans = get_logs(rpc)
    first, last = scans[0][0], scans[-1][1]
    # validBefore - 600 - 300 less the host's lead over latest and latest's over final.
    anchor = 11_400 - 600 - 300 - (now - rpc.final_ts)
    assert anchor - 60 < rpc.stamp(first) <= anchor  # never later, and not much earlier
    assert rpc.stamp(last) > 11_400 >= rpc.stamp(last - 1)


def test_the_finalized_tag_moving_between_calls_cannot_split_the_two_reads(tmp_path):
    # Item 6: the provider's finalized tag moves on after it is read. The state and the
    # scan are both read at the one block read first; the tag is never read twice.
    rpc = Rpc()
    rpc.drift = 50
    rpc.use(LIVE_NONCE, at_ts=12_010)  # final when the scan would re-read the tag
    record(tmp_path, entry(LIVE_NONCE, 13_000, origin="reserve_topup", start_block=11_000))
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    finalized = [r for r in rpc.requests if r["method"] == "eth_getBlockByNumber"
                 and r["params"][0] == "finalized"]
    assert len(finalized) == 1 and max(last for _, last in get_logs(rpc)) == 12_000


def torn_record(tmp_path, fragment, *, old=True):
    from factorylab.runtime.capital_loop import ReserveLock

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        if old:  # an earlier whole entry, expired by the finalized head
            lock.authorization_log(tmp_path / "run")(signed(OLD_NONCE, 11_500), 11_000)
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    path.write_bytes(path.read_bytes() + fragment)
    return path


def repair(tmp_path, now):
    from factorylab.runtime.capital_loop import ReserveLock, repair_torn

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        return repair_torn(lock, now_s=lambda: now)


@pytest.mark.parametrize(("tail", "expected"), [
    (b'"validBefore": "13', "12600"),  # cut mid-digits: unknown, never 13
    (b'"validBefore": 13', "12600"),  # a bare number with no delimiter after it
    (b'"validBefore": "99999999", "va', "12600"),  # capped at repair + 600
    (b'"validBefore": "12400", "va', "12600"),  # legible and plausible: still the bound
])
def test_a_torn_validbefore_counts_only_when_terminated_and_is_capped(
        tmp_path, tail, expected):
    # Items 3 and 9, and Codex on 507c2ea: nothing read from a torn line shortens the
    # bound; its validBefore is always the repair time + 600.
    from factorylab.runtime.capital_loop import read_authorizations

    path = torn_record(tmp_path, b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                       + b'", ' + tail)
    assert repair(tmp_path, 12_000)["repaired"]
    torn = read_authorizations(path)[-1]
    assert torn["validBefore"] == expected and torn["nonces"] == [LIVE_NONCE]


def test_a_torn_nonce_scans_from_the_repair_anchor_never_genesis(tmp_path):
    # Item 3: a torn nonce scans from the legacy anchor on the repair-time bound, a
    # legible start_block in it included (Codex on 507c2ea: it may be wrong).
    rpc = Rpc()
    torn_record(tmp_path, b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "validBefore": "1', old=False)
    repair(tmp_path, 12_000)
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    starts = {first for first, _ in get_logs(rpc)}
    assert min(starts) == rpc.number_at(12_600 - 600 - 0 - 300 - rpc.lag_s) > 0
    other = tmp_path / "other"
    other.mkdir()
    torn_record(other, b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "start_block": 11900, "validBefore": "12', old=False)
    repair(other, 12_000)
    rpc.requests.clear()
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
        resolve(other, rpc, now_s=lambda: rpc.latest_ts)
    assert min(first for first, _ in get_logs(rpc)) == rpc.number_at(
        12_600 - 600 - 0 - 300 - rpc.lag_s)


def test_a_used_torn_nonce_is_a_recovery_not_spent(tmp_path):
    # Item 9: a torn fragment shows no origin and no diary: used, it is a recovery.
    from factorylab.runtime.capital_loop import RECOVERY_REASONS

    rpc = Rpc()
    torn_record(tmp_path, b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "origin": "reserve_topup", "validBefore": "12')
    repair(tmp_path, 12_000)
    rpc.use(LIVE_NONCE, at_ts=11_900)
    with pytest.raises(CapitalLoopRefused,
                       match="recorded_authorization_settled_unbooked") as refused:
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert [a["nonce"] for a in refused.value.detail["authorizations"]] == [LIVE_NONCE]
    assert refused.value.reason in RECOVERY_REASONS


STEPS = ("sidecar", "temporary", "flush", "replace", "directory")


@pytest.mark.parametrize("step", STEPS)
def test_a_crash_at_any_repair_step_leaves_the_old_record_or_the_new(
        tmp_path, monkeypatch, step):
    # Item 4: the repair is a write of a sidecar, a temporary file flushed, a rename and
    # a directory flush; a crash between any two leaves one whole record, never neither.
    import os
    import tempfile

    from factorylab.runtime import capital_loop
    from factorylab.runtime.capital_loop import read_authorizations

    fragment = (b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "validBefore": "12400", "va')
    path = torn_record(tmp_path, fragment)
    before = path.read_bytes()

    class Crash(BaseException):
        pass

    armed = {"sidecar": False, "renamed": False}
    real = {"create": capital_loop._create_sidecar, "durable": capital_loop._durable,
            "replace": os.replace, "mkstemp": tempfile.mkstemp,
            "directory": capital_loop._fsync_directory}

    def create(target, now, data):
        if step == "sidecar":
            raise Crash
        made = real["create"](target, now, data)
        armed["sidecar"] = True
        return made

    def mkstemp(*args, **kwargs):
        if step == "temporary":
            raise Crash
        return real["mkstemp"](*args, **kwargs)

    def durable(fd):
        if step == "flush" and armed["sidecar"]:
            raise Crash
        real["durable"](fd)

    def rename(source, target):
        if step == "replace":
            raise Crash
        real["replace"](source, target)
        armed["renamed"] = True

    def directory(where):
        if step == "directory" and armed["renamed"]:
            raise Crash
        real["directory"](where)

    monkeypatch.setattr(capital_loop, "_create_sidecar", create)
    monkeypatch.setattr(capital_loop.tempfile, "mkstemp", mkstemp)
    monkeypatch.setattr(capital_loop, "_durable", durable)
    monkeypatch.setattr(capital_loop.os, "replace", rename)
    monkeypatch.setattr(capital_loop, "_fsync_directory", directory)
    lock = capital_loop.ReserveLock(RESERVE, lock_dir=tmp_path)  # taken before any crash
    try:
        with pytest.raises(Crash):
            capital_loop.repair_torn(lock, now_s=lambda: 12_000)
    finally:
        monkeypatch.undo()
        lock.close()
    after = path.read_bytes()
    assert after == before or read_authorizations(path)[-1]["kind"] == "torn"
    assert (after == before) == (step != "directory")
    assert list(tmp_path.glob(".authorizations-*")) == []  # no temporary left behind
    for sidecar in tmp_path.glob("*.torn-*"):
        assert sidecar.read_bytes() == fragment
    # The operator runs the repair again: exactly one torn entry, nothing lost.
    repair(tmp_path, 12_001)
    entries = read_authorizations(path)
    assert [e["kind"] for e in entries] == ["authorization", "torn"]
    assert bytes.fromhex(entries[-1]["fragment_hex"]) == fragment


def test_an_unfinalized_settlement_nothing_recorded_refuses_the_cooling_off(tmp_path):
    # Item 5: the cooling-off scan reaches the latest block (detection, not resolution).
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check

    rpc = Rpc()
    rpc.use(LIVE_NONCE, at_ts=12_500)  # after the finalized head, before the latest
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        with pytest.raises(CapitalLoopRefused, match="unrecorded_reserve_authorization"):
            cooling_off_check(lock, window_s=2_520, transport=rpc)
    assert max(last for _, last in get_logs(rpc)) == rpc.latest_number


def test_the_cooling_off_knows_a_torn_fragments_nonces(tmp_path):
    # Item 9: a nonce known only from a torn fragment is still the record's.
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check

    rpc = Rpc()
    torn_record(tmp_path, b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode()
                + b'", "validBefore": "12')
    repair(tmp_path, 12_000)
    rpc.use(LIVE_NONCE, at_ts=11_900)
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        assert cooling_off_check(lock, window_s=2_520, transport=rpc)[
            "authorizations_seen"] == 1


def test_a_recorded_reserve_transaction_explains_its_transfer(tmp_path):
    # Item 7: a plain reserve-key transaction written ahead is the record's; one made by
    # hand is not, and refuses until it leaves the window.
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check

    rpc = Rpc()
    rpc.extra_logs.append({
        "address": BASE.usdc, "blockNumber": hex(11_950),
        "blockHash": "0x" + (11_950).to_bytes(32).hex(), "transactionHash": "0x" + "ee" * 32,
        "data": hex(1), "topics": [event_topic(TRANSFER), "0x" + "0" * 24 + RESERVE.lower()[2:],
                                   "0x" + "0" * 24 + "12" * 20]})
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        with pytest.raises(CapitalLoopRefused, match="unrecorded_reserve_transfer"):
            cooling_off_check(lock, window_s=2_520, transport=rpc)
        lock.authorization_log(None, origin="treasury_cli").record_transaction({
            "tx_hash": "0x" + "EE" * 32, "chain_id": 8453, "from": RESERVE,
            "to": BASE.usdc, "nonce": 7, "start_block": 11_940})
        assert cooling_off_check(lock, window_s=2_520, transport=rpc)["transfers_seen"] == 1
    # A hand transfer refuses until finalized Base is a window (2520 s) past it: then it
    # clears by itself.
    fresh = tmp_path / "fresh"
    rpc.final_ts = 11_950 + 2_520 + 1
    with ReserveLock(RESERVE, lock_dir=fresh) as lock:
        assert cooling_off_check(lock, window_s=2_520, transport=rpc)["transfers_seen"] == 0


@pytest.mark.parametrize(("origin", "how"), [
    ("treasury", None), ("capital_loop", None), (None, None),  # a legacy entry: strict
    ("reserve_topup", "spent"), ("x402_purchase", "spent"), ("compute_proof", "spent"),
])
def test_a_used_entry_resolves_by_its_origins_booking(tmp_path, origin, how):
    # Item 8 and item 9 (a legacy entry's origin defaults to the strict capital_loop).
    rpc = Rpc()
    run = write_run(tmp_path / "run", crash=False)  # a diary that booked nothing
    record(tmp_path, entry(LIVE_NONCE, 12_400, origin=origin, run_dir=str(run.resolve()),
                           start_block=11_000))
    rpc.use(LIVE_NONCE, at_ts=11_800)
    if how is None:
        with pytest.raises(CapitalLoopRefused, match="recorded_authorization_settled_unbooked"):
            resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    else:
        assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
            "resolved_now"]] == [how]


def test_a_treasury_entry_booked_in_its_worlds_diary_resolves_as_financed(tmp_path):
    rpc = Rpc()
    run = tmp_path / "booked"
    run.mkdir()
    path = run / "ledger.jsonl"
    ledger = Ledger(str(path), manifest={"name": "edition6-capital-loop"},
                    clock_ns=lambda: 0, key_path=str(path) + ".key")
    state = {"id": "treasury-0", "steps": ["shadow_send", "venice_top_up"], "index": 1,
             "status": "confirmed", "reference": signed(LIVE_NONCE, 12_400),
             "receipts": [{"nonce": 1_700_000_000_000}, {"nonce": LIVE_NONCE}]}
    ledger.append({"kind": "treasury.confirmed", "state": state})
    ledger.append({"kind": "treasury.financing", "transfer_id": "treasury-0"})
    record(tmp_path, entry(LIVE_NONCE, 12_400, origin="treasury", run_dir=str(run.resolve()),
                           start_block=11_000))
    rpc.use(LIVE_NONCE, at_ts=11_800)
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
        "resolved_now"]] == ["financed"]


def test_the_record_is_never_written_without_its_lock_held(tmp_path):
    # Item 9: an AuthorizationLog whose lock was released writes nothing.
    from factorylab.runtime.capital_loop import ReserveLock

    lock = ReserveLock(RESERVE, lock_dir=tmp_path)
    log = lock.authorization_log(tmp_path / "run")
    lock.close()
    path = tmp_path / f"{RESERVE.lower()}.authorizations.jsonl"
    before = path.read_bytes()
    with pytest.raises(CapitalLoopRefused, match="capital_loop_reserve_lock_not_held"):
        log(signed(LIVE_NONCE, 13_000), 12_000)
    with pytest.raises(CapitalLoopRefused, match="capital_loop_reserve_lock_not_held"):
        log.record_transaction({"tx_hash": "0x" + "ee" * 32, "chain_id": 8453,
                                "from": RESERVE, "to": BASE.usdc, "nonce": 1,
                                "start_block": 12_000})
    with pytest.raises(CapitalLoopRefused, match="capital_loop_reserve_lock_not_held"):
        log.permit(RESERVE)
    assert path.read_bytes() == before
# ---- Wave 10, the reviews of 98fa627


class Hyper:
    """A HyperEVM node for the reserve. ``latest`` is the account nonce its newest block
    shows, which HyperBFT makes final; ``pending`` is the mempool's, deliberately not the
    same (queued transactions), so a replacement that took it would be caught. It holds
    the raw transactions it was sent, the reserve's native ``balance``, and nothing by
    hash."""

    def __init__(self, *, nonce, chain_id=999, pending=None):
        self.latest, self.chain_id, self.balance = nonce, chain_id, 10**18
        self.pending = nonce + 3 if pending is None else pending
        self.sent, self.requests, self.nonce_tags = [], [], []

    def __call__(self, method, url, payload, headers):
        name, params = payload["method"], payload["params"]
        self.requests.append(name)
        if name == "eth_chainId":
            result = hex(self.chain_id)
        elif name == "eth_getBlockByNumber":
            result = {"number": hex(500), "hash": "0x" + "ab" * 32, "timestamp": hex(12_000)}
        elif name == "eth_blockNumber":
            result = hex(500)
        elif name == "eth_getTransactionCount":
            self.nonce_tags.append(params[1])
            result = hex(self.pending if params[1] == "pending" else self.latest)
        elif name == "eth_gasPrice":
            result = hex(100)
        elif name == "eth_estimateGas":
            result = hex(21_000 if params[0].get("data", "0x") in ("", "0x") else 150_000)
        elif name == "eth_getBalance":
            result = hex(self.balance)
        elif name == "eth_getTransactionByHash":
            result = None
        elif name == "eth_sendRawTransaction":
            from eth_utils import keccak

            self.sent.append(params[0])
            result = "0x" + keccak(bytes.fromhex(params[0][2:])).hex()
        else:
            raise AssertionError(f"unexpected RPC {name}")
        return HTTPResponse(200, {"jsonrpc": "2.0", "id": 1, "result": result})


def recorded_transaction(tmp_path, tx_hash, *, chain_id=8453, nonce=7, origin="treasury"):
    from factorylab.runtime.capital_loop import ReserveLock

    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        lock.authorization_log(None, origin=origin).record_transaction({
            "tx_hash": tx_hash, "chain_id": chain_id, "from": RESERVE, "to": BASE.usdc,
            "nonce": nonce, "gas_price": 100, "start_block": 11_900})


def test_a_recorded_reserve_transaction_blocks_the_launch_until_its_nonce_is_final(tmp_path):
    # Item 1: a prepared depositForBurn, say, broadcast and not yet mined, must not land
    # mid-run below the floor.
    stuck = "0x" + "d1" * 32
    rpc = Rpc()
    rpc.consume(RESERVE, 7, at_ts=0)  # nonces 0..6 used long ago; 7 is the recorded one
    recorded_transaction(tmp_path, stuck)
    with pytest.raises(CapitalLoopRefused,
                       match="recorded_transaction_may_still_execute") as refused:
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    [row] = refused.value.detail["transactions"]
    assert row["tx_hash"] == stuck and row["speed_up"].endswith(f"--speed-up {stuck}")
    assert f"--cancel-transaction {stuck} " in row["cancel"]
    rpc.consume(RESERVE, 8, at_ts=12_300)  # mined after the finalized head: not yet final
    with pytest.raises(CapitalLoopRefused, match="recorded_transaction_may_still_execute"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    rpc.final_ts = 12_301
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert [(r["tx_hash"], r["how"]) for r in summary["resolved_now"]] == [
        (stuck, "nonce_consumed")]
    assert resolve(tmp_path, rpc)["open_transactions"] == 0


def reserve_world(tmp_path, reserve):
    """The capital-loop world with a throwaway reserve (a manifest is named by its stem)."""
    world = tmp_path / "edition6-capital-loop.toml"
    world.write_text(Path("worlds/edition6-capital-loop.toml").read_text().replace(
        RESERVE, reserve.address))
    return world


def hyper_signer(tmp_path, monkeypatch, *, nonce=5):
    """A throwaway reserve on a HyperEVM fake, its guard writing to the test's lock dir,
    run from an empty working directory (so no key file of the repo is ever read)."""
    from eth_account import Account

    from factorylab.runtime.capital_loop import ReserveGuard
    from factorylab.world.evm import EVM, HYPEREVM

    reserve = Account.create()  # a throwaway key, never funded
    world = reserve_world(tmp_path, reserve)
    monkeypatch.chdir(tmp_path)
    rpc, hyper = Rpc(), Hyper(nonce=nonce)
    rpc.others[HYPEREVM.rpc] = hyper
    locks = tmp_path / "locks"
    chain = EVM(HYPEREVM, reserve, transport=rpc, gas_budget_wei=10**15)
    chain.transaction_guard = ReserveGuard("treasury", lock_dir=locks)
    return {"reserve": reserve, "rpc": rpc, "hyper": hyper, "locks": locks, "chain": chain,
            "world": world}


def launch_on(s):
    from factorylab.runtime.capital_loop import ReserveLock, check_authorization_record

    with ReserveLock(s["reserve"].address, lock_dir=s["locks"]) as lock:
        return check_authorization_record(lock, transport=s["rpc"],
                                          now_s=lambda: s["rpc"].latest_ts)


def tool(s, *argv):
    from scripts import capital_loop_outstanding

    return capital_loop_outstanding.main(
        [*argv, "--world", str(s["world"]), "--lock-dir", str(s["locks"])],
        transport=s["rpc"])


def decoded(raw):
    import rlp

    nonce, price, _gas, to, value, data, *_ = rlp.decode(bytes.fromhex(raw[2:]))
    return {"nonce": int.from_bytes(nonce), "price": int.from_bytes(price), "to": to,
            "value": value, "data": data}


def test_cancel_transaction_is_the_exit_for_a_dropped_reserve_transaction(
        tmp_path, monkeypatch, capsys):
    # Item 1: a non-mint transaction dropped unmined keeps its nonce unused for ever, and
    # anyone holding its bytes could still send it: the exit consumes the recorded nonce.
    from factorylab.world.evm import HYPEREVM, _with_headroom

    s = hyper_signer(tmp_path, monkeypatch)
    s["hyper"].pending = 8
    stuck = s["chain"].transfer(HYPEREVM.usdc, "0x" + "12" * 20, 1, 10**15)  # never mined
    assert stuck["tx"]["nonce"] == 8
    s["hyper"].pending = 11  # later transactions queued behind it: not the nonce to take
    with pytest.raises(CapitalLoopRefused,
                       match="recorded_transaction_may_still_execute") as refused:
        launch_on(s)
    [row] = refused.value.detail["transactions"]
    assert row["step"] == "transfer" and "will not complete" in row["cancel_consequence"]
    argv = ("--cancel-transaction", stuck["tx_hash"])
    assert tool(s, *argv) == 2  # no key: nothing signed
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", s["reserve"].key.hex())
    assert tool(s, *argv) == 2  # the consequence must be accepted in so many words
    assert "will not complete; it must be recovered by hand" in capsys.readouterr().err
    assert s["hyper"].sent == []
    assert tool(s, *argv, "--i-understand-the-world-step-is-abandoned") == 0
    printed = capsys.readouterr()
    assert s["reserve"].key.hex()[2:] not in printed.out + printed.err
    done = json.loads(printed.out)
    [raw] = s["hyper"].sent
    sent = decoded(raw)
    assert sent["nonce"] == 8  # the recorded nonce, not the pending count
    assert sent["to"] == bytes.fromhex(s["reserve"].address[2:])
    assert sent["value"] == b"" and sent["data"] == b""
    assert sent["price"] >= (stuck["tx"]["gasPrice"] * 9 + 7) // 8
    one = (21_000 * 12 + 9) // 10 * max(_with_headroom(100), (stuck["tx"]["gasPrice"] * 9 + 7)
                                        // 8)
    assert done["max_gas_wei"] == 10 * one  # the stated default: ten such replacements
    with pytest.raises(CapitalLoopRefused, match="recorded_transaction_may_still_execute"):
        launch_on(s)
    s["hyper"].latest = 9  # HyperEVM's latest block is final: the nonce is consumed
    s["hyper"].nonce_tags.clear()
    summary = launch_on(s)
    assert set(s["hyper"].nonce_tags) == {"latest"}  # HyperBFT: latest is final
    assert {(r["tx_hash"], r["how"]) for r in summary["resolved_now"]} == {
        (stuck["tx_hash"], "nonce_consumed"), (done["cancel_tx_hash"], "nonce_consumed")}


def test_a_cctp_mint_is_never_cancelled_and_is_sped_up_identically(
        tmp_path, monkeypatch, capsys):
    # The fourth review (HIGH): cancelling a receiveMessage leaves its burn with nothing
    # minted. A mint's only exit re-signs the identical call at the same nonce.
    from factorylab.runtime.capital_loop import read_authorizations
    from factorylab.world.evm import HYPEREVM, calldata

    s = hyper_signer(tmp_path, monkeypatch)
    data = calldata("receiveMessage(bytes,bytes)", ["bytes", "bytes"], [b"m" * 40, b"a" * 65])
    stuck = s["chain"].prepare(HYPEREVM.transmitter, data, gas_remaining_wei=10**15)
    with pytest.raises(CapitalLoopRefused,
                       match="recorded_transaction_may_still_execute") as refused:
        launch_on(s)
    [row] = refused.value.detail["transactions"]
    assert row["step"] == "mint" and "cancel" not in row
    assert row["speed_up"].endswith(f"--speed-up {stuck['tx_hash']}")
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", s["reserve"].key.hex())
    assert tool(s, "--cancel-transaction", stuck["tx_hash"],
                "--i-understand-the-world-step-is-abandoned") == 2
    assert "never cancelled" in capsys.readouterr().err and s["hyper"].sent == []
    assert tool(s, "--speed-up", stuck["tx_hash"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert tool(s, "--speed-up", stuck["tx_hash"]) == 0  # stuck again: once more
    second = json.loads(capsys.readouterr().out)
    one, two = (decoded(raw) for raw in s["hyper"].sent)
    for sent in (one, two):
        assert sent["nonce"] == stuck["tx"]["nonce"] and sent["value"] == b""
        assert sent["to"] == bytes.fromhex(HYPEREVM.transmitter[2:])
        assert sent["data"] == bytes.fromhex(data[2:])  # the identical call
    assert one["price"] >= (stuck["tx"]["gasPrice"] * 9 + 7) // 8
    assert two["price"] >= (one["price"] * 9 + 7) // 8  # above every recorded price
    steps = [(e["tx_hash"], e["step"], e["origin"]) for e in read_authorizations(
        s["locks"] / f"{s['reserve'].address.lower()}.authorizations.jsonl")]
    # Each replacement is recorded as the world's own (Codex on 507c2ea).
    assert steps == [(stuck["tx_hash"], "mint", "treasury"),
                     (first["speed_up_tx_hash"], "mint", "treasury"),
                     (second["speed_up_tx_hash"], "mint", "treasury")]


def test_a_cancel_waits_for_the_world_that_recorded_it_to_end(tmp_path, monkeypatch, capsys):
    from factorylab.kernel.ledger import LedgerLock
    from factorylab.runtime.capital_loop import ReserveGuard
    from factorylab.world.evm import HYPEREVM

    s = hyper_signer(tmp_path, monkeypatch)
    diary = tmp_path / "runs" / "world.jsonl"
    diary.parent.mkdir()
    diary.write_bytes(b"")
    s["chain"].transaction_guard = ReserveGuard("treasury", ledger=diary, lock_dir=s["locks"])
    stuck = s["chain"].approve(HYPEREVM.usdc, HYPEREVM.messenger, 1, 10**15)
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", s["reserve"].key.hex())
    argv = ("--cancel-transaction", stuck["tx_hash"], "--i-understand-the-world-step-is-abandoned")
    with LedgerLock(diary):  # the world is still running
        assert tool(s, *argv) == 2
    assert "still running" in capsys.readouterr().err and s["hyper"].sent == []
    assert tool(s, *argv) == 0  # it ended


def test_a_cancel_names_a_key_that_is_not_the_reserves(tmp_path, monkeypatch, capsys):
    # A surviving mutant: without the explicit check, the guard still refused, but for
    # another reason. The refusal must say which key is wrong.
    from eth_account import Account

    from factorylab.world.evm import HYPEREVM

    s = hyper_signer(tmp_path, monkeypatch)
    stuck = s["chain"].transfer(HYPEREVM.usdc, "0x" + "12" * 20, 1, 10**15)
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", Account.create().key.hex())
    for argv in (("--cancel-transaction", stuck["tx_hash"],
                  "--i-understand-the-world-step-is-abandoned"),
                 ("--speed-up", stuck["tx_hash"])):
        assert tool(s, *argv) == 2
        refusal = json.loads(capsys.readouterr().err)
        assert refusal["why"] == "the key given is not the reserve that signed it"
    assert s["hyper"].sent == []


def test_a_replacement_with_no_native_gas_names_the_chain_to_fund(
        tmp_path, monkeypatch, capsys):
    from factorylab.world.evm import HYPEREVM

    s = hyper_signer(tmp_path, monkeypatch)
    stuck = s["chain"].transfer(HYPEREVM.usdc, "0x" + "12" * 20, 1, 10**15)
    s["hyper"].balance = 0
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", s["reserve"].key.hex())
    assert tool(s, "--speed-up", stuck["tx_hash"]) == 2
    refusal = json.loads(capsys.readouterr().err)
    assert (refusal["error"], refusal["chain_id"]) == ("replacement_needs_native_gas", 999)
    assert refusal["reserve"] == s["reserve"].address and refusal["needed_wei"] > 0
    s["hyper"].balance = 10**18
    assert tool(s, "--speed-up", stuck["tx_hash"], "--max-gas-wei", "1") == 2
    assert json.loads(capsys.readouterr().err)["error"] == "speed_up_failed"
    assert s["hyper"].sent == []


def test_the_key_file_is_loaded_for_any_spelling_of_the_signing_flags(
        tmp_path, monkeypatch, capsys):
    # The fourth review: _load_dotenv ran only on a literal "--cancel-transaction" argv.
    from factorylab.runtime import cli

    s = hyper_signer(tmp_path, monkeypatch)
    loaded = []
    monkeypatch.setattr(cli, "_load_dotenv", lambda: loaded.append(True))
    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)
    for flag in ("--cancel-transaction=0x" + "ab" * 32, "--speed-up=0x" + "ab" * 32):
        assert tool(s, flag) == 2  # no key in the (empty) key file: nothing signed
    assert loaded == [True, True]
    assert tool(s, "--repair-torn") == 0  # no signing flag: no key file read
    assert loaded == [True, True]


def test_a_cut_hyperevm_transaction_line_is_a_torn_transaction_not_an_authorization(
        tmp_path):
    # Item 2 (cold #1): the hash in a cut transaction line was read as a nonce.
    from factorylab.runtime.capital_loop import read_authorizations
    from factorylab.world.evm import HYPEREVM

    tx_hash = "0x" + "ab" * 32
    line = json.dumps({"chain_id": 999, "from": RESERVE, "gas_price": 125,
                       "kind": "transaction", "ledger": None, "origin": "treasury",
                       "run_dir": None, "start_block": 16, "to": HYPEREVM.usdc,
                       "tx_hash": tx_hash, "tx_nonce": 12}, sort_keys=True).encode()
    fragment = line[:line.index(b'"tx_nonce": ') + len(b'"tx_nonce": 1')]
    path = record(tmp_path)
    path.write_bytes(fragment)
    assert repair(tmp_path, 12_000)["open_transactions"] == [tx_hash]
    torn = read_authorizations(path)[-1]
    assert (torn["torn_kind"], torn["nonces"], torn["tx_hashes"]) == (
        "transaction", [], [tx_hash])
    assert (torn["chain_id"], torn["tx_nonce"], torn["start_block"]) == (999, None, None)
    rpc = Rpc()
    rpc.others[HYPEREVM.rpc] = hyper = Hyper(nonce=12)
    with pytest.raises(CapitalLoopRefused, match="recorded_transaction_may_still_execute"):
        resolve(tmp_path, rpc, now_s=lambda: 12_100)  # the cooling-off window is open
    assert get_logs(rpc) == []  # nothing of it was taken for an authorization's nonce
    later = 12_000 + 600 + 2 * rpc.lag_s
    summary = resolve(tmp_path, rpc, now_s=lambda: later)
    assert [(r["tx_hash"], r["how"]) for r in summary["resolved_now"]] == [(tx_hash, "dropped")]
    assert "eth_getTransactionByHash" in hyper.requests


def test_a_canceled_authorization_is_dead_without_an_acknowledgement(tmp_path):
    # Item 3 (cold #2): EIP-3009 cancelAuthorization sets the state with no debit.
    from factorylab.world.treasury_rails import authorization_status

    rpc = Rpc()
    rpc.cancel(LIVE_NONCE, at_ts=11_500)
    record(tmp_path, entry(LIVE_NONCE, 13_000, origin="treasury", start_block=11_000))
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert [(r["nonce"], r["how"]) for r in summary["resolved_now"]] == [
        (LIVE_NONCE, "canceled")]
    # The rail sees it dead too, and may strand the conversion recoverably.
    status = authorization_status(keyless_base(transport=rpc), RESERVE,
                                  {**signed(LIVE_NONCE, 13_000), "start_block": 10_000})
    assert status["canceled"] and not status["live"] and status["debits"] == []


def booked_diary(path, nonce):
    """A world's diary at ``path`` (its key beside it) that booked ``nonce``'s debit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(str(path), manifest={"name": "edition6-capital-loop"},
                    clock_ns=lambda: 0, key_path=str(path) + ".key")
    state = {"id": "treasury-0", "steps": ["shadow_send", "venice_top_up"], "index": 1,
             "status": "confirmed", "reference": signed(nonce, 12_400),
             "receipts": [{"nonce": 1_700_000_000_000}, {"nonce": nonce}]}
    ledger.append({"kind": "treasury.confirmed", "state": state})
    ledger.append({"kind": "treasury.financing", "transfer_id": "treasury-0"})
    return path


def test_a_world_diary_is_read_at_its_exact_ledger_path(tmp_path):
    # Item 4 (cold #3): a world launched with --ledger runs/foo.jsonl books there, not in
    # runs/ledger.jsonl.
    rpc = Rpc()
    diary = booked_diary(tmp_path / "runs" / "foo.jsonl", LIVE_NONCE)
    record(tmp_path, entry(LIVE_NONCE, 12_400, origin="treasury",
                           run_dir=str(diary.parent.resolve()),
                           ledger=str(diary.resolve()), start_block=11_000))
    rpc.use(LIVE_NONCE, at_ts=11_800)
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
        "resolved_now"]] == ["financed"]


def test_a_use_below_an_unsafe_recorded_head_is_still_found(tmp_path):
    # Item 5 (cold #4): the head read at signing was an unsafe block a reorg replaced by
    # a shorter branch; the authorization was used below the recorded number.
    rpc = Rpc()
    rpc.use(LIVE_NONCE, at_ts=10_900)
    record(tmp_path, entry(LIVE_NONCE, 11_500, origin="reserve_topup", start_block=11_000))
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
        "resolved_now"]] == ["spent"]


def test_a_log_whose_block_cannot_be_read_makes_the_cooling_off_unreadable(tmp_path):
    # Item 6 (cold #6): a log in the unfinalized tail was dropped when its block read null.
    from factorylab.runtime.capital_loop import ReserveLock, cooling_off_check

    rpc = Rpc()
    rpc.use(LIVE_NONCE, at_ts=12_500)  # nothing records it
    rpc.missing.add(rpc.number_at(12_500))
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        with pytest.raises(CapitalLoopRefused, match="cooling_off_unreadable"):
            cooling_off_check(lock, window_s=2_520, transport=rpc)
        rpc.missing.clear()  # the next launch reads it, and refuses on what it shows
        with pytest.raises(CapitalLoopRefused, match="unrecorded_reserve_authorization"):
            cooling_off_check(lock, window_s=2_520, transport=rpc)


def test_a_damaged_middle_line_has_an_exit_that_keeps_what_it_recorded_open(
        tmp_path, capsys):
    # Item 8: authorization_record_unreadable needs an exit too.
    from factorylab.runtime.capital_loop import read_authorizations
    from scripts import capital_loop_outstanding

    path = record(tmp_path, entry(OLD_NONCE, 11_500, origin="reserve_topup",
                                  start_block=11_000))
    whole = path.read_bytes()
    damaged = (b'{"from": "' + RESERVE.encode() + b'", "kind": "authorization", '
               b'"nonce": "' + LIVE_NONCE.encode() + b'", "vali\xff')
    after = json.dumps(entry("0x" + "55" * 32, 14_000, origin="reserve_topup",
                             start_block=11_000), sort_keys=True).encode() + b"\n"
    path.write_bytes(whole + damaged + b"\n" + after)
    with pytest.raises(CapitalLoopRefused, match="authorization_record_unreadable") as refused:
        read_authorizations(path)
    assert "--repair-damaged" in refused.value.detail["repair"]
    argv = ["--repair-damaged", "--lock-dir", str(tmp_path)]
    assert capital_loop_outstanding.main(argv, transport=Rpc()) == 0
    repaired = json.loads(capsys.readouterr().out)
    assert repaired["open_nonces"] == [LIVE_NONCE]
    entries = read_authorizations(path)
    assert [e["kind"] for e in entries] == ["authorization", "torn", "authorization"]
    assert entries[1]["nonces"] == [LIVE_NONCE]
    assert path.read_bytes().startswith(whole) and path.read_bytes().endswith(after)
    sidecar = tmp_path / repaired["sidecar"].rsplit("/", 1)[1]
    assert sidecar.read_bytes() == damaged  # nothing deleted, only moved aside
    assert capital_loop_outstanding.main(argv, transport=Rpc()) == 0  # whole now: a no-op
    assert json.loads(capsys.readouterr().out)["repaired"] is False


def test_two_repairs_in_one_second_each_keep_their_own_sidecar(tmp_path):
    # Item 8: authorization_record_repair_refused is made impossible.
    fragment = b'{"kind": "authorization", "nonce": "' + LIVE_NONCE.encode() + b'", "va'
    path = torn_record(tmp_path, fragment, old=False)
    first = repair(tmp_path, 12_000)
    other = b'{"kind": "authorization", "nonce": "' + OLD_NONCE.encode() + b'", "va'
    path.write_bytes(path.read_bytes() + other)
    second = repair(tmp_path, 12_000)  # the same second
    assert first["sidecar"] != second["sidecar"]
    assert [(tmp_path / r["sidecar"].rsplit("/", 1)[1]).read_bytes()
            for r in (first, second)] == [fragment, other]


# ---- Wave 10, the fourth review of 94255ef


def test_a_cancel_by_another_authorizer_of_the_same_nonce_is_not_ours(tmp_path):
    # A surviving mutant: the AuthorizationCanceled scan without the authorizer topic
    # took another account's cancel of the same nonce for ours.
    rpc = Rpc()
    rpc.cancel(LIVE_NONCE, at_ts=11_200, authorizer="0x" + "12" * 20)
    record(tmp_path, entry(LIVE_NONCE, 11_500, origin="treasury", start_block=11_000))
    assert [(r["nonce"], r["how"]) for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
        "resolved_now"]] == [(LIVE_NONCE, "expired")]


def test_a_testnet_transaction_never_blocks_a_mainnet_launch(tmp_path):
    from factorylab.world.evm import HYPEREVM_TESTNET

    rpc = Rpc()
    rpc.others[HYPEREVM_TESTNET.rpc] = testnet = Hyper(nonce=3, chain_id=998)
    recorded_transaction(tmp_path, "0x" + "d2" * 32, chain_id=998, nonce=3)
    summary = resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert summary["resolved_now"] == [] and testnet.requests == []
    with pytest.raises(CapitalLoopRefused, match="recorded_transaction_may_still_execute"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts, testnet=True)
    testnet.latest = 4  # HyperEVM testnet's latest block is final too
    assert [r["how"] for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts,
                                      testnet=True)["resolved_now"]] == ["nonce_consumed"]


def test_each_chains_rpc_is_overridden_and_a_failure_names_the_one_that_failed(tmp_path):
    from factorylab.world.evm import HYPEREVM

    mine, down = "https://hyperevm.example/rpc", "https://down.example/rpc"

    def unreachable(method, url, payload, headers):
        raise OSError("connection refused")

    rpc = Rpc()
    rpc.others[mine] = hyper = Hyper(nonce=7)
    rpc.others[down] = unreachable
    rpc.others[HYPEREVM.rpc] = unreachable  # the public one is never asked
    recorded_transaction(tmp_path, "0x" + "d3" * 32, chain_id=999, nonce=7)
    with pytest.raises(CapitalLoopRefused, match="recorded_transaction_may_still_execute"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts, rpcs={999: mine})
    assert "eth_getTransactionCount" in hyper.requests
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_unreadable") as failed:
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts, rpcs={999: down})
    assert (failed.value.detail["rpc"], failed.value.detail["chain_id"]) == (down, 999)


def test_the_runner_reads_base_at_the_rpc_its_flag_names(tmp_path, monkeypatch):
    from scripts import edition4_rehearsal as rehearsal

    seen = {}
    monkeypatch.setattr(rehearsal, "run_rehearsal",
                        lambda world, **kwargs: seen.update(kwargs) or {
                            "status": "failed", "cost": {}})
    rehearsal.main(["--out", str(tmp_path / "runs" / "flags"), "--capital-loop",
                    "--rpc-base", "https://base.example/rpc",
                    "--rpc-hyperevm", "https://hyperevm.example/rpc"])
    assert seen["capital_loop_rpcs"] == {8453: "https://base.example/rpc",
                                         999: "https://hyperevm.example/rpc"}
    monkeypatch.undo()
    # And a launch whose Base RPC fails names that RPC, not the public one.
    report = rehearse(tmp_path / "runs" / "elsewhere", Rpc(), tmp_path,
                      capital_loop_rpcs={8453: "https://base.example/rpc"})
    assert report["refusal"]["rpc"] == "https://base.example/rpc"


def test_a_funded_run_takes_no_transport_and_no_other_lock_directory(tmp_path):
    # The fourth review: a library caller could hand the funded loop its own chain answers
    # or its own lock directory, and so a second "only" lock on the reserve.
    from scripts import edition4_rehearsal as rehearsal

    common = {"out": tmp_path / "runs" / "bypass", "capital_loop": True,
              "duration_ns": 3 * 3_600 * 1_000_000_000, "provider": object(),
              "source_root": tmp_path}
    with pytest.raises(rehearsal.RehearsalRefused,
                       match="capital_loop_requires_the_live_transport"):
        rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                capital_loop_transport=Rpc(), **common)
    with pytest.raises(rehearsal.RehearsalRefused,
                       match="capital_loop_requires_the_operator_lock_dir"):
        rehearsal.run_rehearsal("worlds/edition6-capital-loop.toml",
                                capital_loop_lock_dir=tmp_path / "elsewhere", **common)
    assert not (tmp_path / "runs").exists() and not (tmp_path / "elsewhere").exists()


# ---- Wave 10, Codex on 507c2ea


def test_a_damaged_lines_plausible_validbefore_never_shortens_its_bound(tmp_path):
    # P1: a damaged line's legible, terminated validBefore may be wrong; the entry
    # resolves only once finalized Base is past the repair time + 600.
    from factorylab.runtime.capital_loop import ReserveLock, read_authorizations, repair_damaged

    path = record(tmp_path, entry(OLD_NONCE, 11_500, origin="reserve_topup",
                                  start_block=11_000))
    damaged = (b'{"from": "' + RESERVE.encode() + b'", "kind": "authorization", '
               b'"nonce": "' + LIVE_NONCE.encode() + b'", "start_block": 11990, '
               b'"validBefore": "12100", "vali\xff')
    path.write_bytes(damaged + b"\n" + path.read_bytes())
    with ReserveLock(RESERVE, lock_dir=tmp_path) as lock:
        repair_damaged(lock, now_s=lambda: 12_000)
    torn = read_authorizations(path)[0]
    assert (torn["validBefore"], torn["start_block"]) == ("12600", None)
    rpc = Rpc()
    rpc.final_ts = 12_300  # past the line's own 12100, not past the bound
    with pytest.raises(CapitalLoopRefused, match="recorded_authorization_may_still_settle"):
        resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)
    assert min(first for first, _ in get_logs(rpc)) < 11_990 - 300  # the legacy anchor
    rpc.final_ts = 12_601
    assert (LIVE_NONCE, "expired") in [
        (r["nonce"], r["how"]) for r in resolve(tmp_path, rpc, now_s=lambda: rpc.latest_ts)[
            "resolved_now"]]


def test_a_running_worlds_step_cannot_be_cancelled_through_its_replacement(
        tmp_path, monkeypatch, capsys):
    # P2: a sped-up replacement is the world's own, and a cancel checks every entry at
    # the nonce, so the replacement's hash is no way around the running world.
    from factorylab.kernel.ledger import LedgerLock
    from factorylab.runtime.capital_loop import ReserveGuard, read_authorizations
    from factorylab.world.evm import HYPEREVM, calldata

    s = hyper_signer(tmp_path, monkeypatch)
    diary = tmp_path / "runs" / "world.jsonl"
    diary.parent.mkdir()
    diary.write_bytes(b"")
    s["chain"].transaction_guard = ReserveGuard("treasury", run_dir=diary.parent,
                                                ledger=diary, lock_dir=s["locks"])
    data = calldata(
        "depositForBurn(uint256,uint32,bytes32,address,bytes32,uint256,uint32)",
        ["uint256", "uint32", "bytes32", "address", "bytes32", "uint256", "uint32"],
        [1, 6, bytes(32), HYPEREVM.usdc, bytes(32), 1, 2000])
    burn = s["chain"].prepare(HYPEREVM.messenger, data, gas_remaining_wei=10**15)
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", s["reserve"].key.hex())
    with LedgerLock(diary):  # the world is running
        assert tool(s, "--speed-up", burn["tx_hash"]) == 0
        replacement = json.loads(capsys.readouterr().out)["speed_up_tx_hash"]
        for target in (replacement, burn["tx_hash"]):
            assert tool(s, "--cancel-transaction", target,
                        "--i-understand-the-world-step-is-abandoned") == 2
            assert "still running" in json.loads(capsys.readouterr().err)["why"]
    [_, recorded] = [e for e in read_authorizations(
        s["locks"] / f"{s['reserve'].address.lower()}.authorizations.jsonl")]
    assert (recorded["tx_hash"], recorded["step"], recorded["origin"]) == (
        replacement, "burn", "treasury")
    assert (recorded["ledger"], recorded["run_dir"]) == (
        str(diary.resolve()), str(diary.parent.resolve()))
    assert len(s["hyper"].sent) == 1  # the speed-up only
