"""One seed is one world, whatever the interpreter's hash seed (R16b-9).

Python randomises ``str`` hashing per process (``PYTHONHASHSEED``), so iterating a set of
handles runs in a different order in each process. Any such order that reaches the
ledger, a checkpoint or wire bytes makes the same seed write a different diary, and a
resume in a fresh process could diverge from the run that crashed. Two subprocesses,
two hash seeds, one world seed: the ledger items and the checkpoint must be identical.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = """
import hashlib, json, sys
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import durable_state, runtime_state
from factorylab.runtime.worlds import load_manifest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

rt = Runtime(load_manifest("scripted"), events=int(sys.argv[1]), seed=7,
             initial_balance_micro=None, ledger_path=None)
rt.run()
# The hash chain is keyed per ledger: compare what the diary says, in its order, with
# object keys sorted as the ledger writes them (a ``$map`` keeps its own order).
items = [{k: v for k, v in i.items() if k not in ("hash", "prev_hash")}
         for i in rt.ledger._recovery_items()]
print(digest(items),
      digest(durable_state(runtime_state(rt))))
"""


def _digests(hash_seed: str, events: int) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    done = subprocess.run([sys.executable, "-c", PROBE, str(events)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip()


@pytest.mark.gate
def test_one_seed_writes_one_diary_and_one_checkpoint_under_any_hash_seed():
    # Twelve events reach the margin windows whose outcomes were captured in set order.
    assert _digests("0", 12) == _digests("1", 12)


CRASH = """
import hashlib, json, sys
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import durable_state, runtime_state
from factorylab.runtime.worlds import load_manifest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

class ProcessDeath(BaseException):
    pass

rt = Runtime(load_manifest("scripted"), events=20, seed=7, initial_balance_micro=None,
             ledger_path=sys.argv[1])
original = rt._process_event

def interrupted(event):
    result = original(event)
    if rt.ticks_consumed == 8 and str(event.kind) == "Tick":
        raise ProcessDeath
    return result

rt._process_event = interrupted
try:
    rt.run()
except ProcessDeath:
    pass
print(digest(durable_state(runtime_state(rt))))
"""

RESUME = """
import hashlib, json, sys
from factorylab.runtime.resume import durable_state, resume_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

restored = resume_runtime(load_manifest("scripted"), sys.argv[1])
restored.stats.resumes = 0  # the one field a resume adds
state = digest(durable_state(runtime_state(restored)))
restored.stats.resumes = 1
summary = restored.run()
summary["stats"]["resumes"] = 0
for key in ("aggregates", "ledger_verify", "ledger_path"):
    summary.pop(key, None)
print(state, digest(summary))
"""

WHOLE = """
import hashlib, json
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

summary = run_world(load_manifest("scripted"), events=20, seed=7)
for key in ("aggregates", "ledger_verify", "ledger_path"):
    summary.pop(key, None)
print(digest(summary))
"""


def _run(program: str, hash_seed: str, *args: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    done = subprocess.run([sys.executable, "-c", program, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip().splitlines()[-1]


@pytest.mark.gate
def test_a_resume_in_a_process_with_another_hash_seed_continues_the_crashed_world(tmp_path):
    """The crash and the resume run under different hash seeds; the resumed state is the
    crashed one, and the resumed run ends as the uninterrupted world does."""
    path = str(tmp_path / "diary.jsonl")
    crashed = _run(CRASH, "0", path)
    restored, resumed_summary = _run(RESUME, "1", path).split()
    assert restored == crashed
    assert resumed_summary == _run(WHOLE, "2")
