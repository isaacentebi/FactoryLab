"""Behavioural versions and pathology evidence never prescribe factory objectives.

The live immune organ and the forensic report read the same predicate
(``diagnose``) over the same live versioning (``versioning.live``): what the
organ acted on can be reconstructed from the diary, and nothing offline uses a
rule the world did not run under.
"""

from collections import Counter
from copy import deepcopy
from math import fsum
from statistics import fmean, pvariance

from factorylab.versioning import live
from factorylab.versioning.series import mean


def dominant_cells(cells: list[tuple]) -> list[dict]:
    """Top-three occupancy shares break equal-count ties lexically."""
    counts = Counter(cells)
    return [
        {"cell": list(cell), "share": count / len(cells)}
        for cell, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    ]


def slope(values: list[float | None]) -> float | None:
    """Least-squares slopes omit unsupported values but retain their time positions."""
    points = [(i, value) for i, value in enumerate(values) if value is not None]
    if len(points) < 2:
        return None
    xmean = fmean(i for i, _ in points)
    ymean = fmean(value for _, value in points)
    return fsum((i - xmean) * (value - ymean) for i, value in points) / fsum(
        (i - xmean) ** 2 for i, _ in points
    )


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """True runs are maximal, disjoint inclusive spans."""
    result = []
    start = None
    for i, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            result.append((start, i - 1))
            start = None
    return result


def diagnose(windows: list[dict], state: dict, *, k: int, tv_threshold: float,
             gap_threshold: float, registration_bins: tuple[float, ...],
             revision_bins: tuple[float, ...], held: tuple[str, ...] | list[str] = ()
             ) -> dict:
    """The three convergence pathologies at the newest window, and their evidence.

    ``windows`` are the retained windows ending with the newest; ``state`` is the
    live versioning after it (``live.advance``). Essay II.II.a:

    * **stable failure** is "a robust version with a wide spectral gap whose
      input–output distribution is failing against its input": a nonempty set of
      cards violated in every tail window that measured them (``live.
      persistent_violations``; never activity, versioning audit P3) while the
      rolling operator over those cards has a gap of at least
      ``immune.gap_threshold`` (``card_gap``: registrations and revisions, which the
      organ's raised gain invites, cannot narrow it and reset the duration). An
      unmeasured card holds its state (wave 16, second addendum, M-6): a card of the
      previous diagnosis's failing set (``held``) that no tail window measured stays
      in the failing set, so its duration is neither reset nor read as relief, and a
      card never measured never enters it. With the card gap unreadable and the
      attractor held only by such cards, the attractor holds too;
    * **thrash** is a factory that "never settles". Four signals, each priced
      (``unsettled``, the measurement the thrash price observes, is the largest):
      the current version's gap series is volatile (its mean change, above
      ``immune.tv_threshold``); the behaviour repeats with a period (``live.period``:
      "We can think of thrash as oscillation", essay II.IV.b; reads 1); two
      versions in a row, launch excepted, were abandoned before they settled and
      the current one has not (reads 1); or a configuration was refactored faster
      than the loop that corrects it closes (a recorded lifespan shorter than its
      feedback latency, reading ``1 - lifespan / latency``; "Thrash occurs in loops
      whose periods exceed the lifespan of the configurations they are trying to
      error-correct", time audit T14);
    * **learning death** is the frontier "no longer being invoked" or
      "quarantined", which leaves "a single state with no variety": one cell over
      the tail, no registration or revision in it, and the frontier gone
      (``frontier_evidence``; versioning audit P1). Card compliance never enters.
    """
    tail = windows[-k:]
    supported = len(tail) == k
    bins = {"registration_bins": registration_bins, "revision_bins": revision_bins}
    dims, tail_cells = live.cells(tail, **bins)
    failing = live.persistent_violations(tail) if supported else []
    # Missing evidence is neither failure nor relief (M-6): a failing card the whole
    # tail left unmeasured keeps its place; a card never measured never gains one.
    # A card the charter no longer carries (no region in the newest window) is gone,
    # not unmeasured.
    carried = sorted(name for name in held if supported and name not in failing
                     and name in tail[-1].get("regions", {})
                     and all(live.card_violation(w, name) is None for w in tail))
    failing = sorted([*failing, *carried])
    wide = ((state.get("card_gap") is not None and state["card_gap"] >= gap_threshold)
            or (state.get("card_gap") is None and bool(carried)))
    volatile = state.get("volatility") is not None and state["volatility"] > tv_threshold
    short_lived = [row for w in tail for row in w.get("lifespans", ())
                   if row.get("ratio") is not None and row["ratio"] < 1]
    abandoned = state.get("unsettled_run", 0) >= 2 and state.get("settled_tick") is None
    periodic = state.get("period") is not None
    unsettled = max([state.get("volatility") or 0.0, float(periodic), float(abandoned),
                     *(1.0 - row["ratio"] for row in short_lived)])
    frontier = frontier_evidence(tail)
    single = supported and len(set(tail_cells)) == 1
    flags = {
        "stable_failure": bool(failing and wide),
        "learning_death": bool(single and frontier["quiet"] and frontier["gone"]),
        "thrash": bool(supported and (volatile or periodic or abandoned or short_lived)),
    }
    return {
        "flags": flags, "cells": [list(c) for c in tail_cells], "dimensions": dims,
        "gap": state.get("gap"), "rolling_gap": state.get("rolling_gap"),
        "card_gap": state.get("card_gap"),
        "volatility": state.get("volatility"), "version": state.get("version"),
        "settled": state.get("settled_tick") is not None, "abandoned": abandoned,
        "period": state.get("period"), "unsettled": unsettled if supported else None,
        "violated_cards": failing, "short_lived": short_lived, "frontier": frontier,
        "unmeasured_held": carried,
    }


