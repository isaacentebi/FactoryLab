"""B9: a trusted head never permits modified bytes and avoids decrypting the diary prefix."""

import json
from pathlib import Path

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError


def checkpointed(path):
    ledger = Ledger(path, manifest={}, key_path=str(path) + ".key", clock_ns=lambda: 0)
    for number in range(100):
        ledger.append({"kind": "old-private-record", "data": "x" * 1000, "number": number})
    ledger.append({"kind": "snapshot", "state": {"test": True}})
    ledger.append({"kind": "tail", "value": 1})
    return ledger


def test_head_never_hides_modified_prefix_bytes(tmp_path):
    path = tmp_path / "stream.jsonl"
    checkpointed(path)
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    line = json.loads(lines[1])
    token = line["item"]
    line["item"] = token[:25] + ("A" if token[25] != "A" else "B") + token[26:]
    lines[1] = json.dumps(line, separators=(",", ":")).encode() + b"\n"
    damaged = b"".join(lines)
    path.write_bytes(damaged)
    with pytest.raises(LedgerIntegrityError):
        Ledger.reopen(path, manifest={})
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_damaged_optional_head_falls_back_to_streamed_verification(tmp_path, missing):
    path = tmp_path / "stream.jsonl"
    checkpointed(path)
    head = Path(str(path) + ".head")
    if missing:
        head.unlink()
    else:
        head.write_bytes(b"broken cache")
    restored = Ledger.reopen(path, manifest={})
    snapshot, tail = restored._recovery_tail()
    assert snapshot and len(list(tail)) == 1 and restored.verify()
