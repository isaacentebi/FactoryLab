"""Keyless, read-only checks around the hybrid capital-loop rehearsal's real money.

Guarantees nothing here signs, holds or reads a signing key. Every chain read is a
public ``eth_call``, ``eth_getBlockByNumber`` or ``eth_getLogs`` against Base mainnet
through an ``EVM`` built with no account; a run's diary is read the way
``factorylab postmortem`` reads it, with the ledger's own sealing key file beside it
(``ledger.jsonl.key``), never the reserve's key.

Two questions are answered, both from the chain rather than a clock or a typed value
(essay II.IV: a reciprocal flow of capital is only bounded if its bound is observed):

* ``outstanding(run_dir)``: which Venice top-up authorizations a run ever journaled,
  current and superseded, and whether each can still settle; and which shadow sends
  it left unconfirmed. ``scripts/capital_loop_outstanding.py`` prints this.
* ``launch_check(manifest)``: before a capital-loop rehearsal starts, the real reserve
  may lose at most ``max_venice_total_usd`` before reaching its floor, and no earlier
  run left an authorization that could still settle.

Two more facts bound a launch without reading any key. ``ReserveLock`` makes one
capital-loop run per reserve on this host hold the reserve for its whole life (the
floor check reads the chain, which cannot see a signed-but-unsettled authorization, so
two runs side by side could each authorize past it). ``settlement_bound`` refuses a
run too short for one conversion to settle inside it: essay II, IV.c, and AGENTS.md
rule 12, "an inner loop settles at least 3x faster than the outer loop that commands
it". ``submitted_top_ups`` says, at the end of a run, what it left unsettled.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from factorylab.world.evm import BASE, EVM
from factorylab.world.x402 import (
    BASE_RPC,
    MAX_AUTHORIZATION_S,
    Transport,
    http_request,
    usdc_balance,
)

#: How many times faster than the run one conversion must be able to settle
#: (AGENTS.md rule 12, essay II, IV.c: an inner loop settles at least 3x faster than
#: the outer loop that commands it). The run commands the conversion; a run shorter
#: than three settlement horizons leaves most of its conversions settling after it.
SETTLEMENT_RATIO = 3
#: How many times the finality lag sampled at launch a settlement horizon allows: the
#: one sample must also cover the lag growing while the run lasts.
LAG_ALLOWANCE = 2
#: The next step an operator takes when a run ends with a top-up still submitted.
OUTSTANDING_SCRIPT = "scripts/capital_loop_outstanding.py"


class CapitalLoopRefused(RuntimeError):
    """A launch or read refused with a stable reason code and the numbers behind it."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail or {}


def read_items(run_dir: str | Path, *, recorded: bool = False) -> list[dict]:
    """Every diary item of a run, decrypted with the run's own ledger key file.

    ``recorded`` marks the reserve's recorded last run. A run records itself only after
    its launch items exist, so its diary reading empty (header-only, or torn at its
    first record) is truncation, and is refused (``recorded_run_ledger_empty``).

    Guarantees no item that is in the file is silently dropped. Every line but the last
    must be a complete sealed record that this run's key decrypts, and the records must
    form one unbroken chain from the genesis header (each names its sequence number and
    its predecessor's hash), or the read is refused (``run_ledger_unreadable``, naming
    the line). A last line without its newline is read like any other when it decrypts
    and chains (a diary whose final newline was cut is not hiding its newest item); it
    is skipped only when it does not, which is a torn append: the process died
    mid-write, and an item that never finished writing never reached the rail, since
    the treasury journals a step before it acts on it. A run folder carrying another
    run's key (a copy or a restore), a corrupted, removed or reordered middle line, or a
    complete last line that does not decrypt could each hide a ``step_submitted``
    authorization that is still live, so each refuses rather than reading as "nothing
    outstanding".

    What no read of the file alone can detect is a diary cut at a line boundary: the
    lines left are a valid prefix. The defence against that is outside the file: the
    reserve lock's last-run record makes every launch read the last run, and the chain
    (``authorization_status``) is consulted for every authorization that is read.
    """
    from cryptography.fernet import Fernet, InvalidToken

    path = Path(run_dir) / "ledger.jsonl"
    key_path = Path(str(path) + ".key")
    if not path.exists():
        raise CapitalLoopRefused("run_ledger_missing", {"run_dir": str(run_dir)})
    if not key_path.exists():
        raise CapitalLoopRefused("run_ledger_key_missing", {"run_dir": str(run_dir)})
    try:
        cipher = Fernet(key_path.read_bytes().strip())
    except ValueError:
        raise CapitalLoopRefused("run_ledger_key_invalid", {"run_dir": str(run_dir)}) from None
    def unreadable(line: int) -> CapitalLoopRefused:
        return CapitalLoopRefused("run_ledger_unreadable", {"run_dir": str(run_dir),
                                                            "line": line})

    items: list[dict] = []
    with path.open("rb") as stream:
        header = stream.readline()
        # A header torn while the diary was created: nothing was journaled after it.
        torn_header = not header.endswith(b"\n")
        try:
            previous = None if torn_header else json.loads(header)["genesis_hash"]
        except (ValueError, KeyError, TypeError):
            raise unreadable(1) from None
        for seq, line in enumerate(() if torn_header else stream):
            # Only the last line can lack its newline. It counts if it reads; if not,
            # it is the torn final append and nothing after it exists.
            torn = not line.endswith(b"\n")
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or set(record) != {"item"}:
                    raise ValueError
                item = json.loads(cipher.decrypt(record["item"].encode("ascii")))
                if (not isinstance(item, dict) or item.get("seq") != seq
                        or item.get("prev_hash") != previous
                        or not isinstance(item.get("hash"), str)):
                    raise ValueError
            except (ValueError, KeyError, TypeError, AttributeError, InvalidToken):
                if torn:
                    break
                raise unreadable(seq + 2) from None  # 1-based, counting the header
            previous = item["hash"]
            items.append(item)
    if recorded and not items:
        raise CapitalLoopRefused("recorded_run_ledger_empty", {"run_dir": str(run_dir)})
    return items