#: The three accesses whose loss is what "learning death" names (edition 3, C3):
#: the diagnosis is loss of affordable, usable access to investigation and
#: revision, never unchanged behaviour. Each is carried by the immune organ's own
#: window profile as ``access:<name>`` (1.0 present, 0.0 lost, absent unknown).
ACCESS = ("affordable_seat", "registration_route", "revision_route")
ACCESS_MEANING = {
    "affordable_seat": "no affordable seat",
    "registration_route": "no route to registration",
    "revision_route": "no route to revision",
}


def lost_access(tail: list[dict]) -> list[dict]:
    """Which access the tail shows lost, and the reason the organ recorded for it.

    An access is lost when every window of the tail measured it absent; an
    access no window measured is unknown and is not reported as lost.
    """
    result = []
    for name in ACCESS:
        values = [w["profile"].get(f"access:{name}") for w in tail]
        measured = [v for v in values if v is not None]
        if not measured or any(v for v in measured):
            continue
        why = next((w.get("access", {}).get(name) for w in reversed(tail)
                    if isinstance(w.get("access"), dict) and w["access"].get(name)), None)
        result.append({"access": name, "diagnosis": ACCESS_MEANING[name], "why": why})
    return result


def frontier_evidence(tail: list[dict]) -> dict:
    """Whether the surplus-generating frontier is still invoked, from the routers' draws.

    ``quiet``: no registration and no revision in any tail window.
    ``uninvoked_routers`` (when every tail window recorded the routers' draws): the
    frontier (mean-based) routers whose every draw in every tail window gave NOOP at
    least ``1 - gamma``, so their seats were woken by exploration alone, the
    frontier "no longer being invoked" (essay II.II.a; ruling R9, versioning U1),
    whatever the reason. ``quarantined_routers``: the frontier routers that, in
    every tail window, held every unhistoried seat they offered within the organ's
    tolerance of the exploration floor, ``(1 + immune.tv_threshold) * gamma / N``,
    while a historied seat held more than all the other arms together: newcomers
    offered only by exploration, "quarantined" from the incumbents. ``gone`` is
    either. ``improving``, the slopes and ``lost_access`` are the causal annotation,
    never the flag.
    """
    paid_off = slope([w["profile"].get("paid_off") for w in tail])
    realized = slope([w["profile"].get("realized_pnl") for w in tail])
    recorded = bool(tail) and all("frontier_invocation" in w for w in tail)
    evidence: dict = {}
    gone = False
    if recorded:
        frontier = [[row for row in w["frontier_invocation"] if not row.get("core")]
                    for w in tail]
        uninvoked = sorted(set.intersection(*({row["router"] for row in rows
                                               if row.get("uninvoked")} for rows in frontier)))
        quarantined = sorted(set.intersection(*({row["router"] for row in rows
                                                 if row.get("quarantined")}
                                                for rows in frontier)))
        offered = sum(row.get("unhistoried_offered", 0) for rows in frontier for row in rows)
        mass = fsum(row.get("unhistoried_mass", 0.0) for rows in frontier for row in rows)
        evidence = {"uninvoked_routers": uninvoked, "quarantined_routers": quarantined,
                    "unhistoried": {"offered": offered, "mass": mass}}
        gone = bool(uninvoked) or bool(quarantined)
    return {
        "quiet": bool(tail) and all(
            w["profile"].get("registrations") == 0 and w["profile"].get("revision") == 0
            for w in tail
        ),
        "gone": gone,
        "improving": bool(paid_off is not None and paid_off > 0
                          or realized is not None and realized > 0),
        "paid_off_slope": paid_off, "realized_pnl_slope": realized,
        "lost_access": lost_access(tail),
        **evidence,
    }


