"""Behavioural versions and diagnostic evidence never prescribe factory objectives."""

from collections import Counter
from math import fsum
from statistics import fmean, pvariance

from factorylab.versioning.operator import total_variation, transition_operator
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
        i for i, tv in enumerate(distances) if i > 0 and tv is not None and tv > tv_threshold
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
                        [w["profile"][name] for w in group if w["profile"][name] is not None]
                    )
                    for name in group[0]["profile"]
                },
                "charter_edition": group[0]["charter_edition"],
            }
        )
    return result


def violation(region: dict, value: float) -> float:
    """Inclusive max/min/band bounds match PriceController's normalized distance."""
    distance = 0.0
    if region["kind"] in ("min", "band") and value < region["lo"]:
        distance = region["lo"] - value
    elif region["kind"] in ("max", "band") and value > region["hi"]:
        distance = value - region["hi"]
    return distance / region["scale"]


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


def pathologies(
    windows: list[dict],
    cells: list[tuple],
    spans: list[dict],
    *,
    k: int,
    tv_threshold: float,
    gap_threshold: float,
) -> list[dict]:
    """Flags retain their numeric evidence and may overlap; none is a causal verdict.

    Stable failure requires a violating observed card in the most occupied cell
    and the span's transition bound. Learning death uses maximal same-cell runs.
    Thrash means consecutive changes (revisits allowed) with sustained block TV;
    each reported window must have both a changed predecessor and supported TV.
    """
    result = []
    for span in spans:
        start, end = span["start_window"], span["end_window"]
        group = windows[start : end + 1]
        bound = transition_operator(cells[start : end + 1])["gap_bound"]
        dominant = tuple(span["dominant_cells"][0]["cell"])
        violations = []
        for i in range(start, end + 1):
            if cells[i] != dominant:
                continue
            for card, region in sorted(windows[i]["regions"].items()):
                value = windows[i]["profile"].get(card)
                if value is not None and (amount := violation(region, value)) > 0:
                    violations.append(
                        {
                            "window": i,
                            "card": card,
                            "value": value,
                            "region": dict(region),
                            "violation": amount,
                        }
                    )
        if span["duration"] >= k and bound is not None and bound >= gap_threshold and violations:
            result.append(
                {
                    "kind": "stable_failure",
                    "start_window": start,
                    "end_window": end,
                    "evidence": {
                        "gap_bound": bound,
                        "dominant_cell": list(dominant),
                        "violations": violations,
                    },
                }
            )
        consequence = (
            "forecast_skill"
            if any(w["profile"].get("forecast_skill") is not None for w in group)
            else "consequence"
        )
        verdict_slope = slope([w["profile"]["verdict"] for w in group])
        outcome_slope = slope([w["profile"].get(consequence) for w in group])
        if verdict_slope is not None and outcome_slope is not None:
            if verdict_slope > 0 and outcome_slope < 0:
                result.append(
                    {
                        "kind": "overfitting_divergence",
                        "start_window": start,
                        "end_window": end,
                        "evidence": {
                            "verdict_slope": verdict_slope,
                            "outcome_series": consequence,
                            "outcome_slope": outcome_slope,
                        },
                    }
                )
    start = 0
    while start < len(windows):
        end = start
        while end + 1 < len(windows) and cells[end + 1] == cells[start]:
            end += 1
        quiet = [w["profile"]["registrations"] == 0 for w in windows[start : end + 1]]
        for left, right in _runs(quiet):
            if right - left + 1 >= k:
                result.append(
                    {
                        "kind": "learning_death",
                        "start_window": start + left,
                        "end_window": start + right,
                        "evidence": {
                            "cell": list(cells[start]),
                            "registrations": 0.0,
                            "duration": right - left + 1,
                        },
                    }
                )
        start = end + 1
    distances = block_distances(cells, k)
    changing = [
        i > 0 and cells[i] != cells[i - 1] and tv is not None and tv > tv_threshold
        for i, tv in enumerate(distances)
    ]
    for start, end in _runs(changing):
        if end - start + 1 >= k:
            result.append(
                {
                    "kind": "thrash",
                    "start_window": start,
                    "end_window": end,
                    "evidence": {
                        "trailing_tv": distances[start : end + 1],
                        "changes": end - start + 1,
                    },
                }
            )
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
