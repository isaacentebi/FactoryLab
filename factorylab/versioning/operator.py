"""Empirical cell transitions have an explicit Dobrushin spectral-gap bound."""

from bisect import bisect_left
from collections import Counter
from math import floor, fsum, isfinite

from factorylab.versioning.series import CHANNELS


def total_variation(left: list[tuple], right: list[tuple]) -> float | None:
    """Return histogram TV in [0, 1], or None if either sample is empty."""
    if not left or not right:
        return None
    a, b = Counter(left), Counter(right)
    return 0.5 * fsum(abs(a[cell] / len(left) - b[cell] / len(right)) for cell in sorted(a | b))


def contraction(matrix: list[list[float]]) -> dict:
    """Return delta and gap_bound = 1-delta for a stochastic square matrix.

    Dobrushin delta = max(row-pair L1 distance)/2 bounds the second eigenvalue
    modulus from above. gap_bound is therefore a lower bound on the spectral
    gap, not an exact eigensolution or a proof of observed mixing. A singleton
    matrix has delta zero by convention; an empty matrix has no bound.
    """
    n = len(matrix)
    for row in matrix:
        if len(row) != n or any(not isfinite(p) or p < 0 for p in row):
            raise ValueError("operator must be a finite nonnegative square matrix")
        if abs(fsum(row) - 1.0) > 1e-12:
            raise ValueError("operator rows must sum to one")
    if not n:
        return {"delta": None, "gap_bound": None}
    delta = min(
        1.0,
        max(
            (
                0.5 * fsum(abs(a - b) for a, b in zip(left, right, strict=True))
                for i, left in enumerate(matrix)
                for right in matrix[i + 1 :]
            ),
            default=0.0,
        ),
    )
    return {"delta": delta, "gap_bound": 1.0 - delta}


def transition_operator(cells: list[tuple]) -> dict:
    """Rows follow lexical occupied-cell order, with self-loops for unobserved exits.

    Fewer than two windows provide no transition evidence and hence no bound.
    Mixing compares floor(n/2) initial windows with the remaining windows.
    Cells with unobserved exits are explicitly listed so imputation is visible.
    """
    occupied = sorted(set(cells))
    indices = {cell: i for i, cell in enumerate(occupied)}
    counts = [[0] * len(occupied) for _ in occupied]
    for left, right in zip(cells, cells[1:], strict=False):
        counts[indices[left]][indices[right]] += 1
    matrix = []
    unobserved = []
    for i, row in enumerate(counts):
        count = sum(row)
        if count:
            matrix.append([value / count for value in row])
        else:
            matrix.append([float(i == j) for j in range(len(occupied))])
            unobserved.append(list(occupied[i]))
    bound = contraction(matrix) if len(cells) >= 2 else {"delta": None, "gap_bound": None}
    return {
        "cells": [list(cell) for cell in occupied],
        "matrix": matrix,
        **bound,
        "mixing": total_variation(cells[: len(cells) // 2], cells[len(cells) // 2 :]),
        "unobserved_exits": unobserved,
    }


def quantile_cuts(values: list[float], bins: int) -> list[float]:
    """Linear empirical quantiles preserve ties; equal-to-cut values use the lower bin.

    Repeated cuts are retained, so tied data can leave requested bins empty.
    Unsupported dimensions have no cuts and always map to -1.
    """
    values = sorted(values)
    cuts = []
    for i in range(1, bins):
        if not values:
            break
        position = (len(values) - 1) * i / bins
        low = floor(position)
        fraction = position - low
        cuts.append(values[low] * (1 - fraction) + values[min(low + 1, len(values) - 1)] * fraction)
    return cuts


def cell_series(windows: list[dict], cards: list[str], *, bins: int = 3) -> dict:
    """Cells use whole-run quantiles of supported channels and all named cards.

    Channel order follows the specification; cards are lexical. Support exactly
    at half the retained windows qualifies. An empty run has no channel dimensions.
    """
    if type(bins) is not int or bins < 1:
        raise ValueError("bins must be a positive integer")
    dimensions = [
        channel
        for channel in CHANNELS
        if windows and sum(w["profile"][channel] is not None for w in windows) * 2 >= len(windows)
    ] + sorted(cards)
    cuts = {
        name: quantile_cuts(
            [w["profile"][name] for w in windows if w["profile"][name] is not None], bins
        )
        for name in dimensions
    }
    cells = [
        tuple(
            -1 if w["profile"][name] is None else bisect_left(cuts[name], w["profile"][name])
            for name in dimensions
        )
        for w in windows
    ]
    return {"dimensions": dimensions, "cuts": cuts, "cells": cells}
