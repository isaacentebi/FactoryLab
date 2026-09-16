"""The death record outside the diary: a kill is written where a copy of the diary is not.

A killed world's own ledger says it is final, and ``Ledger.reopen`` refuses that
file. An earlier copy of the same diary (a backup restored beside the original,
a ``cp -r`` taken before the kill) says nothing of the kind: its hash chain is
valid, its key opens it, its release digest matches, and nothing in it knows
that a later terminal state occurred (cold audit F1, "backup restoration").
This module is the record that copy cannot carry.

``record_kill`` runs inside ``Termination.kill`` for every kill path (the
operator's ``factorylab kill``, the world's own death by budget or balance, and
the end-of-budget kill) and does three things, none of which may raise into the
kill: it remembers the killed identity for the life of this process, it appends
one JSON line to the local witness file, and, when ``FACTORYLAB_WITNESS_URL``
is set, POSTs the same line. ``killed`` is what resume asks before it restores
anything: the process record, the local file, and (when the URL is set) the
remote receiver. With a receiver configured, the receiver's verdict is part of
the evidence: a receiver that cannot be reached, or answers without a verdict,
makes ``killed`` raise ``WitnessUnavailable`` and resume refuses rather than
proceeding on the local file alone (second reading, P1-01). Without a receiver
the local file decides; that is the weaker guarantee and ``deploy/README.md``
says so.

The local file lives in a ``.witness`` directory that is a *sibling of the
diary's directory*, named from the ledger path: ``runs/funded.jsonl`` is
witnessed in ``.witness/funded.jsonl`` beside ``runs/``. Copying or restoring
the diary directory therefore never carries the witness with it, and every copy
of the diary, wherever it sits under the same parent, resolves to the same
witness. The remote receiver is the guarantee that survives a lost host or an
operator who deletes the local file; the local file is the guarantee that costs
nothing.

Identity is the launch nonce and, when both sides know it, the diary fingerprint
(``Ledger.diary_id``, the hash of the diary's first sealed record). A live world
draws a random nonce, so the nonce alone names it; a deterministic world derives
its nonce from the manifest and the seed, so two independent rehearsals of one
manifest share a nonce by design and are told apart by their diaries.

Standard library only. Nothing here reads a key file, and the receiver's URL is
never printed or logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorylab.kernel import termination as _termination

WITNESS_DIR = ".witness"
URL_ENV = "FACTORYLAB_WITNESS_URL"
POST_TIMEOUT = 10.0  # seconds; the kill line is sent once
QUERY_TIMEOUT = 5.0  # seconds; resume waits this long for the remote's verdict
KILL = "kill"
QUERY = "query"
#: The line a kill writes before it touches a venue, and the one it writes after the
#: wind-down executor has finished. Both are ``kill`` lines: production is dead at both.
PRODUCTION = "production_kill"
#: ``production_state`` in a kill line is always this: the line exists because the
#: population is dead (``runtime/winddown.py`` holds the same word).
KILLED = "killed"
#: The four values ``exposure_state`` may take (``runtime/winddown.py``). Repeated here
#: because this module imports nothing from the runtime it witnesses.
EXPOSURE_STATES = ("flat", "dust_within_precommitted_bound", "wind_down_pending", "unknown")
_REASON = re.compile(r"^[a-z_:]{1,40}$")
_WORLD = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

log = logging.getLogger("factorylab.witness")


class WitnessUnavailable(RuntimeError):
    """A receiver is configured and gave no verdict: unreachable, unusable or silent.

    Raised only by ``killed`` when asked to consult the remote. Resume treats it
    as a refusal (``witness_unavailable``) so an earlier copy of a diary is never
    revived while the one record that could name its death is out of reach.
    """

#: Identities killed in this process: ``(launch_nonce, diary_id)``. A checkpoint
#: restored into a fresh runtime in the same process cannot revive one of these.
_killed_here: set[tuple[str, str | None]] = set()


def witness_path(ledger_path: str | os.PathLike[str]) -> Path:
    """The witness file for a diary: ``<parent of the diary's directory>/.witness/<stem>.jsonl``."""
    ledger = Path(os.path.abspath(ledger_path))
    return ledger.parent.parent / WITNESS_DIR / f"{ledger.stem}.jsonl"


