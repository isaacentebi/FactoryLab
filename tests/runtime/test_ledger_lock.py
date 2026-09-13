import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("owner", ["run", "resume"])
@pytest.mark.parametrize("contender", ["run", "resume"])
def test_two_processes_cannot_own_one_world(tmp_path, owner, contender):
    repo = str(Path(__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": repo}
    path = tmp_path / "synthetic.jsonl"
    code = '''
import sys
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
m = load_manifest('scripted')
if sys.argv[2] == 'resume':
    run_world(m, events=2, ledger_path=sys.argv[1])
    rt = resume_runtime(m, sys.argv[1])
    print('READY', flush=True)
    input()
else:
    rt = Runtime(m, events=2, seed=1, initial_balance_micro=None,
                 ledger_path=sys.argv[1], drip=False, router_gamma=.1)
    original = rt._process_event
    def pause(event):
        result = original(event)
        print('READY', flush=True)
        input()
        return result
    rt._process_event = pause
    rt.run()
'''
    first = subprocess.Popen([sys.executable, "-c", code, str(path), owner],
                             cwd=tmp_path, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "READY"
        before = path.read_bytes()
        second = subprocess.run(
            [sys.executable, "-m", "factorylab.runtime.cli", contender, "--world", "scripted",
             "--ledger", str(path)], cwd=tmp_path, env=env, capture_output=True,
            text=True, timeout=30,
        )
        assert second.returncode == 4, second.stderr
        assert second.stderr == f"factorylab {contender}: ledger_busy\n"
        assert path.read_bytes() == before
    finally:
        first.kill()
        first.communicate(timeout=10)
    # OS ownership vanishes even on SIGKILL; the stable sidecar inode remains.
    from factorylab.kernel.ledger import LedgerLock

    with LedgerLock(path):
        assert Path(str(path) + ".lock").exists()
