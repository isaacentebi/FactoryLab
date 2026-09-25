"""The pathology gauntlet's pass criteria: pure predicates over ledger rows and a manifest.

Chapter II §II.a names four convergence pathologies and §II.b and §IV.b the priced
answer to each. Every criterion here reads only ledger rows (``events``) and the
world's physics (``manifest``, a mapping in the shape of ``WorldManifest`` or its
TOML), so the same predicate runs in three places (phase-2 design G0):

* in ``tests/gauntlet`` over a gauntlet world's in-memory ledger;
* over a dead diary (``replay``): a directory of per-kind ``*.jsonl`` rows such as
  ``work/capital-loop/longrun1-open``, or an ``events.json`` list;
* in a multi-seed sweep (``sweep``), which lives here and not in pytest.

What a criterion may assert (design §1.2, AGENTS rule 2): a property of the physics
under a price, never a target behaviour mix, hold rate or registration count. Every
threshold is derived from the world's own parameters (``Physics``); a constant
appears only where Chapter II names one (the 3:1 ratio is ``timing.min_ratio``).

Each criterion returns a ``Result``: ``pass``, ``fail``, or ``unsupported`` (the
rows carry no evidence either way). ``unsupported`` is never a pass.

Nothing here imports the runtime: the module is importable over a diary with no
world loaded. ``sweep`` imports the gauntlet populations lazily.

Examples::

    uv run python scripts/gauntlet.py replay work/capital-loop/longrun1-open \\
        --world worlds/edition6-capital-loop.toml
    uv run python scripts/gauntlet.py sweep --population sf1 --seeds 1,2,3
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any

PASS, FAIL, UNSUPPORTED = "pass", "fail", "unsupported"

#: Row kinds a diary replay never needs (the snapshot state, raw model I/O and the
#: rendered public blocks dominate a diary's bytes and carry no criterion's evidence).
HEAVY_KINDS = frozenset({"snapshot", "io.result", "io.call", "runtime.input", "artifact.put",
                         "wake.public", "budget", "compute.route", "consequence.cost"})

#: Observations whose penalty share is each decision's own measured contribution
#: (``runtime.pricing._EXACT_SHARES``): their shares differ by design, not by order.
EXACT_SHARES = frozenset({"cost_per_return", "cost_per_attempt", "well_formed_rate",
                          "tool_calls", "turnover"})

#: The pathology labels no seat-visible leaf may carry (S2; schematics withhold them).
PATHOLOGY_WORDS = ("pathology", "stable_failure", "thrash", "learning_death", "overfit")


@dataclass(frozen=True)
class Result:
    """A criterion's reading: its name, ``pass`` / ``fail`` / ``unsupported``, evidence."""

    name: str
    status: str
    evidence: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True only for a pass; an unsupported reading is not a pass."""
        return self.status == PASS

    def __bool__(self) -> bool:  # pragma: no cover - guards accidental truthiness
        raise TypeError("read Result.ok or Result.status, not the Result itself")


def _result(name: str, ok: bool, **evidence: Any) -> Result:
    return Result(name, PASS if ok else FAIL, evidence)


def _unsupported(name: str, why: str, **evidence: Any) -> Result:
    return Result(name, UNSUPPORTED, {"why": why, **evidence})


# --- the world's physics, derived (design §1.3) -------------------------------------


def _section(manifest: Mapping, name: str) -> Mapping:
    value = manifest.get(name) if isinstance(manifest, Mapping) else None
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class Physics:
    """The constants a criterion may use, each read from the world (never typed here).

    Defaults are ``WorldManifest``'s own, so a TOML that omits a key reads what the
    kernel would have loaded. ``H`` is the retained horizon ``min_ratio × k``.
    """

    r: int = 3
    k: int = 3
    eta: float = 0.5
    kp: float = 0.0
    kd: float = 0.0
    decay: float = 0.1
    lambda_max: float = 1.0
    cap: float = 0.5
    min_blame_share: float = 0.1
    price_step: float = 0.05
    gain_step: float = 0.05
    gamma_max: float = 0.5
    tv_threshold: float = 0.2
    gap_threshold: float = 0.8
    novelty_share: float = 0.1
    seat_share: float = 0.25
    trials: int = 3
    sampling_step: float = 0.1
    sampling_cap: float = 0.7
    consequence_share: float = 0.3
    no_swap_regret_kinds: tuple[str, ...] = ()

    @property
    def H(self) -> int:  # noqa: N802 - the design's symbol
        return self.r * self.k


def physics(manifest: Mapping | None) -> Physics:
    """Read ``Physics`` from a manifest mapping (``asdict`` or TOML shape); defaults else."""
    manifest = manifest or {}
    prices, immune = _section(manifest, "prices"), _section(manifest, "immune")
    timing, novelty = _section(manifest, "timing"), _section(manifest, "novelty")
    evaluation = _section(manifest, "evaluation")
    base = Physics()

    def get(section: Mapping, key: str, default: Any) -> Any:
        value = section.get(key)
        return default if value is None else value

    return Physics(
        r=int(get(timing, "min_ratio", base.r)), k=int(get(immune, "k", base.k)),
        eta=float(get(prices, "eta", base.eta)), kp=float(get(prices, "kp", base.kp)),
        kd=float(get(prices, "kd", base.kd)), decay=float(get(prices, "decay", base.decay)),
        lambda_max=float(get(prices, "lambda_max", base.lambda_max)),
        cap=float(get(prices, "penalty_cap", base.cap)),
        min_blame_share=float(get(prices, "min_blame_share", base.min_blame_share)),
        price_step=float(get(immune, "price_step", base.price_step)),
        gain_step=float(get(immune, "gain_step", base.gain_step)),
        gamma_max=float(get(immune, "gamma_max", base.gamma_max)),
        tv_threshold=float(get(immune, "tv_threshold", base.tv_threshold)),
        gap_threshold=float(get(immune, "gap_threshold", base.gap_threshold)),
        novelty_share=float(get(novelty, "share", base.novelty_share)),
        seat_share=float(get(novelty, "seat_share", base.seat_share)),
        trials=int(get(novelty, "trials", base.trials)),
        sampling_step=float(get(evaluation, "sampling_step", base.sampling_step)),
        sampling_cap=float(get(evaluation, "sampling_cap", base.sampling_cap)),
        consequence_share=float(get(evaluation, "consequence_share", base.consequence_share)),
        no_swap_regret_kinds=tuple(get(evaluation, "no_swap_regret_kinds", ())),
    )


def w_sat(ph: Physics, v: float) -> int | None:
    """Windows for the PID penalty to reach ``penalty_cap`` at a constant violation ``v``.

    The least ``w >= 1`` with ``min(lambda_max, kp·v + w·eta·v)·v >= cap``: the
    penalty is ``lambda · v`` (``pricing._penalty_for``) and ``lambda = kp·v + I``
    with ``I = w·eta·v`` after ``w`` violating windows (``PriceController._pid``;
    ``kd`` adds nothing at a constant measurement). None when the cap is never
    reached (``lambda_max · v < cap``) or ``v <= 0``.
    """
    if v <= 0 or ph.lambda_max * v < ph.cap:
        return None
    if ph.eta <= 0:
        return 1 if ph.kp * v * v >= ph.cap else None
    w = max(1, math.ceil((ph.cap / (v * v) - ph.kp) / ph.eta - 1e-12))
    while min(ph.lambda_max, ph.kp * v + w * ph.eta * v) * v < ph.cap:
        w += 1
    return w


def t_release(ph: Physics, lam: float) -> int:
    """Windows for a price ``lam`` to leak to zero at ``decay`` once its violation ends."""
    return math.ceil(max(0.0, lam) / ph.decay - 1e-12)


def t_gamma(ph: Physics, gamma: float, organ_period: int) -> int:
    """Windows for gain raised to ``gamma`` to unwind: steps back × the organ's period."""
    return math.ceil(max(0.0, gamma) / ph.gain_step - 1e-12) * organ_period


