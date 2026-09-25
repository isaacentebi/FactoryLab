"""Release identity: one digest that binds the executing code to the world it runs.

A world's diary already binds its manifest and its venue account. It did not bind
the release executing them, so a refreshed checkout could continue an old
identity with new behaviour (cold audit F1). ``release_digest`` is computed once
per process from the executable bytes alone and ledgered in ``Launch``:

    sha256(sha256(uv.lock) + tree_hash(factorylab/))

The tree hash covers every file under the package as it is on disk, so an
uncommitted edit changes the digest exactly as a new commit does. The git head is
not an input (versioning S2; R8): Chapter II's version "cannot be Git", and a
docs- or tests-only commit changes no kernel physics, so it must not kill a world.
The head is still recorded beside the digest as forensic metadata: git supplies it
when a checkout is present, and a droplet install without git reads the
``RELEASE`` record that ``deploy/install.sh`` wrote at provisioning. Nothing here
reads a key file: the tree walked is the package directory, never the run
directory or the repository root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
ROOT = PACKAGE_DIR.parent
RELEASE_FILE = "RELEASE"
LOCK_FILE = "uv.lock"
EXCLUDED_DIRS = frozenset({"__pycache__"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})
UNRECORDED = "unrecorded"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_hash(directory: Path) -> str:
    """Hash every regular file under ``directory`` by relative path and content, in one order.

    Byte-compiled caches are excluded so that importing the package does not change
    its own identity. Symlinks are hashed by what they point at, like any file.
    """
    directory = Path(directory)
    entries: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix in EXCLUDED_SUFFIXES or not path.is_file():
            continue
        entries.append((relative.as_posix(), _sha256(path.read_bytes())))
    digest = hashlib.sha256()
    for name, content in entries:
        digest.update(name.encode() + b"\0" + content.encode() + b"\n")
    return digest.hexdigest()


def _git_head(root: Path) -> str | None:
    """The checked-out commit, from git when it is installed, else from the .git files."""
    git = shutil.which("git")
    if git is not None:
        try:
            completed = subprocess.run(
                [git, "-C", str(root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None and completed.returncode == 0:
            head = completed.stdout.strip()
            if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
                return head
    return _git_head_from_files(root)


def _git_head_from_files(root: Path) -> str | None:
    """Resolve HEAD without a git binary; a worktree's .git is a file naming its gitdir."""
    dotgit = root / ".git"
    try:
        if dotgit.is_file():
            pointer = dotgit.read_text().strip()
            if not pointer.startswith("gitdir:"):
                return None
            gitdir = Path(pointer.removeprefix("gitdir:").strip())
            if not gitdir.is_absolute():
                gitdir = (root / gitdir).resolve()
        elif dotgit.is_dir():
            gitdir = dotgit
        else:
            return None
        head = (gitdir / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head if len(head) == 40 else None
        ref = head.removeprefix("ref:").strip()
        # A worktree's gitdir holds only its own HEAD; refs live in the common dir.
        common = gitdir
        commondir = gitdir / "commondir"
        if commondir.is_file():
            common = (gitdir / commondir.read_text().strip()).resolve()
        loose = common / ref
        if loose.is_file():
            return loose.read_text().strip()
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


def _recorded_head(root: Path) -> str | None:
    """The head that ``deploy/install.sh`` recorded when git is not available at run time."""
    try:
        record = json.loads((root / RELEASE_FILE).read_text())
    except (OSError, ValueError):
        return None
    head = record.get("git_head") if isinstance(record, dict) else None
    return head if isinstance(head, str) and head else None


def _lock_hash(root: Path) -> str:
    try:
        return _sha256((root / LOCK_FILE).read_bytes())
    except OSError:
        return "missing"


@lru_cache(maxsize=8)
def _info(root: str) -> dict:
    base = Path(root)
    head, source = _git_head(base), "git"
    if head is None:
        head, source = _recorded_head(base), "release_file"
    if head is None:
        head, source = UNRECORDED, "none"
    lock = _lock_hash(base)
    tree = tree_hash(base / PACKAGE_DIR.name)
    return {
        "git_head": head,
        "git_head_source": source,
        "uv_lock_sha256": lock,
        "tree_sha256": tree,
        # The head is forensic only: the executable tree and lock are the identity.
        "release_digest": _sha256((lock + tree).encode()),
    }


def release_info(root: Path | str | None = None) -> dict:
    """The digest and its two inputs, plus the forensic head and where it came from."""
    return dict(_info(str(Path(root or ROOT).resolve())))


def release_digest(root: Path | str | None = None) -> str:
    """The identity of the release executing this process, computed once per root."""
    return release_info(root)["release_digest"]


def write_release_file(root: Path | str | None = None, path: Path | str | None = None) -> dict:
    """Record the release beside the checkout so a later run without git still knows its head."""
    base = Path(root or ROOT).resolve()
    info = release_info(base)
    target = Path(path) if path is not None else base / RELEASE_FILE
    target.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m factorylab.runtime.release",
                                     description="print or record the release identity")
    parser.add_argument("--root", default=None, help="checkout root (default: this package's)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--digest", action="store_true", help="print only the digest")
    group.add_argument("--write", metavar="PATH", nargs="?", const=RELEASE_FILE,
                       help="record the identity as JSON at PATH (default: RELEASE at the root)")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else ROOT
    if args.write is not None:
        target = Path(args.write)
        if not target.is_absolute():
            target = root / target
        info = write_release_file(root, target)
        print(json.dumps(info, sort_keys=True))
        return 0
    if args.digest:
        print(release_digest(root))
        return 0
    print(json.dumps(release_info(root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess in tests
    sys.exit(main())
