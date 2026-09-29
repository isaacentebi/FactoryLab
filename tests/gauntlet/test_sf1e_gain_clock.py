"""SF-1e reads the gain loop on the kernel's own clock: world ticks, not price windows.

The rows are cut from a free scripted replay of the edition-6 rehearsal world on the
longrun1 tape, seed 1, on main's roster
(``tests/fixtures/scripted_s1_sf1e_request_router_rows.json``: every price and organ
close, trimmed to the fields SF-1e reads, and every
``router.created``, ``decision.open``, ``router.learned`` and ``immune.gain`` row of
``router:request ProducerReturn``, in diary order, hashes dropped).

Read in windows, that router failed SF-1e: nine steps of 24 windows each (min_ratio × a
p90 round of 8 windows over the whole diary) gave a bound of window 266, and it stood at
0.45 when its episode ended at 271. The kernel's gain loop is not in windows
(clockwork.py, "one clock domain"): it is due ``min_ratio × I`` ticks after it fired,
lengthened by its own jitter, where ``I`` is the router's meter (the p90 of its latest
``cadence_sample`` round closures, in ticks) read at the fire and again now, and it steps
only at the organ's next acting close. A window here spans 4 to 99 ticks, and the meter
rose from 21 to 81 ticks at window 68: every one of the router's eight steps came at the
first acting close at which its loop was certainly due.
"""

import copy
import json
import tomllib
from pathlib import Path

import pytest

from scripts import gauntlet as g

ROOT = Path(__file__).resolve().parents[2]
ROUTER, KIND = "router:request ProducerReturn", "request ProducerReturn"


@pytest.fixture
def rows():
    return json.loads((ROOT / "tests/fixtures/scripted_s1_sf1e_request_router_rows.json")
                      .read_text())


@pytest.fixture(scope="module")
def manifest():
    return tomllib.loads((ROOT / "worlds/edition6-testnet-rehearsal.toml").read_text())


def _problems(rows, manifest):
    result = g.sf1e_gain(rows, manifest)
    return result.status, result.evidence["problems"]


def test_the_router_was_never_late_on_the_kernels_clock(rows, manifest):
    status, problems = _problems(rows, manifest)
    assert problems == []
    # The last episode is still open at the diary's end, so its outcome is unobserved.
    assert status == g.UNSUPPORTED
    assert g.sf1e_gain(rows, manifest).evidence["clock"] == "ticks"


def test_the_meter_and_the_fire_are_the_kernels(rows, manifest):
    """At window 92 the loop fired at tick 193 on a meter of 21, but the meter now reads
    81, so the kernel waits until 193 + 3 × 81 = 436: the next acting close, window 95
    at tick 476, is the first at which a step was certainly due, and the step is there."""
    ph = g.physics(manifest)
    acts = {act.window: act for act in g.gain_acts(rows, ph)}
    assert (acts[92].tick, acts[92].inner[KIND], acts[92].fired[KIND]) == \
        (377, 81, (193, 21))
    assert not g.certainly_due(acts[92], ph, KIND)
    assert g.certainly_due(acts[95], ph, KIND) and ROUTER in acts[95].stepped


def test_the_same_rows_read_in_windows_are_the_false_failure(rows, manifest):
    """An older diary states no round ticks and is still read in windows; these rows read
    that way fail, which is the failure the replay reported."""
    windowed = [{k: v for k, v in row.items() if k not in ("opened_tick", "closed_tick")}
                for row in rows]
    status, problems = _problems(windowed, manifest)
    assert status == g.FAIL and problems[0]["bound"] == 266


def test_negative_control_a_skipped_due_step_fails(rows, manifest):
    """Drop the real step at window 204: the loop last fired at tick 1020 on a meter of
    81, so it was certainly due from 1020 + ceil(3 × 81 × 1.2) = 1312, and the acting
    close at window 212 (tick 1318) left the router at 0.35."""
    cut = [row for row in rows if not (row["kind"] == "immune.gain" and row["window"] == 204)]
    status, problems = _problems(cut, manifest)
    assert status == g.FAIL
    assert problems[0]["late"] == {"window": 212, "tick": 1318, "gamma": 0.35,
                                   "fired_tick": 1020, "inner_at_fire": 81, "inner_now": 81}


def test_negative_control_a_step_one_act_late_fails(rows, manifest):
    """Move the real step at window 95 (tick 476) to the next acting close, window 99."""
    late = copy.deepcopy(rows)
    step = next(row for row in late if row["kind"] == "immune.gain" and row["window"] == 95)
    late.remove(step)
    at = next(i for i, row in enumerate(late)
              if row["kind"] == "immune.window" and row["window"] == 99)
    late.insert(at + 1, {**step, "window": 99, "tick": 614})
    status, problems = _problems(late, manifest)
    assert status == g.FAIL and problems[0]["late"]["window"] == 95


def _act(window, tick):
    return {"kind": "immune.window", "window": window, "tick": tick, "acts": True,
            "flags": {"stable_failure": True, "thrash": False}}


def _gain(window, tick, before, after):
    return {"kind": "immune.gain", "pathology": "stable_failure", "window": window,
            "router": "router:Tick", "tick": tick, "gamma_before": [before],
            "gamma_after": [after]}


@pytest.mark.parametrize("second, late", [(35, False), (36, True)])
def test_the_jitter_only_lengthens_and_is_bounded(second, late):
    """A loop that fired at tick 0 on a meter of 10 drew a period below 3 × 10 × 1.2 = 36
    (``derived_period``, ``u < 1``): at tick 35 it may not be due yet, at 36 it is."""
    rows = [{"kind": "router.created", "learner_id": "router:Tick", "event_kind": "Tick",
             "replaces": []},
            {"kind": "router.learned", "handle": "d1", "router": "router:Tick",
             "learner": "router:Tick", "action": "a", "path": "direct", "opened_tick": 0,
             "closed_tick": 10, "closed_window": 1},
            _act(1, 0), _gain(1, 0, 0.1, 0.15), _act(2, second)]
    status, problems = _problems(rows, {})
    assert (status == g.FAIL) is late
    if late:
        assert problems[0]["late"]["window"] == 2
