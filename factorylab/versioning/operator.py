"""Empirical cell transitions have an explicit Dobrushin spectral-gap bound."""

from bisect import bisect_left
from collections import Counter
from math import fsum, isfinite

from factorylab.charter.controller import CardRegion, violation


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


def cell_series(
    windows: list[dict], cards: list[str], *,
    registration_bins: tuple[float, ...], revision_bins: tuple[float, ...],
) -> dict:
    """Fixed region-relative cells never recalibrate against the classified sample.

    Cards without an observed, declared region remain unsupported (-1). Activity
    uses absolute manifest cuts; a value equal to a cut stays in the lower bin.
    Raw rewards remain in the profile for analysis, without becoming extra goals.
    """
    dimensions = sorted(cards) + ["registrations", "revision"]
    cuts = {name: [0.0, 1.0] for name in cards}
    cuts.update(registrations=list(registration_bins), revision=list(revision_bins))
    cells = []
    for window in windows:
        cell = []
        for name in dimensions:
            value = window["profile"].get(name)
            if value is None:
                cell.append(-1)
            elif name in ("registrations", "revision"):
                cell.append(bisect_left(cuts[name], value))
            elif (region := window["regions"].get(name)) is not None:
                amount = violation(CardRegion(**dict(region, card_id=name)), value)
                cell.append(0 if amount == 0 else 1 if amount <= 1 else 2)
            else:
                cell.append(-1)
        cells.append(tuple(cell))
    return {"dimensions": dimensions, "cuts": cuts, "cells": cells}
