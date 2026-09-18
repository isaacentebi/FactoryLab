"""T16 (seat 2, finding 3): a verdict is a prediction that the judged return will not be
blamed by the charter. It settles when the return's window closes, its Brier enters the
judge's standing beside payoff skill, the meta is graded against it too, and an antagonist
exposes a judge whose high verdict landed on a return the window blamed."""

from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest


class ProcessDeath(BaseException):
    pass


def _short_window_manifest():
    base = load_manifest("scripted")
    return replace(base, novelty=replace(base.novelty, window_ns=2 * base.tick_interval_ns))


@pytest.mark.slow
def test_resume_after_a_verdict_consequence_item_replays_identically(tmp_path):
    m = _short_window_manifest()
    path = tmp_path / "verdict.jsonl"
    rt = Runtime(m, events=12, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 drip=True, router_gamma=.1)
    append = rt.ledger.append
    seen = []

    def crash_after_append(entry):
        seq = append(entry)
        if entry["kind"] == "verdict.consequence":
            seen.append(entry)
            raise ProcessDeath
        return seq

    rt.ledger.append = crash_after_append
    with pytest.raises(ProcessDeath):
        rt.run()
    assert seen
    resumed = resume_world(m, str(path))
    resumed["stats"]["resumes"] = 0
    assert resumed == run_world(m, events=12, seed=1)
