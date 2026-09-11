import hashlib
import json
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet

from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import KeyStore, Ledger, LedgerIntegrityError
from factorylab.kernel.termination import Termination
from factorylab.kernel.wallet import Wallet


def test_invariant_9_live_items_and_key_are_sealed(ledger):
    ledger.append({"kind": "secret", "private": "hidden"})
    for operation in (
        lambda: ledger.key_store.key,
        lambda: ledger.decrypt_item(0),
        lambda: ledger.key_store._release(object()),
        lambda: ledger.key_store._bind(object()),
    ):
        with pytest.raises(PermissionError):
            operation()
    termination = Termination(ledger=ledger, bus=Bus(ledger))
    with pytest.raises(PermissionError):
        ledger.key_store._release(termination)
    assert not ledger.seal_key_released()


def test_canonical_hash_chain_and_detached_input(clock):
    first, second = Ledger(clock_ns=clock), Ledger(clock_ns=clock)
    entry = {"kind": "x", "nested": {"b": 2, "a": 1}, "label": "λ"}
    assert first.append(entry) == second.append(dict(reversed(list(entry.items())))) == 0
    entry["nested"]["a"] = 999
    for ledger in (first, second):
        Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock).kill("audit")
    item = first.decrypt_item(0)
    assert item == second.decrypt_item(0)
    digest = item.pop("hash")
    serialized = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(serialized.encode()).hexdigest() == digest
    assert first.verify()


def test_encrypted_persistence_and_posttermination_decryption(tmp_path, clock):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, manifest={"world": "scripted"}, clock_ns=clock)
    ledger.append({"kind": "secret", "payload": "needle-in-ledger"})
    before = path.read_bytes()
    assert b"needle-in-ledger" not in before and b'"secret"' not in before
    header, encrypted = (json.loads(line) for line in before.splitlines())
    assert set(header) == {"format", "genesis_hash"}
    assert set(encrypted) == {"item"}
    assert path.stat().st_mode & 0o777 == 0o600
    term = Termination(ledger=ledger, bus=Bus(ledger), clock_ns=clock)
    term.kill("audit")
    assert path.read_bytes().startswith(before)
    item = json.loads(Fernet(term.seal_key).decrypt(encrypted["item"].encode()))
    assert item["payload"] == "needle-in-ledger"
    assert ledger.verify() and ledger.seal_key_released()
    with pytest.raises(FileExistsError):
        Ledger(path)


@pytest.mark.parametrize(
    "tamper",
    ["ciphertext", "reorder", "truncate", "header", "partial", "valid_ciphertext_bad_chain"],
)
def test_invariant_9_tampering_is_detected_and_live_reads_fail_closed(tamper, tmp_path, clock):
    path = tmp_path / "ledger.jsonl"
    # strict mode: every append walks the chain, so any tamper fails the next append
    ledger = Ledger(path, clock_ns=clock, full_verify_every=1)
    ledger.append({"kind": "one"})
    ledger.append({"kind": "two"})
    lines = path.read_bytes().splitlines(keepends=True)
    if tamper == "ciphertext":
        record = json.loads(lines[1])
        token = record["item"]
        record["item"] = token[:20] + ("A" if token[20] != "A" else "B") + token[21:]
        lines[1] = json.dumps(record).encode() + b"\n"
    elif tamper == "reorder":
        lines[1], lines[2] = lines[2], lines[1]
    elif tamper == "truncate":
        lines.pop()
    elif tamper == "header":
        lines[0] = b'{"format":1,"genesis_hash":"forged"}\n'
    elif tamper == "partial":
        lines[-1] = lines[-1][:-2]
    else:
        # Adversarial kernel-level fixture: a valid Fernet token still needs a valid chain.
        keys = ledger.key_store
        record = json.loads(lines[1])
        item = json.loads(keys._decrypt(record["item"].encode()))
        item["kind"] = "altered"
        record["item"] = keys._encrypt(json.dumps(item).encode()).decode()
        lines[1] = json.dumps(record).encode() + b"\n"
    path.write_bytes(b"".join(lines))
    assert not ledger.verify()
    with pytest.raises(LedgerIntegrityError):
        ledger.aggregate("wallet_series")
    with pytest.raises(LedgerIntegrityError):
        ledger.append({"kind": "cannot-hide-corruption"})


