import json

import pytest

from factorylab.kernel.ledger import Ledger


def test_unchanged_prefix_reuses_authentication_but_new_tokens_do_not(monkeypatch):
    ledger = Ledger(clock_ns=lambda: 0)
    for i in range(8):
        ledger.append({"kind": "sample", "value": i})
    cipher = ledger._Ledger__keys
    original = cipher._decrypt
    calls = []
    monkeypatch.setattr(cipher, "_decrypt", lambda token: calls.append(token) or original(token))
    assert ledger.verify() and len(calls) == 8
    assert ledger.verify() and len(calls) == 8
    ledger.append({"kind": "sample", "value": 8})
    assert ledger.verify() and len(calls) == 9


@pytest.mark.parametrize("mutation", ["change", "reorder", "truncate"])
def test_a_cached_prefix_never_hides_changed_memory_ciphertexts(mutation):
    ledger = Ledger(clock_ns=lambda: 0)
    for i in range(3):
        ledger.append({"kind": "sample", "value": i})
    assert ledger.verify()
    tokens = ledger._Ledger__tokens
    if mutation == "change":
        tokens[0] = tokens[0][:-1] + b"!"
    elif mutation == "reorder":
        tokens[0], tokens[1] = tokens[1], tokens[0]
    else:
        tokens.pop()
    assert ledger.verify() is False


@pytest.mark.parametrize("header", [False, True])
def test_a_cached_prefix_never_hides_changed_disk_bytes_or_header(tmp_path, header):
    path = tmp_path / "world.jsonl"
    ledger = Ledger(path, clock_ns=lambda: 0)
    ledger.append({"kind": "sample"})
    assert ledger.verify()
    lines = path.read_text().splitlines()
    row = json.loads(lines[0 if header else 1])
    if header:
        row["genesis_hash"] = "0" * 64
    else:
        row["item"] = row["item"][:-1] + "!"
    lines[0 if header else 1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")
    assert ledger.verify() is False
