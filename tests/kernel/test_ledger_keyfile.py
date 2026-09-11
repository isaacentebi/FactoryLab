import os
import stat

import pytest
from cryptography.fernet import Fernet

from factorylab.kernel.ledger import Ledger


def test_key_file_is_written_0600_and_decrypts_after_the_fact(tmp_path):
    ledger_path = tmp_path / "w.jsonl"
    key_path = tmp_path / "w.key"
    ledger = Ledger(ledger_path, key_path=key_path, clock_ns=lambda: 1)
    ledger.append({"kind": "one"})
    mode = stat.S_IMODE(os.stat(key_path).st_mode)
    assert mode == 0o600
    key = key_path.read_bytes()
    # the persisted key decrypts the persisted items (post-mortem path)
    lines = ledger_path.read_bytes().splitlines()
    import json

    token = json.loads(lines[1])["item"].encode()
    item = json.loads(Fernet(key).decrypt(token))
    assert item["kind"] == "one"
    # the kernel still seals the key from callers while the world is alive
    with pytest.raises(PermissionError):
        _ = ledger.key_store.key


def test_key_file_is_never_reused(tmp_path):
    key_path = tmp_path / "w.key"
    Ledger(tmp_path / "a.jsonl", key_path=key_path, clock_ns=lambda: 1)
    with pytest.raises(FileExistsError):
        Ledger(tmp_path / "b.jsonl", key_path=key_path, clock_ns=lambda: 1)
