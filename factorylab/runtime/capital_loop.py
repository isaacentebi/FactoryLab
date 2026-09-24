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
import re
import secrets
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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


def read_items(run_dir: str | Path, *, recorded: bool = False,
               ledger_path: str | Path | None = None) -> list[dict]:
    """Every diary item of a run, decrypted with the run's own ledger key file.

    The diary is ``<run_dir>/ledger.jsonl``, or ``ledger_path`` exactly when given (a
    world launched with ``--ledger runs/foo.jsonl``), with its key beside it.

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
    lines left are a valid prefix. The defence against that is outside the file: every
    authorization is written ahead of its signature to the reserve's authorization
    record (``AuthorizationLog``), which every launch resolves against the chain
    whatever any diary holds (``check_authorization_record``).
    """
    from cryptography.fernet import Fernet, InvalidToken

    path = Path(ledger_path) if ledger_path is not None else Path(run_dir) / "ledger.jsonl"
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


def _create_durably(path: Path, data: bytes) -> None:
    """Create ``path`` holding ``data`` on stable storage, unless it already exists."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600)
    except FileExistsError:
        return  # a lock file removed by hand beside its records: the records stand
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        _durable(stream.fileno())
    _fsync_directory(path.parent)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


#: The record's entry kinds: an authorization written ahead of its signature, a plain
#: reserve-key transaction written ahead of its return to the signer, a resolution, and
#: a torn or damaged line set aside by ``repair_torn`` or ``repair_damaged``.
ENTRY_KINDS = ("authorization", "transaction", "resolved", "torn")
#: Origins whose signer is a world with a diary: a used authorization of theirs must be
#: booked there (``financed_nonces``). Every other origin (a CLI top-up, a probe, the
#: compute proof, an x402 purchase) has no diary to book it and resolves as spent. An
#: entry without an origin predates origins and was a capital-loop run's: the strict case.
WORLD_ORIGINS = ("capital_loop", "treasury")
#: Seconds added, beyond the host clock's lead measured now, when a legacy entry (no
#: ``start_block``) has its scan start derived from ``validBefore``: the lead at signing
#: time is unknown, and five minutes more than today's lead covers a clock that drifted.
LEGACY_SKEW_CUSHION_S = 300
#: Blocks a recorded ``start_block`` is moved back before a scan begins. The head read
#: at signing may be an unsafe block that a reorg replaced with a shorter branch, so the
#: signature's first possible use can sit below it. Every scan filters by the exact
#: authorizer and nonce, so the margin costs reads and can never match another one.
START_BLOCK_MARGIN = 300


def _chains() -> dict:
    """Every chain a reserve-key transaction of this code base can be on, by id."""
    from factorylab.world.evm import BASE_SEPOLIA, HYPEREVM, HYPEREVM_TESTNET

    return {chain.id: chain for chain in (BASE, BASE_SEPOLIA, HYPEREVM, HYPEREVM_TESTNET)}


def _entry(line: bytes) -> dict | None:
    """One well-formed record entry, or None."""
    try:
        entry = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(entry, dict) or entry.get("kind") not in ENTRY_KINDS:
        return None
    kind = entry["kind"]
    if kind == "torn":
        nonces, hashes = entry.get("nonces"), entry.get("tx_hashes", [])
        ok = (isinstance(nonces, list) and all(isinstance(n, str) for n in nonces)
              and isinstance(hashes, list) and all(isinstance(h, str) for h in hashes)
              and isinstance(entry.get("validBefore"), str))
        return entry if ok else None
    if kind == "transaction":
        ok = (isinstance(entry.get("tx_hash"), str) and isinstance(entry.get("from"), str)
              and type(entry.get("tx_nonce")) is int and entry.get("chain_id") in _chains())
        return entry if ok else None
    if kind == "resolved":
        keys = ("nonce", "tx_hash", "sidecar")
        return entry if any(isinstance(entry.get(key), str) for key in keys) else None
    return entry if isinstance(entry.get("nonce"), str) else None


def _torn(path: Path, fragment: bytes) -> CapitalLoopRefused:
    return CapitalLoopRefused("authorization_record_torn", {
        "record": str(path), "fragment_bytes": len(fragment),
        "repair": "uv run python scripts/capital_loop_outstanding.py --repair-torn"})


def _append_durably(path: Path, entry: dict) -> None:
    """Append one JSON line to an existing record and flush it to stable storage.

    Guarantees the line is on disk before this returns, and raises otherwise. The file
    must already exist (it is created with the reserve's lock), so an append never
    starts a fresh record beside a removed one. It must also end in a newline: a last
    line that is a whole entry missing only its newline is completed first (the newline
    written and flushed); a torn fragment refuses (``authorization_record_torn``) and
    nothing is appended, since a line written onto it would merge with it and make the
    record unreadable for every launch after.
    """
    if not path.exists():
        raise CapitalLoopRefused("capital_loop_authorization_record_missing",
                                 {"record": str(path)})
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        data = os.pread(fd, os.fstat(fd).st_size, 0)
        tail = data[data.rfind(b"\n") + 1:]
        if tail:
            if _entry(tail) is None:
                raise _torn(path, tail)
            _write_all(fd, b"\n")
            _durable(fd)
        _write_all(fd, json.dumps(entry, sort_keys=True).encode() + b"\n")
        _durable(fd)
    finally:
        os.close(fd)


def _chain_block(start_block: Any) -> int:
    if type(start_block) is not int or start_block < 0:
        raise ValueError("start_block must be the chain head read at record time")
    return start_block


class AuthorizationLog:
    """Guarantees a reserve-key signature is on stable storage before it can be used.

    ``x402.sign_transfer_authorization`` calls this with an EIP-3009 authorization's
    message and the chain head it read just before; ``EVM`` calls ``record_transaction``
    for a plain transaction it has signed but not yet returned, and ``sending`` around
    each broadcast. One JSON line (nonce or transaction hash, payer, payee, value,
    validity, ``start_block``, origin, run directory, the diary's exact ledger path) is
    appended to ``<reserve>.authorizations.jsonl`` beside the reserve's lock, outside
    every diary, and flushed (``F_FULLFSYNC`` where the OS has it) before this returns.
    It writes only while its ``ReserveLock`` is held; otherwise, or if the write fails,
    it raises and nothing is signed. ``start_block`` is the latest block when the entry
    was written: the signature does not exist before the entry, so it cannot be used
    before it.
    """

    def __init__(self, lock: ReserveLock, run_dir: str | Path | None, origin: str,
                 ledger: str | Path | None = None):
        self.lock, self.origin = lock, origin
        self.path, self.reserve_address = lock.authorizations_path, lock.reserve_address
        self.run_dir = None if run_dir is None else str(Path(run_dir).resolve())
        self.ledger = None if ledger is None else str(Path(ledger).resolve())

    def _where(self) -> dict:
        return {"origin": self.origin, "run_dir": self.run_dir, "ledger": self.ledger}

    def permit(self, payer: str) -> None:
        """Raise unless this record's lock is held for ``payer``."""
        if self.lock.fd is None:
            raise CapitalLoopRefused("capital_loop_reserve_lock_not_held",
                                     {"lock": str(self.lock.path)})
        if str(payer).lower() != self.reserve_address.lower():
            raise ValueError("payer is not this reserve")

    def __call__(self, authorization: dict, start_block: int) -> None:
        # A journal reference carries its message under "authorization".
        auth = authorization.get("authorization", authorization)
        self.permit(auth["from"])
        nonce = str(auth["nonce"]).lower()
        if (len(nonce) != 66 or not nonce.startswith("0x")
                or any(c not in "0123456789abcdef" for c in nonce[2:])):
            raise ValueError("authorization nonce must be 32 bytes of hex")
        _append_durably(self.path, {
            "kind": "authorization", "nonce": nonce, "from": str(auth["from"]),
            "to": str(auth["to"]), "value": str(auth["value"]),
            "validAfter": str(auth["validAfter"]), "validBefore": str(auth["validBefore"]),
            "start_block": _chain_block(start_block), **self._where()})

    def record_transaction(self, transaction: dict) -> None:
        """Write a signed, not yet returned, reserve-key transaction ahead of its use."""
        self.permit(transaction["from"])
        if int(transaction["chain_id"]) not in _chains():
            raise ValueError("a reserve-key transaction on a chain this code does not know")
        price = transaction.get("gas_price")
        # The call itself (public calldata) and what it does: a stuck one is then re-sent
        # identically, or, unless it is a CCTP mint, cancelled (``replacement_exit``).
        _append_durably(self.path, {
            "kind": "transaction", "tx_hash": str(transaction["tx_hash"]).lower(),
            "chain_id": int(transaction["chain_id"]), "from": str(transaction["from"]),
            "to": str(transaction["to"]), "tx_nonce": int(transaction["nonce"]),
            "data": str(transaction.get("data", "0x")),
            "value": int(transaction.get("value", 0)),
            "step": str(transaction.get("step") or "other"),
            "gas_price": None if price is None else int(price),
            "start_block": _chain_block(transaction["start_block"]), **self._where()})

    @contextmanager
    def sending(self, payer: str, tx_hash: str) -> Iterator[None]:
        """Hold the send of ``tx_hash`` inside this held lock, and only if it is recorded.

        Guarantees the body (the ``eth_sendRawTransaction``) runs while this record's
        lock is held for ``payer`` and only for a transaction whose hash the record
        holds; otherwise ``transaction_not_on_record`` (or the lock's own refusal) and
        nothing is sent.
        """
        self.permit(payer)
        entries = read_authorizations(self.path)
        # A torn line's legible hash is still the record's: a world may send it.
        recorded = {e["tx_hash"].lower() for e in entries if e["kind"] == "transaction"}
        recorded |= {h.lower() for e in entries if e["kind"] == "torn"
                     for h in e.get("tx_hashes", ())}
        if str(tx_hash).lower() not in recorded:
            raise CapitalLoopRefused("transaction_not_on_record", {"tx_hash": str(tx_hash)})
        yield


class ReserveGuard:
    """The write-ahead guard for a signer that holds no capital-loop run's lock.

    Guarantees each authorization or transaction is written ahead exactly as a
    capital-loop run writes it: for the one write it takes the payer's ``ReserveLock``
    (refusing, ``capital_loop_reserve_locked``, while any capital-loop run or other signer
    holds it) and appends through ``AuthorizationLog``. ``sending`` holds that lock from
    the check that the transaction is on the record until its ``eth_sendRawTransaction``
    returns, so no reserve outflow starts while a capital-loop run is alive, and no run
    can start between the check and the send. An EIP-3009 signature is used after the
    lock is released; that is safe, because the next launch reads the record and refuses
    on what it finds live. ``origin`` names the signer (``reserve_topup``,
    ``x402_purchase``, ``treasury``, ``compute_proof``...); ``ledger`` is a world's exact
    diary path, where its bookings are read.
    """

    def __init__(self, origin: str, *, run_dir: str | Path | None = None,
                 ledger: str | Path | None = None, lock_dir: str | Path | None = None):
        self.origin, self.run_dir, self.lock_dir = origin, run_dir, lock_dir
        self.ledger = None if ledger is None else Path(ledger).resolve()

    def _log(self, lock: ReserveLock) -> AuthorizationLog:
        return lock.authorization_log(self.run_dir, origin=self.origin, ledger=self.ledger)

    def permit(self, payer: str) -> None:
        with ReserveLock(str(payer), lock_dir=self.lock_dir) as lock:
            self._log(lock).permit(payer)

    def __call__(self, authorization: dict, start_block: int) -> None:
        auth = authorization.get("authorization", authorization)
        with ReserveLock(str(auth["from"]), lock_dir=self.lock_dir) as lock:
            self._log(lock)(auth, start_block)

    def record_transaction(self, transaction: dict) -> None:
        with ReserveLock(str(transaction["from"]), lock_dir=self.lock_dir) as lock:
            self._log(lock).record_transaction(transaction)

    @contextmanager
    def sending(self, payer: str, tx_hash: str) -> Iterator[None]:
        with ReserveLock(str(payer), lock_dir=self.lock_dir) as lock:
            with self._log(lock).sending(payer, tx_hash):
                yield


def read_authorizations(path: str | Path) -> list[dict]:
    """Every entry of a write-ahead authorization record, in order.

    A last line missing only its newline counts when it is a whole entry (the next
    append completes it). A torn fragment refuses (``authorization_record_torn``,
    repaired with ``--repair-torn``), and so does any other line that is not a whole
    entry (``authorization_record_unreadable``, repaired with ``--repair-damaged``):
    nothing in the record is skipped.
    """
    data = Path(path).read_bytes()
    lines = data.split(b"\n")
    last = lines.pop()
    entries = []
    for number, line in enumerate(lines, start=1):
        entry = _entry(line)
        if entry is None:
            raise CapitalLoopRefused("authorization_record_unreadable", {
                "record": str(path), "line": number,
                "repair": "uv run python scripts/capital_loop_outstanding.py "
                          "--repair-damaged"})
        entries.append(entry)
    if last:
        entry = _entry(last)
        if entry is None:
            raise _torn(Path(path), last)
        entries.append(entry)
    return entries


_NONCE = re.compile(rb"0x[0-9a-fA-F]{64}")
#: A value counts only when it is terminated: a closing quote, or for a bare number a
#: delimiter. "validBefore": "13 cut mid-digits is unknown, never 13.
_VALID_BEFORE = re.compile(rb'"validBefore"\s*:\s*(?:"(\d{1,20})"|(\d{1,20})\s*[,}])')
_START_BLOCK = re.compile(rb'"start_block"\s*:\s*(\d{1,20})\s*[,}]')
_CHAIN_ID = re.compile(rb'"chain_id"\s*:\s*(\d{1,20})\s*[,}]')
_TX_NONCE = re.compile(rb'"tx_nonce"\s*:\s*(\d{1,20})\s*[,}]')
_TX_HASH = re.compile(rb'"tx_hash"\s*:\s*"(0x[0-9a-fA-F]{64})"')
#: A transaction line: its keys sort "chain_id" first, and only it names a tx_hash or
#: tx_nonce. Its 32-byte hash must never be mistaken for an authorization's nonce.
_TRANSACTION = re.compile(
    rb'^\s*\{\s*"chain_id"|"kind"\s*:\s*"transaction"|"tx_hash"\s*:|"tx_nonce"\s*:')


def _terminated(pattern: re.Pattern, fragment: bytes) -> int | None:
    found = pattern.search(fragment)
    if found is None:
        return None
    return int(next(group for group in found.groups() if group is not None))


def _torn_entry(fragment: bytes, reserve: str, sidecar: Path, now: int, bound: int) -> dict:
    """What a line that is not a whole entry may have recorded, kept open.

    A transaction's line gives only its hash, and its chain and account nonce when
    each is terminated; no nonce is read from it, so it never becomes an authorization.
    An authorization's line gives every nonce-like value, a terminated ``validBefore``
    capped at ``bound`` (``bound`` itself when none), and a terminated ``start_block``.
    A transaction's ``start_block`` is kept only when its chain is legibly Base mainnet.
    """
    entry = {"kind": "torn", "from": reserve, "fragment_hex": fragment.hex(),
             "sidecar": str(sidecar), "repaired_s": now}
    if _TRANSACTION.search(fragment):
        chain_id = _terminated(_CHAIN_ID, fragment)
        chain_id = chain_id if chain_id in _chains() else None
        found = _TX_HASH.search(fragment)
        entry.update({
            "torn_kind": "transaction", "nonces": [], "validBefore": str(bound),
            "tx_hashes": [] if found is None else [found.group(1).decode().lower()],
            "chain_id": chain_id, "tx_nonce": _terminated(_TX_NONCE, fragment),
            "start_block": (_terminated(_START_BLOCK, fragment) if chain_id == BASE.id
                            else None)})
        return entry
    named = _terminated(_VALID_BEFORE, fragment)
    entry.update({
        "torn_kind": "authorization",
        "nonces": sorted({n.decode().lower() for n in _NONCE.findall(fragment)}),
        "validBefore": str(bound if named is None else min(named, bound)),
        "start_block": _terminated(_START_BLOCK, fragment)})
    return entry


def _create_sidecar(path: Path, now: int, data: bytes) -> Path:
    """A new file beside ``path`` holding ``data`` on stable storage, never an old one.

    Its name carries the repair time and 32 random bits, created exclusively, so two
    repairs in one second never meet (a collision is drawn again).
    """
    while True:
        sidecar = path.with_name(f"{path.name}.torn-{now}-{secrets.token_hex(4)}")
        try:
            fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_CLOEXEC, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            _durable(stream.fileno())
        _fsync_directory(path.parent)
        return sidecar


def _repair(lock: ReserveLock, *, damaged: bool, now_s: Callable[[], int] | None) -> dict:
    if lock.fd is None:
        raise RuntimeError("the reserve lock is not held")
    path = lock.authorizations_path
    data = path.read_bytes()
    lines = data.split(b"\n")
    tail = lines.pop()
    now = now_s() if now_s is not None else time.time_ns() // 1_000_000_000
    bound = now + MAX_AUTHORIZATION_S
    torn: list[dict] = []

    def set_aside(line: bytes) -> bytes:
        entry = _torn_entry(line, lock.reserve_address, _create_sidecar(path, now, line),
                            now, bound)
        torn.append(entry)
        return json.dumps(entry, sort_keys=True).encode()

    kept = [set_aside(line) if damaged and _entry(line) is None else line for line in lines]
    if tail:
        kept.append(tail if _entry(tail) is not None else set_aside(tail))
    if not torn:
        return {"record": str(path), "repaired": False}
    fd, temporary = tempfile.mkstemp(prefix=".authorizations-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(b"\n".join(kept) + b"\n")
            stream.flush()
            _durable(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"record": str(path), "repaired": True, "sidecar": torn[-1]["sidecar"],
            "sidecars": [e["sidecar"] for e in torn],
            "open_nonces": sorted({n for e in torn for n in e["nonces"]}),
            "open_transactions": sorted({h for e in torn for h in e.get("tx_hashes", ())}),
            "valid_before": int(torn[-1]["validBefore"])}


def repair_torn(lock: ReserveLock, *, now_s: Callable[[], int] | None = None) -> dict:
    """Set a torn last fragment aside, losing nothing, and replace the record atomically.

    Guarantees, at every instant of a crash, either the old record (fragment included,
    still refused as torn) or the new one (the good prefix and a ``torn`` entry) is on
    disk, never neither: the fragment is first written durably to a new sidecar
    (``<record>.torn-<seconds>-<random>``); the new record is written whole to a
    temporary file and flushed; ``os.replace`` swaps it in; the directory is flushed.
    What the fragment may have recorded stays open (``_torn_entry``): an authorization's
    nonce-like values, with its terminated ``validBefore`` capped at the repair time plus
    ``MAX_AUTHORIZATION_S`` (that cap when unknown), or a transaction's hash, chain and
    account nonce. Only the lock's holder repairs; a record that is not torn is left as
    it is.
    """
    return _repair(lock, damaged=False, now_s=now_s)


def repair_damaged(lock: ReserveLock, *, now_s: Callable[[], int] | None = None) -> dict:
    """``repair_torn`` for every line that is not a whole entry, the middle included.

    Guarantees the same atomic sidecar-and-replace, one sidecar per damaged line, each
    line replaced in place by the ``torn`` entry ``_torn_entry`` makes of it, so every
    legible nonce and transaction hash stays open under the same rules. Whole entries
    are kept byte for byte; a record with nothing damaged is left as it is.
    """
    return _repair(lock, damaged=True, now_s=now_s)


AUTHORIZATION_USED = "AuthorizationUsed(address,bytes32)"
AUTHORIZATION_CANCELED = "AuthorizationCanceled(address,bytes32)"
TRANSFER = "Transfer(address,address,uint256)"


def _authorization_used(base: EVM, authorizer: str, nonce: str, block: int) -> bool:
    """USDC's own ``authorizationState(authorizer, nonce)`` at one block, keylessly.

    True once the nonce was used or canceled: either way it can never be used again.
    """
    from factorylab.world.evm import address, calldata

    data = calldata("authorizationState(address,bytes32)", ["address", "bytes32"],
                    [address(authorizer), bytes.fromhex(nonce.removeprefix("0x"))])
    state = base.call("eth_call", [{"to": address(base.chain.usdc), "data": data}, hex(block)])
    return int(state, 16) != 0


def _finalized(base: EVM) -> dict:
    """The finalized and latest blocks, read once each, only when latest is later.

    A provider that answers the ``finalized`` tag with its latest block would make every
    finalized read here a read of an unfinal chain (``finalized_tag_not_behind_latest``).
    Every later read of this check uses these numbers, never the tags again.
    """
    base.check_chain()
    final = base.call("eth_getBlockByNumber", ["finalized", False])
    latest = base.call("eth_getBlockByNumber", ["latest", False])
    view = {"final_number": int(final["number"], 16),
            "final_timestamp": int(final["timestamp"], 16),
            "latest_number": int(latest["number"], 16),
            "latest_timestamp": int(latest["timestamp"], 16)}
    if view["latest_number"] <= view["final_number"]:
        raise CapitalLoopRefused("finalized_tag_not_behind_latest", {
            "rpc": base.rpc, "finalized_block": view["final_number"],
            "latest_block": view["latest_number"]})
    return view


def _timestamp(base: EVM, number: int) -> int:
    block = base.call("eth_getBlockByNumber", [hex(number), False])
    if not block:
        raise CapitalLoopRefused("block_times_unreadable", {"block": number})
    return int(block["timestamp"], 16)


def _block_at_or_before(base: EVM, timestamp: int, final_number: int,
                        final_timestamp: int) -> int:
    """A finalized block at or before ``timestamp``, verified by its own timestamp.

    Blocks are at least a second apart, so the block that many blocks back from the
    finalized one is no later than ``timestamp``; its own timestamp is read to check it.
    That bound is then tightened once, at the block rate measured between the two, and
    the tighter block is used only when its own timestamp is still at or before
    ``timestamp``: the answer is never later than asked, and rarely much earlier.
    """
    if timestamp >= final_timestamp:
        return final_number
    candidate = max(0, final_number - (final_timestamp - timestamp))
    stamp = _timestamp(base, candidate)
    if candidate and stamp > timestamp:
        raise CapitalLoopRefused("block_times_unreadable", {"block": candidate})
    if candidate == final_number or stamp == final_timestamp:
        return candidate
    span = final_timestamp - stamp
    back = -(-(final_timestamp - timestamp) * (final_number - candidate) // span)
    tighter = max(candidate, final_number - back)
    if tighter > candidate and _timestamp(base, tighter) <= timestamp:
        return tighter
    return candidate


def _first_block_after(base: EVM, timestamp: int, low: int, high: int) -> int:
    """The first block in ``[low, high]`` whose timestamp is past ``timestamp``.

    ``high``'s is (the caller checked); block timestamps never decrease, so a binary
    search over block timestamps finds it in about log2(high - low) reads.
    """
    while low < high:
        middle = (low + high) // 2
        if _timestamp(base, middle) > timestamp:
            high = middle
        else:
            low = middle + 1
    return low


def _scan_start(base: EVM, entry: dict, view: dict, host_s: int) -> int:
    """Where an entry's scan begins: ``START_BLOCK_MARGIN`` blocks before its recorded
    ``start_block`` (never below 0), or, for a legacy or torn entry without one, the block
    at ``validBefore`` less ``MAX_AUTHORIZATION_S``, less the host clock's lead now plus
    ``LEGACY_SKEW_CUSHION_S``, less the finality lag, in chain time (never the genesis
    block unless the chain is that young)."""
    if type(entry.get("start_block")) is int:
        return max(0, entry["start_block"] - START_BLOCK_MARGIN)
    lead = max(0, host_s - view["latest_timestamp"])
    lag = view["latest_timestamp"] - view["final_timestamp"]
    anchor = (int(entry["validBefore"]) - MAX_AUTHORIZATION_S - lead
              - LEGACY_SKEW_CUSHION_S - lag)
    return _block_at_or_before(base, anchor, view["final_number"], view["final_timestamp"])


def _authorization_fate(base: EVM, entry: dict, view: dict, host_s: int) -> str:
    """``used``, ``canceled``, ``expired`` or ``live``, from reads that must agree.

    USDC's ``authorizationState`` at the finalized block (true once used or canceled),
    and scans for its ``AuthorizationUsed`` and ``AuthorizationCanceled`` over exactly
    the blocks it could have been used or canceled in: from ``_scan_start`` to the first
    block past its ``validBefore``, or to that same finalized block while none is past
    it yet. The scan's end is that explicit block, never a re-read of the ``finalized``
    tag, and its cost is bounded whatever the entry's age. A state and scans that
    disagree, both events for one nonce, or a scan that fell short of its end, refuse
    (``recorded_authorization_reads_disagree``); nothing is resolved from one read.
    ``expired`` needs the state unused, no log, and the finalized block past
    ``validBefore``. A canceled authorization is dead, like an expired one.
    """
    from factorylab.world.evm import event_topic, word_address

    nonce, authorizer = entry["nonce"].lower(), entry["from"]
    valid_before, final_number = int(entry["validBefore"]), view["final_number"]
    state = _authorization_used(base, authorizer, nonce, final_number)
    start = min(_scan_start(base, entry, view, host_s), final_number)
    end = final_number
    if view["final_timestamp"] > valid_before:
        end = _first_block_after(base, valid_before, start, final_number)
    word = "0x" + word_address(authorizer).hex()
    used, used_to = base.scan(base.chain.usdc, [event_topic(AUTHORIZATION_USED), word, nonce],
                              start, end=end)
    canceled, canceled_to = base.scan(
        base.chain.usdc, [event_topic(AUTHORIZATION_CANCELED), word, nonce], start, end=end)
    if (min(used_to, canceled_to) < end or state != bool(used or canceled)
            or (used and canceled)):
        raise CapitalLoopRefused("recorded_authorization_reads_disagree", {
            "nonce": nonce, "authorization_state_used": state, "logs": len(used),
            "canceled_logs": len(canceled), "scan": [start, end],
            "scanned_to": min(used_to, canceled_to), "finalized_block": final_number})
    if used:
        return "used"
    if canceled:
        return "canceled"
    return "expired" if view["final_timestamp"] > valid_before else "live"


def _open_authorizations(entries: list[dict]) -> dict[str, dict]:
    """Every recorded or torn nonce not yet resolved, with what is known of it."""
    resolved = {e["nonce"].lower() for e in entries
                if e["kind"] == "resolved" and isinstance(e.get("nonce"), str)}
    pending: dict[str, dict] = {}
    for entry in entries:
        if entry["kind"] == "authorization":
            nonce = entry["nonce"].lower()
            if nonce not in resolved:
                pending.setdefault(nonce, entry)
        elif entry["kind"] == "torn" and entry.get("torn_kind") != "transaction":
            for nonce in entry["nonces"]:
                if nonce.lower() not in resolved:
                    pending.setdefault(nonce.lower(), {
                        "kind": "torn", "nonce": nonce.lower(), "from": entry["from"],
                        "validBefore": entry["validBefore"],
                        "start_block": entry.get("start_block"), "origin": "torn",
                        "run_dir": None})
    return pending


def _transaction_key(entry: dict) -> str:
    """A recorded transaction's hash; a torn one's legible hash, else its sidecar."""
    if entry["kind"] == "transaction":
        return entry["tx_hash"].lower()
    hashes = entry.get("tx_hashes") or []
    return hashes[0].lower() if hashes else entry["sidecar"]


def open_transactions(entries: list[dict]) -> dict[str, dict]:
    """Every recorded or torn reserve-key transaction not yet resolved, by its key."""
    resolved = {str(e.get("tx_hash") or e.get("sidecar")).lower() for e in entries
                if e["kind"] == "resolved" and not isinstance(e.get("nonce"), str)}
    pending: dict[str, dict] = {}
    for entry in entries:
        if entry["kind"] == "transaction" or (
                entry["kind"] == "torn" and entry.get("torn_kind") == "transaction"):
            key = _transaction_key(entry)
            if key.lower() not in resolved:
                pending.setdefault(key, entry)
    return pending


def _finalized_number(chain: EVM) -> int:
    chain.check_chain()
    return int(chain.call("eth_getBlockByNumber", ["finalized", False])["number"], 16)


#: Chain ids a launch resolves transactions on: a mainnet capital loop never waits on a
#: testnet transaction, and a testnet launch never on a mainnet one.
MAINNET_CHAINS = (8453, 999)
TESTNET_CHAINS = (84532, 998)
#: HyperEVM blocks are final once produced (HyperBFT one-block finality:
#: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperevm), so its ``latest`` is final.
HYPEREVM_CHAINS = (999, 998)


def chain_reader(chain_id: int, *, transport: Transport = http_request,
                 rpcs: dict | None = None) -> EVM:
    """A keyless ``EVM`` for one chain this code base signs on, at its overridden RPC."""
    return EVM(_chains()[chain_id], None, transport=transport,
               rpc=(rpcs or {}).get(chain_id))


def _reading(chain: EVM, read: Callable[[], Any]) -> Any:
    """``read()``, or ``recorded_authorization_unreadable`` naming the RPC that failed."""
    try:
        return read()
    except CapitalLoopRefused:
        raise
    except Exception:  # noqa: BLE001 - an unread chain resolves nothing
        raise CapitalLoopRefused("recorded_authorization_unreadable", {
            "rpc": chain.rpc, "chain_id": chain.chain.id}) from None


def _transaction_fate(entry: dict, view: dict, host_s: int, *, base: EVM,
                      transport: Transport, rpcs: dict | None = None,
                      network: tuple = MAINNET_CHAINS) -> str:
    """``consumed``, ``dropped`` or ``pending`` for one recorded reserve-key transaction.

    Consumed once the reserve's account nonce on the transaction's own chain is past its
    ``tx_nonce`` at a final block (Base's finalized block; HyperEVM's latest, which is
    final): neither it nor any replacement at that nonce can execute any more (an
    unrecorded transaction that took the nonce is the cooling-off scan's to find). A
    torn line with no legible chain and account nonce is dropped only once a whole
    cooling-off window (``MAX_AUTHORIZATION_S`` plus twice the finality lag) has passed
    since its repair and no chain of ``network`` it can be on knows its hash, or knows
    it only in a final block; otherwise it is pending. A read that fails names its RPC.
    """
    from factorylab.world.evm import address

    chains = _chains()
    chain_id, tx_nonce = entry.get("chain_id"), entry.get("tx_nonce")

    def reader(identity: int) -> EVM:
        return base if identity == BASE.id else chain_reader(identity, transport=transport,
                                                             rpcs=rpcs)

    def final_tag(chain: EVM) -> str:
        if chain is base:
            return hex(view["final_number"])
        if chain.chain.id in HYPEREVM_CHAINS:
            return "latest"  # HyperBFT: a produced block is final (HYPEREVM_CHAINS)
        return hex(_finalized_number(chain))

    if chain_id in chains and type(tx_nonce) is int:
        chain = reader(chain_id)
        count = _reading(chain, lambda: chain.call(
            "eth_getTransactionCount", [address(entry["from"]), final_tag(chain)]))
        return "consumed" if int(count, 16) > tx_nonce else "pending"
    window = MAX_AUTHORIZATION_S + 2 * (view["latest_timestamp"] - view["final_timestamp"])
    if host_s - int(entry["repaired_s"]) < window:
        return "pending"
    mined = False
    for tx_hash in entry.get("tx_hashes") or ():
        for identity in ([chain_id] if chain_id in chains else network):
            chain = reader(identity)

            def lookup(chain: EVM = chain, tx_hash: str = tx_hash) -> str:
                chain.check_chain()
                found = chain.call("eth_getTransactionByHash", [tx_hash])
                if found is None:
                    return "unknown"
                number, tag = found.get("blockNumber"), final_tag(chain)
                final = (int(chain.call("eth_getBlockByNumber", ["latest", False])["number"],
                             16) if tag == "latest" else int(tag, 16))
                return "final" if number is not None and int(number, 16) <= final else "open"

            seen = _reading(chain, lookup)
            if seen == "open":
                return "pending"
            mined = mined or seen == "final"
    return "consumed" if mined else "dropped"


def replacement_exit(key: str, entry: dict) -> dict:
    """What an operator may do about a recorded transaction that has not executed.

    A CCTP mint is only ever sped up: cancelling it would leave its burn with nothing
    minted. Anything else may also be cancelled, once its world has ended, at the price
    of that world's treasury step (``CANCEL_CONSEQUENCE``). A transaction whose call was
    not recorded is waited for.
    """
    script = "uv run python scripts/capital_loop_outstanding.py"
    step = entry.get("step")
    if entry.get("kind") != "transaction" or step is None or entry.get("data") is None:
        return {"wait": "it resolves once its nonce is used or, torn, once a cooling-off "
                        "window has passed"}
    exits = {"speed_up": f"{script} --speed-up {key}"}
    if step != "mint":
        exits["cancel"] = (f"{script} --cancel-transaction {key} "
                           "--i-understand-the-world-step-is-abandoned")
        exits["cancel_consequence"] = CANCEL_CONSEQUENCE
    return exits


#: What cancelling a recorded reserve-key transaction costs the world that made it.
CANCEL_CONSEQUENCE = ("this world's treasury step will not complete; it must be recovered "
                      "by hand")


#: Launch refusals that mean real money may have moved without being booked: the
#: runner exits 3 on them, as at the end of a run that left a top-up unresolved.
RECOVERY_REASONS = ("recorded_authorization_settled_unbooked",
                    "unrecorded_reserve_authorization")


def _booking(entry: dict, financed_by_run: dict) -> str | None:
    """How a used entry resolves: ``financed``, ``spent``, or None for a recovery.

    A world's origin (``WORLD_ORIGINS``; an entry with none is the strict
    ``capital_loop``) resolves only when its diary booked that nonce as financing: the
    exact ledger file the entry names, or, for an entry older than that field, the
    ``ledger.jsonl`` of its run directory. A torn fragment's never does: nothing shows
    where it belonged. Any other origin has no diary and was spent in the open.
    """
    origin = entry.get("origin") or "capital_loop"
    if origin == "torn":
        return None
    if origin not in WORLD_ORIGINS:
        return "spent"
    ledger, run_dir = entry.get("ledger"), entry.get("run_dir")
    key = (ledger, run_dir)
    if key not in financed_by_run:
        try:
            items = (read_items(Path(ledger).parent, ledger_path=ledger) if ledger
                     else read_items(run_dir))
            financed_by_run[key] = financed_nonces(items)
        except (CapitalLoopRefused, TypeError):
            financed_by_run[key] = set()  # an unread diary proves no booking
    return "financed" if entry["nonce"].lower() in financed_by_run[key] else None


def _host_s(now_s: Callable[[], int] | None) -> int:
    return now_s() if now_s is not None else time.time_ns() // 1_000_000_000


def check_authorization_record(lock: ReserveLock, *, transport: Transport = http_request,
                               rpc: str = BASE_RPC,
                               now_s: Callable[[], int] | None = None,
                               rpcs: dict | None = None, testnet: bool = False) -> dict:
    """Resolve everything the reserve's record holds against the chain, or refuse.

    Guarantees no authorization or plain transaction this reserve ever wrote ahead of
    signing is ignored, whatever its diary now holds. For each authorization not yet
    resolved (a torn fragment's nonces included), ``_authorization_fate`` decides from
    reads that must agree:

    * used: resolved when ``_booking`` finds it booked (a world's, in its diary) or
      spent in the open (a diary-less signer's); otherwise refused as a recovery,
      ``recorded_authorization_settled_unbooked``, settled by hand and then acknowledged
      (``scripts/capital_loop_outstanding.py --acknowledge``);
    * canceled or expired: it can never be used (EIP-3009), so resolved;
    * live: refused, ``recorded_authorization_may_still_settle``.

    For each transaction not yet resolved on this launch's network (``MAINNET_CHAINS``,
    or ``TESTNET_CHAINS`` when ``testnet``; the other network's never blocks it),
    ``_transaction_fate``: consumed or dropped is resolved; pending refuses,
    ``recorded_transaction_may_still_execute``, naming each one's exits
    (``replacement_exit``). ``rpcs`` overrides each non-Base chain's RPC by chain id.
    Each resolution is appended to the record. A chain that cannot be read refuses
    (``recorded_authorization_unreadable``, naming that chain's RPC). Only the lock's
    holder resolves.
    """
    entries = lock.authorizations()
    pending = _open_authorizations(entries)
    network = TESTNET_CHAINS if testnet else MAINNET_CHAINS
    transactions = {key: entry for key, entry in open_transactions(entries).items()
                    if entry.get("chain_id") is None or entry.get("chain_id") in network}
    summary = {"open": len(pending), "open_transactions": len(transactions),
               "resolved_now": []}
    if not pending and not transactions:
        return summary
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        view = _finalized(base)
        host_s = _host_s(now_s)
        fates = {nonce: _authorization_fate(base, entry, view, host_s)
                 for nonce, entry in pending.items()}
        tx_fates = {key: _transaction_fate(entry, view, host_s, base=base,
                                           transport=transport, rpcs=rpcs, network=network)
                    for key, entry in transactions.items()}
    except CapitalLoopRefused:
        raise
    except Exception:  # noqa: BLE001 - an unread chain resolves nothing
        raise CapitalLoopRefused("recorded_authorization_unreadable", {
            "record": str(lock.authorizations_path), "rpc": base.rpc}) from None
    financed_by_run: dict[Any, set] = {}
    live, unbooked, executing = [], [], []
    for nonce, entry in pending.items():
        fate = fates[nonce]
        row = {"nonce": nonce, "valid_before": int(entry["validBefore"]),
               "origin": entry.get("origin"), "run_dir": entry.get("run_dir"),
               "ledger": entry.get("ledger"), "finalized_block": view["final_number"],
               "finalized_timestamp": view["final_timestamp"], "fate": fate}
        if fate == "used":
            how = _booking(entry, financed_by_run)
            if how is None:
                unbooked.append(row)
                continue
            lock.resolve(nonce, how)
            summary["resolved_now"].append({**row, "how": how})
        elif fate in ("expired", "canceled"):
            lock.resolve(nonce, fate)
            summary["resolved_now"].append({**row, "how": fate})
        else:
            live.append(row)
    for key, entry in transactions.items():
        fate = tx_fates[key]
        row = {"tx_hash": key, "chain_id": entry.get("chain_id"),
               "tx_nonce": entry.get("tx_nonce"), "step": entry.get("step"),
               "origin": entry.get("origin"), "fate": fate}
        if fate == "pending":
            executing.append({**row, **replacement_exit(key, entry)})
            continue
        how = "nonce_consumed" if fate == "consumed" else "dropped"
        lock.resolve_transaction(entry, how)
        summary["resolved_now"].append({**row, "how": how})
    if unbooked:
        raise CapitalLoopRefused("recorded_authorization_settled_unbooked", {
            "record": str(lock.authorizations_path), "authorizations": unbooked,
            "live": live, "transactions": executing})
    if live:
        raise CapitalLoopRefused("recorded_authorization_may_still_settle", {
            "record": str(lock.authorizations_path), "authorizations": live,
            "transactions": executing})
    if executing:
        raise CapitalLoopRefused("recorded_transaction_may_still_execute", {
            "record": str(lock.authorizations_path), "transactions": executing})
    return summary


def cooling_off_check(lock: ReserveLock, *, window_s: int,
                      transport: Transport = http_request, rpc: str = BASE_RPC) -> dict:
    """Refuse while Base shows the reserve spending through anything the record lacks.

    Guarantees a record rolled back with its directory (a backup restored, a migration)
    cannot hide a settlement from the last ``window_s`` seconds before the finalized
    head, nor one since: the reserve's ``AuthorizationUsed`` and ``Transfer`` events are
    scanned from the block at ``final_timestamp - window_s`` up to the latest block, the
    unfinalized ones included (a reorg can only remove what refuses here, so this is
    detection, never resolution). A log whose block cannot be read makes the scan
    unreadable (``cooling_off_unreadable``, retried at the next launch); none is dropped.
    Every authorization used there must name a nonce the record knows, a torn
    fragment's included (``unrecorded_reserve_authorization``), and every USDC
    ``Transfer`` out of the reserve must share its transaction with one of them or be a
    transaction the record holds, a torn one's legible hash included
    (``unrecorded_reserve_transfer``). An authorization rolled back and not yet used is
    not on chain to find: never restoring or copying the lock directory is the defence
    there (the runbook).
    """
    from factorylab.world.evm import event_topic, word_address

    entries = lock.authorizations()
    known = {e["nonce"].lower() for e in entries if e["kind"] in ("authorization",
                                                                   "resolved")
             and isinstance(e.get("nonce"), str)}
    known |= {n.lower() for e in entries if e["kind"] == "torn" for n in e["nonces"]}
    transactions = {e["tx_hash"].lower() for e in entries if e["kind"] == "transaction"}
    transactions |= {h.lower() for e in entries if e["kind"] == "torn"
                     for h in e.get("tx_hashes", ())}
    reserve = "0x" + word_address(lock.reserve_address).hex()
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        view = _finalized(base)
        start = _block_at_or_before(base, view["final_timestamp"] - window_s,
                                    view["final_number"], view["final_timestamp"])
        end = view["latest_number"]
        used, used_to = base.scan(base.chain.usdc, [event_topic(AUTHORIZATION_USED), reserve],
                                  start, end=end)
        moved, moved_to = base.scan(base.chain.usdc, [event_topic(TRANSFER), reserve],
                                    start, end=end)
    except CapitalLoopRefused:
        raise
    except Exception:  # noqa: BLE001 - an unread chain clears nothing
        raise CapitalLoopRefused("cooling_off_unreadable", {"rpc": base.rpc}) from None
    numbers = {"window_s": window_s, "from_block": start, "to_block": end,
               "finalized_block": view["final_number"],
               "authorizations_seen": len(used), "transfers_seen": len(moved)}
    if min(used_to, moved_to) < end:
        raise CapitalLoopRefused("cooling_off_unreadable", numbers)
    unknown = sorted({log["topics"][2].lower() for log in used
                      if log["topics"][2].lower() not in known})
    if unknown:
        raise CapitalLoopRefused("unrecorded_reserve_authorization",
                                 {**numbers, "nonces": unknown})
    explained = {log.get("transactionHash", "").lower() for log in used} | transactions
    stray = sorted({log.get("transactionHash", "").lower() for log in moved
                    if log.get("transactionHash", "").lower() not in explained})
    if stray:
        raise CapitalLoopRefused("unrecorded_reserve_transfer",
                                 {**numbers, "transactions": stray})
    return numbers


def acknowledge(lock: ReserveLock, nonce: str, *, transport: Transport = http_request,
                rpc: str = BASE_RPC, now_s: Callable[[], int] | None = None) -> dict:
    """Mark as settled by hand a recorded authorization finalized Base shows used.

    Guarantees it resolves only an open nonce of the record (a torn fragment's
    included) that the reads (``_authorization_fate``) agree was used, after the same
    finalized-behind-latest check as every launch; anything else refuses
    (``acknowledge_refused``). Only the lock's holder acknowledges, so no run is alive.
    """
    nonce = nonce.lower()
    entry = _open_authorizations(lock.authorizations()).get(nonce)
    if entry is None:
        raise CapitalLoopRefused("acknowledge_refused",
                                 {"nonce": nonce, "why": "not an open nonce of the record"})
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        view = _finalized(base)
        fate = _authorization_fate(base, entry, view, _host_s(now_s))
    except CapitalLoopRefused as exc:
        raise CapitalLoopRefused("acknowledge_refused",
                                 {"nonce": nonce, "why": exc.reason}) from None
    except Exception:  # noqa: BLE001 - an unread chain acknowledges nothing
        raise CapitalLoopRefused("acknowledge_refused",
                                 {"nonce": nonce, "why": "chain unreadable"}) from None
    if fate != "used":
        raise CapitalLoopRefused("acknowledge_refused", {
            "nonce": nonce, "why": f"finalized Base shows it {fate}, not used"})
    lock.resolve(nonce, "acknowledged")
    return {"nonce": nonce, "finalized_block": view["final_number"], "acknowledged": True}


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
        self.authorizations_path = directory / f"{name}.authorizations.jsonl"
        self.reserve_address = reserve_address
        where = {"reserve_address": reserve_address, "lock": str(self.path),
                 "record": str(self.record_path)}
        if not os.path.lexists(self.path) and (
                os.path.lexists(self.record_path)
                or os.path.lexists(self.authorizations_path)):
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
                _create_durably(self.authorizations_path, b"")
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
        _create_durably(self.record_path, self._record(run_dir))

    def authorization_log(self, run_dir: str | Path | None, *,
                          origin: str = "capital_loop",
                          ledger: str | Path | None = None) -> AuthorizationLog:
        """The write-ahead authorization record, written only while this lock is held.

        ``ledger`` is the exact diary a world's entries are booked in."""
        if self.fd is None:
            raise RuntimeError("the reserve lock is not held")
        return AuthorizationLog(self, run_dir, origin, ledger)

    def reset_instructions(self) -> str:
        """The one manual reset, naming its three files and the pointer copy it keeps."""
        return (f"only with no capital-loop run alive on this host and every recorded "
                f"authorization settled and booked, or dead: copy {self.record_path} "
                f"aside (it points at the last run), then remove all three of "
                f"{self.path}, {self.record_path} and {self.authorizations_path}")

    def authorizations(self) -> list[dict]:
        """Every entry of this reserve's write-ahead authorization record.

        A missing record refuses (``capital_loop_authorization_record_missing``): the
        lock file exists, so the record was either removed since it was created with
        the lock, or the lock predates it; either way it may have held an authorization
        no diary shows. The refusal names the exact three-file reset.
        """
        if not self.authorizations_path.exists():
            raise CapitalLoopRefused("capital_loop_authorization_record_missing", {
                "record": str(self.authorizations_path), "lock": str(self.path),
                "reset": self.reset_instructions()})
        return read_authorizations(self.authorizations_path)

    def resolve(self, nonce: str, how: str) -> None:
        """Durably mark one recorded authorization as settled for good (holder only)."""
        if self.fd is None:
            raise RuntimeError("the reserve lock is not held")
        _append_durably(self.authorizations_path,
                        {"kind": "resolved", "nonce": nonce.lower(), "how": how})

    def resolve_transaction(self, entry: dict, how: str) -> None:
        """Durably mark one recorded or torn transaction as settled for good (holder only)."""
        if self.fd is None:
            raise RuntimeError("the reserve lock is not held")
        key = _transaction_key(entry)
        field = "tx_hash" if key.startswith("0x") else "sidecar"
        _append_durably(self.authorizations_path, {"kind": "resolved", field: key, "how": how})

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
    * plus twice how far the finalized block is behind the present: the later of this
      host's clock and the latest block's timestamp, less the finalized block's
      timestamp. The host clock carries Base's lag, any lead of the host over the chain
      (a ``validBefore`` stamped by a fast clock lies that much later in chain time) and
      any staleness of the node that answered; the latest block's timestamp stands in
      when the host clock is the slow one, so a slow host cannot shrink it either. A
      stale ``latest`` cannot shrink it: it only ever raises the maximum. Doubling is the
      allowance for the lag growing during the run (16 minutes measured, 32 allowed);
    * plus one tick for the rail to look.

    Every read is keyless. The latest block must be above the finalized one
    (``finalized_tag_not_behind_latest`` otherwise): a provider that answers the
    ``finalized`` tag with its latest block would make the lag vanish, and the
    authorization reads the rail relies on would lose their meaning with it. A block
    that cannot be read, or a present that is not past the finalized block, is
    unavailable and refuses (``finality_lag_unreadable``): no typed constant stands in
    for Base's own delay. ``run_ns`` is the run's planned length; the bound says nothing
    of a run the admission cap, a failure, a signal or a kill ends early. A run planned
    shorter than ``SETTLEMENT_RATIO`` horizons is refused
    (``capital_loop_duration_below_settlement_bound``).
    """
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        base.check_chain()
        final = base.call("eth_getBlockByNumber", ["finalized", False])
        latest = base.call("eth_getBlockByNumber", ["latest", False])
        final_number, latest_number = int(final["number"], 16), int(latest["number"], 16)
        final_ts, latest_ts = int(final["timestamp"], 16), int(latest["timestamp"], 16)
    except Exception:  # noqa: BLE001 - an unread lag bounds nothing
        raise CapitalLoopRefused("finality_lag_unreadable", {"rpc": base.rpc}) from None
    if latest_number <= final_number:
        raise CapitalLoopRefused("finalized_tag_not_behind_latest", {
            "rpc": base.rpc, "finalized_block": final_number, "latest_block": latest_number})
    host_s = now_s() if now_s is not None else time.time_ns() // 1_000_000_000
    behind = max(host_s, latest_ts) - final_ts
    if behind <= 0:
        raise CapitalLoopRefused("finality_lag_unreadable", {"rpc": base.rpc})
    window = MAX_AUTHORIZATION_S
    horizon_ns = (window + LAG_ALLOWANCE * behind) * 1_000_000_000 + tick_interval_ns
    numbers = {"validity_window_s": window, "finalized_behind_s": behind,
               "finality_lag_allowance": LAG_ALLOWANCE,
               "tick_interval_ns": tick_interval_ns,
               "settlement_horizon_ns": horizon_ns, "settlement_ratio": SETTLEMENT_RATIO,
               "minimum_run_ns": SETTLEMENT_RATIO * horizon_ns, "run_ns": run_ns}
    if run_ns < SETTLEMENT_RATIO * horizon_ns:
        raise CapitalLoopRefused("capital_loop_duration_below_settlement_bound", numbers)
    return numbers


def submitted_top_ups(items: list[dict]) -> list[dict]:
    """Every conversion that reached its top-up step and has no financing booked.

    Guarantees a conversion counts as unresolved until the diary holds its
    ``treasury.financing``, whatever its last status: a top-up still submitted (its
    authorization may settle after the world is dead), one confirmed on the chain whose
    financing was never written (a stop between ``treasury.confirmed`` and
    ``treasury.financing``), and one stranded after its shadow leg paid. Each row names
    the transfer, its last status, its current authorization's nonce and
    ``validBefore`` (None when none was prepared) and the last stall reason journaled.
    """
    last: dict[str, dict] = {}
    stalled: dict[str, Any] = {}
    financed: set = set()
    for item in items:
        kind, state = str(item.get("kind", "")), item.get("state")
        if kind.startswith("treasury.") and isinstance(state, dict) and state.get("id"):
            last[state["id"]] = state
        if kind == "treasury.pending" and item.get("transfer_id"):
            stalled[item["transfer_id"]] = item.get("reason")
        if kind == "treasury.financing" and item.get("transfer_id"):
            financed.add(item["transfer_id"])
    rows = []
    for transfer_id, state in last.items():
        steps, index = list(state.get("steps") or ()), state.get("index")
        if ("venice_top_up" not in steps or type(index) is not int
                or index < steps.index("venice_top_up") or transfer_id in financed):
            continue
        authorization = (state.get("reference") or {}).get("authorization") or {}
        rows.append({"transfer_id": transfer_id, "status": state.get("status"),
                     "nonce": authorization.get("nonce"),
                     "valid_before": authorization.get("validBefore"),
                     "last_reason": (state.get("pending") or {}).get("reason")
                     or stalled.get(transfer_id)})
    return rows


def financed_nonces(items: list[dict]) -> set[str]:
    """The top-up nonces whose debit this diary booked as financing.

    A nonce counts only when a ``treasury.confirmed`` state carries it in its receipts
    and a ``treasury.financing`` names that same transfer.
    """
    financing = {item.get("transfer_id") for item in items
                 if item.get("kind") == "treasury.financing"}
    nonces = set()
    for item in items:
        state = item.get("state")
        if (item.get("kind") == "treasury.confirmed" and isinstance(state, dict)
                and state.get("id") in financing):
            for receipt in state.get("receipts") or ():
                nonce = receipt.get("nonce") if isinstance(receipt, dict) else None
                if isinstance(nonce, str):
                    nonces.add(nonce.lower())
    return nonces
