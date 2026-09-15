"""The release digest names the executing code: head, lock and the package tree on disk."""

import hashlib
import json
import subprocess
import sys

import pytest

from factorylab.runtime import release

HEX64 = set("0123456789abcdef")


def _fake_checkout(root, *, head=None):
    """A root with a package tree and a lock but no git, optionally a RELEASE record."""
    package = root / release.PACKAGE_DIR.name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "kernel.py").write_text("x = 1\n")
    (root / release.LOCK_FILE).write_text("version = 1\n")
    if head is not None:
        (root / release.RELEASE_FILE).write_text(json.dumps({"git_head": head}))
    return root


def test_tree_hash_is_ordered_content_addressed_and_ignores_bytecode(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "m.py").write_text("print(1)\n")
        (root / "n.py").write_text("print(2)\n")
    assert release.tree_hash(a) == release.tree_hash(b)
    (b / "__pycache__").mkdir()
    (b / "__pycache__" / "n.cpython-313.pyc").write_bytes(b"\0")
    (b / "sub" / "m.pyc").write_bytes(b"\0")
    assert release.tree_hash(a) == release.tree_hash(b)
    (b / "n.py").write_text("print(3)\n")
    assert release.tree_hash(a) != release.tree_hash(b)
    (b / "n.py").write_text("print(2)\n")
    (b / "sub" / "extra.py").write_text("")
    assert release.tree_hash(a) != release.tree_hash(b)


def test_this_checkout_has_a_git_head_and_a_digest_made_of_its_three_inputs():
    info = release.release_info()
    assert info["git_head_source"] == "git"
    assert len(info["git_head"]) == 40 and set(info["git_head"]) <= HEX64
    assert info["uv_lock_sha256"] == hashlib.sha256(
        (release.ROOT / release.LOCK_FILE).read_bytes()).hexdigest()
    assert info["tree_sha256"] == release.tree_hash(release.PACKAGE_DIR)
    expected = hashlib.sha256(
        (info["git_head"] + info["uv_lock_sha256"] + info["tree_sha256"]).encode()).hexdigest()
    assert info["release_digest"] == expected == release.release_digest()


def test_head_resolves_from_the_git_files_when_the_binary_is_absent(monkeypatch):
    with_binary = release._git_head(release.ROOT)
    monkeypatch.setattr(release.shutil, "which", lambda name: None)
    assert release._git_head(release.ROOT) == with_binary
    assert release._git_head_from_files(release.ROOT) == with_binary


def test_without_git_the_recorded_release_file_supplies_the_head_and_says_so(tmp_path):
    recorded = _fake_checkout(tmp_path / "recorded", head="a" * 40)
    info = release.release_info(recorded)
    assert info["git_head"] == "a" * 40 and info["git_head_source"] == "release_file"
    assert info["tree_sha256"] == release.tree_hash(recorded / release.PACKAGE_DIR.name)
    bare = _fake_checkout(tmp_path / "bare")
    unrecorded = release.release_info(bare)
    assert unrecorded["git_head"] == release.UNRECORDED
    assert unrecorded["git_head_source"] == "none"
    # The same tree and lock under a different head is a different release.
    assert unrecorded["tree_sha256"] == info["tree_sha256"]
    assert unrecorded["release_digest"] != info["release_digest"]


def test_an_edited_tree_is_a_different_release(tmp_path):
    root = _fake_checkout(tmp_path / "edited", head="b" * 40)
    before = release.release_digest(root)
    (root / release.PACKAGE_DIR.name / "kernel.py").write_text("x = 2\n")
    release._info.cache_clear()
    assert release.release_digest(root) != before


def test_the_module_prints_the_digest_and_writes_the_release_record(tmp_path):
    root = _fake_checkout(tmp_path / "cli", head="c" * 40)
    digest = subprocess.run(
        [sys.executable, "-m", "factorylab.runtime.release", "--root", str(root), "--digest"],
        capture_output=True, text=True, check=True, cwd=release.ROOT).stdout.strip()
    assert digest == release.release_digest(root)
    target = tmp_path / "RELEASE.out"
    subprocess.run(
        [sys.executable, "-m", "factorylab.runtime.release", "--root", str(root),
         "--write", str(target)],
        capture_output=True, text=True, check=True, cwd=release.ROOT)
    record = json.loads(target.read_text())
    assert record["release_digest"] == digest and record["git_head"] == "c" * 40
    assert set(record) == {"git_head", "git_head_source", "uv_lock_sha256", "tree_sha256",
                           "release_digest"}


@pytest.mark.parametrize("missing", ["lock", "package"])
def test_a_missing_input_is_named_rather_than_guessed(tmp_path, missing):
    root = _fake_checkout(tmp_path / "partial", head="d" * 40)
    if missing == "lock":
        (root / release.LOCK_FILE).unlink()
        assert release.release_info(root)["uv_lock_sha256"] == "missing"
    else:
        package = root / release.PACKAGE_DIR.name
        for path in package.iterdir():
            path.unlink()
        package.rmdir()
        assert release.release_info(root)["tree_sha256"] == hashlib.sha256(b"").hexdigest()
