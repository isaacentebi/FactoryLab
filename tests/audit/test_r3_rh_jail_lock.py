"""Round three, rehearsal row T55: a jailed child owns nothing of the world it runs in.

The rehearsal's first ``factorylab resume`` after a SIGKILL returned
``ledger_busy``, and killing the surviving ``sandbox-exec`` child released it.
Two separate claims are in that sentence. The survival is real on macOS:
``bwrap --die-with-parent`` has no ``sandbox-exec`` equivalent, so a jailed
child outlives a SIGKILLed parent. Holding the writer lock is not: every ledger
descriptor is close-on-exec from the syscall that creates it, so no child can
inherit one, and these tests pin that. What the runner can still guarantee is
that no confined process outlives a call it can see the end of — a timeout, an
error, or an interrupt now kills the whole process group before returning.
"""

import fcntl
import os
import subprocess
import sys
import threading
import time

import pytest

from factorylab.cortex import sandbox
from factorylab.kernel.ledger import Ledger, LedgerLock

SLEEPER = "import time\nfor _ in range(50): time.sleep(0.1)\n"


@pytest.fixture
def world(tmp_path):
    path = tmp_path / "w.jsonl"
    path.write_bytes(b"")
    return path


def test_the_writer_lock_is_close_on_exec(world):
    lock = LedgerLock(world)
    try:
        assert os.get_inheritable(lock.fd) is False
        assert fcntl.fcntl(lock.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
    finally:
        lock.close()


def test_every_ledger_descriptor_is_close_on_exec_from_its_own_syscall(tmp_path, monkeypatch):
    """A descriptor made inheritable for even one instruction can be forked into a child."""
    from factorylab.kernel import ledger as ledger_module

    path = tmp_path / "seen.jsonl"
    real, seen = os.open, []

    def spy(file, flags, *args, **kwargs):
        fd = real(file, flags, *args, **kwargs)
        if str(file).startswith(str(path)):
            seen.append((flags, os.get_inheritable(fd)))
        return fd

    monkeypatch.setattr(ledger_module.os, "open", spy)
    lock = LedgerLock(path)
    try:
        ledger = Ledger(str(path), manifest={"name": "seen"})
        ledger.append({"kind": "probe"})
    finally:
        lock.close()
    assert seen
    assert all(flags & os.O_CLOEXEC and not inheritable for flags, inheritable in seen)


def test_a_child_launched_while_the_lock_is_held_never_holds_it(world):
    """Even a child that inherits everything inheritable cannot inherit the lock."""
    lock = LedgerLock(world)
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE, close_fds=False, start_new_session=True)
    try:
        lock.close()
        assert child.poll() is None  # the child outlives its parent's ownership
        second = LedgerLock(world)
        second.close()
    finally:
        child.kill()
        child.communicate(timeout=10)


@pytest.mark.skipif(not sandbox.jail_available(), reason="no jail on this host")
def test_a_living_jailed_child_never_holds_the_lock(world):
    lock = LedgerLock(world)
    finished = threading.Event()

    def run():
        try:
            sandbox.run_python(SLEEPER, timeout_s=20, cpu_s=10)
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        time.sleep(1.0)  # the jail has launched and the confined interpreter is sleeping
        assert not finished.is_set()
        lock.close()
        second = LedgerLock(world)
        second.close()
        assert not finished.is_set()
    finally:
        worker.join(30)


@pytest.mark.skipif(not sandbox.jail_available(), reason="no jail on this host")
def test_an_interrupted_jail_run_kills_the_confined_process_group():
    """A caller that never returns normally still leaves no confined process behind."""
    class Interrupted(BaseException):
        pass

    launched = {}
    real = subprocess.Popen

    class Interrupting(real):
        def communicate(self, *args, **kwargs):
            launched["pid"] = self.pid
            raise Interrupted

    monkey = type(sandbox.subprocess)("subprocess")
    for name in ("PIPE", "TimeoutExpired", "SubprocessError"):
        setattr(monkey, name, getattr(subprocess, name))
    monkey.Popen = Interrupting
    started = time.monotonic()
    original = sandbox.subprocess
    sandbox.subprocess = monkey
    try:
        with pytest.raises(Interrupted):
            sandbox.run_python(SLEEPER, timeout_s=30, cpu_s=10)
    finally:
        sandbox.subprocess = original
    assert time.monotonic() - started < 2.0  # it did not wait out the confined sleeper
    with pytest.raises(ProcessLookupError):
        os.killpg(launched["pid"], 0)
