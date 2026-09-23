"""Live behavioural versions: the rolling operator, boundaries, a gap per version, settling.

Essay II.II: a factory is versioned "by what it does ... and specifically what it
does now". Its behaviour is partitioned into cells, the operator counts "how often
behavior in one cell leads to behavior in another", and its spectral gap is "a
readout of whether versioning is happening at all". "Versioning the superdark
factory is an active process" (versioning audit M1), so this module keeps that
reading while a world is alive, window by window, and it is the same code the
forensic report replays over a dead world's diary (``report.summary``).

What it keeps (versioning audit M1, M2, C3, P6):

* **Cells** are the region-relative bins of the charter's cards and the activity
  bins, over the dimensions *every* window being compared supports: an
  unsupported reading is missing, never a coordinate of its own, so a card that
  becomes measurable does not open a version.
* **The rolling operator** is counted over the retained horizon of windows
  (``timing.min_ratio × immune.k``); its gap (``observed_gap``) says whether one
  attractor holds the recent behaviour, and the same operator over the cards alone
  (``card_gap``) whether one attractor holds the charter's metrics.
* **Versions.** A charter edition change or a change of the world's terms (its
  published surfaces and prices) opens a version at once: "If a revision to the
  input at the level of the charter happens, the version has changed. If an
  external force changes the terms ... the version has changed." Behaviour opens
  one when the last k windows differ from the rest of the current version by more
  than ``immune.tv_threshold`` plus the sampling allowance of a k-window sample
  (``shift``) at k consecutive closes, and never within 2k windows of the last
  boundary.
* **A gap per version**, over the version's retained windows once it has 2k of them,
  and the series of those readings inside the version, whose volatility is one
  thrash signal; **periodicity** over the retained windows (``period``) is another.
* **Settling time**: the ticks from a version's opening to the first close, 2k
  windows or more into it, at which the last k windows differ from the rest of the
  version by no more than ``tv_threshold`` plus that allowance
  (essay II.IV.c: "how long the output distribution takes to return to a settled
  distribution"). A version superseded before it settles leaves its age as a
  lower bound.

Pure: nothing here imports the runtime, and the state is plain data a checkpoint
carries.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from copy import deepcopy
from math import fsum, sqrt

from factorylab.charter.controller import CardRegion, violation
from factorylab.versioning.operator import gap_from_counts, observed_gap, total_variation

#: The activity dimensions every cell carries beside the cards, when supported.
ACTIVITY = ("registrations", "revision")


def _card_value(window: dict, name: str) -> float | None:
    """A card's reading in a window, or None when unsupported (no value or no region)."""
    value = window["profile"].get(name)
    return value if value is not None and name in window.get("regions", {}) else None


def card_violation(window: dict, name: str) -> float | None:
    """The card's region-relative violation in this window, or None when unsupported."""
    value = _card_value(window, name)
    if value is None:
        return None
    return violation(CardRegion(**dict(window["regions"][name], card_id=name)), value)


def dimensions(windows: list[dict], *, activity: bool = True) -> list[str]:
    """The dimensions every one of ``windows`` supports: cards first, then activity."""
    if not windows:
        return []
    cards = set.intersection(*({name for name in w.get("regions", {})
                                if _card_value(w, name) is not None} for w in windows))
    counted = [name for name in ACTIVITY if activity
               and all(w["profile"].get(name) is not None for w in windows)]
    return sorted(cards) + counted


def cells(windows: list[dict], *, registration_bins: tuple[float, ...],
          revision_bins: tuple[float, ...],
          activity: bool = True) -> tuple[list[str], list[tuple]]:
    """Each window's cell over the dimensions all of them support.

    A card's coordinate is its region-relative bin (0 inside, 1 up to one scale
    unit outside, 2 beyond); activity uses the manifest's cuts, a value equal to a
    cut staying in the lower bin.
    """
    dims = dimensions(windows, activity=activity)
    cuts = {"registrations": list(registration_bins), "revision": list(revision_bins)}
    result = []
    for window in windows:
        cell = []
        for name in dims:
            if name in cuts:
                cell.append(bisect_left(cuts[name], window["profile"][name]))
            else:
                amount = card_violation(window, name)
                cell.append(0 if amount == 0 else 1 if amount <= 1 else 2)
        result.append(tuple(cell))
    return dims, result


