import json

import pytest
from cryptography.fernet import Fernet

from factorylab.kernel.ledger import Ledger
from factorylab.versioning.reader import DiaryError, read_diary


def _ledger(tmp_path):
    path, key = tmp_path / "diary.jsonl", tmp_path / "diary.key"
    ledger = Ledger(path, key_path=key, clock_ns=lambda: 123, manifest={"name": "test"})
    ledger.append({"kind": "test", "unicode": "λ · café", "values": [1, 2.5, None]})
    ledger.append({"kind": "test", "amount": 2_000_001})
    return path, key, ledger


def test_roundtrip(tmp_path):
    path, key, ledger = _ledger(tmp_path)
    result = read_diary(path, key)
    assert ledger.verify()
    assert len(result) == 2 and [item["seq"] for item in result] == [0, 1]
    assert result[0]["unicode"] == "λ · café"
    assert result[1]["amount"] == 2_000_001 and result[1]["ts"] == 123
    assert result[0]["hash"] == result[1]["prev_hash"]


@pytest.mark.parametrize(
    "change",
    [
        "token",
        "seq",
        "prev_hash",
        "hash",
        "value",
        "reorder",
        "header",
        "partial",
        "record",
        "duplicate",
        "missing",
    ],
)
def test_tampering_is_rejected(tmp_path, change):
    path, key, _ = _ledger(tmp_path)
    lines = path.read_bytes().splitlines(keepends=True)
    if change in ("seq", "prev_hash", "hash", "value"):
        cipher = Fernet(key.read_bytes())
        item = json.loads(cipher.decrypt(json.loads(lines[1])["item"].encode()))
        item[{"value": "unicode"}.get(change, change)] = "tampered"
        lines[1] = json.dumps({"item": cipher.encrypt(json.dumps(item).encode()).decode()}).encode()
        lines[1] += b"\n"
    elif change == "token":
        lines[1] = b'{"item":"invalid"}\n'
    elif change == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    elif change == "header":
        lines[0] = json.dumps({"format": 1, "genesis_hash": "0" * 64}).encode() + b"\n"
    elif change == "partial":
        lines[-1] = lines[-1][:-1]
    elif change == "record":
        lines[1] = b"[]\n"
    elif change == "duplicate":
        lines[1] = lines[1].replace(b'{"item":', b'{"item":"unused","item":')
    elif change == "missing":
        lines.pop(1)
    path.write_bytes(b"".join(lines))
    with pytest.raises(DiaryError):
        read_diary(path, key)


def test_empty_ledger_and_bad_key(tmp_path):
    path, key = tmp_path / "empty", tmp_path / "key"
    Ledger(path, key_path=key)
    assert read_diary(path, key) == []
    key.write_bytes(b"not-a-key")
    with pytest.raises(DiaryError):
        read_diary(path, key)


def test_wrong_key_and_missing_file(tmp_path):
    path, key, _ = _ledger(tmp_path)
    key.write_bytes(Fernet.generate_key())
    with pytest.raises(DiaryError):
        read_diary(path, key)
    with pytest.raises(DiaryError):
        read_diary(tmp_path / "missing", key)