def _state_step(state: dict) -> str | None:
    steps, index = state.get("steps") or (), state.get("index")
    return steps[index] if type(index) is int and 0 <= index < len(steps) else None


def journaled_references(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """The run's top-up authorizations (one per nonce) and its unconfirmed shadow sends.

    A top-up reference counts once it was journaled toward a signature
    (``treasury.submitted`` or ``treasury.step_submitted``) or kept as superseded; one
    only prepared was never signed. A shadow send is pending when the last journaled
    state of its transfer is still submitted at the shadow step.
    """
    top_ups: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for item in items:
        state = item.get("state")
        if not isinstance(state, dict) or not str(item.get("kind", "")).startswith("treasury."):
            continue
        last[state.get("id")] = state
        references = list((state.get("route_data") or {}).get("superseded_references") or ())
        if (item["kind"] in ("treasury.submitted", "treasury.step_submitted")
                and _state_step(state) == "venice_top_up" and state.get("reference")):
            references.append(state["reference"])
        for reference in references:
            nonce = (reference.get("authorization") or {}).get("nonce")
            if nonce:
                top_ups.setdefault(nonce, {"transfer_id": state.get("id"), **reference})
    shadows = [{"transfer_id": tid, "nonce": (s.get("reference") or {}).get("nonce"),
                "sink": (s.get("reference") or {}).get("destination")
                or (s.get("reference") or {}).get("sink"),
                "amount_micro": s.get("amount_micro")}
               for tid, s in last.items()
               if s.get("status") == "submitted" and _state_step(s) == "shadow_send"]
    return list(top_ups.values()), shadows


def keyless_base(*, transport: Transport = http_request, rpc: str | None = None) -> EVM:
    """Base mainnet with no account: it can read and can never sign."""
    return EVM(BASE, None, transport=transport, rpc=rpc)


def outstanding(run_dir: str | Path, *, reserve_address: str, base: EVM,
                recorded: bool = False) -> dict[str, Any]:
    """Each journaled top-up's on-chain standing, and the run's pending shadow sends.

    ``recorded`` is ``read_items``'s: the reserve's recorded last run may not be empty.
    """
    from factorylab.world.treasury_rails import authorization_status

    top_ups, shadows = journaled_references(read_items(run_dir, recorded=recorded))
    rows = []
    for reference in top_ups:
        row = {"transfer_id": reference.get("transfer_id"),
               "nonce": reference["authorization"]["nonce"],
               "valid_before": int(reference["authorization"]["validBefore"])}
        try:
            row.update(authorization_status(base, reserve_address, reference))
        except Exception as exc:  # noqa: BLE001 - an unread chain is reported, not guessed
            row["unreadable"] = type(exc).__name__
        rows.append(row)
    return {"run_dir": str(run_dir), "top_ups": rows, "shadow_sends": shadows}


def _unsettled(row: dict) -> bool:
    """An authorization that might still settle: live, or not readable at all."""
    return "unreadable" in row or bool(row.get("live"))


def launch_check(manifest: Any, *, previous_runs: tuple = (),
                 recorded_run: str | Path | None = None,
                 transport: Transport = http_request, rpc: str = BASE_RPC) -> dict:
    """Refuse a capital-loop launch the chain says could overspend; return the numbers.

    The floor is only a bound across runs if it is close to the balance: the reserve
    may lose at most ``max_venice_total_usd`` before reaching it, so
    ``balance - floor <= max_venice_total_usd`` is required, read keylessly now. And
    no earlier run's authorization may still be able to settle, since a crashed
    world's last authorization stays valid on chain for up to its quote's timeout.
    ``recorded_run``, the reserve lock's last holder, is read first and must not be
    empty (``read_items``). Each earlier run is read once, however many ways it was
    named.
    """
    treasury = manifest.treasury
    reserve = treasury.reserve_address
    balance = usdc_balance(reserve, rpc=rpc, transport=transport)
    floor, total = treasury.venice_reserve_floor_micro, treasury.max_venice_total_micro
    numbers = {"reserve_address": reserve, "reserve_usdc_micro": balance,
               "venice_reserve_floor_micro": floor, "max_venice_total_micro": total,
               "spendable_above_floor_micro": balance - floor}
    if balance - floor > total:
        raise CapitalLoopRefused("reserve_floor_leaves_more_than_the_total_cap", numbers)
    base = keyless_base(transport=transport, rpc=rpc)
    previous, seen = [], set()
    runs = ((recorded_run,) if recorded_run is not None else ()) + tuple(previous_runs)
    for position, run_dir in enumerate(runs):
        if Path(run_dir).resolve() in seen:
            continue
        seen.add(Path(run_dir).resolve())
        report = outstanding(run_dir, reserve_address=reserve, base=base,
                             recorded=recorded_run is not None and position == 0)
        previous.append(report)
        live = [row for row in report["top_ups"] if _unsettled(row)]
        if live:
            raise CapitalLoopRefused("previous_run_authorization_may_still_settle", {
                **numbers, "run_dir": str(run_dir), "authorizations": live})
    return {**numbers, "previous_runs": previous}


def default_lock_dir() -> Path:
    """The operator's one capital-loop lock directory on this host.

    Guarantees every checkout, worktree, ``--out`` and copied run folder of the same
    operator account resolves the same directory, so the lock is keyed by the reserve
    and nothing else: it lives under the account's home directory as the password
    database names it, never under a run or a repo, and never under ``$HOME``, which a
    launch could point at a fresh, empty directory.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".factorylab" / "capital-loop"


def _durable(fd: int) -> None:
    """Flush ``fd`` to stable storage, not only to the drive's cache, where the OS can.

    On macOS ``fsync`` hands data to the drive, which may still hold it in a volatile
    cache; ``F_FULLFSYNC`` asks the drive to flush it. Elsewhere, or where a filesystem
    refuses it, ``fsync`` is the strongest request there is.
    """
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError:
            pass
    os.fsync(fd)


def _fsync_directory(directory: Path) -> None:
    """Make a rename or a creation inside ``directory`` durable before returning."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _durable(fd)
    finally:
        os.close(fd)


class ReserveLock:
    """Guarantees one capital-loop run per reserve on this host while it is held.

    A second acquisition for the same reserve address, from any process or checkout,
    raises ``CapitalLoopRefused("capital_loop_reserve_locked")`` until the holder calls
    ``close`` or dies. The lock is an ``flock`` on a close-on-exec descriptor, the
    kernel ledger's own writer-lock pattern (``LedgerLock``): the OS releases it with the
    last descriptor, so a crashed or killed run never wedges it, a child never inherits
    it, and there is no pid file to go stale. The lock file is never unlinked (removing a
    locked path would let a second process lock a fresh inode beside it).

    While held, ``last_run`` and ``record_run`` keep the one run that last held this
    reserve (``<reserve>.last-run.json`` beside the lock). A launch checks that run
    wherever its directory is, and records itself only after its own checks passed:
    every run recorded before it was proven settled or dead by the launch after it, and
    a dead EIP-3009 authorization never revives, so the last holder is the only earlier
    run that can still be live. Per host only: another machine is not excluded (the
    on-chain floor still bounds it).

    The record cannot be switched off by removing it. The process that creates a
    reserve's lock file also creates its record, naming no run, before it takes the
    lock; from then on a lock file without a record refuses every launch
    (``capital_loop_last_run_missing``), and a record without its lock file refuses
    too (``capital_loop_lock_file_missing``), without recreating the lock file: a
    lock file removed while a run held it would otherwise let the next launch lock a
    fresh inode beside the running one. After ``flock`` succeeds, the descriptor's inode
    must still be the one the path names (``capital_loop_lock_replaced`` otherwise), so a
    descriptor opened on a file since removed or replaced never counts as the lock. The
    one way back is the deliberate manual reset in the runbook: remove both files, and
    only while no capital-loop run is alive on this host.
    """

    _FLAGS = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC

    def __init__(self, reserve_address: str, *, lock_dir: str | Path | None = None):
        from factorylab.world.x402 import _address

        self.fd = None
        name = _address(reserve_address).lower()
        directory = Path(lock_dir) if lock_dir is not None else default_lock_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / f"{name}.lock"
        self.record_path = directory / f"{name}.last-run.json"
        self.reserve_address = reserve_address
        where = {"reserve_address": reserve_address, "lock": str(self.path),
                 "record": str(self.record_path)}
        if not os.path.lexists(self.path) and os.path.lexists(self.record_path):
            raise CapitalLoopRefused("capital_loop_lock_file_missing", where)
        try:
            fd = os.open(self.path, self._FLAGS | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fd = os.open(self.path, self._FLAGS)
        else:
            try:
                # This reserve was never locked on this account: say so on disk before
                # anyone can hold it, so a missing record always means a removed one.
                self._create_record(None)
            except BaseException:
                os.close(fd)
                raise
        try:
            os.set_inheritable(fd, False)  # explicit, and a no-op where O_CLOEXEC held
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = os.fstat(fd)
            try:
                named = os.stat(self.path, follow_symlinks=False)
            except FileNotFoundError:
                named = None
            if named is None or (named.st_ino, named.st_dev) != (held.st_ino, held.st_dev):
                raise CapitalLoopRefused("capital_loop_lock_replaced", where)
        except BlockingIOError:
            os.close(fd)
            raise CapitalLoopRefused("capital_loop_reserve_locked", {
                "reserve_address": reserve_address, "lock": str(self.path)}) from None
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def last_run(self) -> Path | None:
        """The run that last held this reserve, or None when the record names none.

        A missing record refuses (``capital_loop_last_run_missing``): the lock file
        exists, so a record was written and has since been removed. A record that cannot
        be read refuses (``capital_loop_last_run_unreadable``). Either may hide a run
        whose authorization is still live.
        """
        if not self.record_path.exists():
            raise CapitalLoopRefused("capital_loop_last_run_missing", {
                "record": str(self.record_path), "lock": str(self.path)})
        try:
            record = json.loads(self.record_path.read_text())
            if record["reserve_address"].lower() != self.reserve_address.lower():
                raise ValueError
            return None if record["run_dir"] is None else Path(record["run_dir"])
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            raise CapitalLoopRefused("capital_loop_last_run_unreadable",
                                     {"record": str(self.record_path)}) from None

    def _record(self, run_dir: str | Path | None) -> bytes:
        return json.dumps({"reserve_address": self.reserve_address,
                           "run_dir": None if run_dir is None
                           else str(Path(run_dir).resolve())}).encode()

    def _create_record(self, run_dir: str | Path | None) -> None:
        """Write the first record, durably, unless one is already there to keep."""
        try:
            fd = os.open(self.record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            return  # a lock file removed by hand beside its record: the record stands
        with os.fdopen(fd, "wb") as stream:
            stream.write(self._record(run_dir))
            stream.flush()
            _durable(stream.fileno())
        _fsync_directory(self.record_path.parent)

    def record_run(self, run_dir: str | Path) -> None:
        """Durably name ``run_dir`` as this reserve's last holder, atomically.

        Guarantees the record on disk is the previous one or this one, never a torn mix,
        and that this one is on disk, its rename included (the directory is synced),
        before it returns. Only the holder may record.
        """
        if self.fd is None:
            raise RuntimeError("the reserve lock is not held")
        fd, temporary = tempfile.mkstemp(prefix=".last-run-", dir=self.record_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(self._record(run_dir))
                stream.flush()
                _durable(stream.fileno())
            os.replace(temporary, self.record_path)
            _fsync_directory(self.record_path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def close(self) -> None:
        """Release the reserve exactly once; process death releases it too."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()


def settlement_bound(run_ns: int, tick_interval_ns: int, *,
                     transport: Transport = http_request, rpc: str = BASE_RPC,
                     now_s: Callable[[], int] | None = None) -> dict:
    """Refuse a run planned shorter than ``SETTLEMENT_RATIO`` settlement horizons.

    A top-up authorization settles, or provably dies, only once a finalized Base block
    is past its debit or past its ``validBefore``, and the rail sees that at its next
    tick. The signer sets ``validBefore`` from this host's clock, at most
    ``MAX_AUTHORIZATION_S`` after it prepares the authorization. So one conversion's
    horizon is, in the chain's own time:

    * the validity window, always the ``MAX_AUTHORIZATION_S`` cap: the signer obeys
      whatever quote it is handed later, so a quote read now bounds nothing;
    * plus twice the host's lead over finalized Base: this host's clock now, less the
      finalized block's timestamp, read in one call. That one difference already holds
      Base's finality lag, any lead of the host clock over the chain (a ``validBefore``
      stamped by a fast clock lies that much later in chain time), and any staleness of
      the node that answered (an old finalized block only lengthens it). A separate
      ``latest`` read cannot shorten it: there is none, so two nodes behind one URL
      cannot pair a stale ``latest`` with a fresh ``finalized``. Doubling is the
      allowance for the lag growing during the run (16 minutes measured, 32 allowed);
    * plus one tick for the rail to look.

    Every read is keyless. A finalized block that cannot be read, or whose timestamp is
    not behind this host's clock (finality always trails real time, so a host clock
    that far behind the chain cannot be reasoned from), is unavailable and refuses
    (``finality_lag_unreadable``): no typed constant stands in for Base's own delay.
    ``run_ns`` is the run's planned length; the bound says nothing of a run the
    admission cap, a failure, a signal or a kill ends early. A run planned shorter than
    ``SETTLEMENT_RATIO`` horizons is refused
    (``capital_loop_duration_below_settlement_bound``).
    """
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        base.check_chain()
        final = base.call("eth_getBlockByNumber", ["finalized", False])
        host_s = now_s() if now_s is not None else time.time_ns() // 1_000_000_000
        behind = host_s - int(final["timestamp"], 16)
        if behind <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001 - an unread lag bounds nothing
        raise CapitalLoopRefused("finality_lag_unreadable", {"rpc": base.rpc}) from None
    window = MAX_AUTHORIZATION_S
    horizon_ns = (window + LAG_ALLOWANCE * behind) * 1_000_000_000 + tick_interval_ns
    numbers = {"validity_window_s": window, "finalized_behind_host_s": behind,
               "finality_lag_allowance": LAG_ALLOWANCE,
               "tick_interval_ns": tick_interval_ns,
               "settlement_horizon_ns": horizon_ns, "settlement_ratio": SETTLEMENT_RATIO,
               "minimum_run_ns": SETTLEMENT_RATIO * horizon_ns, "run_ns": run_ns}
    if run_ns < SETTLEMENT_RATIO * horizon_ns:
        raise CapitalLoopRefused("capital_loop_duration_below_settlement_bound", numbers)
    return numbers


def submitted_top_ups(items: list[dict]) -> list[dict]:
    """Every conversion whose last journaled state is still submitted at its top-up step.

    Such a conversion is unbooked: its authorization may still settle (or settled with
    its credit short), or its top-up is still to be prepared after a paid shadow leg.
    Each row names the transfer, its current authorization's nonce and ``validBefore``
    (None when none was prepared) and the last stall reason journaled for it.
    """
    last: dict[str, dict] = {}
    stalled: dict[str, Any] = {}
    for item in items:
        kind, state = str(item.get("kind", "")), item.get("state")
        if kind.startswith("treasury.") and isinstance(state, dict) and state.get("id"):
            last[state["id"]] = state
        if kind == "treasury.pending" and item.get("transfer_id"):
            stalled[item["transfer_id"]] = item.get("reason")
    rows = []
    for transfer_id, state in last.items():
        if state.get("status") != "submitted" or _state_step(state) != "venice_top_up":
            continue
        authorization = (state.get("reference") or {}).get("authorization") or {}
        rows.append({"transfer_id": transfer_id, "nonce": authorization.get("nonce"),
                     "valid_before": authorization.get("validBefore"),
                     "last_reason": (state.get("pending") or {}).get("reason")
                     or stalled.get(transfer_id)})
    return rows
