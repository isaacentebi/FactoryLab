"""The immune organ: live versions, the convergence pathologies, and their priced answers.

Essay II.II.a-b: "Our goal is to seed the superdark factory with a kind of
incentive-based immune system that corrects each of these pathologies live, in
runtime." At every closed price window the organ reads the window into the live
versioning (``versioning.live``: the rolling operator, the version, its gap and
settling time) and diagnoses the pathologies with the one predicate the forensic
report also replays (``versioning.versions.diagnose``). The answers:

* **stable failure**: "price the duration of failure, ratcheting up penalties the
  longer the factory spends in a wide-spectral-gap attractor": each card of the
  failing attractor is ratcheted by its duration (``PriceController.ratchet``), and
  the ratcheted price reaches abstention too, through the same card penalty a
  router's NOOP bears (ruling R9, ``FeedbackMixin._priced_abstention``). The raise is
  bounded by ``prices.penalty_cap`` like every card penalty; see ``thrash_penalty``
  for why that bound is kept. At the cap the ratchet stops, and the saturation is
  ledgered (``immune.price_ratchet_saturated``) and published with the card's
  statistics, the card's price at its bound (wave 16, R-E).
* **thrash**: "penalize the duration of spectral-gap volatility, incentivizing the
  surplus-retaining core of no-swap-regret learners to stabilize": the diagnosis's
  unsettledness (the version gap's volatility, oscillation, abandoned versions,
  short-lived configurations) is priced by the charter's one price law, a PID whose
  integral accumulates how long it lasts (``THRASH_CARD``), and each round of a
  no-swap-regret router, abstentions included, is charged that price times the
  router's own policy movement (``FeedbackMixin._thrash_charged``): holding still
  is what lowers the charge.
* **learning death**: "delivered as a fact about the world", never as a response:
  the novelty reserve is usable by unhistoried actions of every seat (ruling R5,
  ``RoutingMixin._niche_action``), and a decision taken in the niche bears no card
  penalty (wave 16, R-E). The organ only holds the exploration gain it raised while
  the frontier is gone.

The organ diagnoses every window and acts (gain, ratchet) on its own loop, at least
``min_ratio`` price periods apart (versioning P5). The thrash price is a price and
moves on the price loop, like every card's.
"""

import hashlib
import json
from dataclasses import asdict

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.versioning import live
from factorylab.versioning.series import CHANNELS
from factorylab.versioning.versions import diagnose

#: The version causes whose settling times governance: a revision of the input, by
#: the charter or by the world's terms. Essay II.IV.c: "inject a small, deliberate
#: intent revision and measure how long the output distribution takes to return to a
#: settled distribution ... that settling time corresponds to the period of the
#: slowest feedback loop". The factory's own drift (launch, behaviour) is versioned and
#: ledgered the same way; it is not a revision whose response governance measures.
REVISIONS = frozenset({"charter", "terms"})

#: The thrash price's own entry in its PID (essay II.II.b): "penalize the duration of
#: spectral-gap volatility". It is not a charter card and prices no role's decisions.
THRASH_CARD = "pathology:thrash"


def thrash_controller(ledger, manifest) -> PriceController:
    """The thrash price: the charter's PID law and gains over the version gap's volatility.

    One price law (Chapter II rulings §2: "One price law: PID"): the same ``eta``,
    ``kp``, ``kd``, ``decay`` and one bound, ``penalty_cap``, the charter committed for
    its cards,
    over the volatility of the version gap series, in the region ``[0,
    immune.tv_threshold]``. Its integral accumulates ``eta * v`` for every window
    the volatility stays above that bound, so the price rises with the duration of
    the thrash, and leaks ``decay`` a window once it settles.
    """
    pr, bound = manifest.prices, manifest.immune.tv_threshold
    controller = PriceController(ledger, eta=pr.eta, decay=pr.decay,
                                 penalty_cap=pr.penalty_cap,
                                 min_window_events=pr.min_window_events, kp=pr.kp, kd=pr.kd)
    # The violation is in the gap's own units (it lives in [0, 1]), as a card at a zero
    # bound keeps its observation's declared units.
    controller.register(CardRegion(THRASH_CARD, "max", None, bound, 1.0))
    return controller


