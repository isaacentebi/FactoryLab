"""The release digest names the executing code: the lock and the package tree on disk."""

import hashlib
import json

import pytest

from factorylab.runtime import release


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


def test_an_edited_tree_is_a_different_release(tmp_path):
    root = _fake_checkout(tmp_path / "edited", head="b" * 40)
    before = release.release_digest(root)
    (root / release.PACKAGE_DIR.name / "kernel.py").write_text("x = 2\n")
    release._info.cache_clear()
    assert release.release_digest(root) != before


def test_the_git_head_is_forensic_and_never_changes_the_release(tmp_path):
    """Versioning S2 / R8: a new commit that leaves the executable bytes alone is the
    same release; the head is recorded beside the digest, never inside it."""
    one = _fake_checkout(tmp_path / "one", head="a" * 40)
    two = _fake_checkout(tmp_path / "two", head="b" * 40)
    first, second = release.release_info(one), release.release_info(two)
    assert (first["git_head"], second["git_head"]) == ("a" * 40, "b" * 40)
    assert first["release_digest"] == second["release_digest"]
    (two / release.LOCK_FILE).write_text("version = 2\n")
    release._info.cache_clear()
    assert release.release_digest(two) != first["release_digest"]


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