def organ_step(state: dict, retained: list[dict], held: list[str], *, k: int, horizon: int,
               tv_threshold: float, gap_threshold: float,
               registration_bins: tuple[float, ...],
               revision_bins: tuple[float, ...]) -> tuple[dict, list[dict], dict]:
    """One closed window through the organ: the ONE function the live organ and every
    forensic replay run, so they cannot diverge (Codex on #152).

    Guarantees, over the retained windows (the newest last): each card is read only
    from windows that measured what it measures now (``live.current_metrics``); a card
    the newest window redefined leaves the held failing set (a new metric, M-6); the
    versions advance and the window is diagnosed on those metrics; and the state's
    ``failing`` is the diagnosis's violated cards. Returns (state, events, diagnosis).
    """
    bins = {"registration_bins": registration_bins, "revision_bins": revision_bins}
    metrics = live.current_metrics(retained)
    held = [name for name in held if not live.redefined(retained, name)]
    state, events = live.advance(state, metrics, k=k, horizon=horizon,
                                 tv_threshold=tv_threshold, **bins)
    diagnosis = diagnose(metrics, state, k=k, tv_threshold=tv_threshold,
                         gap_threshold=gap_threshold, held=held, **bins)
    state["failing"] = list(diagnosis["violated_cards"])
    return state, events, diagnosis


def replay(windows: list[dict], *, k: int, horizon: int, tv_threshold: float,
           gap_threshold: float, registration_bins: tuple[float, ...],
           revision_bins: tuple[float, ...]) -> list[dict]:
    """Every window read through the live versioning and diagnosis, in order.

    Returns, per window, the versioning state after it, the events it produced
    and the diagnosis: the forensic reconstruction of what the live organ saw, by the
    organ's own step (``organ_step``) over the same retention.
    """
    state = live.fresh()
    retained: list[dict] = []
    result = []
    held: list[str] = []
    for window in windows:
        retained = [*retained, window][-live.retention(horizon, k):]
        state, events, diagnosis = organ_step(
            state, retained, held, k=k, horizon=horizon, tv_threshold=tv_threshold,
            gap_threshold=gap_threshold, registration_bins=registration_bins,
            revision_bins=revision_bins)
        held = diagnosis["violated_cards"]
        result.append({"state": deepcopy(state), "events": events, "diagnosis": diagnosis})
    return result


def versions(windows: list[dict], readings: list[dict], *, gap_threshold: float,
             registration_bins: tuple[float, ...],
             revision_bins: tuple[float, ...]) -> list[dict]:
    """The version spans the live versioning opened, each with its own operator gap.

    Boundaries are the windows a version opened at (detection indices, never
    inferred change points). Each span's gap is the operator counted over the
    whole span (versioning audit M2) and it is ``durable`` when that gap is at
    least ``gap_threshold``. Means omit missing values.
    """
    if not windows:
        return []
    bins = {"registration_bins": registration_bins, "revision_bins": revision_bins}
    starts = [i for i, reading in enumerate(readings)
              if any(e["kind"] == "boundary" for e in reading["events"])]
    starts = [0, *[s for s in starts if s > 0]]
    causes = {i: next((e["cause"] for e in readings[i]["events"] if e["kind"] == "boundary"),
                      "launch") for i in starts}
    result = []
    for start, stop in zip(starts, [*starts[1:], len(windows)], strict=True):
        group = windows[start:stop]
        span_gap = live.gap(group, **bins)
        settled = next((e["ticks"] for r in readings[start:stop] for e in r["events"]
                        if e["kind"] == "settled" and e["settled"]), None)
        result.append({
            "start_window": start, "end_window": stop - 1, "duration": stop - start,
            "cause": causes[start],
            "dominant_cells": dominant_cells(live.cells(group, **bins)[1]),
            "gap": span_gap, "durable": span_gap is not None and span_gap >= gap_threshold,
            "settling_ticks": settled,
            "mean_profile": {
                name: mean([w["profile"].get(name) for w in group
                            if w["profile"].get(name) is not None])
                for name in group[0]["profile"]
            },
            "charter_edition": group[0]["charter_edition"],
        })
    return result


