"""The one report a dead world's diary yields: versions, pathologies, early warnings.

Deterministic: the same diary gives the same report, and every numeric setting
comes from the genesis manifest the Launch event carries, so the analysis cannot
silently use different thresholds from the world it describes.
"""

import hashlib
import json
from math import isfinite

from factorylab.versioning.operator import cell_series, transition_operator
from factorylab.versioning.series import card_names, ordered, windows
from factorylab.versioning.versions import (
    early_warnings,
    pathologies,
    replay,
    settling,
    versions,
)

#: The cascade's minimum ratio (essay II.IV.c, "3:1 at a minimum"), used for the
#: horizon only when a diary predates the manifest's own ``timing.min_ratio``.
ESSAY_MIN_RATIO = 3


def manifest_parameters(items: list[dict]) -> dict:
    """Recover the committed launch settings; a diary without them supplies no implicit defaults."""
    for item in items:
        event = item.get("event", {}) if item.get("kind") == "event" else {}
        if event.get("kind") != "Launch":
            continue
        payload = event.get("payload", {})
        manifest = payload.get("manifest")
        if not isinstance(manifest, dict) or not isinstance(manifest.get("immune"), dict):
            break
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode()).hexdigest() != payload.get("manifest_hash"):
            raise ValueError("launch manifest hash differs")
        if items and "prev_hash" in items[0]:
            genesis = json.dumps({"manifest": manifest}, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False)
            if hashlib.sha256(genesis.encode()).hexdigest() != items[0]["prev_hash"]:
                raise ValueError("launch manifest differs from ledger genesis")
        timing = manifest.get("timing") if isinstance(manifest.get("timing"), dict) else {}
        return {**manifest["immune"], "min_ratio": timing.get("min_ratio", ESSAY_MIN_RATIO)}
    raise ValueError("versions requires the genesis manifest's immune settings")


def summary(
    items: list[dict], *, window_items: int = 200, k: int | None = None,
    tv_threshold: float | None = None, gap_threshold: float | None = None,
    registration_bins: tuple[float, ...] | None = None,
    revision_bins: tuple[float, ...] | None = None, min_ratio: int | None = None,
) -> dict:
    """Return deterministic analysis using the genesis settings or explicit caller parameters.

    Runtime window observations are replayed through the live versioning and the
    live predicate (``versions.replay``); recorded flags are never treated as
    conclusions. Analysis cannot silently use different numeric defaults from the
    world whose ledger it describes.
    """
    items = ordered(items)
    supplied = dict(k=k, tv_threshold=tv_threshold, gap_threshold=gap_threshold,
                    registration_bins=registration_bins, revision_bins=revision_bins)
    committed = manifest_parameters(items) if any(v is None for v in supplied.values()) else {}
    params = {name: committed[name] if value is None else value for name, value in supplied.items()}
    params["min_ratio"] = (min_ratio if min_ratio is not None
                           else committed.get("min_ratio", ESSAY_MIN_RATIO))
    k = params["k"]
    tv_threshold, gap_threshold = params["tv_threshold"], params["gap_threshold"]
    registration_bins = tuple(params["registration_bins"])
    revision_bins = tuple(params["revision_bins"])
    for name, value in (("window_items", window_items), ("k", k)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    for name, value in (("tv_threshold", tv_threshold), ("gap_threshold", gap_threshold)):
        if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be finite and in [0, 1]")
    for name, cuts in (("registration_bins", registration_bins), ("revision_bins", revision_bins)):
        if (not cuts or any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in cuts)
                or any(a >= b for a, b in zip(cuts, cuts[1:], strict=False))):
            raise ValueError(f"{name} must contain increasing finite nonnegative cuts")
    cards = card_names(items)
    groups = windows(items, window_items=window_items)
    discretized = cell_series(groups, cards, registration_bins=registration_bins,
                              revision_bins=revision_bins)
    cells = discretized["cells"]
    for group, cell in zip(groups, cells, strict=True):
        group["cell"] = list(cell)
    operator = transition_operator(cells)
    operator.update(dimensions=discretized["dimensions"], cuts=discretized["cuts"],
                    durable=operator["gap_bound"] is not None
                    and operator["gap_bound"] >= gap_threshold)
    bins = {"registration_bins": registration_bins, "revision_bins": revision_bins}
    horizon = params["min_ratio"] * k
    readings = replay(groups, k=k, horizon=horizon, tv_threshold=tv_threshold,
                      gap_threshold=gap_threshold, **bins)
    spans = versions(groups, readings, gap_threshold=gap_threshold, **bins)
    return {
        "params": {
            "window_items": window_items,
            "k": k,
            "tv_threshold": tv_threshold,
            "gap_threshold": gap_threshold,
            "registration_bins": list(registration_bins),
            "revision_bins": list(revision_bins),
            "min_ratio": params["min_ratio"],
            "horizon": horizon,
        },
        "windows": groups,
        "operator": operator,
        "versions": spans,
        "pathologies": pathologies(groups, readings, spans),
        "ews": early_warnings(groups, spans, cards, k=k),
        "settling": settling(readings),
    }


def _display(value: float | None) -> str:
    """Unsupported values have an explicit plain-text representation."""
    return "unsupported" if value is None else f"{value:.4g}"


def render(report: dict) -> str:
    """A stable plain-text report labels bounds and evidence without changing the report.

    The last endpoint supplies one EWS line per series, with all three scales;
    earlier version-end signals remain available in the structured summary.
    """
    op = report["operator"]
    lines = [
        f"Factory versions: {len(report['versions'])} across {len(report['windows'])} windows",
        f"Spectral gap lower bound (Dobrushin): {_display(op['gap_bound'])}; "
        f"delta={_display(op['delta'])}; empirical mixing TV={_display(op['mixing'])}",
    ]
    for span in report["versions"]:
        lines.append(
            f"Version {span['start_window']}..{span['end_window']}: "
            f"{span['duration']} windows, charter {span['charter_edition']}, "
            f"opened by {span['cause']}, gap {_display(span['gap'])}"
            f"{' (durable)' if span['durable'] else ''}"
        )
    if not report["pathologies"]:
        lines.append("Pathology evidence: none")
    for flag in report["pathologies"]:
        lines.append(f"Evidence {flag['kind']}: {flag['start_window']}..{flag['end_window']}")
    if report["ews"]:
        for name, scales in report["ews"][-1]["series"].items():
            lines.append(
                f"EWS {name}: "
                + "; ".join(
                    f"{scale['span']}w var={_display(scale['variance'])} "
                    f"acf1={_display(scale['autocorrelation'])}"
                    for scale in scales
                )
            )
    return "\n".join(lines)
