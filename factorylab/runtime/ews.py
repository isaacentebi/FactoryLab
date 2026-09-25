"""Online early warning: variance, autocorrelation and ensemble disagreement, live.

Essay II.III.a: "A system calibrated to EWS requires multiscale attentiveness to a
few important metrics: variance, autocorrelation, and ensemble disagreement ... All
of the above assessments cycle at every level of the dark stack." Ruling R3 keeps
the statistics and makes them live; evaluations M2 publishes them to the
evaluators. At each measurement window's close the runtime appends the window's
score profile to a bounded history and reads ``versioning.versions.early_warnings``
over it at k, 2k and 4k windows (``immune.k``). The table is shown to the seats that
judge, never in a producer's view, and two summaries of it are seed observations a
card may price (``ews_variance``, ``ews_autocorrelation``).
"""

from __future__ import annotations

from typing import Any

from factorylab.versioning.versions import early_warnings

#: The score series every window profile carries, on the unit scale or near it: the
#: mean first-tier verdict, the mean grade of the tiers above, the evaluators'
#: consequence skill and the ensemble disagreement across multi-judged returns.
SCORE_SERIES = ("verdict", "conformity", "consequence", "disagreement")
#: The seed observations that summarise the table; withheld from producers' views.
EWS_OBSERVATIONS = frozenset({"ews_variance", "ews_autocorrelation"})


def score_profile(values: dict[str, float | None]) -> dict[str, float | None]:
    """A window's score series from its observation values (None where unsupported)."""
    return {
        "verdict": values.get("verdict_mean"),
        "conformity": values.get("meta_verdict_mean"),
        "consequence": values.get("forecast_skill"),
        "disagreement": values.get("evaluator_disagreement"),
    }


def history_with(history: list[dict], window: int, profile: dict, k: int) -> list[dict]:
    """``history`` with this window's profile appended, bounded to the longest span (4k)."""
    kept = [row for row in history if row.get("window") != window]
    return [*kept, {"window": window, "profile": dict(profile)}][-4 * k:]


def table(history: list[dict], cards: list[str], k: int) -> dict[str, list[dict]]:
    """The statistics at the history's last window: per series, one row per span.

    Guarantees ``early_warnings``'s own definitions (population variance and the
    centred lag-one autocorrelation over complete spans of k, 2k and 4k windows;
    an incomplete or constant span is None, never zero). The series are the score
    series, every card id given, and the compute wallet's balance.
    """
    if not history:
        return {}
    rows = early_warnings(history, [], list(cards), k=k)
    return rows[-1]["series"] if rows else {}


def summary(series: dict[str, list[dict]]) -> tuple[float | None, float | None]:
    """The largest variance and lag-one autocorrelation among the score series.

    Guarantees both are read at the shortest span any score series supports, so a
    rise is critical slowing down in the fastest-moving evidence (II.III.a: "larger
    swings in variance", "longer to recover from perturbations"); None when no
    score series has a complete span.
    """
    variances, correlations = [], []
    for name in SCORE_SERIES:
        for scale in series.get(name, ()):
            if scale.get("variance") is not None:
                variances.append(scale["variance"])
                if scale.get("autocorrelation") is not None:
                    correlations.append(scale["autocorrelation"])
                break
    return (max(variances) if variances else None,
            max(correlations) if correlations else None)


def view(early_warning: dict[str, Any]) -> dict[str, Any]:
    """What an evaluator is shown: the last close's table and summaries, nothing private."""
    if not early_warning:
        return {"window": None, "series": {}}
    return {key: early_warning[key] for key in ("window", "spans_windows", "summary", "series")
            if key in early_warning}
