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
   checkpoint grows neither with age nor with the decisions the world has completed.
   Over every checkpoint in range, bytes are fit by least squares to ``a + c *
   decisions + g * tick`` and to ``a + c * decisions + g * completed``, with the cost
   of a retained decision ``c`` bounded outside the fit (never negative, never above
   the first block's bytes per retained decision); the growth ``g`` over each range is
   at most 10% of the median checkpoint's bytes. Unbounded, ``c`` could go negative and
   explain away a leak left by every completed decision as retention falls (Sol on
   #179: 7.55 MB leaked, read as no growth).

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
from factorylab.runtime.resume import durable_state, runtime_state
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
    """Least-squares coefficients of ``y`` on ``columns``, rank-safe.

    Columns are taken in order, each orthogonalised against those kept (modified
    Gram-Schmidt); one whose remainder is negligible, a constant (the intercept aside)
    or a combination of earlier ones, is dropped with coefficient 0, and the kept
    columns are solved exactly on that basis. Guarantees an answer for any series:
    dependent columns (retention falling as the world ages) never make it singular.
    """
    n = len(columns)
    basis: list[list[float]] = []
    triangle: list[list[float]] = []  # R of the thin QR, over the kept columns
    kept: list[int] = []
    for j, column in enumerate(columns):
        rest = list(column)
        weights = []
        for q in basis:
            w = sum(a * b for a, b in zip(q, rest, strict=True))
            weights.append(w)
            rest = [a - w * b for a, b in zip(rest, q, strict=True)]
        norm = sum(a * a for a in rest) ** 0.5
        scale = sum(a * a for a in column) ** 0.5
        if norm <= 1e-9 * scale or norm == 0.0:
            continue
        basis.append([a / norm for a in rest])
        triangle.append([*weights, norm])
        kept.append(j)
    projected = [sum(a * b for a, b in zip(q, y, strict=True)) for q in basis]
    solved = [0.0] * len(kept)
    for i in reversed(range(len(kept))):  # back-substitute R · b = Qᵀy
        solved[i] = (projected[i] - sum(triangle[k][i] * solved[k]
                                        for k in range(i + 1, len(kept)))) / triangle[i][i]
    coefficients = [0.0] * n
    for index, value in zip(kept, solved, strict=True):
        coefficients[index] = value
    return coefficients


def _varies(values: list[float]) -> bool:
    return max(values) > min(values)


def _growth(retained: list[float], driver: list[float], sizes: list[float],
            per_decision_max: float) -> tuple[float, float]:
    """(bytes per retained decision, bytes of growth along ``driver`` over its range),
    from ``bytes ~ a + c * retained + g * driver``.

    The decision term is bounded to ``[0, per_decision_max]``, a bound read outside the
    fit: a retained decision costs no negative bytes, and none more than the first
    block's whole checkpoint per retained decision. Unbounded, a fit can explain a leak
    that grows as retained decisions fall (one left behind by every completed decision)
    with a negative per-decision cost and read no growth at all. Where the bound binds,
    the term is fixed at it and the rest refit. A column that does not vary, or depends
    on another (retention falling exactly as the world ages), is dropped by the rank-safe
    fit (``_fit``), and the bound still applies.
    """
    ones = [1.0] * len(sizes)
    if not _varies(driver):
        return 0.0, 0.0
    _a, per_decision, per_step = _fit([ones, retained, driver], sizes)
    if not 0.0 <= per_decision <= per_decision_max:
        per_decision = min(max(per_decision, 0.0), per_decision_max)
        rest = [y - per_decision * r for y, r in zip(sizes, retained, strict=True)]
        _a, per_step = _fit([ones, driver], rest)
    return per_decision, per_step * (max(driver) - min(driver))


def plateau_problems(checkpoints: list[tuple]) -> list[str]:
    """Why ``checkpoints`` (tick, bytes, retained decisions[, completed decisions so far]),
    each written at or after ``FROM``, show a leak, or [] when they show none.

    Guarantees a problem when the least-squares slope of each ``BLOCK``'s median
    retained decisions exceeds ``MAX_DECISION_SLOPE`` of the first block's per block
    (a decision leak), or when bytes, fit to ``a + c * retained + g * tick`` and, given
    completed decisions, to ``a + c * retained + g * completed`` (``_growth``: ``c``
    bounded outside the fit), grow along either by more than ``MAX_AGE_GROWTH`` of the
    median bytes over its range (a leak of anything else a checkpoint carries, with the
    world's age or with the decisions it has completed). Each problem names what it
    measured.
    """
    blocks: dict[int, list[int]] = {}
    for row in checkpoints:
        blocks.setdefault((row[0] - FROM) // BLOCK, []).append(row)
    first = sorted(blocks.items())[0][1]
    decisions = [median(row[2] for row in rows) for _key, rows in sorted(blocks.items())]
    per_decision_max = median(row[1] / max(1, row[2]) for row in first)
    sizes = [float(row[1]) for row in checkpoints]
    retained = [float(row[2]) for row in checkpoints]
    drivers = {"age": [float(row[0]) for row in checkpoints]}
    if all(len(row) > 3 for row in checkpoints):
        drivers["completed decisions"] = [float(row[3]) for row in checkpoints]
    level = median(sizes)
    problems = []
    slope = _slope(decisions)
    growths = {name: _growth(retained, values, sizes, per_decision_max)
               for name, values in drivers.items()}
    series = (f"block median decisions {[round(d) for d in decisions]}; "
              + "; ".join(f"{c:.0f} bytes per decision and {g:+.0f} bytes with {name}"
                          for name, (c, g) in growths.items()))
    if slope > MAX_DECISION_SLOPE * decisions[0]:
        problems.append(f"decision leak: slope {slope:.1f} per block exceeds "
                        f"{MAX_DECISION_SLOPE:.0%} of {decisions[0]:.0f} ({series})")
    for name, (_c, growth) in growths.items():
        if growth > MAX_AGE_GROWTH * level:
            problems.append(f"state leak: {growth:.0f} bytes of growth with {name} "
                            f"exceeds {MAX_AGE_GROWTH:.0%} of {level:.0f} ({series})")
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
            state = durable_state(runtime_state(rt))  # the bytes the diary carries
            world = len(canonical(state["fake_exchange"])) if state["fake_exchange"] else 0
            retained = len(rt.queue.retained())
            completed = (sum(rt.queue.released_counts().values()) + retained
                         - len(rt.queue.outstanding()))
            checkpoints.append((rt.ticks_consumed, len(canonical(state)) - world,
                                retained, completed))
        return written

    rt._snapshot = measured
    rt.run()
    assert rt.ticks_consumed == EVENTS
    assert checkpoints and checkpoints[0][0] < FROM + BLOCK
    assert checkpoints[-1][0] > EVENTS - BLOCK
    assert len({(row[0] - FROM) // BLOCK for row in checkpoints}) == (
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


def test_a_leak_left_by_every_completed_decision_is_not_explained_away():
    """Sol's counterexample on #179: outstanding decisions fall from 1,000 to 849 while
    each completed decision leaves 50 KB behind, 7.55 MB in all (3.3 MB -> 10.5 MB). An
    unbounded fit took it as a negative cost per retained decision and read no growth."""
    checkpoints = []
    for i in range(152):
        retained = 1000 - i
        tick = FROM + i * (EVENTS - FROM) // 151
        size = FIXED + PER_DECISION * retained + 50_000 * (1000 - retained)
        checkpoints.append((tick, size, retained, 1000 - retained))
    problems = plateau_problems(checkpoints)
    assert [p.split(":")[0] for p in problems] == ["state leak", "state leak"]
    assert "with age" in problems[0] and "with completed decisions" in problems[1]
    # Without the completed count the bounded age fit still reads the leak.
    assert plateau_problems([row[:3] for row in checkpoints])


def test_a_perfectly_steady_plateau_is_no_leak_and_no_error():
    """Constant bytes and constant retention leave the decision column constant: it is
    left out of the fit, not a singular system (Sol on #179: ZeroDivisionError)."""
    checkpoints = [(FROM + 100 * i, 3_300_000, 1000, 5 * i) for i in range(35)]
    assert plateau_problems(checkpoints) == []


@pytest.mark.parametrize("leak", [0, 20_000], ids=["steady", "leaking"])
def test_retention_that_falls_exactly_as_the_world_ages_is_fit_not_a_crash(leak):
    """Sol's re-review of #179: 35 checkpoints whose retention falls one decision each as
    age and completions rise, so the three columns are dependent though each varies.
    The fit drops what depends on what came before it and keeps the bound: steady bytes
    are no leak, and bytes that grow 20 KB a checkpoint are one."""
    checkpoints = [(FROM + 100 * i, 3_300_000 + leak * i, 1000 - i, 5 * i)
                   for i in range(35)]
    problems = plateau_problems(checkpoints)
    if leak:
        assert problems and all(p.startswith("state leak") for p in problems)
    else:
        assert problems == []


def test_bytes_per_decision_swing_with_activity_over_fixed_aggregates():
    """Why the second rule failed: fewer decisions over the same aggregates raise bytes
    per decision by a fifth, and the fit reads it as activity, not age."""
    checkpoints = _checkpoints(SEP29_DECISIONS)
    per_decision = [size / retained for _tick, size, retained in checkpoints]
    assert max(per_decision) > 1.2 * min(per_decision)
    assert plateau_problems(checkpoints) == []
