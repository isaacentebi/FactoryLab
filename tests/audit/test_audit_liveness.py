"""Cold audit, seat 4: liveness under unbounded input and unbounded time.

Each test reproduces one finding in docs/audits/v2/defects-fable.md and fails on the
audited commit.
"""


from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


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