def t_learn(delta: float, arms: int) -> int:
    """The EXP3 regret scale in rounds for a reward gap ``delta`` over ``arms`` arms."""
    return math.ceil(arms * math.log(arms) / (delta * delta))


# --- card regions (a pure reading of the controller's own formula) -------------------


def violation(region: Mapping, value: float) -> float:
    """Region-relative distance outside inclusive bounds (``controller.violation``)."""
    kind, lo, hi = region.get("kind"), region.get("lo"), region.get("hi")
    scale = float(region.get("scale") or 1.0)
    distance = 0.0
    if kind in ("min", "band") and lo is not None and value < lo:
        distance = lo - value
    elif kind in ("max", "band") and hi is not None and value > hi:
        distance = value - hi
    return distance / scale


def reference_violation(region: Mapping) -> float:
    """The unit violation SF-0 reads for a card, conditioned on its region kind.

    Every region is normalised by ``relative_region`` so that one unit is a whole
    breach: a nonnegative observation at zero under a positive floor (``min``,
    ``lo > 0``: distance ``lo / lo = 1``), a twofold breach of a positive ceiling
    (``max``, ``hi > 0``), one band width outside a ``band``. A zero bound keeps its
    declared units, where one unit is one declared unit. The relation holds for
    every ``v`` at or below this unit only if it holds at the unit itself, because
    ``w_sat`` is non-increasing in ``v``.
    """
    return 1.0


def sf0_relation(manifest: Mapping, *, regions: Mapping[str, Mapping] | None = None,
                 cards: Iterable[Mapping] | None = None) -> Result:
    """SF-0: the integrator still has headroom at the detection horizon, per card kind.

    Chapter II §II.b prices stable failure by "ratcheting up penalties the longer the
    factory spends": that needs ``W_sat(v_ref) >= H`` for every priced card, where
    ``H = min_ratio × k`` windows is how long the organ needs to see an attractor
    (Astra C-1: conditioned on the card's region kind through ``v_ref``, and on its
    window kind: a ``returns``- or ``forecasts``-kind card's price moves only on a
    new settled sample, at most once a window, so the windows-kind bound — one
    update per window — is the binding one and is the one checked).
    """
    ph = physics(manifest)
    charter = _section(manifest, "charter")
    cards = list(cards if cards is not None else charter.get("cards") or ())
    rows = []
    for card in cards:
        cid = card.get("id")
        window = card.get("window") or {}
        region = (regions or {}).get(cid) or card.get("region") or {}
        v_ref = reference_violation(region)
        saturates = w_sat(ph, v_ref)
        rows.append({"card": cid, "window_kind": window.get("kind"),
                     "region_kind": region.get("kind") or region.get("rule"),
                     "v_ref": v_ref, "w_sat": saturates, "H": ph.H,
                     "ok": saturates is None or saturates >= ph.H})
    if not rows:
        saturates = w_sat(ph, 1.0)
        rows.append({"card": None, "window_kind": "windows", "region_kind": None,
                     "v_ref": 1.0, "w_sat": saturates, "H": ph.H,
                     "ok": saturates is None or saturates >= ph.H})
    return _result("SF-0", all(row["ok"] for row in rows), cards=rows,
                   gain_headroom_windows=min((row["w_sat"] for row in rows
                                              if row["w_sat"] is not None), default=None))


# --- diary access ------------------------------------------------------------------------


def load_events(path: str | Path, *, kinds: Iterable[str] | None = None,
                skip: frozenset[str] = HEAVY_KINDS) -> list[dict]:
    """Ledger rows from a diary, in ledger order (``seq``).

    ``path`` is an ``events.json`` list, a ``.jsonl`` file, or a directory of
    per-kind ``<kind>.jsonl`` files (an opened diary). ``kinds`` keeps only those
    kinds; ``skip`` drops heavy kinds from a directory before they are read.
    """
    path = Path(path)
    wanted = set(kinds) if kinds is not None else None
    rows: list[dict] = []
    if path.is_dir():
        for file in sorted(path.glob("*.jsonl")):
            kind = file.stem
            if kind in skip or (wanted is not None and kind not in wanted):
                continue
            with file.open() as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
    elif path.suffix == ".jsonl":
        with path.open() as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    else:
        rows = json.loads(path.read_text())
    if wanted is not None:
        rows = [row for row in rows if row.get("kind") in wanted]
    return sorted(rows, key=lambda row: row.get("seq", 0))


def rows_of(events: Iterable[Mapping], *kinds: str) -> list[Mapping]:
    """The rows of the named kinds, in the order given."""
    return [row for row in events if row.get("kind") in kinds]


def windows(events: Iterable[Mapping]) -> list[Mapping]:
    """The organ's closed windows (``immune.window``), in order."""
    return rows_of(events, "immune.window")


def flagged(events: Iterable[Mapping], pathology: str) -> list[int]:
    """The window indexes the organ flagged ``pathology`` in."""
    return [w["window"] for w in windows(events) if (w.get("flags") or {}).get(pathology)]