def gap(windows: list[dict], *, activity: bool = True, **bins) -> float | None:
    """The operator's gap bound over these windows' cells, or None without a transition.

    ``activity`` False reads the cards alone: the charter's metrics, what an attractor
    that fails its input is failing on (versioning audit P3).
    """
    return observed_gap(cells(windows, activity=activity, **bins)[1])


def block_distance(windows: list[dict], k: int, **bins) -> float | None:
    """TV between the last k windows and the k before them, over their shared support."""
    if len(windows) < 2 * k:
        return None
    _dims, series = cells(windows[-2 * k:], **bins)
    return total_variation(series[:k], series[k:])


def shift(windows: list[dict], k: int, **bins) -> dict | None:
    """How far the last k windows moved from the version before them, beyond chance.

    Guarantees ``tv``, the TV between the last k windows' cells and every earlier
    window's of ``windows`` (over their shared support), and ``noise``, the
    sampling allowance ``1/2 * sum_i sqrt(q_i (1 - q_i) (1/k + 1/n))`` over the
    earlier windows' occupancy q (n of them): the TV two samples of an unchanged
    distribution, of k and of n windows, reach anyway (the #134 review: stationary
    random behaviour kept opening versions). A shift is a change of behaviour only
    when ``tv`` exceeds ``tv_threshold`` plus that allowance. None with fewer than
    2k windows.
    """
    if len(windows) < 2 * k:
        return None
    _dims, series = cells(windows, **bins)
    base, block = series[:-k], series[-k:]
    counts = Counter(base)
    noise = 0.5 * fsum(sqrt((n / len(base)) * (1 - n / len(base)) * (1 / k + 1 / len(base)))
                       for n in counts.values())
    return {"tv": total_variation(base, block), "noise": noise}


def volatility(series: list[float | None]) -> float | None:
    """Mean absolute change between successive defined gap readings, or None.

    A reading is undefined while a version has no transition yet; the change is
    taken across it, so a version that keeps being cut before it holds shows as
    the jump between the gaps on either side (essay II.II.a: thrash is a gap that
    is "continuously unsettled").
    """
    values = [value for value in series if value is not None]
    if len(values) < 2:
        return None
    return fsum(abs(b - a) for a, b in zip(values, values[1:], strict=False)) / (len(values) - 1)


def period(windows: list[dict], cycles: int, **bins) -> int | None:
    """The shortest period, at least 2 and at most ``cycles``, the last windows repeat.

    Essay II.IV.b: "We can think of thrash as oscillation ... the factory revisits
    the same regions of configuration space on a regular cadence and learns nothing
    new on each pass". Guarantees a period p is returned only when the last
    ``cycles * p`` windows' cells (over their shared support) each equal the cell p
    windows before, and the cycle visits at least two cells: a constant sequence has
    no period. ``cycles`` is the cascade ratio, so the organ names an oscillation only
    after it has repeated as often as an outer loop needs to separate from it, and a
    k of any size cannot hide one. None when no period fits the retained windows.
    """
    for p in range(2, cycles + 1):
        n = cycles * p
        if len(windows) < n:
            break
        series = cells(windows[-n:], **bins)[1]
        if len(set(series[-p:])) >= 2 and all(
                series[i] == series[i - p] for i in range(p, n)):
            return p
    return None


