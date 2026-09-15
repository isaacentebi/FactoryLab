"""C9: the artifact archive keeps content-addressed bytes, ledgered first, never deleted."""

import hashlib
import os
from pathlib import Path

import pytest

from factorylab.kernel.artifacts import (
    MAX_TOOL_READ_BYTES,
    ArtifactError,
    ArtifactStore,
    artifact_root,
)
from factorylab.kernel.ledger import Ledger


class Clock:
    def __init__(self):
        self.now = 7

    def __call__(self):
        self.now += 1
        return self.now


def store(tmp_path=None):
    ledger = Ledger(None, manifest={"name": "artifacts"})
    root = artifact_root(tmp_path / "world.jsonl") if tmp_path is not None else None
    return ArtifactStore(ledger, root=root, clock_ns=Clock()), ledger


def test_artifact_root_sits_beside_the_ledger():
    assert artifact_root("runs/testnet.jsonl") == Path("runs/testnet.artifacts")
    assert artifact_root(Path("/x/world.jsonl")) == Path("/x/world.artifacts")


def test_put_ledgers_before_writing_and_get_returns_the_same_bytes(tmp_path):
    archive, ledger = store(tmp_path)
    data = b'{"n": 1}'
    sha = archive.put(data, owner="prog-a", kind="program.state")
    assert sha == hashlib.sha256(data).hexdigest()
    entry = ledger._recovery_items()[-1]
    assert {k: entry[k] for k in ("kind", "sha", "owner", "artifact_kind", "bytes")} == {
        "kind": "artifact.put", "sha": sha, "owner": "prog-a",
        "artifact_kind": "program.state", "bytes": len(data),
    }
    assert entry["ts"] == 8
    path = archive.root / sha
    assert path.read_bytes() == data and os.stat(path).st_mode & 0o777 == 0o600
    assert archive.get(sha) == data
    assert archive.owner_for(sha) == "prog-a"
    assert archive.list() == [{"sha": sha, "owner": "prog-a", "kind": "program.state",
                               "bytes": len(data), "ts": 8}]


def test_put_is_idempotent_by_content_and_the_first_owner_stands(tmp_path):
    archive, ledger = store(tmp_path)
    first = archive.put(b"same", owner="a", kind="k")
    second = archive.put(b"same", owner="b", kind="other")
    assert first == second
    assert archive.owner_for(first) == "a"
    assert sum(i["kind"] == "artifact.put" for i in ledger._recovery_items()) == 2
    assert [p.name for p in archive.root.iterdir()] == [first]


def test_list_filters_by_owner_in_put_order(tmp_path):
    archive, _ = store(tmp_path)
    one = archive.put(b"1", owner="a", kind="k")
    two = archive.put(b"2", owner="b", kind="k")
    three = archive.put(b"3", owner="a", kind="k")
    assert [r["sha"] for r in archive.list()] == [one, two, three]
    assert [r["sha"] for r in archive.list(owner="a")] == [one, three]
    assert archive.list(owner="nobody") == []


def test_get_refuses_unknown_malformed_and_tampered(tmp_path):
    archive, _ = store(tmp_path)
    sha = archive.put(b"kept", owner="a", kind="k")
    with pytest.raises(ArtifactError):
        archive.get("0" * 64)
    with pytest.raises(ArtifactError):
        archive.get("not-a-sha")
    with pytest.raises(ArtifactError):
        archive.get(sha.upper())
    (archive.root / sha).write_bytes(b"swapped")
    with pytest.raises(ArtifactError):
        archive.get(sha)
    assert archive.owner_for("0" * 64) is None


def test_memory_store_when_the_world_has_no_ledger_path():
    archive, ledger = store()
    sha = archive.put(b"in memory", owner="a", kind="k")
    assert archive.get(sha) == b"in memory"
    assert ledger._recovery_items()[-1]["kind"] == "artifact.put"
    with pytest.raises(ArtifactError):
        archive.get("f" * 64)


def test_put_validates_its_arguments():
    archive, _ = store()
    with pytest.raises(TypeError):
        archive.put("text", owner="a", kind="k")
    with pytest.raises(ArtifactError):
        archive.put(b"x", owner="", kind="k")
    with pytest.raises(ArtifactError):
        archive.put(b"x", owner="a", kind="")


def test_read_view_is_bounded_and_marks_binary(tmp_path):
    archive, _ = store(tmp_path)
    text = archive.put(b'{"n": 2}', owner="prog-a", kind="program.state")
    assert archive.read(text) == {"sha": text, "owner": "prog-a", "kind": "program.state",
                                  "bytes": 8, "text": '{"n": 2}'}
    binary = archive.put(b"\xff\xfe\x00", owner="a", kind="blob")
    assert archive.read(binary)["base64"] == "//4A"
    big = archive.put(b"x" * (MAX_TOOL_READ_BYTES + 1), owner="a", kind="blob")
    view = archive.read(big)
    assert view["bytes"] == MAX_TOOL_READ_BYTES + 1 and "exceeds" in view["error"]
    assert archive.read("0" * 64) == {"error": "unknown artifact"}
    assert "error" in archive.read(None)


def test_retirement_is_not_the_archives_business(tmp_path):
    """Nothing here deletes: the archive has no remove, and a file survives its owner."""
    archive, _ = store(tmp_path)
    sha = archive.put(b"machinery", owner="retired-seat", kind="program.state")
    assert not any(name.startswith(("delete", "remove", "retire")) for name in dir(archive))
    assert archive.get(sha) == b"machinery" and archive.owner_for(sha) == "retired-seat"