def _runs(indexes: list[int]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive integers, as inclusive (start, end) pairs."""
    result: list[tuple[int, int]] = []
    for index in sorted(set(indexes)):
        if result and index == result[-1][1] + 1:
            result[-1] = (result[-1][0], index)
        else:
            result.append((index, index))
    return result


def acting_period(events: Iterable[Mapping], ph: Physics) -> int:
    """The organ's measured period in windows: the widest gap between two acting closes.

    At least ``min_ratio`` (versioning P5); the measured value when the run acted
    twice or more, since jitter only lengthens it.
    """
    acts = [w["window"] for w in windows(events) if w.get("acts")]
    gaps = [b - a for a, b in zip(acts, acts[1:], strict=False)]
    return max([ph.r, *gaps])


def card_windows(events: Iterable[Mapping]) -> list[Mapping]:
    """Each closed price window: its index, card values and regions (``price.window``)."""
    return rows_of(events, "price.window")


def card_violations(events: Iterable[Mapping], card: str) -> dict[int, float]:
    """The card's region-relative violation at each closed price window that measured it."""
    result = {}
    for row in card_windows(events):
        values, regions = row.get("values") or {}, row.get("regions") or {}
        if card in values and card in regions:
            result[row["window"]] = violation(regions[card], float(values[card]))
    return result


# --- stable failure (§3.1) ----------------------------------------------------------------


def sf1a_detection(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1a: stable failure is first flagged at most ``H`` windows after the card violates."""
    ph = physics(manifest)
    violated = card_violations(events, card)
    onset = next((w for w, v in sorted(violated.items()) if v > 0), None)
    flags = flagged(events, "stable_failure")
    if onset is None:
        return _unsupported("SF-1a", "the card was never violated", card=card)
    first = min((w for w in flags if w >= onset), default=None)
    if first is None:
        span = sum(1 for w, v in violated.items() if w >= onset and v > 0)
        if span <= ph.H:
            return _unsupported("SF-1a", "violated for no more than H windows", onset=onset)
        return _result("SF-1a", False, onset=onset, first_flag=None, H=ph.H)
    return _result("SF-1a", first <= onset + ph.H, onset=onset, first_flag=first, H=ph.H)


def sf1b_ratchet_cadence(events: list[Mapping], manifest: Mapping) -> Result:
    """SF-1b: ratchets move on the organ's loop, and duration strictly rises while flagged.

    The organ acts (gain, ratchet) only on its own loop (versioning P5), so duration is
    read on the grid of acting windows. At each acting window flagged stable failure
    and not thrash, a card the organ ratcheted at the previous acting window carries
    ``duration = previous + 1``, and a first ratchet carries 1 (Astra M-6). Two
    failures are told apart:

    * **duration reset** — the flag persisted at consecutive acting windows but the
      card's duration fell back (or the card went unratcheted in between): the
      duration price stopped ratcheting while the attractor held;
    * **missed reset** — an acting window was not flagged (a transient resolution)
      and the card's next ratchet did not start again at 1.

    Every ratchet sits in an acting, flagged window.
    """
    closes = windows(events)
    acting = [w["window"] for w in closes if w.get("acts")]
    sf = set(flagged(events, "stable_failure"))
    thrash = set(flagged(events, "thrash"))
    ratchets = rows_of(events, "immune.price_ratchet")
    if not ratchets:
        return _unsupported("SF-1b", "no ratchet was issued", flagged=len(sf))
    at: dict[tuple[str, int], int] = {(row["card_id"], row["window"]): row["duration"]
                                      for row in ratchets}
    problems = [{"unflagged_ratchet": row["window"], "card": row["card_id"]}
                for row in ratchets if row["window"] not in sf or row["window"] in thrash]
    for cid in sorted({row["card_id"] for row in ratchets}):
        previous = 0
        for window in acting:
            held = window in sf and window not in thrash
            duration = at.get((cid, window))
            if duration is not None and held and duration != previous + 1:
                kind = "missed_reset" if duration > previous + 1 else "duration_reset"
                problems.append({"card": cid, "window": window, kind: [previous, duration]})
            if held and duration is None and previous:
                problems.append({"card": cid, "window": window,
                                 "duration_reset": [previous, None]})
            previous = duration if (held and duration is not None) else 0
    return _result("SF-1b", not problems, problems=problems[:20], acting=len(acting),
                   ratchets=len(ratchets))


def _saturated_updates(events: list[Mapping], card: str, ph: Physics) -> list[Mapping]:
    """The card's price updates from the first whose penalty reached ``penalty_cap`` on."""
    updates = [row for row in rows_of(events, "price.update") if row.get("card_id") == card]
    first = next((i for i, row in enumerate(updates)
                  if row["lambda_after"] * row["violation"] >= ph.cap - 1e-12), None)
    return [] if first is None else updates[first:]


def sf1c_anti_windup(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1c: once the penalty sits at ``penalty_cap``, the integral is exactly constant.

    Wave 16 R-E (amended): "while the penalty sits at penalty_cap, λ's integrator
    does not integrate (it is frozen)". Read only on runs of consecutive updates whose
    penalty ``λ·v`` is at the cap: within each run the integral is equal, not merely
    close, from one update to the next. A run ends at any update below the cap (the
    violation eased, and the integral may then legitimately move) and a new run starts
    at the next capped update.
    """
    ph = physics(manifest)
    updates = [row for row in rows_of(events, "price.update") if row.get("card_id") == card]
    runs: list[list[Mapping]] = []
    current: list[Mapping] = []
    for row in updates:
        if row["violation"] > 0 and row["lambda_after"] * row["violation"] >= ph.cap - 1e-12:
            current.append(row)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    if not runs:
        return _unsupported("SF-1c", "the penalty never sat at the cap for two updates",
                            card=card)
    moved = [(a.get("i"), b.get("i")) for run in runs
             for a, b in zip(run, run[1:], strict=False) if a.get("i") != b.get("i")]
    return _result("SF-1c", not moved, card=card, runs=len(runs),
                   integrals=[[row.get("i") for row in run][:12] for run in runs[:3]],
                   moved=moved[:5])


def sf1d_escalation(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1d: saturation is ledgered and its duration rises by one per window.

    Wave 16 R-E: "At saturation, ledger the fact and publish it to governance". After
    ``min_ratio`` consecutive windows at the cap, a ``…saturated`` row exists for the
    card, and consecutive saturated rows carry durations rising by one.
    """
    ph = physics(manifest)
    after = _saturated_updates(events, card, ph)
    if len(after) < ph.r:
        return _unsupported("SF-1d", "fewer than min_ratio updates at the cap", card=card)
    rows = [row for row in events if str(row.get("kind", "")).endswith("saturated")
            and row.get("card_id") == card]
    durations = [row.get("duration") for row in rows]
    rising = all(isinstance(d, int) for d in durations) and all(
        b == a + 1 for a, b in zip(durations, durations[1:], strict=False))
    return _result("SF-1d", bool(rows) and rising, card=card, saturated_rows=len(rows),
                   durations=durations[:12])


def sf1e_gain(events: list[Mapping], manifest: Mapping) -> Result:
    """SF-1e: γ reaches ``gamma_max`` within ⌈(γmax−γ0)/gain_step⌉·A windows of the flag,
    and never unwinds while stable failure (without thrash) is flagged."""
    ph = physics(manifest)
    flags = flagged(events, "stable_failure")
    if not flags:
        return _unsupported("SF-1e", "stable failure was never flagged")
    thrash = set(flagged(events, "thrash"))
    period = acting_period(events, ph)
    gains = rows_of(events, "immune.gain")
    by_router: dict[str, list[Mapping]] = defaultdict(list)
    for row in gains:
        by_router[row["router"]].append(row)
    problems, reached = [], {}
    first_flag = flags[0]
    flag_set = set(flags)
    for router, rows in by_router.items():
        gamma0 = min(rows[0]["gamma_before"])
        steps = math.ceil((ph.gamma_max - gamma0) / ph.gain_step - 1e-9)
        # A router's gain is its own outer loop (time audit T2): at least the organ's
        # period, and its own measured gap between steps when that is longer.
        raised = [row["window"] for row in rows
                  if max(row["gamma_after"]) > max(row["gamma_before"])]
        own = max([period, *(b - a for a, b in zip(raised, raised[1:], strict=False))])
        top = next((row["window"] for row in rows
                    if min(row["gamma_after"]) >= ph.gamma_max - 1e-12), None)
        reached[router] = {"window": top, "period": own}
        # The first act after the flag may come up to one period late.
        if top is None or top > first_flag + (steps + 1) * own:
            if max(flags) >= first_flag + (steps + 1) * own:
                problems.append({"router": router, "reached_at": top, "steps": steps,
                                 "period": own, "first_flag": first_flag})
        for row in rows:
            if (row["window"] in flag_set and row["window"] not in thrash
                    and max(row["gamma_after"]) < max(row["gamma_before"])):
                problems.append({"router": router, "unwound_while_flagged": row["window"]})
    if not by_router:
        return _result("SF-1e", False, why="no gain row while flagged", first_flag=first_flag)
    return _result("SF-1e", not problems, problems=problems[:10], reached=reached,
                   organ_period=period)


def ld1a_accrual(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1a: each reserve window accrues exactly ``min(cap, carried + cap × accrued)``,
    ``cap = ⌊budget × novelty.share⌋`` (integer micro-USD), whatever the population did."""
    ph = physics(manifest)
    rows = rows_of(events, "novelty.window")
    if not rows:
        return _unsupported("LD-1a", "no reserve window opened")
    num, den = float(ph.novelty_share).as_integer_ratio()
    bad = []
    for row in rows:
        a_num, a_den = (int(x) for x in (str(row["accrued"]).split("/") + ["1"])[:2])
        cap = int(row["budget"]) * num // den
        expected = min(cap, int(row["carried"]) + cap * a_num // a_den)
        if cap != int(row["cap"]) or expected != int(row["amount"]):
            bad.append({"seq": row.get("seq"), "cap": row["cap"], "expected_cap": cap,
                        "amount": row["amount"], "expected": expected})
    return _result("LD-1a", not bad, windows=len(rows), bad=bad[:5])


def sf1f_route_open(events: list[Mapping], manifest: Mapping) -> Result:
    """SF-1f: the registration route stays open in every window and the reserve accrues."""
    closes = windows(events)
    if not closes:
        return _unsupported("SF-1f", "no window closed")
    shut = [w["window"] for w in closes
            if (w.get("profile") or {}).get("access:registration_route") != 1.0]
    accrual = ld1a_accrual(events, manifest)
    return _result("SF-1f", not shut and accrual.ok, closed=shut[:10],
                   accrual=accrual.status)


def penalty_by_handle(events: list[Mapping]) -> dict[str, Mapping]:
    """The ``price.penalty`` row of each settled decision."""
    return {row["handle"]: row for row in rows_of(events, "price.penalty")}


def sf2_gradient(events: list[Mapping], manifest: Mapping, *, card: str,
                 relievers: set[str], holders: set[str]) -> Result:
    """SF-2a: Δ(t) = penalty(holder) − penalty(reliever) follows the D5 attribution.

    Wave 16 D5: "A decision that relieved the card's violation bears 0; every
    non-relieving decision in the window … bears an equal share, frozen at window
    close." So in each window where the card is violated and either arm was priced:
    both arms were priced (a window that dropped every holder charge is a failure, not
    a pass), every reliever's share of ``card`` is 0, and every holder's share is
    ``1 / n_nonrelieving``, where the non-relieving set is complete: every decision
    priced on the card in that window whose seat is not a reliever, plus every NOOP
    drawn in that window by a router that drew either arm (R9: an abstention is a
    non-relieving decision too).
    """
    seats = decision_seats(events)
    actors = {row["handle"]: row.get("actor") for row in rows_of(events, "decision.open")}
    opened_in = _decision_windows(events)
    shares: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    nonrelieving: dict[int, set[str]] = defaultdict(set)
    routers: set[str] = set()
    for handle, row in penalty_by_handle(events).items():
        seat = seats.get(handle)
        for term in row.get("terms") or ():
            if term.get("card_id") != card or term.get("violation", 0) <= 0:
                continue
            if seat not in relievers:
                nonrelieving[term["window"]].add(handle)
            side = ("reliever" if seat in relievers else
                    "holder" if seat in holders else None)
            if side is not None:
                shares[term["window"]][side].append(float(term["share"]))
                routers.add(actors.get(handle))
    if not shares:
        return _unsupported("SF-2a", "no violated window priced either arm", card=card)
    for row in rows_of(events, "router.abstention_priced"):
        window = opened_in.get(row["handle"])
        if window in shares and row.get("router") in routers:
            nonrelieving[window].add(row["handle"])
    problems = []
    for window, sides in sorted(shares.items()):
        missing = [side for side in ("reliever", "holder") if not sides.get(side)]
        if missing:
            problems.append({"window": window, "missing": missing})
        if any(s != 0.0 for s in sides.get("reliever", ())):
            problems.append({"window": window, "reliever_shares": sides["reliever"][:4]})
        n = len(nonrelieving[window])
        wrong = [s for s in sides.get("holder", ()) if n == 0 or abs(s - 1 / n) > 1e-9]
        if wrong:
            problems.append({"window": window, "holder_shares": sorted(set(wrong))[:4],
                             "n_nonrelieving": n})
    return _result("SF-2a", not problems, problems=problems[:10], windows=len(shares))


def sf2b_order_blind(events: list[Mapping], manifest: Mapping, *, card: str,
                     role_of: Callable[[str], str | None] | None = None) -> Result:
    """SF-2b: two non-relieving decisions of one role in one window bear equal shares,
    whatever their settlement order (wave 16 D5's test, read over a run)."""
    seats = decision_seats(events)
    groups: dict[tuple, set[float]] = defaultdict(set)
    order: dict[tuple, list[float]] = defaultdict(list)
    for row in rows_of(events, "price.penalty"):
        role = role_of(row["handle"]) if role_of else None
        for term in row.get("terms") or ():
            if term.get("card_id") != card or term.get("violation", 0) <= 0:
                continue
            if term.get("owner") is not None or term.get("observation") in EXACT_SHARES:
                continue  # an attributable or own-contribution share is not a generic split
            key = (term["window"], role)
            groups[key].add(round(float(term["share"]), 12))
            order[key].append(float(term["share"]))
    split = {key: sorted(values) for key, values in groups.items() if len(values) > 1}
    if not groups:
        return _unsupported("SF-2b", "no violated window priced two decisions", card=card)
    # Longrun1's shape: shares falling as 1/rank of settlement in the window.
    rank_shaped = sum(1 for key in split
                      if all(b <= a for a, b in zip(order[key], order[key][1:], strict=False)))
    return _result("SF-2b", not split, split_windows=len(split), windows=len(groups),
                   rank_shaped=rank_shaped, example=next(iter(split.items()), None),
                   seats=len(set(seats.values())))


# --- decisions, draws and seats -----------------------------------------------------------


def decision_seats(events: Iterable[Mapping]) -> dict[str, str]:
    """Each decision handle's drawn arm (a seat id, or NOOP), from ``decision.open``."""
    return {row["handle"]: (row.get("propensity") or {}).get("chosen")
            for row in rows_of(events, "decision.open")}


def returned_handles(events: Iterable[Mapping]) -> set[str]:
    """Handles on which some seat returned (an ``invocation`` row of any status)."""
    return {row["handle"] for row in rows_of(events, "invocation") if row.get("handle")}


# --- thrash (§3.2) --------------------------------------------------------------------------


def thrash_series(events: list[Mapping]) -> list[tuple[int, float, float, bool]]:
    """Per closed window: (index, thrash λ, thrash penalty, thrash flagged)."""
    out = []
    for w in windows(events):
        thrash = w.get("thrash") or {}
        out.append((w["window"], float(thrash.get("lambda") or 0.0),
                    float(thrash.get("penalty") or 0.0), bool((w.get("flags") or {})
                                                            .get("thrash"))))
    return out


def th1a_detection(events: list[Mapping], manifest: Mapping, *, cycle_start: int) -> Result:
    """TH-1a: thrash is flagged at most ``H`` windows after a cycle starts."""
    ph = physics(manifest)
    first = min((w for w in flagged(events, "thrash") if w >= cycle_start), default=None)
    return _result("TH-1a", first is not None and first <= cycle_start + ph.H,
                   cycle_start=cycle_start, first_flag=first, H=ph.H)


def th1b_duration(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1b: the thrash price does not fall across consecutive flagged windows until its
    penalty reaches the cap, and rises over some such run (its integral holds duration)."""
    ph = physics(manifest)
    series = thrash_series(events)
    runs = _runs([w for w, _lam, _pen, flag in series if flag])
    by_window = {w: (lam, pen) for w, lam, pen, _flag in series}
    falls, rose = [], False
    for start, end in runs:
        # The window before the run is the price the thrash started from.
        lams = [by_window[w] for w in range(start - 1, end + 1) if w in by_window]
        for (a, pa), (b, _pb) in zip(lams, lams[1:], strict=False):
            if pa >= ph.cap - 1e-12:
                break
            if b < a - 1e-12:
                falls.append({"run": [start, end], "from": a, "to": b})
            if b > a + 1e-12:
                rose = True
    if not runs:
        return _unsupported("TH-1b", "thrash was never flagged")
    return _result("TH-1b", rose and not falls, runs=runs[:8], falls=falls[:5], rose=rose)


def th1b2_frozen(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1b (wave 16): at the cap the thrash price's integral is frozen (SF-1c's rule)."""
    result = sf1c_anti_windup(events, manifest, card="pathology:thrash")
    return Result("TH-1b-antiwindup", result.status, result.evidence)


def _tv(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    return 0.5 * math.fsum(abs(a.get(x, 0.0) - b.get(x, 0.0)) for x in set(a) | set(b))


def expected_thrash_charges(events: list[Mapping], manifest: Mapping) -> dict[str, float]:
    """Each core router draw's charge: ``min(cap, λ_t · min(1, TV))`` (``_record_movement``).

    λ_t is the thrash price the last closed window left in force before the draw; TV
    is the total variation from the same router's previous draw, over the union of
    both menus. Zero for a router's first draw.
    """
    ph = physics(manifest)
    lam, last, out = 0.0, {}, {}
    for row in events:
        kind = row.get("kind")
        if kind == "immune.window":
            lam = float((row.get("thrash") or {}).get("lambda") or 0.0)
        elif kind == "decision.open":
            prop = row.get("propensity") or {}
            router = row.get("actor")
            if not isinstance(router, str) or not router.startswith("router:"):
                continue
            if router.split(":", 1)[1].split("#")[0].split("@")[0] \
                    not in ph.no_swap_regret_kinds:
                continue
            now = dict(zip(prop.get("action_ids") or (), prop.get("probs") or (),
                           strict=False))
            before = last.get(router)
            moved = _tv(now, before) if before else 0.0
            last[router] = now
            out[row["handle"]] = min(ph.cap, lam * min(1.0, moved))
    return out


def th1c_movement(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1c: every charge is price × the router's own movement, and every one lands.

    The set of handles charged (``thrash.charged``) equals the set of core draws whose
    expected charge ``min(cap, λ_t·min(1, TV))`` is positive: a draw that did not move
    is never charged, and no draw that moved under a price goes uncharged (a mechanism
    that drops some charges fails here). Each charge equals its expected amount to
    1e-12.
    """
    expected = expected_thrash_charges(events, manifest)
    charged = rows_of(events, "thrash.charged")
    positive = {h for h, c in expected.items() if c > 0}
    if not positive and not charged:
        return _unsupported("TH-1c", "no core draw moved under a thrash price")
    landed = {row["handle"] for row in charged}
    missing, unexpected = sorted(positive - landed), sorted(landed - positive)
    bad = [{"handle": row["handle"], "charge": row["charge"],
            "expected": expected.get(row["handle"])}
           for row in charged
           if abs(float(row["charge"]) - float(expected.get(row["handle"], 0.0))) > 1e-12]
    return _result("TH-1c", not missing and not unexpected and not bad,
                   charged=len(charged), expected_positive=len(positive),
                   missing=missing[:5], unexpected=unexpected[:5], bad=bad[:5])


def th1d_frontier(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1d: no charge reaches a mean-based router, a niche decision or a frontier NOOP."""
    ph = physics(manifest)
    niche = {row["handle"] for row in rows_of(events, "niche.action")}
    bad = []
    for row in rows_of(events, "thrash.charged"):
        kind = str(row.get("router", "")).split(":", 1)[-1].split("#")[0].split("@")[0]
        if kind not in ph.no_swap_regret_kinds or row["handle"] in niche:
            bad.append({"handle": row["handle"], "router": row.get("router")})
    return _result("TH-1d", not bad, bad=bad[:5], charged=len(rows_of(events,
                                                                      "thrash.charged")))


def th1e_release(events: list[Mapping], manifest: Mapping, *, steady_from: int) -> Result:
    """TH-1e: after the cycle stops, the flag clears within H windows and the thrash price
    reaches zero within ``T_rel(λ_peak) + 1`` windows of the clear."""
    ph = physics(manifest)
    series = [s for s in thrash_series(events) if s[0] >= steady_from]
    if not series:
        return _unsupported("TH-1e", "no window after the cycle stopped")
    peak = max((lam for w, lam, _p, _f in thrash_series(events)), default=0.0)
    cleared = next((w for w, _lam, _p, flag in series if not flag
                    and all(not f for w2, _l, _pp, f in series if w2 >= w)), None)
    if cleared is None:
        return _result("TH-1e", False, why="the flag never cleared for good")
    zero = next((w for w, lam, _p, _f in series if w >= cleared and lam <= 1e-12), None)
    bound = t_release(ph, peak) + 1
    last = series[-1][0]
    ok = cleared <= steady_from + ph.H and (
        (zero is not None and zero <= cleared + bound) or last < cleared + bound)
    return _result("TH-1e", ok, cleared=cleared, zero=zero, bound=bound, peak=peak,
                   steady_from=steady_from, H=ph.H)


def th1f_priority(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1f: in a window flagged thrash and stable failure, gain moves γ down only."""
    both = set(flagged(events, "thrash")) & set(flagged(events, "stable_failure"))
    if not both:
        return _unsupported("TH-1f", "no window was flagged with both")
    bad = [row for row in rows_of(events, "immune.gain") if row["window"] in both
           and (row.get("pathology") != "thrash"
                or max(row["gamma_after"]) > max(row["gamma_before"]))]
    return _result("TH-1f", not bad, windows=sorted(both)[:10], bad=bad[:3])


def thrash_rate(events: list[Mapping], manifest: Mapping) -> tuple[int, int]:
    """(flagged, supported) closed windows after the first ``H``: the null's reading."""
    ph = physics(manifest)
    closes = windows(events)[ph.H:]
    return sum(1 for w in closes if (w.get("flags") or {}).get("thrash")), len(closes)


def _log_binom_cdf(x: int, n: int, p: float) -> float:
    """log P(Bin(n, p) <= x), by log-sum-exp over exact log terms."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 0.0 if x >= n else -math.inf
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
             + i * math.log(p) + (n - i) * math.log1p(-p) for i in range(0, x + 1)]
    top = max(terms)
    return top + math.log(math.fsum(math.exp(t - top) for t in terms))


def clopper_pearson_upper(x: int, n: int, confidence: float = 0.95) -> float:
    """The exact one-sided Clopper–Pearson upper bound on a binomial rate.

    The p with ``P(Bin(n, p) <= x) = 1 − confidence``; 1 when ``x == n``. Found by
    bisection on the exact binomial CDF (no external dependency).
    """
    if n <= 0:
        return 1.0
    if x >= n:
        return 1.0
    alpha = 1.0 - confidence
    lo, hi = x / n, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _log_binom_cdf(x, n, mid) > math.log(alpha):
            lo = mid
        else:
            hi = mid
    return hi


def th4_null(events: list[Mapping], manifest: Mapping, *, synthetic: tuple[int, int],
             confidence: float = 0.95) -> Result:
    """TH-4: iid card behaviour in a world is flagged thrash no more than the detector's
    own synthetic null allows: the world's rate after the first H windows is at most the
    one-sided Clopper–Pearson upper bound of the synthetic rate (Astra H-1)."""
    flags, n = thrash_rate(events, manifest)
    if n == 0:
        return _unsupported("TH-4", "no window after the first H")
    x_syn, n_syn = synthetic
    bound = clopper_pearson_upper(x_syn, n_syn, confidence)
    return _result("TH-4", flags / n <= bound, world=[flags, n], world_rate=flags / n,
                   synthetic=[x_syn, n_syn], bound=bound)


def th2_short_lived(events: list[Mapping], manifest: Mapping, *, loop: str) -> Result:
    """TH-2: every refactor of ``loop`` after the first yields a lifespan row, a lifespan
    shorter than its correcting loop is read as thrash with ``unsettled >= 1 − ratio``,
    and no admitted registration is refused for its speed."""
    rows = [row for row in rows_of(events, "config.lifespan") if row.get("loop") == loop]
    if not rows:
        return _unsupported("TH-2", "the loop was never refactored twice", loop=loop)
    worst = min(rows, key=lambda row: row["ratio"])
    readings = [float((w.get("thrash") or {}).get("unsettled") or 0.0)
                for w in windows(events)]
    short = worst["ratio"] < 1
    ok = (not short) or any(u >= 1 - worst["ratio"] - 1e-12 for u in readings)
    speed = [row for row in rows_of(events, "registration.rejected")
             if any(word in str(row.get("reason", "")).lower()
                    for word in ("too soon", "too fast", "lifespan", "speed", "rate limit"))]
    return _result("TH-2", ok and not speed, lifespans=len(rows), worst_ratio=worst["ratio"],
                   max_unsettled=max(readings, default=None), speed_refusals=len(speed))


def th3_governance_gap(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-3: charter activations stand at least ``min_ratio`` × the slowest loop apart.

    Chapter II §IV.c: "an inner loop must resolve itself several times faster than the
    outer loop that commands it". Each ``charter.cadence`` row states the earliest next
    activation; every later activation is at or after it.
    """
    ph = physics(manifest)
    cadence = rows_of(events, "charter.cadence")
    activations = rows_of(events, "charter.activate")
    if len(activations) < 2:
        return _unsupported("TH-3", "fewer than two activations", activations=len(activations))
    bad = []
    for row in cadence:
        later = [c for c in cadence if c["activation_ns"] > row["activation_ns"]]
        nxt = min(later, key=lambda c: c["activation_ns"], default=None)
        if nxt is not None and nxt["activation_ns"] < row["earliest_ns"]:
            bad.append({"at": row["activation_ns"], "next": nxt["activation_ns"],
                        "earliest": row["earliest_ns"]})
        span = row["earliest_ns"] - row["activation_ns"]
        if row.get("slowest_period_ns") and span < ph.r * row["slowest_period_ns"]:
            bad.append({"at": row["activation_ns"], "span": span,
                        "required": ph.r * row["slowest_period_ns"]})
    return _result("TH-3", not bad, activations=len(activations), bad=bad[:5])


# --- learning death (§3.4) -------------------------------------------------------------------


def niche_handles(events: Iterable[Mapping]) -> set[str]:
    """Decisions taken in the unhistoried niche: an unhistoried action (``niche.action``)
    or an unhistoried seat's protected compute (``novelty.compute`` that used some)."""
    return ({row["handle"] for row in rows_of(events, "niche.action")}
            | {row["handle"] for row in rows_of(events, "novelty.compute")
               if int(row.get("used") or 0) > 0})


def ld1d_exemption(events: list[Mapping], manifest: Mapping, *, minimum: int = 10) -> Result:
    """LD-1d: every niche decision bears no card penalty, exactly (wave 16 R-E amended)."""
    niche = niche_handles(events)
    priced = [row for row in rows_of(events, "price.penalty") if row["handle"] in niche]
    if len(priced) < minimum:
        return _unsupported("LD-1d", "fewer than the required niche decisions settled",
                            niche=len(priced), minimum=minimum)
    bad = [row["handle"] for row in priced if row["penalty"] != 0]
    return _result("LD-1d", not bad, niche=len(priced), penalized=bad[:10])


def ld1e_detection(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1e: a frontier quarantined for a whole tail is flagged learning-dead within H."""
    ph = physics(manifest)
    closes = windows(events)
    quarantined = [w["window"] for w in closes
                   if any(row.get("quarantined") and not row.get("core")
                          for row in w.get("frontier_invocation") or ())]
    runs = [r for r in _runs(quarantined) if r[1] - r[0] + 1 >= ph.k]
    if not runs:
        return _unsupported("LD-1e", "no frontier router was quarantined for k windows")
    dead = flagged(events, "learning_death")
    late = [r for r in runs if not any(r[0] <= w <= r[0] + ph.H for w in dead)]
    return _result("LD-1e", not late, runs=runs[:5], flagged=dead[:10])


def ld1f_hold(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1f: while learning death is flagged, no gain row lowers γ."""
    dead = set(flagged(events, "learning_death"))
    if not dead:
        return _unsupported("LD-1f", "learning death was never flagged")
    bad = [row for row in rows_of(events, "immune.gain")
           if row["window"] in dead and max(row["gamma_after"]) < max(row["gamma_before"])]
    return _result("LD-1f", not bad, flagged=len(dead), bad=bad[:3])


def i3c_niche_no_worse_than_noop(events: list[Mapping], manifest: Mapping) -> Result:
    """I-3c: a niche decision bears no more card penalty than a NOOP of the same window
    (R-E: "Failed exploration there must never be worse than NOOP", read in penalty
    terms, design Q-G2)."""
    niche = niche_handles(events)
    window_of = _decision_windows(events)
    noop_penalty: dict[int, float] = {}
    for row in rows_of(events, "router.abstention_priced"):
        window = window_of.get(row["handle"])
        if window is not None:
            noop_penalty[window] = min(noop_penalty.get(window, math.inf), row["penalty"])
    bad, checked = [], 0
    for row in rows_of(events, "price.penalty"):
        window = window_of.get(row["handle"])
        if row["handle"] in niche and window in noop_penalty:
            checked += 1
            if row["penalty"] > noop_penalty[window] + 1e-12:
                bad.append({"handle": row["handle"], "window": window,
                            "penalty": row["penalty"], "noop": noop_penalty[window]})
    if not checked:
        return _unsupported("I-3c", "no niche decision shares a window with a NOOP")
    return _result("I-3c", not bad, checked=checked, worse=bad[:5])


def _decision_windows(events: Iterable[Mapping]) -> dict[str, int]:
    """The price window each decision opened in (the window closed after it + 1)."""
    window, out = 1, {}
    for row in events:
        if row.get("kind") == "price.window":
            window = row["window"] + 1
        elif row.get("kind") == "decision.open":
            out[row["handle"]] = window
    return out


def i4a_no_blind_step_back(events: list[Mapping], manifest: Mapping) -> Result:
    """I-4a: the sampling actuator never steps the mix back while it cannot see.

    A ``sampling.lower`` on an actuator period whose history has no supported
    consequence slope (``outcome_slope`` None) reads missing evidence as compliance,
    against the versions module's own rule that "an unmeasured reading is missing
    evidence not compliance" (design I-4, P-3).
    """
    rows = rows_of(events, "sampling.lower", "sampling.raise")
    if not rows:
        return _unsupported("I-4a", "the actuator never moved")
    blind = [row for row in rows if row["kind"] == "sampling.lower"
             and row.get("outcome_slope") is None]
    return _result("I-4a", not blind, moves=len(rows), blind_lowers=len(blind),
                   example=blind[:2])


# --- overfitting (§3.3) -----------------------------------------------------------------------


def of2d_authorship(events: list[Mapping], manifest: Mapping, *,
                    seats: set[str] | None = None) -> Result:
    """OF-2d: every holdout and challenge traces to a seat's return; the kernel adds none.

    A challenge row's ``handle`` names a decision on which a seat returned; when
    ``seats`` is given, that decision's drawn arm is one of them.
    """
    returned = returned_handles(events)
    drawn = decision_seats(events)
    rows = [row for row in events if str(row.get("kind", "")).startswith(
        ("holdout.", "challenge.", "charter.challenge"))]
    proposals = [row for row in rows if row.get("handle")]
    if not rows:
        return _unsupported("OF-2d", "no holdout or challenge row")
    bad = [row for row in proposals if row["handle"] not in returned
           or (seats is not None and drawn.get(row["handle"]) not in seats
               and not _child_of_seat(events, row["handle"], seats))]
    return _result("OF-2d", bool(proposals) and not bad, rows=len(rows),
                   traced=len(proposals) - len(bad), bad=[r["handle"] for r in bad][:5])


def of1a_outside_the_loop(events: list[Mapping], manifest: Mapping) -> Result:
    """OF-1a: the realized consequence of a return is a fact of the world, the same for
    every judge that read it: every ``verdict.consequence`` row on one return, in one
    phase, carries the same ``y`` whatever that judge's ``q`` (§III.b: "from outside the
    factory's input"). A ``y`` that moved with a verdict would be the verdict grading
    itself."""
    by_return: dict[tuple, set] = defaultdict(set)
    qs: dict[tuple, set] = defaultdict(set)
    for row in rows_of(events, "verdict.consequence"):
        key = (row["about_handle"], row.get("phase"))
        by_return[key].add(row.get("y"))
        qs[key].add(row.get("q"))
    shared = {key: ys for key, ys in by_return.items() if len(qs[key]) > 1}
    if not shared:
        return _unsupported("OF-1a", "no return was judged twice with different verdicts")
    split = {str(key): sorted(ys, key=str) for key, ys in shared.items() if len(ys) > 1}
    return _result("OF-1a", not split, returns=len(shared), split=list(split.items())[:5])


def of3a_sampling_behind_return(events: list[Mapping], manifest: Mapping) -> Result:
    """OF-3a: every judge draw on a return is ledgered after that return: the sampling
    decision stays behind the return, so no seat can alias a sampler it cannot predict.

    A draw on a ``ProducerReturn`` event comes after both the event that published the
    return (its own ledger row, carried with its ``about_handle``) and the producer's
    invocation that made it."""
    returned = {row["handle"]: row["seq"] for row in rows_of(events, "invocation")}
    published = {row["event"]["id"]: ((row["event"].get("payload") or {}).get("about_handle"),
                                      row["seq"])
                 for row in rows_of(events, "event")
                 if (row.get("event") or {}).get("kind") == "ProducerReturn"}
    checked, early = 0, []
    for row in rows_of(events, "decision.open"):
        handle, event_seq = published.get(row.get("event_id"), (None, None))
        if handle in returned:
            checked += 1
            if row["seq"] <= event_seq or row["seq"] <= returned[handle]:
                early.append(row["handle"])
    if not checked:
        return _unsupported("OF-3a", "no judge draw on a return")
    return _result("OF-3a", not early, draws=checked, early=early[:5])


def of2c_holdout_bites(events: list[Mapping], manifest: Mapping, *, card: str,
                      seats: set[str], after_window: int) -> Result:
    """OF-2c: after the holdout's activation, some decision of ``seats`` bears a share of
    the card's violation *beyond* its region violation: the part the failed holdout
    adds (``PriceController.observe``'s ``holdout``), attributed to that decision."""
    region_violation = card_violations(events, card)
    closed_at = {row["window"]: row["seq"] for row in card_windows(events)}
    drawn = decision_seats(events)
    bitten, checked = [], 0
    for handle, row in penalty_by_handle(events).items():
        if drawn.get(handle) not in seats:
            continue
        for term in row.get("terms") or ():
            if term["card_id"] != card or term["window"] <= after_window:
                continue
            checked += 1
            # A decision settled while its window was still open is priced on the last
            # closed measurement (``_penalty_terms`` reads the live card samples).
            window = term["window"]
            reference = window if closed_at.get(window, math.inf) < row["seq"] else window - 1
            extra = term["violation"] - region_violation.get(reference, 0.0)
            if extra > 1e-12 and term["share"] > 0:
                bitten.append({"handle": handle, "window": term["window"], "holdout": extra})
    if not checked:
        return _unsupported("OF-2c", "no decision of the seats was priced after activation")
    return _result("OF-2c", bool(bitten), checked=checked, bitten=bitten[:5])


def _child_of_seat(events: list[Mapping], handle: str, seats: set[str]) -> bool:
    for row in rows_of(events, "invocation"):
        if row.get("handle") == handle and row.get("assembly_id") in seats:
            return True
    return False


# --- the pricing-not-steering invariants that read rows alone (design §1.2) -----------------


def s1_draw_sovereignty(events: list[Mapping], manifest: Mapping | None = None) -> Result:
    """S1: every drawn arm is the router's own sample, replayed from its logged seed.

    ``Random(rng_seed).choices(action_ids, weights=probs)[0] == chosen`` for every
    sampled ``decision.open`` (``learners.router.Router.route``), the logged
    distribution sums to 1, and every order, registration and amendment row that
    names a decision names one a seat returned on.
    """
    bad, checked = [], 0
    for row in rows_of(events, "decision.open"):
        prop = row.get("propensity") or {}
        if prop.get("source") != "sampled" or prop.get("rng_seed") is None:
            continue
        ids, probs = list(prop["action_ids"]), [float(p) for p in prop["probs"]]
        checked += 1
        drawn = Random(int(prop["rng_seed"])).choices(ids, weights=probs, k=1)[0]
        if drawn != prop["chosen"] or abs(math.fsum(probs) - 1.0) > 1e-9:
            bad.append(row["handle"])
    returned = returned_handles(events)
    acts = [row for row in events if str(row.get("kind", "")).startswith(
        ("order.intent", "registry.register", "charter.propose"))
        and isinstance(row.get("handle"), str) and row["handle"].startswith("decision-")]
    unreturned = [row["handle"] for row in acts if row["handle"] not in returned]
    if not checked:
        return _unsupported("S1", "no sampled decision")
    return _result("S1", not bad and not unreturned, draws=checked, bad_draws=bad[:5],
                   acts=len(acts), unreturned=unreturned[:5])


def s4_boundedness(events: list[Mapping], manifest: Mapping) -> Result:
    """S4: every settled penalty ≤ ``penalty_cap``; every reward and effective score in
    [0, 1]; every ratchet ends at or below ``lambda_max``."""
    ph = physics(manifest)
    bad = []
    for row in rows_of(events, "price.penalty"):
        effective = row.get("effective")
        if row["penalty"] > ph.cap + 1e-12 or (effective is not None
                                               and not 0 <= effective <= 1):
            bad.append({"kind": row["kind"], "handle": row["handle"]})
    for row in rows_of(events, "router.abstention_priced", "router.decline_priced",
                       "thrash.charged"):
        if not 0 <= float(row["reward"]) <= 1:
            bad.append({"kind": row["kind"], "handle": row["handle"]})
    for row in rows_of(events, "immune.price_ratchet"):
        if row["lambda_after"] > ph.lambda_max + 1e-12:
            bad.append({"kind": row["kind"], "card": row["card_id"]})
    return _result("S4", not bad, bad=bad[:5])


def s5_neutral_imputation(events: list[Mapping], manifest: Mapping) -> Result:
    """S5: an abstention and a declined commission are credited by one formula:
    ``clip(neutral − penalty, 0, 1)`` on the router's own neutral (R9, D4)."""
    rows = rows_of(events, "router.abstention_priced", "router.decline_priced")
    if not rows:
        return _unsupported("S5", "no abstention or decline was priced")
    bad = [row["handle"] for row in rows
           if abs(float(row["reward"]) - min(1.0, max(0.0, float(row["neutral"])
                                                      - float(row["penalty"])))) > 1e-12]
    return _result("S5", not bad, priced=len(rows), bad=bad[:5])


def s5b_observed_neutral(events: list[Mapping], manifest: Mapping) -> Result:
    """S5b / I-2b (wave 16 D4): after a router's first settled round, what nothing
    delivered is credited is the router's observed mean, never the 0.5 prior.

    Reads the priced abstentions and declines against each router's own settled
    rounds: the raw score of every seat decision it drew that settled with an observed
    score before the row (``price.penalty`` ``raw``; D4: "keep sums of raw scores, not
    effective ones"). Once a router has one, its ``neutral`` must equal their mean to
    1e-9, whatever that mean is (a router whose rounds truly average 0.5 credits 0.5);
    before the first, the prior stands and the row is not read.
    """
    seats = decision_seats(events)
    actors = {row["handle"]: row.get("actor") for row in rows_of(events, "decision.open")}
    raws: dict[str, list[float]] = defaultdict(list)
    bad, checked = [], 0
    for row in events:
        kind = row.get("kind")
        if kind == "price.penalty":
            handle = row.get("handle")
            if row.get("raw") is not None and seats.get(handle) not in (None, "NOOP"):
                raws[actors.get(handle)].append(float(row["raw"]))
        elif kind in ("router.abstention_priced", "router.decline_priced"):
            observed = raws.get(row.get("router"))
            if observed:
                checked += 1
                mean = math.fsum(observed) / len(observed)
                if abs(float(row["neutral"]) - mean) > 1e-9:
                    bad.append({"handle": row["handle"], "neutral": row["neutral"],
                                "observed_mean": mean, "rounds": len(observed)})
    if not checked:
        return _unsupported("S5b", "no abstention was priced after a settled round")
    return _result("S5b", not bad, checked=checked, mismatched=len(bad), example=bad[:3])


def s7_gain_targets(events: list[Mapping], manifest: Mapping | None = None) -> Result:
    """S7: gain rows name only the kernel's own routers, never a seat-registered learner."""
    gains = rows_of(events, "immune.gain")
    if not gains:
        return _unsupported("S7", "no gain row")
    bad = [row["router"] for row in gains if not str(row.get("router")).startswith("router:")]
    return _result("S7", not bad, gains=len(gains), bad=bad[:5])


def s8_gain_rows_uniform(events: list[Mapping], manifest: Mapping) -> Result:
    """S8 (ledger half): each gain act moves every exploration row of a router by one
    common step, within ``[seed, gamma_max]``: γ is a scalar over all arms, so the act
    redistributes uniformly and cannot favour an arm (Astra C-2). The instrumented half
    (weights untouched; per-arm change symmetric) is ``gain_neutral``."""
    ph = physics(manifest)
    gains = rows_of(events, "immune.gain")
    if not gains:
        return _unsupported("S8", "no gain row")
    bad = []
    for row in gains:
        steps = {round(b - a, 12) for a, b in zip(row["gamma_before"], row["gamma_after"],
                                                  strict=True)}
        if len(steps) != 1 or abs(next(iter(steps))) > ph.gain_step + 1e-12 \
                or max(row["gamma_after"]) > ph.gamma_max + 1e-12:
            bad.append({"router": row["router"], "window": row["window"], "steps": sorted(steps)})
    return _result("S8", not bad, gains=len(gains), bad=bad[:5])


def gain_neutral(before: Mapping[str, Any], after: Mapping[str, Any]) -> Result:
    """S8 (instrumented half): one gain act changed only γ, uniformly across arms.

    ``before`` and ``after`` are a router's saved EXP3 bases, ``{"gamma", "weights"
    (or "log_weights"), "actions"}`` lists. Guarantees: the weights are identical,
    every base's γ moved by one common step, and each arm's probability moved by the
    arm-symmetric map ``p' = a·p + b`` with one ``(a, b)`` per base — the change is
    ``(γ' − γ)(1/K − w_i/Σw)``, never a term chosen per arm.
    """
    problems = []
    rows_b, rows_a = before.get("bases") or [], after.get("bases") or []
    if len(rows_b) != len(rows_a):
        return _result("S8-instrumented", False, why="the router's rows changed")
    steps = set()
    for b, a in zip(rows_b, rows_a, strict=True):
        wb, wa = _weights(b), _weights(a)
        if wb != wa:
            problems.append({"weights_changed": True})
            continue
        steps.add(round(a["gamma"] - b["gamma"], 12))
        pb, pa = _probs(b), _probs(a)
        if len(pb) >= 2:
            k = len(pb)
            scale = (1 - a["gamma"]) / (1 - b["gamma"]) if b["gamma"] < 1 else 1.0
            shift = a["gamma"] / k - scale * b["gamma"] / k
            off = max(abs(q - (scale * p + shift)) for p, q in zip(pb, pa, strict=True))
            if off > 1e-12:
                problems.append({"asymmetric": off})
    if len(steps) > 1:
        problems.append({"steps": sorted(steps)})
    return _result("S8-instrumented", not problems, problems=problems[:5])


def _weights(base: Mapping) -> list[float]:
    """A base's weights in its action order (saved as ``{action: log weight}``)."""
    raw = base.get("log_weights", base.get("weights")) or []
    if isinstance(raw, Mapping):
        actions = list(base.get("actions") or raw)
        return [float(raw[a]) for a in actions if a in raw]
    return [float(x) for x in raw]


def _probs(base: Mapping) -> list[float]:
    """An EXP3 base's distribution over its arms: ``(1 − γ)·w/Σw + γ/K``."""
    raw = _weights(base)
    if not raw:
        return []
    if "log_weights" in base:
        top = max(raw)
        raw = [math.exp(x - top) for x in raw]
    total = math.fsum(raw)
    k, gamma = len(raw), float(base["gamma"])
    return [(1 - gamma) * w / total + gamma / k for w in raw]


# --- the registry, the replay and the sweep ---------------------------------------------------

#: Criteria that need no per-population parameter: what ``replay`` runs over a diary.
GENERIC: dict[str, Callable[[list[Mapping], Mapping], Result]] = {
    "SF-1b": sf1b_ratchet_cadence,
    "SF-1e": sf1e_gain,
    "SF-1f": sf1f_route_open,
    "LD-1a": ld1a_accrual,
    "LD-1d": ld1d_exemption,
    "LD-1e": ld1e_detection,
    "LD-1f": ld1f_hold,
    "TH-1b": th1b_duration,
    "TH-1c": th1c_movement,
    "TH-1d": th1d_frontier,
    "TH-1f": th1f_priority,
    "TH-3": th3_governance_gap,
    "OF-1a": of1a_outside_the_loop,
    "OF-3a": of3a_sampling_behind_return,
    "I-3c": i3c_niche_no_worse_than_noop,
    "I-4a": i4a_no_blind_step_back,
    "S1": s1_draw_sovereignty,
    "S4": s4_boundedness,
    "S5": s5_neutral_imputation,
    "S5b": s5b_observed_neutral,
    "S7": s7_gain_targets,
    "S8": s8_gain_rows_uniform,
}

#: Per-card criteria: ``replay`` runs each over every card the diary priced.
PER_CARD: dict[str, Callable[..., Result]] = {
    "SF-1a": sf1a_detection,
    "SF-1c": sf1c_anti_windup,
    "SF-1d": sf1d_escalation,
    "SF-2b": sf2b_order_blind,
}


def replay(events: list[Mapping], manifest: Mapping) -> list[Result]:
    """Every generic and per-card criterion over one diary, in a stable order."""
    results = [Result("SF-0", *_sf0_parts(manifest, events))]
    results += [fn(events, manifest) for fn in GENERIC.values()]
    cards = sorted({row["card_id"] for row in rows_of(events, "price.update")
                    if not str(row["card_id"]).startswith("pathology:")})
    for name, fn in PER_CARD.items():
        for card in cards:
            result = fn(events, manifest, card=card)
            results.append(Result(f"{name}[{card}]", result.status, result.evidence))
    return results


def _sf0_parts(manifest: Mapping, events: list[Mapping]) -> tuple[str, dict]:
    regions = {}
    for row in card_windows(events):
        regions.update(row.get("regions") or {})
    result = sf0_relation(manifest, regions=regions)
    return result.status, result.evidence


def _manifest_from(path: str | None) -> dict:
    if path is None:
        return {}
    text = Path(path).read_text()
    return json.loads(text) if path.endswith(".json") else tomllib.loads(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rp = sub.add_parser("replay", help="run every criterion over a dead diary")
    rp.add_argument("diary")
    rp.add_argument("--world", help="the world's TOML (or a manifest JSON) for its physics")
    rp.add_argument("--json", action="store_true", help="print JSON lines")
    sw = sub.add_parser("sweep", help="run a gauntlet population over several seeds")
    sw.add_argument("--population", required=True)
    sw.add_argument("--seeds", default="1,2,3")
    args = parser.parse_args(argv)
    if args.command == "replay":
        manifest = _manifest_from(args.world)
        events = load_events(args.diary)
        for result in replay(events, manifest):
            if args.json:
                print(json.dumps({"criterion": result.name, "status": result.status,
                                  "evidence": result.evidence}, default=str))
            else:
                evidence = json.dumps(result.evidence, default=str)
                print(f"{result.status:12} {result.name:32} {evidence[:220]}")
        return 0
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tests.gauntlet import populations  # the only runtime import, and a lazy one

    for seed in (int(s) for s in args.seeds.split(",")):
        run = populations.POPULATIONS[args.population](seed=seed)
        for result in populations.CRITERIA[args.population](run):
            print(f"seed={seed} {result.status:12} {result.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
