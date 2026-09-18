"""Edition 2 hard casts (C4): the release that launched a world is the only one that resumes it.

Cold audit F1: identity bound the manifest and the venue account, never the
executable. These witnesses run the scripted world through a real ledger file:
Launch carries the digest, a different digest is refused with its own reason
code and a ledgered ``failed_resume``, the same digest resumes past that note,
and the deploy witness records the same facts outside the diary.
"""

import json

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime import release
from factorylab.runtime.loop import run_world
from factorylab.runtime.reasons import Reason
from factorylab.runtime.resume import (
    ResumeError,
    resume_reason,
    resume_world,
)
from factorylab.runtime.worlds import load_manifest

OTHER_RELEASE = "f" * 64


def items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


def launched(tmp_path):
    manifest = load_manifest("scripted")
    path = tmp_path / "world.jsonl"
    run_world(manifest, events=2, seed=1, ledger_path=str(path))
    return manifest, path


def test_a_different_release_is_refused_with_a_ledgered_failed_resume(tmp_path, monkeypatch):
    manifest, path = launched(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
    with pytest.raises(ResumeError) as refused:
        resume_world(manifest, str(path))
    assert refused.value.code == "release_mismatch"
    assert resume_reason(refused.value) is Reason.RELEASE_MISMATCH
    # The diary grew by the refusal and nothing else; no earlier byte changed.
    assert path.read_bytes().startswith(before) and path.read_bytes() != before
    failed = [item for item in items(path, manifest) if item.get("kind") == "failed_resume"]
    assert len(failed) == 1
    assert failed[0]["reason"] == "release_mismatch"
    assert failed[0]["running_release_digest"] == OTHER_RELEASE
    assert failed[0]["ledgered_release_digest"] == release._info(str(release.ROOT))[
        "release_digest"]
    assert not any(item.get("kind") == "resume.begin" for item in items(path, manifest))


def test_the_same_release_resumes_past_a_refused_attempt(tmp_path, monkeypatch):
    manifest, path = launched(tmp_path)
    with monkeypatch.context() as patched:
        patched.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
        with pytest.raises(ResumeError):
            resume_world(manifest, str(path))
    summary = resume_world(manifest, str(path))
    assert summary["stats"]["resumes"] == 1 and summary["ledger_verify"]
    kinds = [item.get("kind") for item in items(path, manifest)]
    assert kinds.count("failed_resume") == 1 and kinds.count("resume.begin") == 1
    assert kinds.index("failed_resume") < kinds.index("resume.begin")
