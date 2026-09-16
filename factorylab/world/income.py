"""The seller's receipt spool: paid calls the wake host served on the runtime's behalf.

A paid call is money the wallet's pots received, so it is written down before
the program runs. The wake host's server appends one receipt per settled call
here; the runtime books each as ``income.earned`` through ``Treasury.collect_income``,
since only the runtime may append to the ledger. This module knows nothing of
the factory: it is a file format the world writes and the treasury reads.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def _receipt(value: Any) -> dict | None:
    """A spool line is a receipt only with a service, a positive amount and a reference."""
    if not isinstance(value, dict):
        return None
    service, micro, tx = value.get("service"), value.get("micro"), value.get("tx")
    if (not isinstance(service, str) or not service or type(micro) is not int or micro <= 0
            or not isinstance(tx, str) or not tx):
        return None
    # The payment's identity travels with it: chain, log index, asset and
    # recipient, alongside the service it paid for. A receipt that names only a
    # transaction hash is not an identity the chain can be asked about.
    return {k: value.get(k) for k in ("service", "micro", "tx", "payer", "program",
                                      "version", "ts", "chain", "log_index", "asset",
                                      "recipient")}


def read_income_spool(path: str, offset: int) -> dict:
    """Return the complete receipt lines after ``offset`` and the new offset.

    Only newline-terminated lines are read, so a line the server is still writing
    waits for the next tick. A spool shorter than the offset has been replaced;
    nothing is read from it, because re-reading would book receipts twice.
    """
    if type(offset) is not int or offset < 0:
        raise ValueError("spool offset must be a nonnegative integer")
    spool = Path(path)
    try:
        size = spool.stat().st_size
    except OSError:
        return {"offset": offset, "receipts": []}
    if size < offset:
        return {"offset": offset, "receipts": []}
    receipts = []
    with spool.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            try:
                receipt = _receipt(json.loads(line))
            except ValueError:
                receipt = None
            if receipt is not None:
                receipts.append(receipt)
    return {"offset": offset, "receipts": receipts}


class IncomeSpool:
    """Append-only receipts for paid calls the server served on the runtime's behalf."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, receipt: dict) -> dict:
        if _receipt(receipt) is None:
            raise ValueError("invalid receipt")
        line = json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n"
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        return receipt