def identity_path(ledger_path: str | os.PathLike[str], world: str | None,
                  launch_nonce: str | None) -> Path | None:
    """The witness file keyed by launch identity: ``.witness/<world>-<nonce[:16]>.jsonl``.

    The file named from the diary's stem moves when the diary is renamed; this one
    does not, because nothing in its name comes from the diary's filename (R3-C).
    A world with no launch nonce has no identity to key on and keeps the stem file
    alone. Both files live in the same ``.witness`` directory, so a copy of the
    diary's directory still carries neither.
    """
    if not launch_nonce:
        return None
    name = world if isinstance(world, str) and _WORLD.match(world) else "unknown"
    ledger = Path(os.path.abspath(ledger_path))
    return ledger.parent.parent / WITNESS_DIR / f"{name}-{launch_nonce[:16]}.jsonl"


def receiver_identity() -> str | None:
    """A stable name for the configured receiver, or None: the URL is never disclosed.

    The hash goes into ``Launch`` and every checkpoint, so a resume can tell "the
    receiver this world launched under" from "some other receiver" without the
    address itself ever reaching the diary, the wake or a log line.
    """
    url = os.environ.get(URL_ENV, "").strip()
    if not url:
        return None
    return hashlib.sha256(url.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _configured() -> bool:
    """Whether the operator named a receiver at all, usable or not."""
    return bool(os.environ.get(URL_ENV, "").strip())


def _receiver_url() -> str | None:
    """The configured receiver, or None: HTTPS anywhere, plain HTTP only to the loopback."""
    url = os.environ.get(URL_ENV, "").strip()
    if not url or any(c in url for c in '"\\\n\r'):
        return None
    if url.startswith("https://"):
        return url
    if re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url):
        return url
    return None


def _matches(line: dict, launch_nonce: str, diary: str | None) -> bool:
    """A kill line names this identity: same nonce, and the same diary when both know it."""
    if line.get("event") != KILL or line.get("launch_nonce") != launch_nonce:
        return False
    recorded = line.get("diary")
    return recorded is None or diary is None or recorded == diary


def _local_lines(path: Path):
    try:
        with path.open("rb") as stream:
            for raw in stream:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(line, dict):
                    yield line
    except OSError:
        return