def pathologies(windows: list[dict], readings: list[dict], spans: list[dict]) -> list[dict]:
    """Consecutive flagged windows, with the evidence the live organ read at each."""
    result = []
    for kind in ("stable_failure", "learning_death", "thrash"):
        flags = [r["diagnosis"]["flags"][kind] for r in readings]
        for start, end in _runs(flags):
            result.append({
                "kind": kind, "start_window": start, "end_window": end,
                "evidence": {"windows": [r["diagnosis"] for r in readings[start:end + 1]]},
            })
    # This retrospective signal remains a separate diagnosis; it does not change
    # the three convergence predicates or infer consequence from a raw verdict.
    for span in spans:
        start, end = span["start_window"], span["end_window"]
        group = windows[start:end + 1]
        consequence = ("forecast_skill" if any(
            w["profile"].get("forecast_skill") is not None for w in group
        ) else "consequence")
        verdict_slope = slope([w["profile"].get("verdict") for w in group])
        outcome_slope = slope([w["profile"].get(consequence) for w in group])
        if (verdict_slope is not None and outcome_slope is not None
                and verdict_slope > 0 and outcome_slope < 0):
            result.append({
                "kind": "overfitting_divergence", "start_window": start, "end_window": end,
                "evidence": {"verdict_slope": verdict_slope, "outcome_series": consequence,
                             "outcome_slope": outcome_slope},
            })
    return sorted(result, key=lambda item: (item["start_window"], item["end_window"], item["kind"]))


def settling(readings: list[dict]) -> list[dict]:
    """Every version's settling reading: ticks to settle, or a superseded version's age.

    Essay II.IV.c: "measure how long the output distribution takes to return to a
    settled distribution". ``settled`` False is a lower bound: the version was
    superseded first. A version still open and unsettled has no reading.
    """
    return [{"version": e["version"], "cause": e["cause"], "window": e["window"],
             "settled": e["settled"], "ticks": e["ticks"]}
            for r in readings for e in r["events"] if e["kind"] == "settled"]


def early_warnings(
    windows: list[dict], spans: list[dict], cards: list[str], *, k: int
) -> list[dict]:
    """Population variance and centered lag-one ACF require complete supported spans.

    ACF = sum((x[t]-mean)*(x[t-1]-mean))/sum((x[t]-mean)**2).
    Constant or singleton spans have undefined ACF. Missing values are never
    compressed across time. Each endpoint/series has k, 2k and 4k observations.
    """
    series = ["verdict", "conformity", "consequence", *cards, "balance", "disagreement"]
    ends = sorted(
        {span["end_window"] for span in spans} | ({len(windows) - 1} if windows else set())
    )
    result = []
    for end in ends:
        signals = {}
        for name in series:
            scales = []
            for length in (k, 2 * k, 4 * k):
                values = [
                    w["profile"].get(name) for w in windows[max(0, end + 1 - length) : end + 1]
                ]
                variance = autocorrelation = None
                count = sum(value is not None for value in values)
                if len(values) == length and count == length:
                    variance = pvariance(values)
                    center = fmean(values)
                    denom = fsum((value - center) ** 2 for value in values)
                    if denom > 0:
                        autocorrelation = (
                            fsum(
                                (a - center) * (b - center)
                                for a, b in zip(values, values[1:], strict=False)
                            )
                            / denom
                        )
                scales.append(
                    {
                        "span": length,
                        "supported": count,
                        "variance": variance,
                        "autocorrelation": autocorrelation,
                    }
                )
            signals[name] = scales
        result.append({"end_window": end, "series": signals})
    return result

