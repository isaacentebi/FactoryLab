"""The checkpoint plateaus (wave 17b; the rule as ruled in wave 16).

Essay II.I.b: the queue holds "outstanding decisions awaiting their reward"; II.IV.c:
a verdict "is consumed ... and then discarded", and what persists is aggregates. With
settled decisions released, the world's checkpoint holds what is still owed plus
aggregates, so on a scripted world it does not grow with the world's age.

What it holds does track activity: wave 16 keeps a decision until its outcome is
fixed at its horizon and its margin window is read, so a busier stretch retains more.
"Does not grow" is therefore two assertions, each aimed at one kind of leak, over the
500-event blocks from 1,500 to 5,000 world events (``plateau_problems``):

1. no decision leak: the least-squares slope of each block's median retained
   decisions is at most +2% of the first block's value per block;
2. no leak of state other than decisions: each block's median checkpoint bytes per
   retained decision stays within 10% of the first block's.

The simulated venue's own books (``fake_exchange``: every fill, mid and funding row the
simulator ever produced, which it answers history queries from) are the world, not the
factory, and on a live world they are the venue's; they are reported beside the
factory's own bytes and are not part of the bound.
"""

from statistics import median

import pytest

from factorylab.kernel.ledger import canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import runtime_state
from factorylab.runtime.worlds import load_manifest

EVENTS = 5_000
FROM = 1_500
BLOCK = 500
#: The largest decision slope per block, as a fraction of the first block's retention.
MAX_DECISION_SLOPE = 0.02
#: How far a block's bytes per retained decision may stray from the first block's.
FOOTPRINT_BAND = 0.10


def _slope(values: list[float]) -> float:
    """The least-squares slope of ``values`` against their index."""
    n = len(values)
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / sxx


def plateau_problems(decisions: list[float], footprints: list[float]) -> list[str]:
    """Why per-block medians of retained decisions and of checkpoint bytes per retained
    decision show a leak, or [] when they show none.

    Guarantees a problem when the decisions' least-squares slope exceeds
    ``MAX_DECISION_SLOPE`` of the first block's value per block (a decision leak), or
    when any block's footprint lies outside ``FOOTPRINT_BAND`` of the first block's (a
    leak of anything else a checkpoint carries). Each problem names both series.
    """
    series = f"decisions {[round(d) for d in decisions]}; " \
             f"bytes per decision {[round(f) for f in footprints]}"
    problems = []
    slope = _slope(decisions)
    if slope > MAX_DECISION_SLOPE * decisions[0]:
        problems.append(f"decision leak: slope {slope:.1f} per block exceeds "
                        f"{MAX_DECISION_SLOPE:.0%} of {decisions[0]:.0f} ({series})")
    far = [i for i, f in enumerate(footprints)
           if abs(f - footprints[0]) > FOOTPRINT_BAND * footprints[0]]
    if far:
        problems.append(f"state leak: bytes per decision outside {FOOTPRINT_BAND:.0%} of "
                        f"{footprints[0]:.0f} in blocks {far} ({series})")
    return problems


@pytest.mark.slow
def test_the_checkpoint_plateaus_between_1500_and_5000_events():
    rt = Runtime(load_manifest("scripted"), events=EVENTS, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1)
    sizes = []
    snapshot = rt._snapshot

    def measured(boundary):
        written = snapshot(boundary)
        if written and rt.ticks_consumed >= FROM:
            # The state the checkpoint just wrote: nothing changed since but the held
            # venue reads a checkpoint never carries.
            state = runtime_state(rt)
            world = len(canonical(state["fake_exchange"])) if state["fake_exchange"] else 0
            sizes.append((rt.ticks_consumed, len(canonical(state)) - world,
                          len(rt.queue.retained())))
        return written

    rt._snapshot = measured
    rt.run()
    assert rt.ticks_consumed == EVENTS
    assert sizes and sizes[0][0] < FROM + BLOCK and sizes[-1][0] > EVENTS - BLOCK
    # One checkpoint follows the window it closes, so single checkpoints swing with the
    # activity of the windows behind them: each 500-event block's median is the level.
    blocks = {}
    for tick, size, retained in sizes:
        blocks.setdefault((tick - FROM) // BLOCK, []).append((size, retained))
    rows = [rows for _key, rows in sorted(blocks.items())]
    assert len(rows) == (EVENTS - FROM) // BLOCK
    decisions = [median(retained for _size, retained in block) for block in rows]
    footprints = [median(size / max(1, retained) for size, retained in block)
                  for block in rows]
    assert not plateau_problems(decisions, footprints)


# --- the rule itself, on synthetic series (check tier) ---------------------------------

A06E159_DECISIONS = [1205, 1169, 1350, 1107, 1278, 1364, 1046]
A06E159_BYTES = [4344747, 4171807, 4934851, 4052833, 4388799, 4782593, 3486974]


def test_a_steady_five_percent_growth_in_decisions_is_a_decision_leak():
    decisions = [1000 * 1.05 ** i for i in range(7)]
    problems = plateau_problems(decisions, [3500.0] * 7)
    assert len(problems) == 1 and problems[0].startswith("decision leak")
    assert "bytes per decision" in problems[0]  # both series are reported


def test_a_three_percent_footprint_drift_is_a_state_leak_by_the_last_block():
    footprints = [3500 * 1.03 ** i for i in range(7)]
    problems = plateau_problems([1200.0] * 7, footprints)
    assert len(problems) == 1 and problems[0].startswith("state leak")
    assert "blocks [4, 5, 6]" in problems[0]


def test_the_a06e159_series_is_no_leak():
    """The run that failed the old +/-10% byte band: activity, not growth."""
    footprints = [b / d for b, d in zip(A06E159_BYTES, A06E159_DECISIONS, strict=True)]
    assert _slope(A06E159_DECISIONS) < 0
    assert plateau_problems(A06E159_DECISIONS, footprints) == []