def test_aggregate_views_use_only_declared_fields(ledger, clock, queue, open_decision):
    wallet = Wallet(100, ledger, clock_ns=clock)
    handle = open_decision()
    hold = wallet.reserve(25, handle, "fallback")
    wallet.commit(hold, 20)
    ledger.append({"kind": "invocation", "assembly_id": "new", "private_prompt": "secret"})
    clock.now += 10
    queue.settle(
        handle,
        channel="outcome",
        score=0.7,
        status="settled",
        definition_version="v1",
        sampling_ref="private-ref",
    )
    assert ledger.aggregate("wallet_series") == {
        "series": [{"ts": 100, "balance": 100}, {"ts": 100, "balance": 80}]
    }
    assert ledger.aggregate("spend_by_capability") == {"spend": {"new": 20}}
    assert ledger.aggregate("invocations_by_assembly") == {"counts": {"new": 1}}
    assert ledger.aggregate("action_frequencies") == {"counts": {"new": 1}}
    assert ledger.aggregate("settlement_latency") == {
        "count": 1,
        "total_ns": 10,
        "min_ns": 10,
        "max_ns": 10,
    }
    assert ledger.aggregate("settlement_latency", until_ns=110)["count"] == 0
    assert ledger.aggregate("settlement_latency", since_ns=110)["count"] == 1
    with pytest.raises(ValueError):
        ledger.aggregate("items")
    with pytest.raises(ValueError):
        ledger.aggregate("wallet_series", handle=handle)
    with pytest.raises(ValueError):
        ledger.aggregate("wallet_series", since_ns=2, until_ns=1)


def test_key_store_has_no_public_release_and_metadata_cannot_be_injected(ledger):
    assert not hasattr(KeyStore(), "release")
    for key in ("hash", "seq", "prev_hash"):
        with pytest.raises(ValueError):
            ledger.append({key: "fake"})
    with pytest.raises(ValueError):
        ledger.append({"value": float("nan")})
    assert ledger.verify()


def test_aggregate_window_spend_joins_earlier_decision(ledger, clock, queue, open_decision):
    wallet = Wallet(100, ledger, clock_ns=clock)
    handle = open_decision()
    clock.now = 105
    wallet.commit(wallet.reserve(20, handle, "fallback"), 12)
    assert ledger.aggregate("spend_by_capability", since_ns=105) == {"spend": {"new": 12}}


def test_contract_serialization_is_supported(ledger, contract_factory):
    ledger.append(
        {
            "kind": "contract",
            "contract": replace(contract_factory(), permissions=frozenset({"b", "a"})),
        }
    )
    assert ledger.verify()


def test_default_mode_catches_tail_tampers_immediately_and_earlier_edits_periodically(
    tmp_path, clock
):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, clock_ns=clock, full_verify_every=4)
    for i in range(3):
        ledger.append({"kind": f"k{i}"})
    lines = path.read_bytes().splitlines(keepends=True)
    # same-size edit of an earlier line: flip one byte inside the token in place
    raw = bytearray(lines[1])
    pos = raw.index(b'"item":"') + len(b'"item":"') + 20
    raw[pos] = ord("A") if raw[pos] != ord("A") else ord("B")
    lines[1] = bytes(raw)
    path.write_bytes(b"".join(lines))
    assert ledger.healthy()  # cheap check passes, by design
    assert not ledger.verify()  # the full walk does not
    with pytest.raises(LedgerIntegrityError):
        ledger.aggregate("wallet_series")
    # the 4th append passes the cheap check; the 5th (4 stored items) runs the full walk
    ledger.append({"kind": "k3"})
    with pytest.raises(LedgerIntegrityError):
        ledger.append({"kind": "periodic"})
    # tail tampers are caught immediately
    ledger2 = Ledger(tmp_path / "l2.jsonl", clock_ns=clock)
    ledger2.append({"kind": "a"})
    p2 = tmp_path / "l2.jsonl"
    p2.write_bytes(p2.read_bytes()[:-3] + b"xx\n")
    assert not ledger2.healthy()
    with pytest.raises(LedgerIntegrityError):
        ledger2.append({"kind": "b"})
