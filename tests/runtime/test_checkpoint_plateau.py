"""The checkpoint plateaus (wave 17b; the rule as ruled in wave 16).

Essay II.I.b: the queue holds "outstanding decisions awaiting their reward"; II.IV.c:
a verdict "is consumed ... and then discarded", and what persists is aggregates. With
settled decisions released, the world's checkpoint holds what is still owed plus
aggregates, so on a scripted world it does not grow with the world's age.

What it holds does track activity: wave 16 keeps a decision until its outcome is
fixed at its horizon and its margin window is read, so a busier stretch retains more.
"Does not grow" is therefore two assertions, each aimed at one kind of leak, over the
checkpoints written between 1,500 and 5,000 world events (``plateau_problems``):

1. no decision leak: the least-squares slope of each 500-event block's median
   retained decisions is at most +2% of the first block's value per block;
2. no leak of state other than decisions: holding the retained decisions fixed, the
   checkpoint does not grow with age. Over every checkpoint in range, bytes are fit by
   least squares to ``a + c * decisions + g * tick``; the growth ``g`` over the range
   is at most 10% of the median checkpoint's bytes.

Why a fit, not bytes per decision (the rule until 2026-09-29, which failed on a
world with no leak): a checkpoint is about 1.2 MB of aggregates that do not scale
with decisions plus about 2.1 KB per retained decision (seed 1, measured per
component at every checkpoint from 1,500 to 5,000 events). A stretch whose cadence
runs faster (36 checkpoints in the fourth block, 15 in the first) retains fewer
decisions (624 against 1,071), so the fixed part divided by fewer decisions raised
bytes per decision by 21% while every component but one stayed flat or shrank. The
fit separates the two; on that run the age term is negative (about -34 bytes a
tick). The one component that does grow with age, a SnapshotLearner's spent-handle
tombstones (about 6 bytes a tick, 2% of the aggregates over the range), is below
this bound and is reported separately.

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
#: The largest growth with age over the range, decisions held fixed, as a fraction of
#: the median checkpoint's bytes.
MAX_AGE_GROWTH = 0.10


def _slope(values: list[float]) -> float:
    """The least-squares slope of ``values`` against their index."""
    n = len(values)
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    return sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values)) / sxx


def _fit(columns: list[list[float]], y: list[float]) -> list[float]:
    """Least-squares coefficients of ``y`` on ``columns`` (the normal equations)."""
    n = len(columns)
    rows = [[sum(a * b for a, b in zip(columns[i], columns[j], strict=True))
             for j in range(n)]
            + [sum(a * b for a, b in zip(columns[i], y, strict=True))] for i in range(n)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda r: abs(rows[r][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        rows[i] = [v / rows[i][i] for v in rows[i]]
        for r in range(n):
            if r != i:
                rows[r] = [a - rows[r][i] * b for a, b in zip(rows[r], rows[i], strict=True)]
    return [row[n] for row in rows]


def plateau_problems(checkpoints: list[tuple[int, int, int]]) -> list[str]:
    """Why ``checkpoints`` (tick, bytes, retained decisions), each written at or after
    ``FROM``, show a leak, or [] when they show none.

    Guarantees a problem when the least-squares slope of each ``BLOCK``'s median
    retained decisions exceeds ``MAX_DECISION_SLOPE`` of the first block's per block
    (a decision leak), or when, with bytes fit to ``a + c * decisions + g * tick``, the
    age term ``g`` over the range exceeds ``MAX_AGE_GROWTH`` of the median bytes (a leak
    of anything else a checkpoint carries). Each problem names what it measured.
    """
    blocks: dict[int, list[int]] = {}
    for tick, _size, retained in checkpoints:
        blocks.setdefault((tick - FROM) // BLOCK, []).append(retained)
    decisions = [median(values) for _key, values in sorted(blocks.items())]
    ticks = [float(tick) for tick, _size, _retained in checkpoints]
    sizes = [float(size) for _tick, size, _retained in checkpoints]
    _a, per_decision, per_tick = _fit(
        [[1.0] * len(checkpoints), [float(r) for _t, _s, r in checkpoints], ticks], sizes)
    growth, level = per_tick * (max(ticks) - min(ticks)), median(sizes)
    series = (f"block median decisions {[round(d) for d in decisions]}; "
              f"{per_decision:.0f} bytes per decision, {per_tick:+.1f} bytes per tick")
    problems = []
    slope = _slope(decisions)
    if slope > MAX_DECISION_SLOPE * decisions[0]:
        problems.append(f"decision leak: slope {slope:.1f} per block exceeds "
                        f"{MAX_DECISION_SLOPE:.0%} of {decisions[0]:.0f} ({series})")
    if growth > MAX_AGE_GROWTH * level:
        problems.append(f"state leak: {growth:.0f} bytes of growth with age exceeds "
                        f"{MAX_AGE_GROWTH:.0%} of {level:.0f} ({series})")
    return problems


# Soak, not slow: 5,000 events is about eleven minutes on one core, longer than the
# whole slow tier's five-minute budget, and no shorter world reaches steady retention
# with enough 500-event blocks behind it to read a slope. Run it with
# ``uv run pytest -m soak`` before a change to what a checkpoint retains.
@pytest.mark.soak
def test_the_checkpoint_plateaus_between_1500_and_5000_events():
    rt = Runtime(load_manifest("scripted"), events=EVENTS, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1)
    checkpoints = []
    snapshot = rt._snapshot

    def measured(boundary):
        written = snapshot(boundary)
        if written and rt.ticks_consumed >= FROM:
            # The state the checkpoint just wrote: nothing changed since but the held
            # venue reads a checkpoint never carries.
            state = runtime_state(rt)
            world = len(canonical(state["fake_exchange"])) if state["fake_exchange"] else 0
            checkpoints.append((rt.ticks_consumed, len(canonical(state)) - world,
                                len(rt.queue.retained())))
        return written

    rt._snapshot = measured
    rt.run()
    assert rt.ticks_consumed == EVENTS
    assert checkpoints and checkpoints[0][0] < FROM + BLOCK
    assert checkpoints[-1][0] > EVENTS - BLOCK
    assert len({(tick - FROM) // BLOCK for tick, _s, _r in checkpoints}) == (
        EVENTS - FROM) // BLOCK
    assert not plateau_problems(checkpoints)


# --- the rule itself, on synthetic series (check tier) ---------------------------------

#: Aggregates and bytes per retained decision of the scripted world (seed 1, 2026-09-29).
FIXED, PER_DECISION = 1_200_000, 2_100
#: Block medians of two real runs, retained decisions and checkpoint bytes. a06e159
#: failed the first rule (a +/-10% band on bytes); 2026-09-29 (b8d685d, seed 1) failed
#: the second (a +/-10% band on bytes per decision: its fourth block, whose cadence ran
#: twice as fast, retained 624 decisions over the same aggregates).
A06E159_DECISIONS = [1205, 1169, 1350, 1107, 1278, 1364, 1046]
A06E159_BYTES = [4344747, 4171807, 4934851, 4052833, 4388799, 4782593, 3486974]
SEP29_DECISIONS = [1071, 963, 916, 624, 941, 836, 815]
SEP29_BYTES = [3589391, 3355999, 3183650, 2546482, 3218731, 2831044, 2914480]


def _medians(decisions, sizes):
    """One checkpoint per block, mid-block, at the block's medians."""
    return [(FROM + block * BLOCK + BLOCK // 2, size, retained)
            for block, (retained, size) in enumerate(zip(decisions, sizes, strict=True))]


def _checkpoints(decisions, fixed=lambda tick: FIXED, per_block=6):
    """Checkpoints over the range: ``per_block`` in each block, with retained decisions
    swinging around the block's value and bytes ``fixed(tick)`` plus the decisions'."""
    out = []
    for block, level in enumerate(decisions):
        for k in range(per_block):
            tick = FROM + block * BLOCK + k * BLOCK // per_block
            retained = round(level * (0.85 + 0.3 * k / (per_block - 1)))
            out.append((tick, round(fixed(tick) + PER_DECISION * retained), retained))
    return out


def test_a_steady_five_percent_growth_in_decisions_is_a_decision_leak():
    problems = plateau_problems(_checkpoints([1000 * 1.05 ** i for i in range(7)]))
    assert len(problems) == 1 and problems[0].startswith("decision leak")
    assert "bytes per decision" in problems[0]  # what it measured is reported


def test_aggregates_that_grow_a_fifth_over_the_range_are_a_state_leak():
    """About 70 bytes a tick, with decisions level: the leak the rule is for."""
    growth = 0.2 * (FIXED + PER_DECISION * 1000) / (EVENTS - FROM)
    problems = plateau_problems(_checkpoints(
        [1000] * 7, fixed=lambda tick: FIXED + growth * (tick - FROM)))
    assert len(problems) == 1 and problems[0].startswith("state leak")


def test_a_leak_hidden_by_falling_activity_is_still_a_state_leak():
    """Fewer retained decisions each block while the aggregates grow: bytes per
    decision would rise with or without the leak, the age term sees only the leak."""
    growth = 0.2 * (FIXED + PER_DECISION * 1000) / (EVENTS - FROM)
    problems = plateau_problems(_checkpoints(
        [1000 * 0.97 ** i for i in range(7)],
        fixed=lambda tick: FIXED + growth * (tick - FROM)))
    assert [p.split(":")[0] for p in problems] == ["state leak"]


@pytest.mark.parametrize(("decisions", "sizes"), [(A06E159_DECISIONS, A06E159_BYTES),
                                                  (SEP29_DECISIONS, SEP29_BYTES)],
                         ids=["a06e159", "sep29"])
def test_the_runs_that_failed_the_earlier_rules_show_no_leak(decisions, sizes):
    """Activity, not growth: each earlier rule's false alarm is no leak under this one."""
    assert plateau_problems(_medians(decisions, sizes)) == []


def test_bytes_per_decision_swing_with_activity_over_fixed_aggregates():
    """Why the second rule failed: fewer decisions over the same aggregates raise bytes
    per decision by a fifth, and the fit reads it as activity, not age."""
    checkpoints = _checkpoints(SEP29_DECISIONS)
    per_decision = [size / retained for _tick, size, retained in checkpoints]
    assert max(per_decision) > 1.2 * min(per_decision)
    assert plateau_problems(checkpoints) == []
