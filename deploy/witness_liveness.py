#!/usr/bin/env python3
"""Witness the wake's liveness transitions: one line into dormancy, one line out.

Runs after every wake publish (``factorylab-wake.service``, ``ExecStartPost``):

    witness_liveness.py --wake /srv/factorylab/www/wake.json \\
        --state /srv/factorylab/runs/funded.liveness

Reads ``liveness.status`` from the published ``wake.json`` (``alive``,
``dormant`` or ``terminated``, contract C2) and compares it with the status
this helper last witnessed, kept in the small state file. A change into
``dormant`` runs ``witness.sh dormant entered``; a change from ``dormant`` back
to ``alive`` runs ``witness.sh dormant exited``. Nothing else is witnessed
here: ``launch`` and ``failed_resume`` belong to ``start.sh``, and ``kill`` is
written by the runtime itself from inside ``Termination.kill``
(``factorylab/runtime/witness.py``) with ``start.sh`` and the kill runbook adding
the outside view, so a world that is killed while dormant gets its ``kill`` line
from them and no ``exited`` line from us.

Idempotent across republishes: the state file is rewritten only after the
witness line was appended, so an hour with no change emits nothing and a
failed append is retried on the next publish. An unreadable wake, or a status
outside the closed vocabulary (the wake writes ``unavailable`` when its
verification fails), changes nothing. Nothing here reads the ledger or a key.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

STATUSES = ("alive", "dormant", "terminated")
WITNESS_SH = Path(__file__).resolve().with_name("witness.sh")


def read_status(wake: Path) -> str | None:
    """The wake's published liveness status, or None when it cannot be read."""
    try:
        data = json.loads(wake.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    liveness = data.get("liveness") if isinstance(data, dict) else None
    status = liveness.get("status") if isinstance(liveness, dict) else None
    return status if status in STATUSES else None


def read_state(state: Path) -> str:
    """The status last witnessed; a world with no record is taken to have been alive."""
    try:
        recorded = state.read_text(encoding="utf-8").strip()
    except OSError:
        return "alive"
    return recorded if recorded in STATUSES else "alive"


def write_state(state: Path, status: str) -> None:
    """Replace the record atomically, mode 0600, beside the ledger it describes."""
    state.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{state.name}-", dir=state.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(status + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def transition(previous: str, current: str) -> str | None:
    """The witness reason for a change, or None when nothing is to be witnessed."""
    if previous == current:
        return None
    if current == "dormant":
        return "entered"
    if previous == "dormant" and current == "alive":
        return "exited"
    return None


def witness(script: Path, reason: str) -> bool:
    """Run ``witness.sh dormant <reason>``; True when it appended its line."""
    completed = subprocess.run(["bash", str(script), "dormant", reason],
                               capture_output=True, timeout=120, check=False)
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wake", required=True, type=Path, help="the published wake.json")
    parser.add_argument("--state", required=True, type=Path,
                        help="file remembering the last witnessed status (under runs/)")
    parser.add_argument("--witness", type=Path, default=WITNESS_SH,
                        help="witness script (default: deploy/witness.sh beside this file)")
    args = parser.parse_args(argv)
    current = read_status(args.wake)
    if current is None:
        return 0
    previous = read_state(args.state)
    reason = transition(previous, current)
    if reason is not None and not witness(args.witness, reason):
        return 1  # the record stays behind so the next publish tries again
    if previous != current:
        write_state(args.state, current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
