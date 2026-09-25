"""Sealed sidecar bytes (wave 17): only this diary's key unseals them, and names hide content.

Each test attempts to violate one guarantee of ``Ledger.seal_bytes`` /
``Ledger._unseal_bytes`` / ``Ledger.sidecar_name`` and asserts it is refused.
"""

import hashlib

import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError


def test_sealed_bytes_round_trip_under_their_own_diary_key():
    ledger = Ledger()
    data = bytes(range(256)) * 4
    token = ledger.seal_bytes(data)
    assert data not in token
    assert ledger._unseal_bytes(token) == data


def test_another_diary_s_key_cannot_unseal():
    token = Ledger().seal_bytes(b"one world's private state")
    with pytest.raises(LedgerIntegrityError):
        Ledger()._unseal_bytes(token)


@pytest.mark.parametrize("damage", ["flip", "truncate", "extend", "garbage"])
def test_an_altered_token_is_refused_never_returned(damage):
    ledger = Ledger()
    token = bytearray(ledger.seal_bytes(b"x" * 100))
    if damage == "flip":
        token[len(token) // 2] ^= 0x01
    elif damage == "truncate":
        token = token[:-5]
    elif damage == "extend":
        token += b"AAAA"
    else:
        token = bytearray(b"not a token")
    with pytest.raises(LedgerIntegrityError):
        ledger._unseal_bytes(bytes(token))


def test_sealing_refuses_what_is_not_bytes():
    with pytest.raises(TypeError):
        Ledger().seal_bytes("text")


def test_a_sidecar_name_is_keyed_one_per_digest_and_hides_the_digest():
    one, two = Ledger(), Ledger()
    digest = hashlib.sha256(b"an answer the world read").hexdigest()
    name = one.sidecar_name(digest)
    assert name == one.sidecar_name(digest)
    assert len(name) == 64 and set(name) <= set("0123456789abcdef")
    assert name != digest and name != hashlib.sha256(digest.encode()).hexdigest()
    # Another key names the same bytes differently: the disk cannot confirm a guess.
    assert name != two.sidecar_name(digest)
    assert name != one.sidecar_name(hashlib.sha256(b"another answer").hexdigest())


@pytest.mark.parametrize("digest", ["", "ABC", "g" * 64, "a" * 63, 7, None])
def test_a_sidecar_name_needs_a_sha256_digest(digest):
    with pytest.raises(ValueError):
        Ledger().sidecar_name(digest)


def test_sealing_writes_nothing_to_the_chain(tmp_path):
    ledger = Ledger(tmp_path / "w.jsonl", key_path=tmp_path / "w.jsonl.key")
    before = (tmp_path / "w.jsonl").read_bytes()
    ledger._unseal_bytes(ledger.seal_bytes(b"state"))
    ledger.sidecar_name("0" * 64)
    assert (tmp_path / "w.jsonl").read_bytes() == before