#: The tier whose behaviour an observation measures, where that is not the role its card
#: answers for: a judge's verdicts and forecasts are the judges' behaviour, a meta's
#: conformity the metas', an antagonist's exposure the antagonists' (wave 16, second
#: addendum, I-10: a penalty lands on the decisions whose behaviour it measures).
MEASURED_TIER = {"verdict_mean": "evaluator", "verdict_std": "evaluator",
                 "evaluator_disagreement": "evaluator", "forecast_skill": "evaluator",
                 "meta_verdict_mean": "meta", "exposure_win_rate": "antagonist"}


def thrash_roles(rt, windows: list[dict]) -> list[str]:
    """The roles whose behaviour the thrash signals read as moving.

    Guarantees the roles measured by the cards whose region-relative cell took more
    than one value over the retained horizon the diagnosis read (``live.cells`` over
    ``timing.min_ratio × immune.k`` windows): a card on ``MEASURED_TIER``'s
    observations names that tier, any other card the role it answers for. A card
    answering for ``all``, and movement in activity alone, name no role: then the
    price lands where essay II.II.b puts it, on the no-swap-regret core.
    """
    horizon = rt.m.timing.min_ratio * rt.m.immune.k
    span = windows[-horizon:]
    if len(span) < 2:
        return []
    bins = {"registration_bins": rt.m.immune.registration_bins,
            "revision_bins": rt.m.immune.revision_bins}
    dims, series = live.cells(span, activity=False, **bins)
    cards = {f"card:{card.id}": card for card in rt.charter.cards}
    roles = set()
    for i, name in enumerate(dims):
        card = cards.get(name)
        if card is None or len({cell[i] for cell in series}) < 2:
            continue
        observation = card.observation.strip().lower()
        role = MEASURED_TIER.get(observation, card.answers_for)
        if role != "all":
            roles.add(role)
    return sorted(roles)


def thrash_penalty(rt) -> dict:
    """Update the thrash price from the volatility just read, and the penalty it sets.

    Guarantees the price observes ``u``, the diagnosis's unsettledness (the
    largest of the gap series' volatility, a periodic oscillation, abandoned
    versions and short-lived configurations, ``versions.diagnose``), and the
    penalty is ``min(lambda * v, prices.penalty_cap)``, where ``v`` is ``u``'s
    distance above ``immune.tv_threshold``, zero while it is unsupported or inside
    that bound. The penalty is the published reading; what a round is charged is
    the price times the router's own movement (``FeedbackMixin._thrash_charged``).

    ``prices.penalty_cap`` binds this and the stable-failure ratchet alike (versioning
    audit P4 asked whether it should). It is kept: a reward is a unit-interval score,
    and a penalty that took all of it from every arm would leave the learner no
    difference to learn from, so the duration price would stop moving it at all;
    and the essay's own warning is that "gain ramped high enough to kick a system out
    of an overdamped attractor will, if unchecked, overshoot into an oscillation
    condition (thrash)". What keeps staying costly beyond the cap is that abstention
    bears the same price (ruling R9), so the cap no longer makes waiting the escape.
    At the cap the integral is frozen and the ratchet stops (wave 16, R-E): a wound-up
    integral would keep the price high long after the attractor is left.
    """
    unsettled = rt.stats.versions.get("unsettled")
    controller = rt.thrash_controller
    if unsettled is not None:
        controller.observe(THRASH_CARD, unsettled, window_end_event=rt.n)
    price = controller.price(THRASH_CARD)
    violation = controller.violation(THRASH_CARD, unsettled) if unsettled is not None else 0.0
    penalty = min(price * violation, rt.m.prices.penalty_cap)
    return {"unsettled": unsettled, "violation": violation, "lambda": price,
            "penalty": penalty}


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
      own entitlement (or the unallocated commons) can pay for.
    * **route to registration** — an affordable seat and a novelty reserve with
      something left in it, which is what admits a new tool, program, model or
      observation. The reserve is read inside the window it belongs to: the organ
      runs at the window's close, before the next reserve window opens (versioning
      audit C1), so what is read is what the closing window still held.
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
        live_seats = len(rt.assemblies) - len(rt.retired_assemblies)
        seats = rt.m.committee.seats
        registration = facts["registration_route"][0]
        if registration is False:
            facts["revision_route"] = (False, "revision needs the registration route it lost")
        elif live_seats < seats:
            facts["revision_route"] = (
                False, f"a committee needs {seats} seats and {live_seats} are live")
        else:
            facts["revision_route"] = (registration, None)
    except Exception:  # noqa: BLE001
        facts["revision_route"] = (None, None)
    return facts