def _count(state: dict, span: list[dict], **bins) -> dict:
    """The current version's transition and occupancy counts, over its whole life.

    Guarantees the counts cover every window the version has had while its cells'
    dimensions stay the same; a change of dimensions (a card gaining or losing
    support) recounts from the retained windows, the only ones whose cells are
    comparable. Cells are keyed as text so the counts are plain data.
    """
    dims, series = cells(span, **bins)
    keys = [",".join(map(str, cell)) for cell in series]
    book = state.get("book")
    if not book or book["version"] != state["version"] or book["dims"] != dims:
        book = {"version": state["version"], "dims": dims, "windows": len(keys),
                "occupancy": dict(Counter(keys)), "last": keys[-1],
                "transitions": dict(Counter(f"{a}|{b}" for a, b in zip(keys, keys[1:],
                                                                         strict=False)))}
    else:
        pair = f"{book['last']}|{keys[-1]}"
        book["transitions"][pair] = book["transitions"].get(pair, 0) + 1
        book["occupancy"][keys[-1]] = book["occupancy"].get(keys[-1], 0) + 1
        book["last"], book["windows"] = keys[-1], book["windows"] + 1
    state["book"] = book
    return book


def retention(horizon: int, k: int) -> int:
    """How many closed windows the organ keeps: the operator's horizon, or ``cycles²``.

    ``cycles = horizon // k`` is the cascade ratio; a period up to it is named only
    after ``cycles`` repeats (``period``), so a small k cannot hide an oscillation.
    """
    cycles = max(2, horizon // k)
    return max(horizon, cycles * cycles)


def fresh() -> dict:
    """The state before the first window: no version is open yet."""
    return {"version": 0, "start_window": None, "start_tick": None, "cause": None,
            "settled_tick": None, "gap": None, "rolling_gap": None, "card_gap": None,
            "gaps": [],
            "volatility": None, "closed": [], "boundaries": 0, "unsettled_run": 0,
            "period": None, "shifting": 0}


def tick(window: dict) -> int:
    """The world tick a window closed at (its index, for a diary that predates ticks)."""
    return int(window.get("tick", window["index"]))


def advance(state: dict, windows: list[dict], *, k: int, horizon: int, tv_threshold: float,
            registration_bins: tuple[float, ...],
            revision_bins: tuple[float, ...]) -> tuple[dict, list[dict]]:
    """Read the newest closed window (the last of ``windows``) into the live versions.

    ``windows`` are the retained closed windows, oldest first: the rolling operator
    reads the last ``horizon`` of them, and periodicity (``period``) up to
    ``cycles²`` of them, ``cycles = horizon // k`` (the cascade ratio). Returns the
    next state and what happened at this window: a ``boundary`` (the version that
    closed and why the next opened) and a ``settled`` reading (a version's settling
    time, or a superseded version's age as a lower bound, ``settled`` False). The
    state keeps at most ``horizon`` closed versions.

    Guarantees, the #134 review: a version's gap is read only once it has 2k
    windows (a whole operator's worth: two blocks), and its gap series starts empty
    at every boundary, so no volatility is carried from one version into the next;
    settling compares two complete blocks inside the version.
    """
    bins = {"registration_bins": registration_bins, "revision_bins": revision_bins}
    cycles = max(2, horizon // k)
    retained = windows
    windows = windows[-horizon:]
    state = deepcopy(state)
    window = windows[-1]
    now = tick(window)
    events: list[dict] = []
    cause = None
    if state["start_window"] is None:
        cause = "launch"
    else:
        previous = windows[-2] if len(windows) >= 2 else None
        span = [w for w in windows if w["index"] >= state["start_window"]]
        if previous is not None and previous["charter_edition"] != window["charter_edition"]:
            cause = "charter"
        elif (previous is not None and previous.get("terms") is not None
              and window.get("terms") is not None and previous["terms"] != window["terms"]):
            cause = "terms"
        elif len(span) >= 2 * k:
            moved = shift(span, k, **bins)
            if moved is not None and moved["tv"] > tv_threshold + moved["noise"]:
                # Debounced: a change of behaviour is one that persists, beyond chance,
                # at k consecutive closes; a single noisy block is not a new version.
                state["shifting"] = state.get("shifting", 0) + 1
                if state["shifting"] >= k:
                    cause = "behaviour"
            else:
                state["shifting"] = 0
    if cause is not None:
        if state["start_window"] is not None:
            closed = {"version": state["version"], "start_window": state["start_window"],
                      "end_window": windows[-2]["index"] if len(windows) >= 2 else None,
                      "start_tick": state["start_tick"], "end_tick": now,
                      "cause": state["cause"], "gap": state["gap"],
                      "settling_ticks": (None if state["settled_tick"] is None
                                         else state["settled_tick"] - state["start_tick"])}
            state["closed"] = [*state["closed"], closed][-horizon:]
            events.append({"kind": "boundary", "closed": closed, "cause": cause,
                           "window": window["index"], "tick": now,
                           "version": state["version"] + 1})
            if state["settled_tick"] is None:
                events.append({"kind": "settled", "version": state["version"],
                               "cause": state["cause"], "settled": False,
                               "ticks": now - state["start_tick"], "window": window["index"]})
                # Versions abandoned before they settled, one after another: the factory
                # keeps moving on without ever holding a state (essay II.II.a, thrash
                # "never settles"). One such version is a transition, not thrash, and
                # the launch version is the world's warm-up, never counted.
                if state["cause"] != "launch":
                    state["unsettled_run"] = state.get("unsettled_run", 0) + 1
            state["boundaries"] += 1
        state.update(version=state["version"] + 1, start_window=window["index"],
                     start_tick=now, cause=cause, settled_tick=None, gaps=[], shifting=0)
    span = [w for w in windows if w["index"] >= state["start_window"]]
    if state["settled_tick"] is None and len(span) >= 2 * k:
        moved = shift(span, k, **bins)
        distance = moved["tv"] if moved is not None else None
        if (moved is not None and not state.get("shifting")
                and moved["tv"] <= tv_threshold + moved["noise"]):
            state["settled_tick"] = now
            state["unsettled_run"] = 0
            events.append({"kind": "settled", "version": state["version"],
                           "cause": state["cause"], "settled": True,
                           "ticks": now - state["start_tick"], "window": window["index"],
                           "tv": distance})
    book = _count(state, span, **bins)
    # A version's operator is counted over its whole life (``_count``) and read once
    # it has two blocks of k windows; a younger version has no gap reading yet, so
    # its warm-up cannot read as volatility, and a stationary one's reading converges.
    state["gap"] = (gap_from_counts({tuple(pair.split("|")): n
                                     for pair, n in book["transitions"].items()},
                                    book["occupancy"])
                    if book["windows"] >= 2 * k else None)
    state["rolling_gap"] = gap(windows, **bins)
    # The same rolling operator over the cards alone: whether the charter's metrics sit
    # in one attractor, whatever the activity around them (versioning audit P3).
    state["card_gap"] = gap(windows, activity=False, **bins)
    if state["gap"] is not None:
        state["gaps"] = [*state.get("gaps", []), state["gap"]][-2 * k:]
    state["volatility"] = volatility(state.get("gaps", []))
    state["period"] = period(retained, cycles, **bins)
    return state, events


def persistent_violations(tail: list[dict]) -> list[str]:
    """Cards violated in every tail window that measured them, measured at least once.

    The failing attractor is this set (versioning audit P3): activity dimensions
    never enter it, so a registration cannot reset its duration, and a window that
    did not measure a card is missing evidence, not compliance.
    """
    names = sorted({name for w in tail for name in w.get("regions", {})})
    result = []
    for name in names:
        readings = [card_violation(w, name) for w in tail]
        measured = [v for v in readings if v is not None]
        if measured and all(v > 0 for v in measured):
            result.append(name)
    return result
