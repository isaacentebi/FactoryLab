"""Behavioural versions and diagnostic evidence never prescribe factory objectives."""

from collections import Counter
from math import fsum
from statistics import fmean, pvariance

from factorylab.charter.controller import CardRegion, violation
from factorylab.versioning.operator import cell_series, total_variation, transition_operator
from factorylab.versioning.series import mean


def block_distances(cells: list[tuple], k: int) -> list[float | None]:
    """Distances compare two complete adjacent trailing blocks at each window end."""
    return [
        total_variation(cells[end - 2 * k : end - k], cells[end - k : end])
        if end >= 2 * k
        else None
        for end in range(1, len(cells) + 1)
    ]


def dominant_cells(cells: list[tuple]) -> list[dict]:
    """Top-three occupancy shares break equal-count ties lexically."""
    counts = Counter(cells)
    return [
        {"cell": list(cell), "share": count / len(cells)}
        for cell, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    ]


def versions(windows: list[dict], cells: list[tuple], *, k: int, tv_threshold: float) -> list[dict]:
    """Every above-threshold detection window starts a version, without debounce.

    Boundaries are detection indices, not retroactively inferred change points.
    Inclusive spans cover every retained window once. Means omit missing values.
    """
    if not windows:
        return []
    distances = block_distances(cells, k)
    starts = [0] + [
        i for i, tv in enumerate(distances) if i > 0 and (
            windows[i]["charter_edition"] != windows[i - 1]["charter_edition"]
            or tv is not None and tv > tv_threshold
        )
    ]
    result = []
    for start, stop in zip(starts, starts[1:] + [len(windows)], strict=True):
        group = windows[start:stop]
        result.append(
            {
                "start_window": start,
                "end_window": stop - 1,
                "duration": stop - start,
                "dominant_cells": dominant_cells(cells[start:stop]),
                "mean_profile": {
                    name: mean(
                        [w["profile"].get(name) for w in group
                         if w["profile"].get(name) is not None]
                    )
                    for name in group[0]["profile"]
                },
                "charter_edition": group[0]["charter_edition"],
            }
        )
    return result


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


def diagnose(
    windows: list[dict], *, k: int,
    registration_bins: tuple[float, ...], revision_bins: tuple[float, ...],
) -> dict:
    """One causal predicate serves live correction and offline reconstruction.

    New cards need k observations to establish failure; removing a dimension
    preserves the remaining history. A region change is measured against the
    region applicable in that window. Thrash needs k actual changes and no
    compliant window, rather than volatility in a moving histogram partition.
    """
    tail = windows[-k:]
    supported = len(tail) == k
    common = set.intersection(*(set(w["regions"]) for w in tail)) if tail else set()
    fixed = cell_series(tail, sorted(common), registration_bins=registration_bins,
                        revision_bins=revision_bins)
    cells = fixed["cells"]
    same = supported and len(set(cells)) == 1
    violated = [
        {cid for cid, region in w["regions"].items()
         if w["profile"].get(cid) is not None
         and violation(CardRegion(**dict(region, card_id=cid)), w["profile"][cid]) > 0}
        for w in windows[-(k + 1):]
    ]
    failures = set.intersection(*violated[-k:]) if supported else set()
    changes = []
    recent = windows[-(k + 1):]
    for previous, current in zip(recent, recent[1:], strict=False):
        names = sorted(set(previous["regions"]) & set(current["regions"]))
        pair = cell_series([previous, current], names, registration_bins=registration_bins,
                           revision_bins=revision_bins)["cells"]
        changes.append(pair[0] != pair[1])
    frontier = frontier_evidence(tail)
    flags = {
        "stable_failure": bool(same and failures),
        # Learning death is the frontier gone (essay: extinguished or quarantined), not a
        # count of edits: stable cells, no registrations or revisions, no improvement in
        # consequence outcomes and a compliance the cards cannot vouch for.
        "learning_death": bool(same and frontier["quiet"] and not frontier["improving"]
                               and not frontier["holding"]),
        "thrash": bool(len(changes) == k and all(changes) and all(violated)),
    }
    return {
        "flags": flags, "cells": [list(c) for c in cells],
        "dimensions": fixed["dimensions"],
        "gap_bound": transition_operator(cells)["gap_bound"],
        "violated_cards": sorted(failures), "changes": changes, "frontier": frontier,
    }