def terms_digest(rt) -> str:
    """A digest of the terms the world offers: its own published surfaces and prices.

    Essay II.II: "If an external force changes the terms through which an input can
    be satisfied by a present distribution of the factory's output, the version has
    changed" (versioning audit M3). The terms are the world's tools and their prices
    (never the population's own tools or connectors, which it wrote itself) and the
    current price of every model the manifest named.
    """
    rt._ensure_connector_tool()  # the fixed primitives, published lazily, are terms
    own = set(rt.population_tools)
    tools = {tid: [spec.get("kind"), spec.get("price_micro_per_call")]
             for tid, spec in rt.tool_specs.items()
             if tid not in own and spec.get("kind") not in ("population", "connector")}
    prices = {}
    for tier in rt.m.models:
        price = rt.prices.prices.get(tier.id)
        prices[tier.id] = None if price is None else repr(price)
    payload = json.dumps({"tools": tools, "models": prices}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def close_window(rt, values: dict[str, float]) -> None:
    """Read the closed window into the live versions, diagnose, price thrash, then act.

    Guarantees, in this order: the window record joins the retained horizon
    (``timing.min_ratio × immune.k`` windows); every version boundary and settling
    reading is ledgered, and the settling of a version a revision opened reaches the
    governance cadence (``REVISIONS``, ``GovernanceCadence.record_settling``), with its
    age while it is unsettled; the flags and their evidence are
    ledgered (``pathology.*``, ``immune.window``); the thrash price moves; and only
    then, on the organ's own loop, the gain and the ratchet.
    """
    spec = rt.m.immune
    k = spec.k
    horizon = rt.m.timing.min_ratio * k
    bins = {"registration_bins": spec.registration_bins, "revision_bins": spec.revision_bins}
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
    access = access_evidence(rt)
    profile.update({f"access:{name}": None if present is None else float(present)
                    for name, (present, _why) in access.items()})
    now = rt.ticks_consumed
    previous = rt.stats.immune_windows[-1] if rt.stats.immune_windows else None
    if previous is None:
        rt.config_ticks.setdefault("charter", 0)  # the launch edition's first tick
    elif previous["charter_edition"] != rt.charter.edition:
        # Time audit T14: an edition replaces the charter; governance corrects it on
        # its slowest loop.
        configuration_changed(rt, "charter", rt.cadence.slowest_period_events())
    current = {
        "index": rt.window.index, "tick": now, "charter_edition": rt.charter.edition,
        "terms": terms_digest(rt), "profile": profile,
        # The frontier signal (ruling R9; versioning P1, U1): each router's draws this
        # window, its NOOP floor and its draw mass on unhistoried seats.
        "frontier_invocation": rt.frontier_invocation(),
        # Configurations refactored since the last close, against their loops (T14).
        "lifespans": list(rt.lifespan_log),
        "access": {name: why for name, (_present, why) in access.items() if why},
        "regions": {f"card:{cid}": asdict(region) for cid, region in regions.items()},
    }
    rt.lifespan_log = []
    windows = [*rt.stats.immune_windows, current][-live.retention(horizon, k):]
    # The previous diagnosis's failing set: a card in it that this tail leaves
    # unmeasured holds its state (wave 16, second addendum, M-6).
    held = list((rt.stats.versions or {}).get("failing", []))
    state, events = live.advance(rt.stats.versions or live.fresh(), windows, k=k,
                                 horizon=horizon, tv_threshold=spec.tv_threshold, **bins)
    for event in events:
        kind = event["kind"]
        rt.ledger.append({"kind": f"version.{kind}", **{n: v for n, v in event.items()
                                                        if n != "kind"}})
        if kind == "settled" and event["cause"] in REVISIONS:
            rt.cadence.record_settling(version=event["version"], cause=event["cause"],
                                       ticks=event["ticks"], settled=event["settled"])
    rt.cadence.track_version(version=state["version"], opened=state["start_tick"],
                             settled=(state["settled_tick"] is not None
                                      or state["cause"] not in REVISIONS))
    diagnosed = diagnose(windows, state, k=k, tv_threshold=spec.tv_threshold,
                         gap_threshold=spec.gap_threshold, held=held, **bins)
    state["failing"] = list(diagnosed["violated_cards"])
    flags = diagnosed.pop("flags")
    evidence = {"window": current["index"], **diagnosed}
    rt.stats.immune_windows = windows
    state["unsettled"] = diagnosed["unsettled"]  # what the thrash price observes
    rt.stats.versions = state
    rt.stats.pathologies = flags
    for kind, detected in flags.items():
        if detected:
            rt.ledger.append({"kind": f"pathology.{kind}", **evidence})
    # Wave 16, second addendum (I-10): the thrash price is charged on the routers of
    # the tiers whose behaviour moved, never shifted to another tier's.
    rt.stats.thrash = {**thrash_penalty(rt), "roles": thrash_roles(rt, windows)}
    # Versioning P5, time audit T2: the organ diagnoses every closed window but acts
    # (gain, ratchet) only on its own loop, at least ``min_ratio`` price-loop
    # periods apart with its own jitter, so it never revises the controller at the
    # controller's own frequency (iatrogenic thrash, essay II.IV.c).
    inner = rt.clockwork.period("price", default=rt.m.timing.min_ratio)
    acts = rt.clockwork.due("immune", now, inner)
    rt.ledger.append({"kind": "immune.window", **evidence, "profile": profile, "flags": flags,
                      "regions": current["regions"], "charter_edition": rt.charter.edition,
                      "tick": now, "terms": current["terms"],
                      "frontier_invocation": current["frontier_invocation"],
                      "lifespans": current["lifespans"], "thrash": rt.stats.thrash,
                      "acts": acts})
    if not acts:
        return
    schedule = rt.clockwork.fire("immune", now, inner)
    rt._ledger_loop("immune", schedule, inner_loop="price")
    # Oscillation has priority: gain ramped into an attractor overshoots into thrash
    # (essay II.IV.b), so a thrashing factory's raised exploration unwinds first.
    ratcheted: set[str] = set()
    if flags["thrash"]:
        _gain(rt, "thrash", current["index"])
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
    # An unmeasured failing card holds its duration whatever the flags say (M-6):
    # missing evidence ends no failure.
    unmeasured = {cid.removeprefix("card:") for cid in diagnosed.get("unmeasured_held", ())}
    for card_id in rt.controller.card_ids():
        if card_id not in ratcheted and card_id not in unmeasured:
            rt.controller.end_failure(card_id, window=current["index"])


def configuration_changed(rt, loop: str, latency: int) -> None:
    """One configuration of ``loop`` was replaced: record how long it lived.

    Essay II.IV.b (time audit T14): "Thrash occurs in loops whose periods exceed
    the lifespan of the configurations they are trying to error-correct." A
    lifespan is the ticks between two changes of one loop's configuration (a seat's
    contract version, a router's epoch, a charter edition); ``latency`` is the
    measured period of the loop that corrects it. A ratio below one is a correction
    that lands on the configuration's successor, and the organ reads it as thrash.
    The first configuration of a loop has no lifespan yet.
    """
    now = rt.ticks_consumed
    last = rt.config_ticks.get(loop)
    rt.config_ticks[loop] = now
    if last is None:
        return
    latency = max(1, int(latency))
    row = {"loop": loop, "lifespan_ticks": now - last, "latency_ticks": latency,
           "ratio": (now - last) / latency, "tick": now}
    rt.lifespan_log = [*rt.lifespan_log, row]
    rt.ledger.append({"kind": "config.lifespan", **row, "ts": rt.clock.now_ns})
