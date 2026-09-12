"""Live, bounded pathology correction uses public window observations, never the diary."""

from dataclasses import asdict
from math import isfinite

from factorylab.charter.controller import PriceController
from factorylab.runtime.observations import observation_for
from factorylab.versioning.operator import cell_series, transition_operator
from factorylab.versioning.series import CHANNELS
from factorylab.versioning.versions import block_distances, violation


class ImmunePriceController(PriceController):
    """The existing checkpointed controller can borrow extra decay for one window."""

    def set_decay(self, value: float, *, ledger, window: int) -> None:
        """A decay change takes effect only after its evidence is durable.

        This narrow adapter owns the existing controller's checkpointed decay slot;
        no alternate price state or recovery path is introduced.
        """
        if not isfinite(value) or value <= 0:
            raise ValueError("decay must be finite and positive")
        before = self.snapshot()["parameters"]["decay"]
        if before != value:
            ledger.append({"kind": "immune.decay", "window": window,
                           "decay_before": before, "decay_after": value})
            self._PriceController__decay = value


def _bases(saved: dict) -> list[dict]:
    if saved["algorithm"] == "EXP3":
        return [saved]
    if "inner" in saved:
        return _bases(saved["inner"])
    return [base for row in saved.get("bases", []) for base in _bases(row)]


def gamma(learner) -> float:
    """Return exploration shared by a router's EXP3 rows, including delayed swap learners."""
    return _bases(learner.state())[0]["gamma"]


def _gain(rt, kind: str, window: int) -> None:
    spec = rt.m.immune
    for router in rt._all_router_states():
        saved = router.state()
        bases = _bases(saved["router"]["learner"])
        before = [base["gamma"] for base in bases]
        after = [
            max(old, min(spec.gamma_max, old + spec.gain_step))
            if kind == "stable_failure" else min(old, max(router.seed_gamma, old - spec.gain_step))
            for old in before
        ]
        if before == after:
            continue
        for base, value in zip(bases, after, strict=True):
            base["gamma"] = value
        # Restore before the append to validate every row and retained delayed snapshot.
        replacement = type(router).restore(saved)
        rt.ledger.append({"kind": "immune.gain", "pathology": kind, "window": window,
                          "router": router.learner.id, "gamma_before": before,
                          "gamma_after": after})
        router.learner, router.router = replacement.learner, replacement.router


def close_window(rt, values: dict[str, float]) -> None:
    """Each closure publishes evidence and ratchets only after sustained supported pathology.

    Stable failure and learning death use the last k cells. Block TV compares two
    k-window blocks; k consecutive supported distances need 3k-1 retained windows.
    Quantiles are recomputed over that bounded live history, with missing values
    retained as missing. A charter revision starts a fresh comparison horizon.
    """
    spec = rt.m.immune
    previous = rt.stats.immune_windows
    if previous and previous[-1]["charter_edition"] != rt.charter.edition:
        previous = []
    cards = sorted(f"card:{c.id}" for c in rt.charter.cards)
    profile = {channel: None for channel in CHANNELS}
    profile.update({
        "verdict": values.get("verdict_mean"),
        "conformity": values.get("meta_verdict_mean"),
        "consequence": values.get("forecast_skill"),
        "exposure": values.get("exposure_win_rate"),
    })
    profile.update({f"card:{c.id}": values.get(o.id)
                    if (o := observation_for(c.observation)) is not None else None
                    for c in rt.charter.cards})
    # Activity is a separate observation even when the charter has no registration card.
    profile["registrations"] = values.get("registrations", 0.0)
    current = {
        "index": rt.window.index, "charter_edition": rt.charter.edition,
        "profile": profile,
        "regions": {f"card:{cid}": asdict(region) for cid, region in rt.regions.items()
                    if rt.controller.price(cid) > 0},
    }
    windows = [*previous, current][-(3 * spec.k - 1):]
    cells = cell_series(windows, cards, bins=spec.bins)["cells"]
    tail = cells[-spec.k:]
    supported = len(tail) == spec.k
    same = supported and len(set(tail)) == 1
    bound = transition_operator(tail)["gap_bound"]
    violated = [
        {cid for cid, region in w["regions"].items()
         if (value := w["profile"].get(cid)) is not None and violation(region, value) > 0}
        for w in windows[-spec.k:]
    ]
    failures = set.intersection(*violated) if supported else set()
    distances = block_distances(cells, spec.k)[-spec.k:]
    flags = {
        "stable_failure": bool(same and bound is not None and bound >= spec.gap_threshold
                               and failures),
        "thrash": len(distances) == spec.k and all(
            tv is not None and tv > spec.tv_threshold for tv in distances
        ),
        "learning_death": bool(same and all(
            w["profile"]["registrations"] == 0 for w in windows[-spec.k:]
        )),
    }
    evidence = {"window": current["index"], "cells": [list(c) for c in tail],
                "gap_bound": bound, "block_tv": distances,
                "violated_cards": sorted(cid.removeprefix("card:") for cid in failures)}
    for kind, detected in flags.items():
        if detected:
            rt.ledger.append({"kind": f"pathology.{kind}", **evidence})
    rt.ledger.append({"kind": "immune.window", **evidence, "profile": profile, "flags": flags})
    rt.stats.immune_windows = windows
    rt.stats.pathologies = flags
    # Oscillation has priority if coarse cells make the two signals overlap.
    if flags["thrash"]:
        _gain(rt, "thrash", current["index"])
        rt.controller.set_decay(rt.m.prices.decay + spec.decay_step,
                                ledger=rt.ledger, window=current["index"] + 1)
    elif flags["stable_failure"]:
        _gain(rt, "stable_failure", current["index"])
