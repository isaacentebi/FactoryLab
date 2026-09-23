"""Live, bounded pathology correction uses public window observations, never the diary."""

from dataclasses import asdict
from math import isfinite

from factorylab.charter.controller import PriceController
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
        before = self.decay
        if before != value:
            ledger.append({"kind": "immune.decay", "window": window,
                           "decay_before": before, "decay_after": value})
            self.decay = value


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
    """Move every router's exploration one ``gain_step``: up on stable failure, else down.

    Up is bounded by ``gamma_max``; down (thrash, or ``cleared`` once no pathology
    that the gain answers is diagnosed) never goes below the router's own seed
    gamma, so a ratchet the organ raised unwinds after the attractor is left and a
    router that was never raised is not touched. Each change is ledgered first.

    A router's gain is an outer loop over that router's own rounds (time audit T2):
    a kind's routers step at most once per ``min_ratio`` times their measured round
    period, so a step is never taken on rounds drawn under the previous one.
    """
    spec = rt.m.immune
    now = rt.ticks_consumed
    stepped: set[str] = set()
    for router in rt._all_router_states():
        loop = f"gain:{router.kind}"
        inner = rt.clockwork.measured(f"router:{router.kind}")
        if router.kind not in stepped and not rt.clockwork.due(loop, now, inner):
            continue
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
                          "gamma_after": after, "tick": now})
        router.learner, router.router = replacement.learner, replacement.router
        if router.kind not in stepped:
            rt.clockwork.fire(loop, now, inner)
            stepped.add(router.kind)