def _append(path: Path, line: dict) -> bool:
    """Append one line, creating the directory (0700) and file (0600) as needed."""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            payload = json.dumps(line, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            remaining = memoryview(payload)
            while remaining:
                remaining = remaining[os.write(descriptor, remaining):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True
    except OSError:
        log.warning("witness: local append failed")
        return False


def _post(url: str, line: dict, *, timeout: float) -> dict | None:
    """POST one line; the parsed JSON object the receiver answers with, or None."""
    body = json.dumps(line, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = response.read(65536)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        parsed = json.loads(answer) if answer.strip() else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


#: What the kill about to happen owed the venue (edition 3, C5, R3-C): whether the
#: manifest precommitted a wind-down, how many orders it sent, how many operations the
#: executor accounted for, what the account said afterwards and how many records the
#: diary refused while it ran. Set by the kill path (``runtime/venue.py``,
#: ``runtime/cli.py``) and read once by ``record_kill``. A kill that never set it is
#: witnessed as ``wind_down: false`` with no orders and an unknown exposure state,
#: which is the truth about every world before the contract existed.
_pending_wind_down: dict[str, Any] = {"wind_down": False, "orders": 0, "operations": 0,
                                      "exposure_state": "unknown", "ledger_failures": 0}


def note_wind_down(*, wind_down: bool, orders: int, exposure_state: str = "unknown",
                   operations: int | None = None, ledger_failures: int = 0) -> None:
    """Record what the next kill line should say about the venue. Never raises."""
    _pending_wind_down["wind_down"] = bool(wind_down)
    _pending_wind_down["orders"] = int(orders) if type(orders) is int else 0
    _pending_wind_down["operations"] = (_pending_wind_down["orders"]
                                        if type(operations) is not int else operations)
    _pending_wind_down["exposure_state"] = (exposure_state
                                            if exposure_state in EXPOSURE_STATES else "unknown")
    _pending_wind_down["ledger_failures"] = (int(ledger_failures)
                                             if type(ledger_failures) is int else 0)


def kill_line(*, world: str | None, launch_nonce: str | None, release_digest: str | None,
              ledger_head: str | None, diary: str | None, reason: str | None,
              wind_down: bool = False, wind_down_orders: int = 0, event: str = KILL,
              exposure_state: str = "unknown", operations: int = 0,
              ledger_failures: int = 0) -> dict:
    """The one line a kill writes: the identity, the release, the diary prefix and the reason.

    Two states, never one (R3-C). ``production_state`` is always ``killed`` here:
    this line exists because the world is dead. ``exposure_state`` is what the venue
    still held when the line was written, and it is honest about not knowing.
    """
    line = {
        "world": world if isinstance(world, str) and _WORLD.match(world) else "unknown",
        "event": event, "ts": _now(),
        "release_digest": release_digest or "unavailable",
        "ledger_head": ledger_head or "absent",
        "launch_nonce": launch_nonce,
        "reason": reason if isinstance(reason, str) and _REASON.match(reason) else "none",
        # The kill contract, outside the diary: what this world owed the venue and how
        # many orders it actually sent. A reader of a restored copy learns both.
        "wind_down": bool(wind_down),
        "wind_down_orders": int(wind_down_orders) if type(wind_down_orders) is int else 0,
        # The two states R3-C keeps apart, and what the executor accounted for.
        "production_state": KILLED,
        "exposure_state": exposure_state if exposure_state in EXPOSURE_STATES else "unknown",
        "wind_down_operations": int(operations) if type(operations) is int else 0,
        "wind_down_ledger_failures": (int(ledger_failures)
                                      if type(ledger_failures) is int else 0),
    }
    if diary is not None:
        line["diary"] = diary
    return line


def _write(ledger, reason: str, *, stage: str, clear: bool) -> dict | None:
    """Write one kill line for ``ledger``: process memory, both local files, the receiver.

    ``stage`` is ``production_kill`` for the line written before the wind-down
    executor runs and ``kill`` for the one written with the terminal event. Both
    say the world is dead; only the second can know what the venue was left
    holding. Never raises.
    """
    try:
        identity = ledger.identity()
        nonce, diary = identity.get("launch_nonce"), ledger.diary_id
        if nonce is not None:
            _killed_here.add((nonce, diary))
        path = ledger.path
        if path is None:
            return None
        line = kill_line(world=identity.get("world"), launch_nonce=nonce,
                         release_digest=identity.get("release_digest"),
                         ledger_head=ledger.byte_hash(), diary=diary, reason=reason,
                         wind_down=_pending_wind_down["wind_down"],
                         wind_down_orders=_pending_wind_down["orders"],
                         exposure_state=_pending_wind_down["exposure_state"],
                         operations=_pending_wind_down["operations"],
                         ledger_failures=_pending_wind_down["ledger_failures"])
        line["stage"] = stage
        if clear:
            note_wind_down(wind_down=False, orders=0)  # one note belongs to one kill
        # Append first: the local files are the record; the receiver holds a copy.
        # The identity-keyed file is the one a renamed diary still resolves to.
        _append(witness_path(path), line)
        keyed = identity_path(path, identity.get("world"), nonce)
        if keyed is not None:
            _append(keyed, line)
        url = _receiver_url()
        if url is not None and _post(url, line, timeout=POST_TIMEOUT) is None:
            log.warning("witness: the receiver did not take the kill line")
        return line
    except Exception:  # noqa: BLE001 - nothing may raise into a kill
        log.warning("witness: the kill could not be witnessed")
        return None


def record_kill(ledger, reason: str) -> dict | None:
    """Witness a kill for ``ledger`` in the process, the local files and the receiver.

    Called by ``Termination.kill`` after the terminal event is in the diary. Never
    raises: the kill is already final on the object and in the ledger, and a
    witness that cannot be written is logged, not fatal. Returns the line that
    was written, or None when nothing could be (no identity, or a memory-only
    ledger, which has no place outside itself to be witnessed).
    """
    return _write(ledger, reason, stage=KILL, clear=True)


def record_production_kill(ledger, reason: str) -> dict | None:
    """Witness that production is dead, before the wind-down executor touches a venue.

    The kill path writes this only when the manifest precommitted a wind-down,
    because only then is there a window between the death of the population and
    the terminal event. If the process dies inside that window the diary has no
    ``Terminated`` event, but this line is already outside it: the identity is
    recorded as killed, no copy of that diary resumes, and the next kill
    reconciles the wind-down by operation id and seals it.
    """
    return _write(ledger, reason, stage=PRODUCTION, clear=False)


def killed(*, world: str | None, launch_nonce: str | None, diary: str | None,
           ledger_path: str | os.PathLike[str] | None, remote: bool = True) -> str | None:
    """Where, if anywhere, this identity is recorded as killed: process, local or remote.

    ``None`` is not proof of life: it says only that no record was found where
    this process could look. With ``remote`` and a receiver configured, the
    receiver's answer is required: ``{"killed": true}`` is final, ``{"killed":
    false}`` clears it, and anything else (unreachable, an unusable URL, an
    answer without a verdict) raises ``WitnessUnavailable`` rather than letting
    the local file stand in for the record the receiver was configured to keep.
    """
    if launch_nonce is None:
        return None
    if any(nonce == launch_nonce and (d is None or diary is None or d == diary)
           for nonce, d in _killed_here):
        return "process"
    if ledger_path is not None:
        # The identity-keyed file first: it is the one that survives a renamed
        # diary. The file named from the diary's stem is read as well, so every
        # world witnessed before identity keying is still found.
        keyed = identity_path(ledger_path, world, launch_nonce)
        for path in ([] if keyed is None else [keyed]) + [witness_path(ledger_path)]:
            for line in _local_lines(path):
                if _matches(line, launch_nonce, diary):
                    return "local"
    if not remote or not _configured():
        return None
    url = _receiver_url()
    if url is None:
        log.warning("witness: the configured receiver URL is not usable; no verdict")
        raise WitnessUnavailable("the configured witness receiver URL is not usable")
    query = {"world": world if isinstance(world, str) and _WORLD.match(world) else "unknown",
             "event": QUERY, "ts": _now(), "launch_nonce": launch_nonce}
    if diary is not None:
        query["diary"] = diary
    answer = _post(url, query, timeout=QUERY_TIMEOUT)
    if answer is None:
        log.warning("witness: the receiver was unreachable; no verdict")
        raise WitnessUnavailable("the witness receiver was unreachable")
    if answer.get("killed") is True:
        return "remote"
    if answer.get("killed") is False:
        return None
    log.warning("witness: the receiver gave no verdict")
    raise WitnessUnavailable("the witness receiver gave no verdict")


# Installed when the runtime package loads (``factorylab/runtime/__init__.py``), so
# every kill path that runs under the runtime, the CLI's included, is witnessed.
# The kernel names no upper layer; it calls whatever is bound here.
_termination.bind_witness(record_kill)
