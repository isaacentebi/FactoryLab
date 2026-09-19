"""B11: the CLI cannot launch a world whose genesis recovery cannot reconstruct."""

from argparse import Namespace

import pytest

from factorylab.runtime.cli import _cmd_run
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
