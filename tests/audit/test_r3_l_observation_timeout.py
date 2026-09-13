"""CPU exhaustion and wall deadlines have the same observation outcome."""

import signal
from types import SimpleNamespace

import pytest

from factorylab.cortex.tools import ObservationRunner


@pytest.mark.parametrize("returncode,timed_out", [(-signal.SIGKILL, False),
                                               (-signal.SIGXCPU, False),
                                               (128 + signal.SIGKILL, False),
                                               (128 + signal.SIGXCPU, False), (-1, True)])
def test_resource_timeout_has_stable_reason(monkeypatch, returncode, timed_out):
    monkeypatch.setattr("factorylab.cortex.tools.jail_available", lambda: True)
    monkeypatch.setattr("factorylab.cortex.tools.run_python", lambda *a, **kw: SimpleNamespace(
        returncode=returncode, timed_out=timed_out, stdout="", stderr=""))
    assert ObservationRunner(timeout_s=2).run("def observe(facts): pass", {}) == (None, "timeout")