def _holds(window: dict) -> bool:
    """Every card of the window is measured against a region it satisfies."""
    profile, regions = window["profile"], window["regions"]
    cards = set(regions) | {name for name in profile if name.startswith("card:")}
    return bool(cards) and all(
        name in regions and profile.get(name) is not None
        and violation(CardRegion(**dict(regions[name], card_id=name)), profile[name]) == 0
        for name in cards
    )


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
    access no window measured is unknown and is not reported as lost. Protected
    exploration buys an option: a population that can still afford to look and
    to revise, and does not, has not lost anything, so nothing is listed.
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
    """The surplus-generating frontier is gone only when the tail is quiet and flat.

    ``quiet``: no registrations and no revisions in any tail window. ``improving``:
    the consequence outcomes rise over the tail (a positive least-squares slope of
    the paid-off rate or of realized P&L; an unmeasured series never improves).
    ``holding``: every card in every tail window is measured and inside its
    region, which a charter with no measured card cannot show.
    ``uninvoked_routers`` (present when every tail window recorded the routers'
    frontier invocation): the routers that woke their seats only by exploration in
    every tail window, the frontier "no longer being invoked" (essay II.II.a).

    ``lost_access`` is the causal half the diagnosis is actually about: which of
    the affordable seat, the route to registration and the route to revision the
    tail shows gone, and the reason the organ recorded. A quiet tail with every
    access intact is a population that could look and chose not to; the flag
    still fires on the gone-frontier rule, and the record says what was lost, so
    "unchanged behaviour" is never the finding on its own.
    """
    paid_off = slope([w["profile"].get("paid_off") for w in tail])
    realized = slope([w["profile"].get("realized_pnl") for w in tail])
    invocation = {}
    if tail and all("frontier_invocation" in w for w in tail):
        # The routers' own frontier signal (ruling R9): a router whose every draw in
        # every tail window left its seats to exploration alone. Recorded as evidence
        # for the flag; wave 5 rebuilds the immune organ's predicate around it
        # (versioning P1) and this is the hook it reads.
        invocation["uninvoked_routers"] = sorted(
            set.intersection(*({row["router"] for row in w["frontier_invocation"]
                                if row["uninvoked"]} for w in tail)))
    return {
        "quiet": bool(tail) and all(
            w["profile"].get("registrations") == 0 and w["profile"].get("revision") == 0
            for w in tail
        ),
        "improving": bool(paid_off is not None and paid_off > 0
                          or realized is not None and realized > 0),
        "holding": bool(tail) and all(_holds(w) for w in tail),
        "paid_off_slope": paid_off, "realized_pnl_slope": realized,
        "lost_access": lost_access(tail),
        **invocation,
    }


def pathologies(
    windows: list[dict], cells: list[tuple], spans: list[dict], *, k: int,
    registration_bins: tuple[float, ...], revision_bins: tuple[float, ...],
) -> list[dict]:
    """Consecutive detection windows retain the same causal evidence used by the live organ."""
    evidence = [diagnose(windows[:end + 1], k=k, registration_bins=registration_bins,
                         revision_bins=revision_bins) for end in range(len(windows))]
    result = []
    for kind in ("stable_failure", "learning_death", "thrash"):
        for start, end in _runs([e["flags"][kind] for e in evidence]):
            result.append({
                "kind": kind, "start_window": start, "end_window": end,
                "evidence": {"windows": evidence[start:end + 1]},
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


def settling(
    items: list[dict],
    windows: list[dict],
    cells: list[tuple],
    *,
    k: int,
    tv_threshold: float,
) -> list[dict]:
    """Each activation compares fully post-activation blocks to k preceding closed windows.

    A window containing activation is excluded from both blocks unless activation
    is its first item. Elapsed windows count closures after activation. Missing
    baseline support or failure to cross strictly below threshold yields None.
    Activations in a dropped tail remain present with no observed settling time.
    """
    result = []
    for item in items:
        if item.get("kind") != "charter.activate":
            continue
        seq = item["seq"]
        before = [w["index"] for w in windows if w["end_seq"] < seq]
        after = [w["index"] for w in windows if w["start_seq"] >= seq]
        activation_window = next((w["index"] for w in windows if w["end_seq"] >= seq), None)
        duration = settled_at = tv = None
        if len(before) >= k and after:
            baseline = [cells[i] for i in before[-k:]]
            for end in range(after[0] + k - 1, len(windows)):
                distance = total_variation(baseline, cells[end - k + 1 : end + 1])
                if distance < tv_threshold:
                    settled_at, tv = end, distance
                    duration = end - len(before) + 1
                    break
        result.append(
            {
                "amendment_id": item["amendment_id"],
                "edition": item["edition"],
                "activation_seq": seq,
                "activation_window": activation_window,
                "windows_after_activation": duration,
                "settled_window": settled_at,
                "tv": tv,
            }
        )
    return result
