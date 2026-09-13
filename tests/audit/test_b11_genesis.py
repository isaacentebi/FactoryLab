"""B11: the CLI cannot launch a world whose genesis recovery cannot reconstruct."""

from argparse import Namespace

import pytest

from factorylab.runtime.cli import _cmd_resume, _cmd_run
from factorylab.runtime.reasons import Reason
from factorylab.runtime.resume import ResumeError
from factorylab.runtime.worlds import load_manifest


def test_changed_tick_override_is_refused_before_creating_a_diary(tmp_path, capsys, monkeypatch):
    m = load_manifest("scripted")
    path = tmp_path / "world.jsonl"
    monkeypatch.setattr("factorylab.runtime.loop.run_world",
                        lambda *a, **kw: pytest.fail("a refused override cannot launch"))
    args = Namespace(world="scripted", tick_interval=str(m.tick_interval_ns + 1), ledger=str(path))
    assert _cmd_run(args) == 2
    assert capsys.readouterr().err == "factorylab run: tick_override_refused\n"
    assert not path.exists()


@pytest.mark.parametrize("error,code", [
    (ResumeError("private exception detail", code="venue_account_mismatch"),
     Reason.VENUE_ACCOUNT_MISMATCH),
    (ResumeError("private exception detail", code="arbitrary-provider-text"),
     Reason.INVALID_SNAPSHOT),
    (RuntimeError("private exception detail"), Reason.ADAPTER_UNAVAILABLE),
    (FileNotFoundError("private exception detail"), Reason.MANIFEST_UNAVAILABLE),
])
def test_refused_resume_emits_only_a_closed_reason_code(tmp_path, monkeypatch, capsys, error, code):
    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr("factorylab.runtime.resume.resume_world", refuse)
    args = Namespace(world="scripted", ledger=str(tmp_path / "world.jsonl"))
    assert _cmd_resume(args) == 1
    assert capsys.readouterr().err == f"factorylab resume: {code.value}\n"