def access_evidence(rt) -> dict[str, tuple[bool, str | None]]:
    """What the population can still afford to do, and why it cannot when it cannot.

    Learning death is loss of affordable, usable access to investigation and
    revision (GPT-6 13; edition 3 C3), so the organ measures the access itself
    rather than inferring it from quiet behaviour:

    * **affordable seat** — some live seat's cheapest probe is inside what its
      own entitlement (or the unallocated commons) can pay for. Without one no
      investigation can be bought at any price.
    * **route to registration** — an affordable seat and a novelty reserve with
      something left in it, which is what admits a new tool, program, model or
      observation.
    * **route to revision** — the registration route plus enough live seats to
      draw the committee that votes an amendment or a challenge through.

    Every fact is read from live kernel state and every failure is caught: a
    diagnostic may not be able to stop a run, so an access it cannot measure is
    reported as unknown (``None``) and never as lost.
    """
    facts: dict[str, tuple[bool | None, str | None]] = {}
    cheapest = spendable = None
    try:
        cheapest = rt._cheapest_seat_micro()
        entitlements = rt.budget.entitlements().values()
        spendable = max([*entitlements, rt.budget.unallocated()], default=0)
    except Exception:  # noqa: BLE001 - a diagnostic never breaks the run
        facts["affordable_seat"] = (None, None)
    if "affordable_seat" not in facts:
        if cheapest is None:
            facts["affordable_seat"] = (False, "no live seat is priced")
        elif spendable < cheapest:
            facts["affordable_seat"] = (
                False, f"cheapest seat costs {cheapest} micro-USD; the richest seat holds "
                       f"{spendable}")
        else:
            facts["affordable_seat"] = (True, None)
    affordable = facts["affordable_seat"][0]
    try:
        remaining = int(rt.reserve.remaining())
        if affordable is False:
            facts["registration_route"] = (False, "registration needs a seat nobody can afford")
        elif not remaining:
            facts["registration_route"] = (False, "the novelty reserve window has nothing left")
        else:
            facts["registration_route"] = (affordable, None)
    except Exception:  # noqa: BLE001
        facts["registration_route"] = (None, None)
    try:
        live = len(rt.assemblies) - len(rt.retired_assemblies)
        seats = rt.m.committee.seats
        registration = facts["registration_route"][0]
        if registration is False:
            facts["revision_route"] = (False, "revision needs the registration route it lost")
        elif live < seats:
            facts["revision_route"] = (
                False, f"a committee needs {seats} seats and {live} are live")
        else:
            facts["revision_route"] = (registration, None)
    except Exception:  # noqa: BLE001
        facts["revision_route"] = (None, None)
    return facts


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
    measured = rt.window.closed_values or {}
    regions = rt.window.closed_regions
    profile.update({f"card:{c.id}": measured.get(c.id) for c in rt.charter.cards})
    # Activity is a separate observation even when the charter has no registration card.
    profile["registrations"] = values.get("registrations", 0.0)
    profile["revision"] = values.get("revision_rate", 0.0)
    # Consequence outcomes: a rising paid-off rate or realized P&L is a live frontier.
    profile["paid_off"] = values.get("consequence_paid_off_rate")
    profile["realized_pnl"] = values.get("realized_pnl_usd")
    # The access facts learning death is actually about ride in the organ's own
    # window profile, so a diagnosis can say which access is lost and why.
    access = access_evidence(rt)
    profile.update({f"access:{name}": None if present is None else float(present)
                    for name, (present, _why) in access.items()})
    current = {
        "index": rt.window.index, "charter_edition": rt.charter.edition,
        "profile": profile,
        # The frontier signal (ruling R9; versioning U1, time T16): which routers woke
        # their seats only by exploration this window. It is evidence inside the one
        # learning-death diagnosis below, never a flag of its own.
        "frontier_invocation": rt.frontier_invocation(),
        "access": {name: why for name, (_present, why) in access.items() if why},
        "regions": {f"card:{cid}": asdict(region) for cid, region in regions.items()
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
    # Versioning P5, time audit T2: the organ diagnoses every closed window but acts
    # (gain, decay, ratchet) only on its own loop, at least ``min_ratio`` price-loop
    # periods apart with its own jitter, so it never revises the controller at the
    # controller's own frequency (iatrogenic thrash, essay II.IV.c).
    now = rt.ticks_consumed
    inner = rt.clockwork.period("price", default=rt.m.timing.min_ratio)
    acts = rt.clockwork.due("immune", now, inner)
    rt.ledger.append({"kind": "immune.window", **evidence, "profile": profile, "flags": flags,
                      "regions": current["regions"], "charter_edition": rt.charter.edition,
                      "acts": acts})
    rt.stats.immune_windows = windows
    # Learning death's response is this flag alone: the reserve reads it at the next
    # window boundary and issues the one extra novelty trial per assembly.
    rt.stats.pathologies = flags
    if not acts:
        return
    schedule = rt.clockwork.fire("immune", now, inner)
    rt._ledger_loop("immune", schedule, inner_loop="price")
    # Oscillation has priority if coarse cells make the two signals overlap.
    ratcheted: set[str] = set()
    if flags["thrash"]:
        _gain(rt, "thrash", current["index"])
        rt.controller.set_decay(rt.m.prices.decay + spec.decay_step,
                                ledger=rt.ledger, window=current["index"] + 1)
    elif flags["stable_failure"]:
        _gain(rt, "stable_failure", current["index"])
        # Essay II.II.b: stable failure is priced by its duration. Every violated card
        # of the failing attractor has its price ratcheted up by how long the factory
        # has sat there, never relieved: the gain to leave the attractor must grow.
        known = set(rt.controller.card_ids())
        for cid in diagnosed["violated_cards"]:
            card_id = cid.removeprefix("card:")
            if card_id in known:
                rt.controller.ratchet(card_id, window=current["index"], step=spec.price_step)
                ratcheted.add(card_id)
    elif not flags["learning_death"]:
        # The attractor is left: the exploration the organ added unwinds toward seed.
        # A learning-dead window holds it, since less exploration is the wrong answer.
        _gain(rt, "cleared", current["index"])
    for card_id in rt.controller.card_ids():
        if card_id not in ratcheted:
            rt.controller.end_failure(card_id, window=current["index"])
