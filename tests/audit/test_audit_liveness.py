"""Cold audit, seat 4: liveness under unbounded input and unbounded time.

Each test reproduces one finding in docs/audits/v2/defects-fable.md and fails on the
audited commit.
"""

import os
import subprocess
import sys

from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.market import discover
from factorylab.world.x402 import HTTPResponse, X402Error


class RepeatingIndex:
    """A discovery index that answers every offset with the same full page and no total."""

    def __init__(self):
        self.pages = 0

    def __call__(self, method, url, payload, headers):
        self.pages += 1
        if self.pages > 25:
            raise AssertionError("discover() paged more than 25 times on a repeating index")
        items = [{"resource": f"https://seller-{i}.example/v1", "accepts": [], "description": ""}
                 for i in range(100)]
        return HTTPResponse(200, {"items": items, "pagination": {"offset": (self.pages - 1) * 100,
                                                                 "limit": 100}})


def test_discover_terminates_on_a_repeating_index():
    """Finding 10 (minor): market.discover pages until `offset >= total`, but a paginator that
    omits `total` and repeats resources never hits `len(items) < page_limit`; the tool call,
    and the event holding it, never returns. A page budget bounds it."""
    index = RepeatingIndex()
    try:
        result = discover(query="nothing-matches", limit=5, transport=index)
    except X402Error:
        result = None  # the sentinel above fired inside the transport
    assert index.pages <= 25, f"discover() requested {index.pages} pages and never returned"
    assert result == []


def test_resume_memory_does_not_scale_with_the_whole_diary(tmp_path, scripted_run):
    """Finding 11 (round-one Opus #13, recorded as fixed by 'diary streaming', still on main):
    Ledger.reopen reads the whole file and _recovery_items decrypts every item into a list
    before the last snapshot is even located. Resume RSS is ~9x the diary; a droplet that
    must run for a year cannot resume after weeks. Net RSS must stay within a few diary sizes."""
    path = str(scripted_run("scripted", 60, 1).copy_to(tmp_path / "world"))
    size = os.path.getsize(path)
    code = """
import resource, sys
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
rt = resume_runtime(load_manifest('scripted'), sys.argv[1])
rt._ledger_lock.close()
print(base, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""
    out = subprocess.run([sys.executable, "-c", code, path], capture_output=True, text=True,
                         check=True)
    base, peak = (int(v) for v in out.stdout.split()[-2:])
    scale = 1 if sys.platform == "darwin" else 1024
    net = (peak - base) * scale
    assert net <= 3 * size, f"resume used {net/1e6:.0f} MB for a {size/1e6:.1f} MB diary"


def test_a_death_before_the_launch_snapshot_leaves_a_launchable_world(tmp_path, monkeypatch):
    """Finding 13 (minor): Runtime.__init__ creates the ledger file before it constructs the
    venue adapter (a network call). A crash in that window leaves a ledger with no snapshot;
    `run` then refuses the existing file and `resume` refuses the missing snapshot, so
    deploy/start.sh loops forever on a world that never launched."""
    from factorylab.kernel.ledger import Ledger
    from factorylab.runtime.resume import ResumeError, resume_world

    path = str(tmp_path / "w.jsonl")
    original = Ledger._append
    calls = {"n": 0}

    class Died(BaseException):
        pass

    def die_on_third(self, entry):
        calls["n"] += 1
        if calls["n"] == 3:
            raise Died()
        return original(self, entry)

    monkeypatch.setattr(Ledger, "_append", die_on_third)
    m = load_manifest("scripted")
    try:
        run_world(m, events=2, seed=1, ledger_path=path)
    except Died:
        pass
    monkeypatch.setattr(Ledger, "_append", original)
    import gc

    gc.collect()  # a real death releases the OS lock; the abandoned Runtime holds it here
    outcomes = {}
    try:
        outcomes["resume"] = resume_world(m, path)["stats"]["events"]
    except ResumeError as exc:
        outcomes["resume"] = f"refused: {exc}"
    try:
        outcomes["run"] = run_world(m, events=2, seed=1, ledger_path=path)["stats"]["events"]
    except FileExistsError as exc:
        outcomes["run"] = f"refused: {type(exc).__name__}"
    assert any(isinstance(v, int) for v in outcomes.values()), outcomes
