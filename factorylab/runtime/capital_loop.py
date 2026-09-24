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
import tempfile
from pathlib import Path
from typing import Any

from factorylab.world.evm import BASE, EVM
from factorylab.world.x402 import (
    BASE_RPC,
    MAX_AUTHORIZATION_S,
    TOP_UP_MICRO,
    VENICE_URL,
    Transport,
    http_request,
    parse_quote,
    usdc_balance,
)

#: How many times faster than the run one conversion must be able to settle
#: (AGENTS.md rule 12, essay II, IV.c: an inner loop settles at least 3x faster than
#: the outer loop that commands it). The run commands the conversion; a run shorter
#: than three settlement horizons leaves most of its conversions settling after it.
SETTLEMENT_RATIO = 3
#: The next step an operator takes when a run ends with a top-up still submitted.
OUTSTANDING_SCRIPT = "scripts/capital_loop_outstanding.py"


class CapitalLoopRefused(RuntimeError):
    """A launch or read refused with a stable reason code and the numbers behind it."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail or {}


def read_items(run_dir: str | Path) -> list[dict]:
    """Every diary item of a run, decrypted with the run's own ledger key file.

    Guarantees no item is silently dropped. Every line but the last must be a complete
    sealed record that this run's key decrypts, and the records must form one unbroken
    chain from the genesis header (each names its sequence number and its predecessor's
    hash), or the read is refused (``run_ledger_unreadable``, naming the line). The only
    line ever skipped is a torn last one, with no newline: the process died mid-append,
    and an item that never finished writing never reached the rail, since the treasury
    journals a step before it acts on it. A run folder carrying another run's key (a copy
    or a restore), a corrupted, removed or reordered middle line, or a complete last line
    that does not decrypt could each hide a ``step_submitted`` authorization that is
    still live, so each refuses rather than reading as "nothing outstanding".
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

    items = []
    with path.open("rb") as stream:
        header = stream.readline()
        if not header.endswith(b"\n"):
            return []  # torn while the diary was being created: nothing was journaled
        try:
            previous = json.loads(header)["genesis_hash"]
        except (ValueError, KeyError, TypeError):
            raise unreadable(1) from None
        for seq, line in enumerate(stream):
            if not line.endswith(b"\n"):
                break  # only the last line can lack its newline: the torn final append
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
                raise unreadable(seq + 2) from None  # 1-based, counting the header
            previous = item["hash"]
            items.append(item)
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


def outstanding(run_dir: str | Path, *, reserve_address: str, base: EVM) -> dict[str, Any]:
    """Each journaled top-up's on-chain standing, and the run's pending shadow sends."""
    from factorylab.world.treasury_rails import authorization_status

    top_ups, shadows = journaled_references(read_items(run_dir))
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
                 transport: Transport = http_request, rpc: str = BASE_RPC) -> dict:
    """Refuse a capital-loop launch the chain says could overspend; return the numbers.

    The floor is only a bound across runs if it is close to the balance: the reserve
    may lose at most ``max_venice_total_usd`` before reaching it, so
    ``balance - floor <= max_venice_total_usd`` is required, read keylessly now. And
    no earlier run's authorization may still be able to settle, since a crashed
    world's last authorization stays valid on chain for up to its quote's timeout.
    Each earlier run is read once, however many ways it was named.
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
    for run_dir in previous_runs:
        if Path(run_dir).resolve() in seen:
            continue
        seen.add(Path(run_dir).resolve())
        report = outstanding(run_dir, reserve_address=reserve, base=base)
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
    and nothing else: it lives under the home directory, never under a run or a repo.
    """
    return Path.home() / ".factorylab" / "capital-loop"


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
    """

    def __init__(self, reserve_address: str, *, lock_dir: str | Path | None = None):
        from factorylab.world.x402 import _address

        self.fd = None
        name = _address(reserve_address).lower()
        directory = Path(lock_dir) if lock_dir is not None else default_lock_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / f"{name}.lock"
        self.record_path = directory / f"{name}.last-run.json"
        self.reserve_address = reserve_address
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.set_inheritable(fd, False)  # explicit, and a no-op where O_CLOEXEC held
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise CapitalLoopRefused("capital_loop_reserve_locked", {
                "reserve_address": reserve_address, "lock": str(self.path)}) from None
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def last_run(self) -> Path | None:
        """The run that last held this reserve, or None when none was ever recorded.

        A record that cannot be read refuses (``capital_loop_last_run_unreadable``):
        it may name a run whose authorization is still live.
        """
        if not self.record_path.exists():
            return None
        try:
            record = json.loads(self.record_path.read_text())
            if record["reserve_address"].lower() != self.reserve_address.lower():
                raise ValueError
            return Path(record["run_dir"])
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            raise CapitalLoopRefused("capital_loop_last_run_unreadable",
                                     {"record": str(self.record_path)}) from None

    def record_run(self, run_dir: str | Path) -> None:
        """Durably name ``run_dir`` as this reserve's last holder, atomically.

        Guarantees the record on disk is the previous one or this one, never a torn mix,
        and that this one is on disk before it returns. Only the holder may record.
        """
        if self.fd is None:
            raise RuntimeError("the reserve lock is not held")
        data = json.dumps({"reserve_address": self.reserve_address,
                           "run_dir": str(Path(run_dir).resolve())}).encode()
        fd, temporary = tempfile.mkstemp(prefix=".last-run-", dir=self.record_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.record_path)
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
                     venice_url: str = VENICE_URL) -> dict:
    """Refuse a run shorter than ``SETTLEMENT_RATIO`` conversion settlement horizons.

    A top-up authorization settles, or provably dies, only once a finalized Base block
    is past its debit or past its ``validBefore``, and the rail sees that at its next
    tick. So one conversion's horizon is its validity window (the quote's
    ``maxTimeoutSeconds``, capped at ``MAX_AUTHORIZATION_S`` exactly as the signer caps
    it) plus Base's finality lag plus one tick. The window is read from Venice's unpaid
    quote (a POST with no payment and no credential, which signs and moves nothing);
    when that quote cannot be read, the cap stands in, which can only lengthen the
    bound. The lag is read keylessly now, the latest block's timestamp less the
    finalized block's; unreadable, the launch is refused (``finality_lag_unreadable``),
    since no typed constant stands in for Base's own delay. A run shorter than
    ``SETTLEMENT_RATIO`` horizons is refused
    (``capital_loop_duration_below_settlement_bound``).
    """
    window, source = MAX_AUTHORIZATION_S, "cap"
    try:
        quote = parse_quote(transport("POST", venice_url.rstrip("/") + "/x402/top-up", {}, {}),
                            amount_micro=TOP_UP_MICRO)
        window, source = min(quote.accepted["maxTimeoutSeconds"], MAX_AUTHORIZATION_S), "quote"
    except Exception:  # noqa: BLE001 - the cap bounds every window this adapter signs
        pass
    base = keyless_base(transport=transport, rpc=rpc)
    try:
        base.check_chain()
        latest = base.call("eth_getBlockByNumber", ["latest", False])
        final = base.call("eth_getBlockByNumber", ["finalized", False])
        lag = int(latest["timestamp"], 16) - int(final["timestamp"], 16)
        if lag < 0:
            raise ValueError
    except Exception:  # noqa: BLE001 - an unread lag bounds nothing
        raise CapitalLoopRefused("finality_lag_unreadable", {"rpc": base.rpc}) from None
    horizon_ns = (window + lag) * 1_000_000_000 + tick_interval_ns
    numbers = {"validity_window_s": window, "validity_source": source,
               "finality_lag_s": lag, "tick_interval_ns": tick_interval_ns,
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
