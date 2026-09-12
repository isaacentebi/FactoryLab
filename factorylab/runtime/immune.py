"""Live, bounded pathology correction uses public window observations, never the diary."""

from dataclasses import asdict
from math import isfinite

from factorylab.charter.controller import PriceController
from factorylab.runtime.observations import observation_for
from factorylab.versioning.series import CHANNELS
from factorylab.versioning.versions import diagnose


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
    """Fixed cells retain history across editions and publish causal diagnostic evidence."""
    spec = rt.m.immune
    previous = rt.stats.immune_windows
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
    profile["revision"] = values.get("revision_rate", 0.0)
    current = {
        "index": rt.window.index, "charter_edition": rt.charter.edition,
        "profile": profile,
        "regions": {f"card:{cid}": asdict(region) for cid, region in rt.regions.items()
                    },
    }
    windows = [*previous, current][-(spec.k + 1):]
    diagnosed = diagnose(windows, k=spec.k, registration_bins=spec.registration_bins,
                         revision_bins=spec.revision_bins)
    flags = diagnosed.pop("flags")
    evidence = {"window": current["index"], **diagnosed}
    for kind, detected in flags.items():
        if detected:
            rt.ledger.append({"kind": f"pathology.{kind}", **evidence})
    rt.ledger.append({"kind": "immune.window", **evidence, "profile": profile, "flags": flags,
                      "regions": current["regions"], "charter_edition": rt.charter.edition})
    rt.stats.immune_windows = windows
    rt.stats.pathologies = flags
    if flags["learning_death"]:
        grant_novelty(rt, current["index"] + 1)
    # Oscillation has priority if coarse cells make the two signals overlap.
    if flags["thrash"]:
        _gain(rt, "thrash", current["index"])
        rt.controller.set_decay(rt.m.prices.decay + spec.decay_step,
                                ledger=rt.ledger, window=current["index"] + 1)
    elif flags["stable_failure"]:
        _gain(rt, "stable_failure", current["index"])
        for cid in diagnosed["violated_cards"]:
            rt.controller.relieve(cid.removeprefix("card:"), window=current["index"] + 1)


def grant_novelty(rt, window: int) -> None:
    """One extra trial per existing assembly is available only in the next window.

    A trial ends when its first own producer consequence settles; continuations
    share that trial. The A13 reserve lifetime policy can consume this allowance
    without changing the detection mechanism.
    """
    rt.ledger.append({"kind": "immune.novelty", "window": window,
                      "trials_per_assembly": 1, "assemblies": sorted(rt.assemblies)})
    rt.immune_trials = {"window": window, "assemblies": dict.fromkeys(rt.assemblies)}


def novelty_available(rt, action_id: str) -> bool:
    """Extra exploratory access ends after its own consequence settles or its window expires."""
    grants = rt.immune_trials
    if rt.window.index != grants.get("window") or action_id not in grants.get("assemblies", {}):
        return False
    trial = grants["assemblies"][action_id]
    return trial is None or not trial["settled"]


def settle_novelty(rt, handles: tuple[str | None, ...]) -> None:
    """Only delivery of an attributed consequence consumes an extra exploratory trial."""
    for action, trial in rt.immune_trials.get("assemblies", {}).items():
        if trial is not None and not trial["settled"] and trial["handle"] in handles:
            rt.ledger.append({"kind": "immune.novelty_settled", "assembly_id": action,
                              "handle": trial["handle"], "window": rt.immune_trials["window"]})
            trial["settled"] = True
