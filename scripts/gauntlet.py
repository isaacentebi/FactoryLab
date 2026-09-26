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
import functools
import hashlib
import json
import math
import sys
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from random import Random
from typing import Any

PASS, FAIL, UNSUPPORTED = "pass", "fail", "unsupported"

#: Row kinds a diary replay never needs (the snapshot state, raw model I/O and the
#: rendered public blocks dominate a diary's bytes and carry no criterion's evidence).
HEAVY_KINDS = frozenset({"snapshot", "io.result", "io.call", "runtime.input", "artifact.put",
                         "wake.public", "budget", "consequence.cost"})

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


class Malformed(Exception):
    """A row lacks a field its kind's emitter always writes: missing evidence, never a
    value. A criterion that reads it fails (``criterion``), naming the row and field."""

    def __init__(self, row: Mapping, path: str) -> None:
        super().__init__(f"{row.get('kind')} lacks {path}")
        self.evidence = {"kind": row.get("kind"), "field": path, "seq": row.get("seq"),
                         "handle": row.get("handle"), "window": row.get("window")}


def need(row: Mapping, path: str) -> Any:
    """The value at the dotted ``path`` of ``row`` (None only where the row holds an
    explicit null). Raises ``Malformed`` when a key on the way is absent: the caller
    reads only fields its kind's emitter always writes, so absence is a malformed row,
    never a default."""
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise Malformed(row, path)
        value = value[part]
    return value


def criterion(name: str) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    """Guarantees the criterion fails, naming the row and field, when a row it reads
    lacks a field the kernel always writes (``Malformed``), instead of reading the
    absence as a value."""
    def wrap(fn: Callable[..., Result]) -> Callable[..., Result]:
        @functools.wraps(fn)
        def run(*args: Any, **kwargs: Any) -> Result:
            try:
                return fn(*args, **kwargs)
            except Malformed as exc:
                return _result(name, False, malformed=exc.evidence,
                               why="a row lacks a field its kind's emitter always writes")
        run.criterion = name  # type: ignore[attr-defined]
        return run
    return wrap


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
    """Windows for a price ``lam`` to leak to exactly zero once its violation ends.

    Kernel-exact: ``PriceController._pid`` leaks the integral as
    ``max(0.0, integral − decay)`` a window in floating point, so the count is that
    loop's, not ``⌈lam / decay⌉`` (0.5 at decay 0.1 takes six windows, not five: the
    fifth leaves 2.8e-17).
    """
    lam, windows = max(0.0, lam), 0
    while lam > 0.0:
        lam, windows = max(0.0, lam - ph.decay), windows + 1
    return windows


def t_gamma(ph: Physics, gamma: float, organ_period: int) -> int:
    """Windows for gain raised to ``gamma`` to unwind to zero: steps back × the organ's
    period, the steps counted by ``immune._gain``'s own float loop
    (``max(floor, old − gain_step)``)."""
    gamma, steps = max(0.0, gamma), 0
    while gamma > 0.0:
        gamma, steps = max(0.0, gamma - ph.gain_step), steps + 1
    return steps * organ_period


def gain_steps(ph: Physics, gamma: float) -> int:
    """Gain acts to raise ``gamma`` to ``gamma_max``, counted by ``immune._gain``'s own
    float loop (``min(gamma_max, old + gain_step)``): kernel-exact, never a ceiling of a
    quotient."""
    steps = 0
    while gamma < ph.gamma_max:
        gamma, steps = min(ph.gamma_max, gamma + ph.gain_step), steps + 1
    return steps


def t_learn(delta: float, arms: int) -> int:
    """The EXP3 regret scale in rounds for a reward gap ``delta`` over ``arms`` arms."""
    return math.ceil(arms * math.log(arms) / (delta * delta))


# --- card regions (a pure reading of the controller's own formula) -------------------


def violation(region: Mapping, value: float) -> float:
    """Region-relative distance outside inclusive bounds (``controller.violation``)."""
    # A region is ``asdict(CardRegion)``: kind, lo, hi and a positive scale are always
    # written (charter/controller.py ``CardRegion``); lo or hi is None by the kernel's
    # own schema where the kind has no such bound.
    kind, lo, hi = need(region, "kind"), need(region, "lo"), need(region, "hi")
    scale = float(need(region, "scale"))
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


@criterion("SF-0")
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


class DiaryInvalid(ValueError):
    """A diary that fails its schema, or is not bound to the world and seed it claims."""


def _file_kind(stem: str) -> str:
    # An opened diary splits ``event`` rows by their event kind: event_<Kind>.jsonl.
    return "event" if stem.startswith("event_") else stem


def load_events(path: str | Path, *, kinds: Iterable[str] | None = None,
                skip: frozenset[str] = HEAVY_KINDS) -> list[dict]:
    """Ledger rows from a diary, in ledger order (``seq``).

    ``path`` is an ``events.json`` list, a ``.jsonl`` file, or a directory of
    per-kind ``<kind>.jsonl`` files (an opened diary). ``kinds`` keeps only those
    kinds; ``skip`` drops heavy kinds from a directory before they are read. Refused
    (``DiaryInvalid``) unless every row is an object with a ``kind`` and an integer
    ``seq``, no ``seq`` repeats, and a directory's row sits in its kind's file (an
    ``event`` row in ``event_<its event kind>.jsonl``).
    """
    path = Path(path)
    wanted = set(kinds) if kinds is not None else None
    rows: list[dict] = []
    if path.is_dir():
        for file in sorted(path.glob("*.jsonl")):
            kind = _file_kind(file.stem)
            if file.stem in skip or kind in skip or (wanted is not None
                                                     and kind not in wanted):
                continue
            with file.open() as handle:
                read = [json.loads(line) for line in handle if line.strip()]
            for row in read:
                event_kind = (row.get("event") or {}).get("kind") if isinstance(row, dict) \
                    else None
                if not isinstance(row, dict) or row.get("kind") != kind or (
                        kind == "event" and f"event_{event_kind}" != file.stem):
                    raise DiaryInvalid(f"{file.name} holds a row of another kind")
            rows.extend(read)
    elif path.suffix == ".jsonl":
        with path.open() as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    else:
        rows = json.loads(path.read_text())
    if wanted is not None:
        rows = [row for row in rows if isinstance(row, dict) and row.get("kind") in wanted]
    bad = [i for i, row in enumerate(rows) if not isinstance(row, dict)
           or not isinstance(row.get("kind"), str) or not isinstance(row.get("seq"), int)
           or isinstance(row.get("seq"), bool)]
    if bad:
        raise DiaryInvalid(f"{len(bad)} rows lack a kind or an integer seq (first: {bad[0]})")
    seqs = [row["seq"] for row in rows]
    if len(seqs) != len(set(seqs)):
        raise DiaryInvalid("a seq repeats: two rows claim one place in the ledger")
    return sorted(rows, key=lambda row: row["seq"])


def manifest_hash(manifest: Mapping) -> str:
    """``WorldManifest.manifest_hash`` of a launched manifest's JSON:
    ``sha256(json.dumps(sort_keys, compact))``."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def diary_identity(events: Iterable[Mapping]) -> dict[str, Any]:
    """The world a diary ran: its Launch event's manifest, verified against the
    ``manifest_hash`` the Launch ledgered. Refused (``DiaryInvalid``) when the diary has
    no Launch or two, or the manifest does not hash to its hash."""
    launches = [row for row in rows_of(events, "event")
                if (row.get("event") or {}).get("kind") == "Launch"]
    if len(launches) != 1:
        raise DiaryInvalid(f"{len(launches)} Launch rows: a diary is bound to one launch")
    payload = launches[0]["event"].get("payload") or {}
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict) or manifest_hash(manifest) != payload.get(
            "manifest_hash"):
        raise DiaryInvalid("the Launch manifest does not hash to its manifest_hash")
    return {"name": manifest.get("name"), "seed": manifest.get("seed"),
            "manifest_hash": payload["manifest_hash"],
            "launch_nonce": payload.get("launch_nonce"), "manifest": manifest}


def bind_diary(events: list[Mapping], *, world: str | None = None, seed: int | None = None,
               manifest: Mapping | None = None) -> tuple[Mapping, dict[str, Any]]:
    """The manifest a diary's criteria read, bound to the world and seed it claims.

    Guarantees: the diary's Launch is intact (``diary_identity``); a claimed ``world``
    is the launched manifest's name; a claimed ``seed`` is the launched manifest's
    ``seed`` (the Launch is the ledger's only record of the seed, so a run whose runtime
    overrode it must launch a manifest carrying it, as ``populations.run`` does); a
    ``manifest`` given alongside has the launched physics. Returns the launched manifest
    (the physics the world ran under) and the binding's evidence. Anything else raises
    ``DiaryInvalid``.
    """
    identity = diary_identity(events)
    launched = identity["manifest"]
    evidence = {"world": identity["name"], "manifest_hash": identity["manifest_hash"],
                "seed": identity["seed"]}
    if world is not None and identity["name"] != world:
        raise DiaryInvalid(f"the diary launched {identity['name']!r}, not {world!r}")
    if seed is not None and identity["seed"] != seed:
        raise DiaryInvalid(f"the diary launched seed {identity['seed']!r}, not {seed}")
    if manifest is not None and physics(manifest) != physics(launched):
        raise DiaryInvalid("the manifest given is not the physics the diary launched")
    return launched, evidence


def rows_of(events: Iterable[Mapping], *kinds: str) -> list[Mapping]:
    """The rows of the named kinds, in the order given."""
    return [row for row in events if row.get("kind") in kinds]


def windows(events: Iterable[Mapping]) -> list[Mapping]:
    """The organ's closed windows (``immune.window``), in order."""
    return rows_of(events, "immune.window")


def flagged(events: Iterable[Mapping], pathology: str) -> list[int]:
    """The window indexes the organ flagged ``pathology`` in."""
    # ``immune.window`` carries every pathology's flag (versions.py ``diagnose``).
    return [w["window"] for w in windows(events) if need(w, f"flags.{pathology}")]


def _runs(indexes: list[int]) -> list[tuple[int, int]]:
    """Maximal runs of consecutive integers, as inclusive (start, end) pairs."""
    result: list[tuple[int, int]] = []
    for index in sorted(set(indexes)):
        if result and index == result[-1][1] + 1:
            result[-1] = (result[-1][0], index)
        else:
            result.append((index, index))
    return result


# --- entity sets: every row kind that names an entity ------------------------------------

#: The ledger kinds that name a card, and the fields that name it. A criterion that
#: iterates cards reads ``diary_cards``: the union over every one of these, so a card
#: the diary names anywhere (registered, priced, measured, violated, ratcheted,
#: saturated, posted, refused) is judged. Enumerated from the emitting code (every
#: ``ledger.append`` whose row carries ``card_id``) and pinned against it by
#: tests/gauntlet/test_criteria_schema.py. ``values.*``/``regions.*`` are id-keyed
#: mappings. A card id is kept verbatim, except in the fields ``CARD_PREFIXED`` names,
#: where the kernel's encoding adds one ``card:`` prefix that is removed once.
CARD_SOURCES: dict[str, tuple[str, ...]] = {
    "price.register": ("card_id",),
    "price.proposed": ("card_id",),
    "price.removed": ("card_id",),
    "price.region": ("card_id",),
    "price.region_cleared": ("card_id",),
    "price.update": ("card_id",),
    "price.skipped": ("card_id",),
    "price.unparsed": ("card_id",),
    "price.unattributed": ("card_id",),
    "price.margin": ("card_id",),
    "price.window": ("values.*", "regions.*"),
    "immune.window": ("violated_cards[]", "regions.*"),
    "immune.price_ratchet": ("card_id",),
    "immune.price_ratchet_ended": ("card_id",),
    "immune.price_ratchet_saturated": ("card_id",),
    "lambda_post.adopted": ("card_id",),
    "lambda_post.aggregate": ("card_id",),
    "lambda_post.settled": ("card_id",),
    "charter.refused": ("card_id",),
}
#: The ledger kinds that name a router, and the fields that name it (only a value in the
#: kernel's router namespace, ``router:``, is a router: routing.py ``_build_router``,
#: governance.py's proposed routers). ``router_presence`` reads every one.
ROUTER_SOURCES: dict[str, tuple[str, ...]] = {
    "decision.open": ("actor",),
    "immune.gain": ("router",),
    "immune.window": ("frontier_invocation[].router", "frontier.quarantined_routers[]",
                      "frontier.uninvoked_routers[]"),
    "router.created": ("learner_id", "replaces[]"),
    "router.retained": ("learner_id",),
    "router.drained": ("learner_id",),
    "router.step_rescaled": ("learner_id",),
    "router.abstention_priced": ("router",),
    "router.decline_priced": ("router",),
    "propensity.unlearned": ("learner_id",),
    "thrash.charged": ("router",),
    "compute.route": ("router",),
    "route.excluded": ("router",),
    "request.child": ("router",),
    "actor.retire": ("actor",),
    "actor.successor": ("actor", "successor"),
}
#: The configuration loops a refactor names (TH-2 runs over each): its lifespan rows
#: and the organ's windows that carry them.
LOOP_SOURCES: dict[str, tuple[str, ...]] = {
    "config.lifespan": ("loop",),
    "immune.window": ("lifespans[].loop",),
}
#: Every entity set a criterion iterates, what builds it and from what. Sets read from
#: one kind name why no other kind can name that entity.
ENTITY_SETS: dict[str, str] = {
    "cards (replay's per-card criteria, SF-1b)": "diary_cards: CARD_SOURCES",
    "routers (SF-1e)": "router_presence: ROUTER_SOURCES",
    "loops (replay's TH-2)": "diary_loops: LOOP_SOURCES",
    "quarantined routers (LD-1e)": "immune.window frontier_invocation: the organ's "
                                   "quarantine evidence is written nowhere else "
                                   "(immune.frontier_evidence)",
    "routers with a γ (LD-1f, S7, S8)": "immune.gain: a router's γ is ledgered only "
                                        "when the organ moves it (immune._gain)",
    "core draws (TH-1c)": "decision.open: a charge is on a draw's movement, and draws "
                          "are ledgered only there (queue.open)",
    "seats (decision_seats)": "decision.open propensity.chosen: the drawn arm of a "
                              "decision is ledgered only there",
}


def _named(value: Any, path: str) -> list[Any]:
    """The values at ``path`` in ``value``: ``a.b`` descends, ``[]`` spreads a list, and
    ``*`` takes a mapping's keys."""
    head, _, rest = path.partition(".")
    if head == "*":
        return list(value) if isinstance(value, Mapping) else []
    spread = head.endswith("[]")
    item = value.get(head.removesuffix("[]")) if isinstance(value, Mapping) else None
    items = list(item) if spread and isinstance(item, list | tuple) else [item]
    if not rest:
        return [x for x in items if x is not None]
    return [x for i in items for x in _named(i, rest)]


def _entities(events: Iterable[Mapping], sources: Mapping[str, tuple[str, ...]]
              ) -> Iterator[tuple[Mapping, str]]:
    """Each (row, name) where a row of a source kind names an entity."""
    for row in events:
        for path in sources.get(row.get("kind"), ()):
            for name in _named(row, path):
                if isinstance(name, str) and name:
                    yield row, name


#: The card fields whose kernel encoding prefixes the id with ``card:``, each with the
#: line that writes it. Only here is one prefix removed; everywhere else the id is the
#: card's own, verbatim (a card registered as ``card:latency`` keeps that id).
CARD_PREFIXED: dict[tuple[str, str], str] = {
    ("immune.window", "regions.*"):
        'immune.py close_window: "regions": {f"card:{cid}": ...} (immune.py:303)',
    ("immune.window", "violated_cards[]"):
        "live.persistent_violations returns the window's regions keys, f\"card:{cid}\" "
        "(immune.py:303); the organ removes the prefix once to ratchet (immune.py:359)",
}


def diary_cards(events: Iterable[Mapping]) -> list[str]:
    """Every card the diary names, from every kind that names one (``CARD_SOURCES``),
    pathology cards (``pathology:thrash``, the thrash price's own) included. Ids are
    verbatim, except one ``card:`` prefix removed where the kernel adds it
    (``CARD_PREFIXED``)."""
    names = set()
    for row in events:
        kind = row.get("kind")
        for path in CARD_SOURCES.get(kind, ()):
            for name in _named(row, path):
                if isinstance(name, str) and name:
                    names.add(name.removeprefix("card:") if (kind, path) in CARD_PREFIXED
                              else name)
    return sorted(names)


def diary_loops(events: Iterable[Mapping]) -> list[str]:
    """Every configuration loop a refactor names (``LOOP_SOURCES``)."""
    return sorted({name for _row, name in _entities(events, LOOP_SOURCES)})


def acting_period(events: Iterable[Mapping], ph: Physics) -> int:
    """The organ's measured period in windows: the widest gap between two acting closes.

    At least ``min_ratio`` (versioning P5); the measured value when the run acted
    twice or more, since jitter only lengthens it.
    """
    acts = [w["window"] for w in windows(events) if need(w, "acts")]
    gaps = [b - a for a, b in zip(acts, acts[1:], strict=False)]
    return max([ph.r, *gaps])


def card_windows(events: Iterable[Mapping]) -> list[Mapping]:
    """Each closed price window: its index, card values and regions (``price.window``)."""
    return rows_of(events, "price.window")


def card_violations(events: Iterable[Mapping], card: str) -> dict[int, float]:
    """The card's region-relative violation at each closed price window that measured it."""
    result = {}
    for row in card_windows(events):
        # ``price.window`` always writes both (pricing.py ``close_window``).
        values, regions = need(row, "values"), need(row, "regions")
        if card in values and card in regions:
            result[row["window"]] = violation(regions[card], float(values[card]))
    return result


# --- stable failure (§3.1) ----------------------------------------------------------------


def violation_episodes(violated: Mapping[int, float], k: int) -> list[tuple[list[int], int]]:
    """The card's violation episodes as the kernel reads persistence: each episode's
    measured violating windows, and the last window its evidence can still be read.

    ``versions.diagnose`` reads ``live.persistent_violations`` over the tail of the last
    ``k`` closed windows: a card fails while every window of the tail that measured it
    violated, and one did. So an episode is a run of measured violations with no measured
    compliance between them; a window that did not measure the card neither ends nor
    extends it; and it expires when its evidence leaves the tail, when ``k`` windows pass
    with no measurement (consecutive measured violations more than ``k`` windows apart
    are two episodes). Its end is the window before a measured compliance, or the last
    window whose tail still holds its last measurement.
    """
    episodes: list[tuple[list[int], int]] = []
    current: list[int] | None = None
    for window, value in sorted(violated.items()):
        if value > 0:
            if current is not None and window - current[-1] <= k:
                current.append(window)
                continue
            if current is not None:
                episodes.append((current, current[-1] + k - 1))
            current = [window]
        elif current is not None:
            episodes.append((current, min(window - 1, current[-1] + k - 1)))
            current = None
    if current is not None:
        episodes.append((current, current[-1] + k - 1))
    return episodes


def card_flagged(events: Iterable[Mapping], card: str) -> list[int]:
    """The windows the organ flagged stable failure *on this card*: flagged, with the card
    among the tail's persistently violated cards (``violated_cards``, the kernel's own
    per-card reading, ``versions.diagnose``)."""
    return [w["window"] for w in windows(events)
            if need(w, "flags.stable_failure")
            and f"card:{card}" in need(w, "violated_cards")]


@criterion("SF-1a")
def sf1a_detection(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1a: in every violation episode of the card, stable failure is flagged on it by
    the time ``H`` measured violations past the episode's onset have been observed.

    Episodes are the kernel's (``violation_episodes``, K-SF: ``live.py:334`` read by
    ``versions.py:86``): measured violations, reset by measured compliance, expired when
    their evidence leaves the ``k``-window tail, never summed across episodes. Duration
    counts measured violating observations, never unmeasured gap windows: the deadline is
    the window of the episode's ``H + 1``-th measured violation (with a measurement every
    window, ``onset + H``). Only a flag naming this card, inside the episode, counts. Per
    episode: a flag by the deadline detects it; a later flag, or none once the episode
    holds more than ``H`` observations, fails it; a shorter unflagged episode is no
    evidence. ``pass`` needs one detected episode and no failed one.
    """
    ph = physics(manifest)
    episodes = violation_episodes(card_violations(events, card), ph.k)
    if not episodes:
        return _unsupported("SF-1a", "the card was never violated", card=card)
    flags = card_flagged(events, card)
    detected, failed, short = [], [], []
    for observed, end in episodes:
        onset = observed[0]
        first = min((w for w in flags if onset <= w <= end), default=None)
        deadline = observed[ph.H] if len(observed) > ph.H else None
        entry = {"onset": onset, "observations": len(observed), "end": end,
                 "first_flag": first, "deadline": deadline, "H": ph.H}
        if first is not None and (deadline is None or first <= deadline):
            detected.append(entry)
        elif first is not None or deadline is not None:
            failed.append(entry)
        else:
            short.append(entry)
    if not detected and not failed:
        return _unsupported("SF-1a", "no episode held more than H measured violations",
                            card=card, episodes=short[:5])
    return _result("SF-1a", not failed, card=card, detected=detected[:5], failed=failed[:5],
                   short=len(short))


@criterion("SF-1b")
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

    Every ratchet sits in an acting window flagged on its own card, and every card the
    controller knew (``price.register``, not ``price.removed``) that such a window names
    among its ``violated_cards`` is ratcheted there (**missed ratchet** otherwise). The
    cards read are every card the diary names (``diary_cards``). The episode is the
    card's own (A, ``attractor_windows``): it begins where the kernel names the card
    among a flagged window's ``violated_cards`` and lasts while the flag holds (without
    thrash) and no measurement shows the card compliant. A window that did not measure
    the card is missing evidence, not compliance (``live.persistent_violations``), so a
    card that drops out of ``violated_cards`` only for want of a measurement is still in
    its attractor, and a reset there is a duration reset (Astra M-6, longrun1 window
    24). ``pass`` needs one rise observed (a ratchet of duration 2 or more): a run of
    first ratchets alone never exercised the duration.
    """
    closes = windows(events)
    acting = [w["window"] for w in closes if need(w, "acts")]
    thrash = set(flagged(events, "thrash"))
    # One prefix removed, as the organ does (CARD_PREFIXED; immune.py:359).
    holding = {w["window"]: {c.removeprefix("card:") for c in need(w, "violated_cards")}
               for w in closes if need(w, "flags.stable_failure")
               and w["window"] not in thrash}
    ratchets = rows_of(events, "immune.price_ratchet")
    at: dict[tuple[str, int], int] = {(row["card_id"], row["window"]): row["duration"]
                                      for row in ratchets}
    problems = [{"unflagged_ratchet": row["window"], "card": row["card_id"]}
                for row in ratchets if row["card_id"] not in holding.get(row["window"], ())]
    # The cards the controller knew at each close: registered (``price.register``) and
    # not removed (``price.removed``). The organ ratchets every violated card of a
    # flagged acting window that it knows (immune.close_window: ``for cid in
    # diagnosed["violated_cards"]: if card_id in known: ratchet``).
    known: set[str] = set()
    known_at: dict[int, frozenset[str]] = {}
    for row in events:
        kind = row.get("kind")
        if kind == "price.register":
            known.add(row["card_id"])
        elif kind == "price.removed":
            known.discard(row["card_id"])
        elif kind == "immune.window":
            known_at[row["window"]] = frozenset(known)
    # Every card the diary names (``diary_cards``), not only the ratcheted ones: a card
    # the organ should have ratcheted and never did is read too.
    for cid in diary_cards(events):
        attractor = attractor_windows(closes, holding, card_violations(events, cid), cid)
        previous = 0
        for window in acting:
            held = window in attractor
            duration = at.get((cid, window))
            if duration is not None and held and duration != previous + 1:
                kind = "missed_reset" if duration > previous + 1 else "duration_reset"
                problems.append({"card": cid, "window": window, kind: [previous, duration]})
            if held and duration is None and previous:
                problems.append({"card": cid, "window": window,
                                 "duration_reset": [previous, None]})
            elif (duration is None and cid in holding.get(window, ())
                  and cid in known_at.get(window, ())):
                problems.append({"card": cid, "window": window, "missed_ratchet": True})
            previous = duration if (held and duration is not None) else 0
    if not ratchets and not problems:
        return _unsupported("SF-1b", "no ratchet was issued", flagged=len(holding))
    rose = any(row["duration"] >= 2 for row in ratchets)
    if not problems and not rose:
        return _unsupported("SF-1b", "no ratchet followed another: the duration never had "
                            "a chance to rise", ratchets=len(ratchets))
    return _result("SF-1b", not problems, problems=problems[:20], acting=len(acting),
                   ratchets=len(ratchets))


def attractor_windows(closes: list[Mapping], holding: Mapping[int, set[str]],
                      measured: Mapping[int, float], card: str) -> set[int]:
    """The windows a card sits in its failing attractor: from a window flagged stable
    failure without thrash that names it among ``violated_cards``, while the flag holds
    and until a window measures it compliant (an unmeasured window ends nothing)."""
    inside, held = False, set()
    for w in closes:
        window = w["window"]
        if window not in holding:
            inside = False
        elif card in holding[window]:
            inside = True
        elif window in measured and measured[window] <= 0:
            inside = False
        if inside:
            held.add(window)
    return held


def capped_runs(events: list[Mapping], card: str, ph: Physics) -> list[list[Mapping]]:
    """The card's maximal runs of consecutive price updates whose penalty ``λ·v`` sits at
    ``penalty_cap`` with the violation persisting. Any update below the cap ends a run.

    "At the cap" is the kernel's own branch, exactly: ``_penalty_for`` bears
    ``min(λ·v, penalty_cap)``, so the cap binds when ``λ·v >= penalty_cap``."""
    runs: list[list[Mapping]] = []
    current: list[Mapping] = []
    for row in rows_of(events, "price.update"):
        if row.get("card_id") != card:
            continue
        if row["violation"] > 0 and row["lambda_after"] * row["violation"] >= ph.cap:
            current.append(row)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def update_windows(events: Iterable[Mapping]) -> dict[int, int]:
    """Each ``price.update`` row's price window, by the row's ``id``: the window whose
    ``price.window`` row closed at the same ``window_end_event``."""
    # Both kinds always write ``window_end_event`` (pricing.py, controller.py ``observe``).
    closes = {need(row, "window_end_event"): row["window"] for row in card_windows(events)}
    return {id(row): closes[need(row, "window_end_event")]
            for row in rows_of(events, "price.update")
            if need(row, "window_end_event") in closes}


@criterion("SF-1c")
def sf1c_anti_windup(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1c: once the penalty sits at ``penalty_cap``, the integral is exactly constant.

    Wave 16 R-E (amended): "while the penalty sits at penalty_cap, λ's integrator
    does not integrate (it is frozen)". Read only on runs of consecutive updates whose
    penalty ``λ·v`` is at the cap (``capped_runs``): within each run the integral is
    equal, not merely close, from one update to the next. A run ends at any update below
    the cap (the violation eased, and the integral may then legitimately move) and a new
    run starts at the next capped update.
    """
    ph = physics(manifest)
    runs = [run for run in capped_runs(events, card, ph) if len(run) >= 2]
    if not runs:
        return _unsupported("SF-1c", "the penalty never sat at the cap for two updates",
                            card=card)
    # ``price.update`` always carries the PID's integral ``i`` (controller.py ``_pid``
    # terms): two rows without one are malformed, never "equal".
    moved = [(need(a, "i"), need(b, "i")) for run in runs
             for a, b in zip(run, run[1:], strict=False) if need(a, "i") != need(b, "i")]
    return _result("SF-1c", not moved, card=card, runs=len(runs),
                   integrals=[[row.get("i") for row in run][:12] for run in runs[:3]],
                   moved=moved[:5])


@criterion("SF-1d")
def sf1d_escalation(events: list[Mapping], manifest: Mapping, *, card: str) -> Result:
    """SF-1d: sustained saturation is ledgered and its duration rises by one per window.

    Wave 16 R-E: "At saturation, ledger the fact and publish it to governance". Demanded
    only once the penalty has sat at the cap for ``min_ratio`` consecutive updates (the
    same partition as SF-1c: one capped update followed by uncapped ones is not
    sustained saturation). Durations are read per saturation episode (A): an episode
    starts at duration 1 and each next row is the previous plus one, at the next window;
    a new episode may start at 1 after the cap released, never mid-count. Episodes are aligned, not
    counted: every sustained run needs an episode whose rows *inside the run's windows*
    (``update_windows``) reach a duration of ``min_ratio``: overlapping the run, or
    reaching ``min_ratio`` outside it, is not escalation of that run. A saturation row
    names its ``window``.
    """
    ph = physics(manifest)
    at = update_windows(events)
    sustained = [run for run in capped_runs(events, card, ph) if len(run) >= ph.r]
    longest = max((len(run) for run in capped_runs(events, card, ph)), default=0)
    rows = [row for row in events if str(row.get("kind", "")).endswith("saturated")
            and row.get("card_id") == card]
    episodes: list[list[Mapping]] = []
    malformed = []
    current: list[Mapping] | None = None
    for row in rows:
        d, window = row.get("duration"), row.get("window")
        if not isinstance(window, int) or isinstance(window, bool):
            malformed.append({"duration": d, "window": window})
            current = None
        elif isinstance(d, int) and not isinstance(d, bool) and d == 1:
            current = [row]
            episodes.append(current)
        elif (current is not None and isinstance(d, int) and not isinstance(d, bool)
              and d == current[-1]["duration"] + 1 and window == current[-1]["window"] + 1):
            current.append(row)
        else:
            # A broken count stays broken until a new episode starts at 1.
            malformed.append({"duration": d, "window": window})
            current = None
    spans = [(min(r["window"] for r in e), max(r["window"] for r in e), e[-1]["duration"])
             for e in episodes]
    if not sustained:
        if malformed:
            # A broken saturation count is observed whatever the runs show.
            return _result("SF-1d", False, card=card, saturated_rows=len(rows),
                           malformed=malformed[:5], longest_run=longest)
        return _unsupported("SF-1d", "the penalty never sat at the cap for min_ratio "
                            "consecutive updates", card=card, longest_run=longest)
    unmatched = []
    for run in sustained:
        windows_of = [at[id(row)] for row in run if id(row) in at]
        if not windows_of:
            unmatched.append({"run": None, "why": "the run's windows are not ledgered"})
            continue
        lo, hi = min(windows_of), max(windows_of)
        if not any(max((x["duration"] for x in e if lo <= x["window"] <= hi), default=0)
                   >= ph.r for e in episodes):
            unmatched.append({"run": [lo, hi]})
    return _result("SF-1d", bool(rows) and not malformed and not unmatched,
                   card=card, saturated_rows=len(rows), episodes=spans[:6],
                   sustained_runs=len(sustained), unmatched=unmatched[:5],
                   malformed=malformed[:5], longest_run=longest)


def gamma_of(values: list[float]) -> float:
    """A router's γ as the kernel reads it: its first base's (``immune.gamma``,
    ``_bases(learner.state())[0]["gamma"]``)."""
    return values[0]


def lowers(row: Mapping) -> bool:
    """Whether a gain row lowered γ on any base (every base is a γ the router draws with)."""
    return any(b < a for a, b in zip(row["gamma_before"], row["gamma_after"], strict=True))


def raises(row: Mapping) -> bool:
    """Whether a gain row raised γ on any base."""
    return any(b > a for a, b in zip(row["gamma_before"], row["gamma_after"], strict=True))


def router_presence(events: list[Mapping]) -> dict[str, int]:
    """Each router's first window in the diary, over every kind that names a router
    (``ROUTER_SOURCES``): a row the organ writes (``immune.*``) at its own ``window``,
    any other in the price window it was written in (the window closed after it + 1).
    Only names in the kernel's router namespace (``router:``) are routers."""
    window, first = 1, {}
    for row in events:
        kind = row.get("kind")
        if kind == "price.window":
            window = row["window"] + 1
        at = row["window"] if (str(kind).startswith("immune.")
                               and isinstance(row.get("window"), int)) else window
        for _row, name in _entities([row], ROUTER_SOURCES):
            if name.startswith("router:"):
                first[name] = min(first.get(name, at), at)
    return first


def router_retirements(events: list[Mapping]) -> dict[str, int]:
    """The window each router was replaced in: a ``router.created`` row naming it in
    ``replaces`` (routing.py ``_build_router``: the replaced router is retained, not
    stepped, since ``_all_router_states`` holds only live routers)."""
    window, out = 1, {}
    for row in events:
        if row.get("kind") == "price.window":
            window = row["window"] + 1
        elif row.get("kind") == "router.created":
            for old in need(row, "replaces"):  # routing.py ``_build_router``: always
                out.setdefault(old, window)
    return out


def router_round_periods(events: list[Mapping]) -> dict[str, int]:
    """Each router's round period in windows: the p90 of its rounds' closures, from the
    window a decision it drew opened in to the window the decision settled in
    (``decision.settle``, when the router learns the round), never below 1. It is the
    world's measure of the router's loop (``clockwork.record("router:<kind>")`` in
    ``FeedbackMixin._learn_router_return``), read from the rounds, never from the gain
    rows it bounds."""
    opened = _decision_windows(events)
    actor = {row["handle"]: row.get("actor") for row in rows_of(events, "decision.open")}
    window, closures = 1, defaultdict(list)
    for row in events:
        if row.get("kind") == "price.window":
            window = row["window"] + 1
        elif row.get("kind") == "decision.settle":
            handle = need(row, "return.handle")  # queue.py ``settle``: always
            router, start = actor.get(handle), opened.get(handle)
            if isinstance(router, str) and start is not None:
                closures[router].append(max(0, window - start))
    out = {}
    for router, values in closures.items():
        ordered = sorted(values)
        out[router] = max(1, ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)])
    return out


@criterion("SF-1e")
def sf1e_gain(events: list[Mapping], manifest: Mapping) -> Result:
    """SF-1e: in each stable-failure episode, γ reaches ``gamma_max`` within
    ``(steps + 1)·A`` windows of the episode's first window, and never unwinds while
    stable failure (without thrash) is flagged.

    An episode is a run of consecutive windows flagged stable failure and not thrash
    (``immune.close_window`` raises gain exactly there: thrash takes priority), read
    from its own start, never across episodes (A). Per router and episode, γ₀ is the
    router's latest state before its bound starts (the last earlier gain row's
    ``gamma_after``, else the first row's ``gamma_before``), read as the kernel reads γ
    (``gamma_of``, the first base); ``steps`` is ``gain_steps(γ₀)``, the kernel's own
    float loop (C); ``A`` is the cadence the kernel allows that router's gain
    (``immune._gain``, time audit T2): the organ's own (``acting_period``, never below
    ``min_ratio``), or ``min_ratio`` times the router's round period
    (``router_round_periods``) when that is longer. Both are read from the organ's closes
    and the router's rounds, never from the gain rows under test, which would let a slow
    router set its own deadline. A router's bound starts at the episode's start, or
    at the router's first window (``router_presence``) when it was registered during
    the episode; a router that first appears after the episode is not judged by it. An
    unwind is a lowering of any base (``lowers``).

    The routers judged are every router with decisions in the diary
    (``router_presence``), not only those with gain rows; one replaced before its bound
    (``router_retirements``) is no evidence.

    ``fail`` when an episode stayed flagged through a router's bound without that router
    reaching the top, or γ unwound while flagged; ``unsupported`` when an episode still
    open at the diary's end has a bound beyond it, or when a router present while a step
    was due has no gain row at all (the kernel writes none for a router already at
    ``gamma_max``, immune.py ``_gain``, so γ is unobserved); ``pass`` needs one (router, episode)
    that reached the top within its bound (B). An episode that resolved before a
    router's bound is no evidence for that router.
    """
    ph = physics(manifest)
    closes = windows(events)
    flags = flagged(events, "stable_failure")
    if not flags:
        return _unsupported("SF-1e", "stable failure was never flagged")
    thrash = set(flagged(events, "thrash"))
    episodes = _runs([w for w in flags if w not in thrash])
    period = acting_period(events, ph)
    by_router: dict[str, list[Mapping]] = defaultdict(list)
    for row in rows_of(events, "immune.gain"):
        by_router[row["router"]].append(row)
    flag_set = set(flags)
    presence = router_presence(events)
    # Every router the diary names (``router_presence``: created, drawing, charged,
    # carried, in the organ's frontier evidence), not only those with gain rows: the
    # kernel's routers are named ``router:<kind>`` (routing.py ``_build_router``) or
    # ``router:<kind>:<learner>:<n>`` (governance.py, a proposed router); an
    # ``assembly:`` actor (a committee seat, a market post) is no router and has no gain.
    routers = sorted(set(by_router) | set(presence))
    if not routers:
        return _unsupported("SF-1e", "no router drew a decision or had its gain moved")
    retired = router_retirements(events)
    last = max(w["window"] for w in closes)
    problems, reached, pending, resolved, stateless = [], {}, [], 0, []
    rounds = router_round_periods(events)
    for router in routers:
        rows = by_router.get(router, [])
        born = presence[router]  # every router judged is present (gain rows included)
        gone = retired.get(router)
        own = max(period, ph.r * rounds.get(router, 1))
        for start, end in episodes:
            if born > end or (gone is not None and gone <= start):
                continue  # the router did not exist during this episode
            begin = max(start, born)
            if not rows:
                # A present router with no gain row has no observable γ. The kernel
                # writes none for a router already at gamma_max (immune.py ``_gain``:
                # ``if before == after: continue``) or not yet due (``clockwork.due``),
                # so its absence is neither a pass nor a failure once a step was due.
                if min(end, gone - 1 if gone is not None else end) >= begin + own:
                    stateless.append({"router": router, "episode": [start, end],
                                      "begin": begin, "period": own})
                else:
                    resolved += 1
                continue
            prior = [row for row in rows if row["window"] < begin]
            inside = [row for row in rows if begin <= row["window"] <= end]
            gamma0 = (gamma_of(prior[-1]["gamma_after"]) if prior
                      else gamma_of(rows[0]["gamma_before"]))
            steps = gain_steps(ph, gamma0)
            top = begin if steps == 0 else next(
                (row["window"] for row in inside
                 if gamma_of(row["gamma_after"]) >= ph.gamma_max), None)
            # The first act after the flag may come up to one period late.
            bound = begin + (steps + 1) * own
            entry = {"router": router, "episode": [start, end], "begin": begin,
                     "reached_at": top, "steps": steps, "period": own, "bound": bound}
            if top is not None and top <= bound:
                reached.setdefault(router, {"window": top, "period": own,
                                            "episode": [start, end]})
            elif gone is not None and gone <= bound:
                resolved += 1  # replaced before its bound: the kernel stops stepping it
            elif end >= bound:
                problems.append(entry)
            elif end == last:
                pending.append(entry)
            else:
                resolved += 1
        for row in rows:
            if row["window"] in flag_set and row["window"] not in thrash and lowers(row):
                problems.append({"router": router, "unwound_while_flagged": row["window"]})
    evidence = {"problems": problems[:10], "reached": reached, "pending": pending[:10],
                "resolved": resolved, "stateless": stateless[:10],
                "episodes": episodes[:10], "organ_period": period}
    if problems:
        return _result("SF-1e", False, **evidence)
    if pending:
        return _unsupported("SF-1e", "an open episode's bound lies beyond the diary",
                            **evidence)
    if stateless:
        return _unsupported("SF-1e", "a router present while a step was due has no gain "
                            "row, so its γ is unobserved", **evidence)
    if not reached:
        return _unsupported("SF-1e", "every episode resolved before a router's bound",
                            **evidence)
    return _result("SF-1e", True, **evidence)


def novelty_share_ratio(ph: Physics) -> tuple[int, int]:
    """The novelty share as ``NoveltyReserve`` holds it: ``Decimal(str(share))``, as an
    exact integer ratio."""
    return Decimal(str(ph.novelty_share)).as_integer_ratio()


@criterion("LD-1a")
def ld1a_accrual(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1a: each reserve window accrues exactly ``min(cap, carried + cap × accrued)``,
    ``cap = ⌊budget × novelty.share⌋`` (integer micro-USD), whatever the population did.

    The arithmetic is ``NoveltyReserve.open_window``'s own: the share as
    ``Decimal(str(share))`` (never the float's binary expansion: at share 0.3 on 10 µUSD
    the kernel's cap is 3, a float ratio's 2), its exact integer ratio, integer floor
    division, and ``accrued`` as the exact ``Fraction`` the row records.
    """
    ph = physics(manifest)
    rows = rows_of(events, "novelty.window")
    if not rows:
        return _unsupported("LD-1a", "no reserve window opened")
    num, den = novelty_share_ratio(ph)
    bad = []
    for row in rows:
        accrued = Fraction(str(row["accrued"]))
        cap = int(row["budget"]) * num // den
        expected = min(cap, int(row["carried"]) + cap * accrued.numerator // accrued.denominator)
        if cap != int(row["cap"]) or expected != int(row["amount"]):
            bad.append({"seq": row.get("seq"), "cap": row["cap"], "expected_cap": cap,
                        "amount": row["amount"], "expected": expected})
    return _result("LD-1a", not bad, windows=len(rows), bad=bad[:5])


@criterion("SF-1f")
def sf1f_route_open(events: list[Mapping], manifest: Mapping) -> Result:
    """SF-1f: the registration route stays open in every window that measured it, and the
    reserve accrues.

    ``access:registration_route`` is ``1.0`` present, ``0.0`` lost, and absent or null
    when unknown (``immune.close_window``: ``None if present is None``): an unmeasured
    window is missing evidence, never a shut route. With no window that measured the
    route, the result is ``unsupported``.
    """
    closes = windows(events)
    # The profile is always written; an ``access:`` key absent from it is the organ's own
    # "unknown" (versions.py ``ACCESS``: "absent unknown"), read as unmeasured.
    readings = {w["window"]: need(w, "profile").get("access:registration_route")
                for w in closes}
    measured = {w: v for w, v in readings.items() if v is not None}
    shut = [w for w, v in measured.items() if v != 1.0]
    accrual = ld1a_accrual(events, manifest)
    if shut or accrual.status == FAIL:
        # An observed violation (a shut route, a wrong accrual) fails whatever else is
        # missing.
        return _result("SF-1f", False, closed=shut[:10], measured=len(measured),
                       accrual=accrual.status)
    if not closes:
        return _unsupported("SF-1f", "no window closed", accrual=accrual.status)
    if not shut and not measured:
        return _unsupported("SF-1f", "no window measured the registration route",
                            unmeasured=len(readings))
    if not shut and accrual.status == UNSUPPORTED:
        return _unsupported("SF-1f", "no reserve window opened", closed=[])
    return _result("SF-1f", not shut and accrual.ok, closed=shut[:10],
                   measured=len(measured), accrual=accrual.status)


def penalty_by_handle(events: list[Mapping]) -> dict[str, Mapping]:
    """The ``price.penalty`` row of each settled decision."""
    return {row["handle"]: row for row in rows_of(events, "price.penalty")}


@criterion("SF-2a")
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
    routers: dict[int, set[str]] = defaultdict(set)
    for handle, row in penalty_by_handle(events).items():
        seat = seats.get(handle)
        for term in need(row, "terms"):  # pricing.py ``_settle_priced``: always
            if need(term, "card_id") != card or need(term, "violation") <= 0:
                continue
            if seat not in relievers:
                nonrelieving[term["window"]].add(handle)
            side = ("reliever" if seat in relievers else
                    "holder" if seat in holders else None)
            if side is not None:
                shares[term["window"]][side].append(float(term["share"]))
                routers[term["window"]].add(actors.get(handle))
    if not shares:
        return _unsupported("SF-2a", "no violated window priced either arm", card=card)
    for row in rows_of(events, "router.abstention_priced"):
        window = opened_in.get(row["handle"])
        # A NOOP is non-relieving in its window only for a router that drew either arm
        # in that same window (R9, D5): the draws it abstained among.
        if window in shares and need(row, "router") in routers.get(window, ()):
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


@criterion("SF-2b")
def sf2b_order_blind(events: list[Mapping], manifest: Mapping, *, card: str,
                     role_of: Callable[[str], str | None] | None = None) -> Result:
    """SF-2b: two non-relieving decisions of one role in one window bear equal shares,
    whatever their settlement order (wave 16 D5's test, read over a run)."""
    seats = decision_seats(events)
    groups: dict[tuple, set[float]] = defaultdict(set)
    order: dict[tuple, list[float]] = defaultdict(list)
    for row in rows_of(events, "price.penalty"):
        role = role_of(row["handle"]) if role_of else None
        for term in need(row, "terms"):  # pricing.py ``_settle_priced``: always
            if need(term, "card_id") != card or need(term, "violation") <= 0:
                continue
            if term.get("owner") is not None or term.get("observation") in EXACT_SHARES:
                continue  # an attributable or own-contribution share is not a generic split
            key = (term["window"], role)
            groups[key].add(round(float(term["share"]), 12))
            order[key].append(float(term["share"]))
    # An equality needs a pair (B): only a (window, role) group of two or more decisions
    # can show its shares equal or split.
    comparable = {key for key, shares in order.items() if len(shares) >= 2}
    split = {key: sorted(groups[key]) for key in comparable if len(groups[key]) > 1}
    if not comparable:
        return _unsupported("SF-2b", "no (window, role) group priced two decisions",
                            card=card, groups=len(groups))
    # Longrun1's shape: shares falling as 1/rank of settlement in the window.
    rank_shaped = sum(1 for key in split
                      if all(b <= a for a, b in zip(order[key], order[key][1:], strict=False)))
    return _result("SF-2b", not split, split_windows=len(split), windows=len(comparable),
                   rank_shaped=rank_shaped, example=next(iter(split.items()), None),
                   seats=len(set(seats.values())))


# --- decisions, draws and seats -----------------------------------------------------------


def decision_seats(events: Iterable[Mapping]) -> dict[str, str]:
    """Each decision handle's drawn arm (a seat id, or NOOP), from ``decision.open``."""
    return {row["handle"]: need(row, "propensity.chosen")
            for row in rows_of(events, "decision.open")}


def returned_handles(events: Iterable[Mapping]) -> set[str]:
    """Handles on which some seat returned (an ``invocation`` row of any status)."""
    return {row["handle"] for row in rows_of(events, "invocation") if row.get("handle")}


# --- thrash (§3.2) --------------------------------------------------------------------------


def thrash_series(events: list[Mapping]) -> list[tuple[int, float, float, bool]]:
    """Per closed window: (index, thrash λ, thrash penalty, thrash flagged)."""
    out = []
    for w in windows(events):
        # immune.py ``close_window`` ledgers ``"thrash": rt.stats.thrash``, which
        # ``thrash_penalty`` always returns with its ``lambda`` and ``penalty``: a window
        # without them is malformed, never a zero price.
        out.append((w["window"], float(need(w, "thrash.lambda")),
                    float(need(w, "thrash.penalty")), bool(need(w, "flags.thrash"))))
    return out


@criterion("TH-1a")
def th1a_detection(events: list[Mapping], manifest: Mapping, *, cycle_start: int) -> Result:
    """TH-1a: thrash is flagged at most ``H`` windows after a cycle starts.

    The flag counted is the onset of an episode (a run of consecutive flagged windows,
    ``versions.pathologies``) at or after the cycle's start (A): an episode already
    flagged in the window before the cycle started belongs to what came before, and
    detection cannot be read through it (``unsupported``).
    """
    ph = physics(manifest)
    thrash = flagged(events, "thrash")
    if cycle_start - 1 in set(thrash):
        return _unsupported("TH-1a", "thrash was already flagged when the cycle started",
                            cycle_start=cycle_start)
    first = min((w for w in thrash if w >= cycle_start), default=None)
    last = max((w["window"] for w in windows(events)), default=None)
    if first is None and (last is None or last < cycle_start + ph.H):
        return _unsupported("TH-1a", "the diary ends before H windows after the cycle "
                            "started", cycle_start=cycle_start, last=last, H=ph.H)
    return _result("TH-1a", first is not None and first <= cycle_start + ph.H,
                   cycle_start=cycle_start, first_flag=first, H=ph.H)


@criterion("TH-1b")
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
            # The kernel's own values, compared exactly: a price that holds is equal.
            if pa >= ph.cap:
                break
            if b < a:
                falls.append({"run": [start, end], "from": a, "to": b})
            if b > a:
                rose = True
    if not runs:
        return _unsupported("TH-1b", "thrash was never flagged")
    return _result("TH-1b", rose and not falls, runs=runs[:8], falls=falls[:5], rose=rose)


@criterion("TH-1b-antiwindup")
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
            lam = float(need(row, "thrash.lambda"))
        elif kind == "decision.open":
            prop = need(row, "propensity")
            router = need(row, "actor")
            if not isinstance(router, str) or not router.startswith("router:"):
                continue
            if router.split(":", 1)[1].split("#")[0].split("@")[0] \
                    not in ph.no_swap_regret_kinds:
                continue
            now = dict(zip(need(prop, "action_ids"), need(prop, "probs"), strict=True))
            before = last.get(router)
            moved = _tv(now, before) if before else 0.0
            last[router] = now
            out[row["handle"]] = min(ph.cap, lam * min(1.0, moved))
    return out


@criterion("TH-1c")
def th1c_movement(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1c: every charge is price × the router's own movement, and every one lands.

    The set of handles charged (``thrash.charged``) equals the set of core draws whose
    expected charge ``min(cap, λ_t·min(1, TV))`` is positive: a draw that did not move
    is never charged, and no draw that moved under a price goes uncharged (a mechanism
    that drops some charges fails here). Each such draw is charged exactly once (a
    duplicate charge fails even at the right amount), and each charge equals its
    expected amount exactly: the expectation is ``_record_movement``'s own arithmetic
    (``fsum`` TV, ``min(cap, λ·min(1, TV))``) on the same floats.
    """
    expected = expected_thrash_charges(events, manifest)
    charged = rows_of(events, "thrash.charged")
    positive = {h for h, c in expected.items() if c > 0}
    if not positive and not charged:
        return _unsupported("TH-1c", "no core draw moved under a thrash price")
    counts = Counter(row["handle"] for row in charged)
    landed = set(counts)
    duplicated = sorted(h for h, n in counts.items() if n > 1)
    missing, unexpected = sorted(positive - landed), sorted(landed - positive)
    bad = [{"handle": row["handle"], "charge": row["charge"],
            "expected": expected.get(row["handle"])}
           for row in charged
           if float(row["charge"]) != float(expected.get(row["handle"], 0.0))]
    return _result("TH-1c", not missing and not unexpected and not bad and not duplicated,
                   charged=len(charged), expected_positive=len(positive),
                   missing=missing[:5], unexpected=unexpected[:5], bad=bad[:5],
                   duplicated=duplicated[:5])


@criterion("TH-1d")
def th1d_frontier(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1d: no charge reaches a mean-based router, a niche decision or a frontier NOOP."""
    ph = physics(manifest)
    niche = {row["handle"] for row in rows_of(events, "niche.action")}
    bad = []
    for row in rows_of(events, "thrash.charged"):
        kind = str(need(row, "router")).split(":", 1)[-1].split("#")[0].split("@")[0]
        if kind not in ph.no_swap_regret_kinds or row["handle"] in niche:
            bad.append({"handle": row["handle"], "router": row.get("router")})
    charged = len(rows_of(events, "thrash.charged"))
    if not charged:
        return _unsupported("TH-1d", "no round was charged")
    return _result("TH-1d", not bad, bad=bad[:5], charged=charged)


@criterion("TH-1e")
def th1e_release(events: list[Mapping], manifest: Mapping, *, steady_from: int) -> Result:
    """TH-1e: after the cycle stops, the flag clears within H windows and the thrash price
    reaches zero within ``T_rel(λ_peak) + 1`` windows of the clear.

    Three readings, never a pass on truncated evidence: ``pass`` only for a clear within
    H and an observed zero price within the bound; ``fail`` when a bound the diary fully
    covers passed without it; ``unsupported`` when the diary ends before a bound it has
    not yet met.

    The peak is the released episode's own (A): the largest price from the window before
    that episode (the latest pre-episode state) through the clear, never an earlier
    episode's. The release bound is ``t_release``'s kernel-exact loop and the zero is an
    exact 0.0 (C). With no thrash episode before the clear there is nothing to release.
    """
    ph = physics(manifest)
    full = thrash_series(events)
    series = [s for s in full if s[0] >= steady_from]
    if not series:
        return _unsupported("TH-1e", "no window after the cycle stopped")
    last = series[-1][0]
    cleared = next((w for w, _lam, _p, flag in series if not flag
                    and all(not f for w2, _l, _pp, f in series if w2 >= w)), None)
    evidence: dict[str, Any] = {"steady_from": steady_from, "H": ph.H, "last": last}
    if cleared is None or cleared > steady_from + ph.H:
        if cleared is None and last < steady_from + ph.H:
            return _unsupported("TH-1e", "the diary ends before the flag had H windows "
                                "to clear", **evidence)
        return _result("TH-1e", False, why="the flag did not clear within H", cleared=cleared,
                       **evidence)
    runs = [r for r in _runs([w for w, _l, _p, flag in full if flag]) if r[1] < cleared]
    if not runs:
        return _unsupported("TH-1e", "no thrash episode came before the clear", **evidence)
    start = runs[-1][0]
    peak = max(lam for w, lam, _p, _f in full if start - 1 <= w <= cleared)
    evidence.update(episode=list(runs[-1]), peak=peak)
    bound = t_release(ph, peak) + 1
    zero = next((w for w, lam, _p, _f in series if w >= cleared and lam == 0.0), None)
    evidence.update(cleared=cleared, zero=zero, bound=bound)
    if zero is not None and zero <= cleared + bound:
        return _result("TH-1e", True, **evidence)
    if last < cleared + bound:
        return _unsupported("TH-1e", "the diary ends before the release bound", **evidence)
    return _result("TH-1e", False, **evidence)


@criterion("TH-1f")
def th1f_priority(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-1f: in a window flagged thrash and stable failure, gain moves γ down only."""
    both = set(flagged(events, "thrash")) & set(flagged(events, "stable_failure"))
    if not both:
        return _unsupported("TH-1f", "no window was flagged with both")
    acts = [row for row in rows_of(events, "immune.gain") if row["window"] in both]
    bad = [row for row in acts if need(row, "pathology") != "thrash" or raises(row)]
    if not acts:
        # (B): with no gain act in such a window the priority was never exercised.
        return _unsupported("TH-1f", "no gain act in a window flagged with both",
                            windows=sorted(both)[:10])
    return _result("TH-1f", not bad, windows=sorted(both)[:10], acts=len(acts), bad=bad[:3])


def thrash_rate(events: list[Mapping], manifest: Mapping) -> tuple[int, int]:
    """(flagged, supported) closed windows after the first ``H``: the null's reading."""
    ph = physics(manifest)
    closes = windows(events)[ph.H:]
    return sum(1 for w in closes if need(w, "flags.thrash")), len(closes)


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


@criterion("TH-4")
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


@criterion("TH-2")
def th2_short_lived(events: list[Mapping], manifest: Mapping, *, loop: str) -> Result:
    """TH-2: every refactor of ``loop`` after the first yields a lifespan row, a lifespan
    shorter than its correcting loop is read as thrash with ``unsettled >= 1 − ratio``,
    and no admitted registration is refused for its speed.

    The reading is scoped to the windows whose tail holds that lifespan (A): the first
    ``immune.window`` that carries it in ``lifespans`` and the ``k − 1`` after it, which
    is where ``versions.diagnose`` reads ``short_lived``. Each supported one of them
    flags thrash with ``unsettled >= 1 − ratio``, compared exactly (the kernel's own
    ``1.0 − ratio``). A lifespan no later window carries was never read (``fail``).
    ``pass`` needs one short lifespan read by a supported window; with none (no short
    lifespan, or one the diary ended before reading) the result is ``unsupported``,
    unless a refusal for speed already fails it.
    """
    ph = physics(manifest)
    rows = [row for row in rows_of(events, "config.lifespan") if row.get("loop") == loop]
    speed = [row for row in rows_of(events, "registration.rejected")
             if any(word in str(need(row, "reason")).lower()
                    for word in ("too soon", "too fast", "lifespan", "speed", "rate limit"))]
    if not rows:
        if speed:
            # A refusal for speed is observed whatever the loop's lifespans show.
            return _result("TH-2", False, loop=loop, speed_refusals=len(speed))
        return _unsupported("TH-2", "the loop was never refactored twice", loop=loop)
    closes = windows(events)
    unread, misread, checked = [], [], 0
    for row in rows:
        if row["ratio"] >= 1:
            continue
        carrier = next((i for i, w in enumerate(closes)
                        if any(need(item, "loop") == loop
                               and need(item, "tick") == need(row, "tick")
                               for item in need(w, "lifespans"))), None)
        if carrier is None:
            if any(need(w, "seq") > need(row, "seq") for w in closes):
                unread.append(row.get("tick"))
            continue
        tail = [w for w in closes[carrier:carrier + ph.k]
                if need(w, "unsettled") is not None]  # None: an unsupported tail
        if tail:
            checked += 1
        for w in tail:
            if not need(w, "flags.thrash") or w["unsettled"] < 1.0 - row["ratio"]:
                misread.append({"window": w["window"], "ratio": row["ratio"],
                                "unsettled": w["unsettled"]})
    worst = min(rows, key=lambda row: row["ratio"])
    evidence = {"lifespans": len(rows), "worst_ratio": worst["ratio"],
                "short_checked": checked, "unread": unread[:5], "misread": misread[:5],
                "speed_refusals": len(speed)}
    if not unread and not misread and not speed and checked == 0:
        # (B): no short lifespan was read by a supported window (none was short, or the
        # diary ended before a window carried it), so the reading was never exercised.
        return _unsupported("TH-2", "no short lifespan was read by a supported window",
                            **evidence)
    return _result("TH-2", not unread and not misread and not speed, **evidence)


@criterion("TH-3")
def th3_governance_gap(events: list[Mapping], manifest: Mapping) -> Result:
    """TH-3: charter revisions stand at least ``min_ratio`` × the slowest loop apart.

    Chapter II §IV.c: "an inner loop must resolve itself several times faster than the
    outer loop that commands it". Read on the kernel's own rows (``GovernanceCadence``):
    a ``charter.boundary`` row records ``boundary_ns``, the anchor it was measured from
    (``previous_ns``: the launch or the previous boundary) and ``slowest_period_ns``;
    every passed motion activates at a boundary, in a ``charter.cadence`` row whose
    ``previous_activation_ns`` is that boundary's anchor and whose ``earliest_ns`` is
    the *next* threshold. So:

    * every boundary stands ``min_ratio`` × its slowest period after its anchor;
    * every activation instant is a boundary instant, and distinct activation instants
      stand ``min_ratio`` × the later one's slowest period apart;
    * no activation is earlier than its own anchor.

    Without a boundary row the result is ``unsupported``: activations alone cannot show
    they stand at boundaries.
    """
    ph = physics(manifest)
    boundaries = rows_of(events, "charter.boundary")
    cadence = rows_of(events, "charter.cadence")
    instants = sorted({row["activation_ns"] for row in cadence})
    bad = []
    for row in boundaries:
        gap = row["boundary_ns"] - row["previous_ns"]
        if gap < ph.r * row["slowest_period_ns"]:
            bad.append({"boundary_ns": row["boundary_ns"], "gap": gap,
                        "required": ph.r * row["slowest_period_ns"]})
    at_boundary = {row["boundary_ns"] for row in boundaries}
    # Rows sharing an activation instant bind it by the slowest period any of them read.
    slowest: dict[int, int] = {}
    for row in cadence:
        instant = row["activation_ns"]
        slowest[instant] = max(slowest.get(instant, 0), row["slowest_period_ns"])
    for row in cadence:
        # Without boundary rows, "at a boundary" is unread (B); the other two readings
        # need only the activations, and a violation of either fails regardless.
        if boundaries and row["activation_ns"] not in at_boundary:
            bad.append({"activation_ns": row["activation_ns"], "not_at_a_boundary": True})
        if row["activation_ns"] < row["previous_activation_ns"]:
            bad.append({"activation_ns": row["activation_ns"], "before_its_anchor": True})
    for earlier, later in zip(instants, instants[1:], strict=False):
        if later - earlier < ph.r * slowest[later]:
            bad.append({"activations": [earlier, later], "gap": later - earlier,
                        "required": ph.r * slowest[later]})
    if not boundaries and not bad:
        # (B): the gap is read at boundaries; activations alone cannot show that each
        # stands at one, so without boundary rows and no violation there is no evidence.
        return _unsupported("TH-3", "no governance boundary", activations=len(cadence),
                            instants=len(instants))
    return _result("TH-3", not bad, boundaries=len(boundaries), activations=len(cadence),
                   instants=len(instants), bad=bad[:5])


# --- learning death (§3.4) -------------------------------------------------------------------


def niche_handles(events: Iterable[Mapping]) -> set[str]:
    """Decisions taken in the unhistoried niche: an unhistoried action (``niche.action``)
    or an unhistoried seat's protected compute (``novelty.compute`` that used some)."""
    return ({row["handle"] for row in rows_of(events, "niche.action")}
            | {row["handle"] for row in rows_of(events, "novelty.compute")
               if int(need(row, "used")) > 0})  # kernel/reserve.py: always written


@criterion("LD-1d")
def ld1d_exemption(events: list[Mapping], manifest: Mapping, *, minimum: int = 10) -> Result:
    """LD-1d: every niche decision bears no card penalty, exactly (wave 16 R-E amended)."""
    niche = niche_handles(events)
    priced = [row for row in rows_of(events, "price.penalty") if row["handle"] in niche]
    bad = [row["handle"] for row in priced if row["penalty"] != 0]
    if len(priced) < minimum and not bad:
        # The minimum is evidence for a pass; one penalized niche decision fails alone.
        return _unsupported("LD-1d", "fewer than the required niche decisions settled",
                            niche=len(priced), minimum=minimum)
    return _result("LD-1d", not bad, niche=len(priced), penalized=bad[:10])


@criterion("LD-1e")
def ld1e_detection(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1e: a frontier router quarantined for a whole tail is flagged learning-dead
    within H.

    A tail is one router's own run of k or more consecutive quarantined windows, keyed
    by router identity as the organ's evidence is (same router across the tail): two
    routers quarantined in alternate windows make no tail. Only a flag that names this
    router (``frontier.quarantined_routers`` or ``uninvoked_routers``, the kernel's
    ``frontier_evidence``) detects its tail: another router's flag does not (A). The
    flag must fall inside the quarantined run and within ``H`` of its start; a run that
    cleared unflagged failed, whatever was flagged after it.
    """
    ph = physics(manifest)
    closes = windows(events)
    by_router: dict[str, list[int]] = defaultdict(list)
    for w in closes:
        for row in need(w, "frontier_invocation"):
            # routing.py ``frontier_invocation`` writes each of these on every row.
            if need(row, "quarantined") and not need(row, "core"):
                by_router[str(need(row, "router"))].append(w["window"])
    runs = sorted((start, end, router) for router, indexes in by_router.items()
                  for start, end in _runs(indexes) if end - start + 1 >= ph.k)
    if not runs:
        return _unsupported("LD-1e", "no frontier router was quarantined for k windows")
    dead = flagged(events, "learning_death")
    naming: dict[str, set[int]] = defaultdict(set)
    for w in closes:
        if w["window"] in set(dead):
            # ``frontier_evidence`` writes both lists whenever every tail window
            # recorded ``frontier_invocation``, which ``close_window`` always does.
            for router in [*need(w, "frontier.quarantined_routers"),
                           *need(w, "frontier.uninvoked_routers")]:
                naming[str(router)].add(w["window"])
    last = max(w["window"] for w in closes)
    # A flag detects a run only inside it and by its deadline: after the run cleared,
    # the organ's tail (``persistent_violations`` over ``windows[-k:]``) holds an
    # unquarantined window, so a later flag reads something else (A).
    unmet = [r for r in runs
             if not any(r[0] <= w <= min(r[1], r[0] + ph.H) for w in naming[r[2]])]
    # A run still open at the diary's end is pending until its H deadline; one that
    # cleared before the diary ended is decided.
    late = [r for r in unmet if r[1] < last or last >= r[0] + ph.H]
    if unmet and not late:
        return _unsupported("LD-1e", "the diary ends before H windows after a quarantine "
                            "began", runs=unmet[:5], last=last)
    return _result("LD-1e", not late, runs=runs[:5], flagged=dead[:10], late=late[:5])


@criterion("LD-1f")
def ld1f_hold(events: list[Mapping], manifest: Mapping) -> Result:
    """LD-1f: while learning death is flagged, no gain row lowers γ.

    (B) The hold is exercised only at an acting window flagged learning-dead while some
    router's γ stood above the lowest it was ever seen at (its seed, or near it): there
    the ``cleared`` step would have lowered it (``immune.close_window``). A diary with no
    such window is ``unsupported``, never a vacuous pass.
    """
    dead = set(flagged(events, "learning_death"))
    if not dead:
        return _unsupported("LD-1f", "learning death was never flagged")
    gains = rows_of(events, "immune.gain")
    bad = [row for row in gains if row["window"] in dead and lowers(row)]
    by_router: dict[str, list[Mapping]] = defaultdict(list)
    for row in gains:
        by_router[row["router"]].append(row)
    exercised = []
    for w in windows(events):
        if w["window"] not in dead or not need(w, "acts"):
            continue
        for router, rows in by_router.items():
            floor = min(min(row["gamma_before"] + row["gamma_after"]) for row in rows)
            prior = [row for row in rows if row["window"] < w["window"]]
            if prior and gamma_of(prior[-1]["gamma_after"]) > floor:
                exercised.append({"window": w["window"], "router": router})
    if not bad and not exercised:
        return _unsupported("LD-1f", "no acting learning-dead window found γ above its floor",
                            flagged=len(dead))
    return _result("LD-1f", not bad, flagged=len(dead), exercised=exercised[:5], bad=bad[:3])


@criterion("I-3c")
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


@criterion("I-4a")
def i4a_no_blind_step_back(events: list[Mapping], manifest: Mapping) -> Result:
    """I-4a: the sampling actuator never steps the mix back while it cannot see.

    A ``sampling.lower`` on an actuator period whose history has no supported
    consequence slope (``outcome_slope`` None) reads missing evidence as compliance,
    against the versions module's own rule that "an unmeasured reading is missing
    evidence not compliance" (design I-4, P-3).
    """
    rows = rows_of(events, "sampling.lower", "sampling.raise")
    lowers = [row for row in rows if row["kind"] == "sampling.lower"]
    if not lowers:
        # (B): a raise never steps back, so only a lower exercises the property.
        return _unsupported("I-4a", "the actuator never stepped back", moves=len(rows))
    # feedback.py writes ``outcome_slope`` on every step, None when unmeasured.
    blind = [row for row in lowers if need(row, "outcome_slope") is None]
    return _result("I-4a", not blind, moves=len(rows), blind_lowers=len(blind),
                   example=blind[:2])


# --- overfitting (§3.3) -----------------------------------------------------------------------


@criterion("OF-2d")
def of2d_authorship(events: list[Mapping], manifest: Mapping, *,
                    seats: set[str] | None = None) -> Result:
    """OF-2d: every holdout and challenge traces to a seat's return; the kernel adds none.

    Every row that authors a holdout or a challenge (``holdout.proposed``,
    ``challenge.proposed``: the only kinds that add one) names, as its ``handle``, a
    decision on which a seat returned; when ``seats`` is given, that decision's drawn arm
    is one of them. A proposing row with no handle is a failure, not a skip.
    """
    returned = returned_handles(events)
    drawn = decision_seats(events)
    proposals = rows_of(events, "holdout.proposed", "challenge.proposed")
    if not proposals:
        return _unsupported("OF-2d", "no holdout or challenge was proposed")
    bad = [row.get("handle") for row in proposals
           if not isinstance(row.get("handle"), str) or row["handle"] not in returned
           or (seats is not None and drawn.get(row["handle"]) not in seats
               and not _child_of_seat(events, row["handle"], seats))]
    return _result("OF-2d", not bad, proposals=len(proposals),
                   traced=len(proposals) - len(bad), bad=bad[:5])


@criterion("OF-1a")
def of1a_outside_the_loop(events: list[Mapping], manifest: Mapping) -> Result:
    """OF-1a: the realized consequence of a return is a fact of the world, the same for
    every judge that read it: every ``verdict.consequence`` row on one return, in one
    phase, carries the same ``y`` whatever that judge's ``q`` (§III.b: "from outside the
    factory's input"). A ``y`` that moved with a verdict would be the verdict grading
    itself.

    Repetition is the count of consequence rows on one (return, phase) that carry a
    ``y``: two rows or more are checked, and any difference in ``y`` fails, whether or
    not their ``q`` differ. A row with ``y`` null (not yet known) is not a reading."""
    by_return: dict[tuple, list] = defaultdict(list)
    for row in rows_of(events, "verdict.consequence"):
        # feedback.py ``_score_verdict`` writes ``y`` (a measured float) and ``phase``
        # in every row: a null or absent one is malformed, never "not yet known".
        y = need(row, "y")
        if y is None:
            raise Malformed(row, "y")
        by_return[(need(row, "about_handle"), need(row, "phase"))].append(y)
    shared = {key: ys for key, ys in by_return.items() if len(ys) > 1}
    if not shared:
        return _unsupported("OF-1a", "no return carries two consequence rows in one phase")
    split = {str(key): sorted(set(ys), key=str) for key, ys in shared.items()
             if len(set(ys)) > 1}
    return _result("OF-1a", not split, returns=len(shared), split=list(split.items())[:5])


@criterion("OF-3a")
def of3a_sampling_behind_return(events: list[Mapping], manifest: Mapping) -> Result:
    """OF-3a: every judge draw on a return is ledgered after that return: the sampling
    decision stays behind the return, so no seat can alias a sampler it cannot predict.

    A draw on a ``ProducerReturn`` event comes after both the event that published the
    return (its own ledger row, carried with its ``about_handle``) and the producer's
    invocation that made it."""
    returned = {row["handle"]: row["seq"] for row in rows_of(events, "invocation")
                if isinstance(row.get("handle"), str) and row["handle"]}
    # loop.py emits every ProducerReturn with its ``about_handle``.
    published = {row["event"]["id"]: (need(row, "event.payload.about_handle"), row["seq"])
                 for row in rows_of(events, "event")
                 if need(row, "event.kind") == "ProducerReturn"}
    checked, early = 0, []
    for row in rows_of(events, "decision.open"):
        # A draw on an event that is no ProducerReturn is not a draw on a return.
        handle, event_seq = published.get(need(row, "event_id"), (None, None))
        if handle in returned:
            checked += 1
            if row["seq"] <= event_seq or row["seq"] <= returned[handle]:
                early.append(row["handle"])
    if not checked:
        return _unsupported("OF-3a", "no judge draw on a return")
    return _result("OF-3a", not early, draws=checked, early=early[:5])


@criterion("OF-2c")
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
        for term in need(row, "terms"):
            if need(term, "card_id") != card or need(term, "window") <= after_window:
                continue
            # A decision settled while its window was still open is priced on the last
            # closed measurement (``_penalty_terms`` reads the live card samples).
            window = term["window"]
            reference = window if closed_at.get(window, math.inf) < row["seq"] else window - 1
            if reference not in region_violation:
                continue  # no measured region violation to subtract: missing evidence
            checked += 1
            extra = term["violation"] - region_violation[reference]
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


#: Every act a seat's return causes, and the field its emitting row names the deciding
#: handle in: an order, a transfer, a registration, a charter amendment, a holdout or
#: metric challenge, and a retirement motion. ``CharterBook.propose`` and the retirement
#: motion ledger their dataclass as it is, which calls it ``proposer_handle``.
ACT_KINDS: dict[str, str] = {
    "order.intent": "handle",
    "treasury.intent": "handle",
    "registry.register": "handle",
    "charter.propose": "proposer_handle",
    "holdout.proposed": "handle",
    "challenge.proposed": "handle",
    "retirement.proposed": "proposer_handle",
}


#: The kernel's tolerance on a propensity's sum (queue.py ``PropensityRecord.validate``:
#: ``math.isclose(math.fsum(probs), 1.0, rel_tol=0, abs_tol=1e-12)``).
PROPENSITY_SUM_TOLERANCE = 1e-12


def propensity_problem(prop: Any) -> str | None:
    """Why ``prop`` breaks the ``PropensityRecord`` contract the kernel validates at
    open (queue.py ``PropensityRecord.validate``), or None. Guarantees every clause is
    read before any value is used: source sampled or declared (its default "sampled");
    ``action_ids`` a non-empty list of unique non-empty strings; ``probs`` the same
    length, each a finite real (never a bool) in [0, 1], summing to 1 within
    ``PROPENSITY_SUM_TOLERANCE``; ``chosen`` in ``action_ids`` (a declared one with
    positive mass); ``rng_seed`` an integer (never a bool)."""
    if not isinstance(prop, Mapping):
        return "no propensity"
    if prop.get("source", "sampled") not in ("sampled", "declared"):
        return "source is neither sampled nor declared"
    ids, probs = prop.get("action_ids"), prop.get("probs")
    if not isinstance(ids, list | tuple) or not ids:
        return "no action support"
    if any(not isinstance(a, str) or not a for a in ids):
        return "an action id is not a non-empty string"
    if len(set(ids)) != len(ids):
        return "an action id repeats"
    if not isinstance(probs, list | tuple) or len(probs) != len(ids):
        return "probs and action_ids differ in length"
    if any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
           for p in probs):
        return "a probability is not a finite number in [0, 1]"
    if not math.isclose(math.fsum(probs), 1.0, rel_tol=0,
                        abs_tol=PROPENSITY_SUM_TOLERANCE):
        return "the probabilities do not sum to 1"
    if type(prop.get("rng_seed")) is not int:
        return "the seed is not an integer"
    chosen = prop.get("chosen")
    if chosen not in ids:
        return "the chosen action is outside the support"
    if prop.get("source", "sampled") == "declared" and probs[ids.index(chosen)] <= 0:
        return "a declared choice has no mass"
    return None


@criterion("S1")
def s1_draw_sovereignty(events: list[Mapping], manifest: Mapping | None = None) -> Result:
    """S1: every drawn arm is the router's own sample, replayed from its logged seed.

    ``Random(rng_seed).choices(action_ids, weights=probs)[0] == chosen`` for every
    sampled ``decision.open`` (``learners.router.Router.route``), the logged
    distribution sums to 1, and every act row (``ACT_KINDS``) names, in its handle
    field, a decision a seat returned on. No act row is skipped: one with no handle, or
    a handle that is not a returned decision, is an act the kernel took for a seat.
    """
    bad, checked, malformed = [], 0, []
    for row in rows_of(events, "decision.open"):
        # ``Decision.propensity`` is a required ``PropensityRecord``: its whole contract
        # is checked before the draw is replayed, and a row that breaks it is malformed
        # (it fails, never raises, never passes).
        prop = row.get("propensity")
        why = propensity_problem(prop)
        if why is not None:
            bad.append(row.get("handle"))
            malformed.append({"handle": row.get("handle"), "why": why})
            continue
        if prop.get("source", "sampled") != "sampled":
            continue  # a declared field was drawn by the seat, not the kernel
        ids, probs = list(prop["action_ids"]), [float(p) for p in prop["probs"]]
        checked += 1
        drawn = Random(prop["rng_seed"]).choices(ids, weights=probs, k=1)[0]
        if drawn != prop["chosen"]:
            bad.append(row["handle"])
    returned = returned_handles(events)
    acts = [(row["kind"], row.get(ACT_KINDS[row["kind"]]))
            for row in rows_of(events, *ACT_KINDS)]
    unreturned = [{"kind": kind, "handle": h} for kind, h in acts
                  if not isinstance(h, str) or h not in returned]
    evidence = {"draws": checked, "bad_draws": bad[:5], "acts": len(acts),
                "unreturned": unreturned[:5], "malformed_propensities": malformed[:5]}
    if bad or unreturned:
        # An observed violation fails whatever else the diary lacks: an act no seat's
        # return traces to is the kernel acting for a seat.
        return _result("S1", False, **evidence)
    if not checked and not acts:
        return _unsupported("S1", "no sampled decision and no act", **evidence)
    return _result("S1", True, **evidence)


#: Every ledger kind that carries a learned, settled or graded score, and the fields that
#: hold it, each a unit-interval quantity. Enumerated from the emitting code (every
#: ``ledger.append`` in factorylab/ whose row names a reward, score, grade, credit,
#: forecast q or outcome y) and pinned against it by tests/gauntlet/test_criteria_schema.py.
UNIT_FIELDS: dict[str, tuple[str, ...]] = {
    "decision.settle": ("return.score",),
    "price.penalty": ("raw", "effective"),
    "router.abstention_priced": ("neutral", "reward"),
    "router.decline_priced": ("neutral", "reward"),
    "router.carried": ("reward",),
    "router.step_rescaled": ("reward", "stepped_as"),
    "thrash.charged": ("reward", "reward_before", "charge"),
    "propensity.learned": ("reward",),
    "evaluator.settled": ("consequence", "grade", "reward"),
    "evaluator.meta_grade": ("grade",),
    "meta.consequence": ("conformity", "score"),
    "verdict.mean": ("score",),
    "verdict.consequence": ("q", "y", "score"),
    "verdict.consequence_late": ("q", "y"),
    "consequence.marked": ("y",),
    "counter.opened": ("q", "judge_q"),
    "counter.settled": ("q", "judge_q", "y", "score"),
    "exposure.settled": ("score",),
    "lambda_post.settled": ("score",),
    "composed.settled": ("verdict", "reward"),
    "policy.outcome": ("q", "y", "score"),
    "uptake.anticipated": ("q",),
    "uptake.forecast": ("q",),
}
#: A penalty that is a weighted sum over roles (``_priced_abstention``): its float sum
#: may pass the cap by an ulp, so it alone is compared with 1e-12 of slack. Every other
#: capped field is ``min(…, penalty_cap)`` times a share ≤ 1 and is compared exactly.
WEIGHTED_PENALTY_KINDS = frozenset({"router.abstention_priced", "router.decline_priced"})
#: The unit fields the kernel writes as a boolean outcome: a motion's kept promise
#: (``policy.outcome``'s ``y = promise_kept(...)``, governance.py). Every other unit
#: field (a reward, a grade, a probability, a score, a realized y) is a number, and a
#: boolean there fails S4.
BOOLEAN_OUTCOMES = frozenset({("policy.outcome", "y")})
#: The unit fields the kernel writes as an explicit None, each with the condition on its
#: own row under which the emitting code does so (``OMITTED`` names the ones it leaves
#: out). Every other ``UNIT_FIELDS`` field is written, as a value, by every emitter of
#: its kind: absent or None there fails S4. Read from the emitters, and pinned against
#: them by tests/gauntlet/test_criteria_schema.py:
#: - ``price.penalty`` ``raw``/``effective``: None when the settlement is unresolved,
#:   and then the row names ``unresolved`` (pricing.py ``_settle_priced``).
#: - ``exposure.settled`` ``score``: None when no judge was scored on the antagonist's
#:   return (feedback.py, the two ``"score": None`` emitters).
#: - ``counter.settled`` ``score``: None when the judged return's outcome was not
#:   measured (feedback.py, the censored emitter), which also leaves out ``q``,
#:   ``judge_q`` and ``y`` (``OMITTED``); the measured emitter writes all four.
#: - ``policy.outcome`` ``y``: None unless the motion's outcome settled
#:   (governance.py: ``outcome = promise_kept(...) if status is SETTLED else None``).
#: - ``evaluator.settled`` ``grade``/``consequence``: each None when that signal did not
#:   arrive (``PendingJudgement.grade``/``consequence``); ``reward`` None only when both
#:   are (``evaluation_reward``).
#: - ``composed.settled`` ``verdict``: None when no judge's verdict was held;
#:   ``reward`` None only when the verdict, the credit and the tool-use credit all are
#:   (``composed_reward``).
NULLABLE: dict[tuple[str, str], Any] = {
    ("price.penalty", "raw"): lambda row: "unresolved" in row,
    ("price.penalty", "effective"): lambda row: "unresolved" in row,
    ("exposure.settled", "score"): lambda row: True,
    ("counter.settled", "score"): lambda row: not {"q", "judge_q", "y"} & set(row),
    ("policy.outcome", "y"): lambda row: row.get("status") != "settled",
    ("evaluator.settled", "grade"): lambda row: True,
    ("evaluator.settled", "consequence"): lambda row: True,
    ("evaluator.settled", "reward"): (
        lambda row: row.get("grade") is None and row.get("consequence") is None),
    ("composed.settled", "verdict"): lambda row: True,
    ("composed.settled", "reward"): (
        lambda row: all(row.get(k) is None for k in ("verdict", "credit",
                                                     "tool_use_credit"))),
}
#: The unit fields an emitter leaves out of its row, each with the condition under which
#: it does: only ``counter.settled``'s censored emitter, which writes ``score: None`` and
#: no ``q``, ``judge_q`` or ``y``. Every other field is present in every row of its kind.
OMITTED: dict[tuple[str, str], Any] = {
    ("counter.settled", name): (lambda row: "score" in row and row["score"] is None)
    for name in ("q", "judge_q", "y")
}
#: The penalty a settlement or an abstention bears is bounded by ``penalty_cap``.
CAPPED_FIELDS: dict[str, tuple[str, ...]] = {
    "price.penalty": ("penalty",),
    "router.abstention_priced": ("penalty",),
    "router.decline_priced": ("penalty",),
    "thrash.charged": ("charge",),
}


def _field(row: Mapping, path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        value = value.get(part) if isinstance(value, Mapping) else None
    return value


#: What ``_lookup`` returns for a key the row does not hold (never an explicit null).
ABSENT = object()


def _lookup(row: Mapping, path: str) -> Any:
    """The value at the dotted ``path``, ``ABSENT`` when a key on the way is missing: an
    absent field and an explicit null are different evidence."""
    value: Any = row
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return ABSENT
        value = value[part]
    return value


def _bounded(value: Any, lo: float, hi: float) -> bool:
    """A real number (never a bool, a string or None), finite, within [lo, hi]."""
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and math.isfinite(value) and lo <= value <= hi)


@criterion("S4")
def s4_boundedness(events: list[Mapping], manifest: Mapping) -> Result:
    """S4: every settled penalty is a finite number in [0, ``penalty_cap``], present in
    every row of its kind (``CAPPED_FIELDS``); every learned, settled or graded score of
    every kind that carries one (``UNIT_FIELDS``) in [0, 1] and present, unless the
    kernel writes that field as None under that row's condition (``NULLABLE``); every
    ratchet ends in
    [0, ``lambda_max``]. ``unsupported`` when the rows carry no such value at all."""
    ph = physics(manifest)
    bad, checked = [], 0
    for kind, fields in UNIT_FIELDS.items():
        for row in rows_of(events, kind):
            for name in fields:
                value = _lookup(row, name)
                checked += 1
                if value is ABSENT:
                    # Only a field the kernel's emitter leaves out, under the condition
                    # it does, may be absent (``OMITTED``); every other absence fails.
                    omitted = OMITTED.get((kind, name))
                    if omitted is None or not omitted(row):
                        bad.append({"kind": kind, "field": name, "value": None,
                                    "missing": True, "absent": True,
                                    "handle": row.get("handle")})
                    continue
                if value is None:
                    # An explicit null only where the kernel writes one, under the
                    # condition it does (``NULLABLE``).
                    allowed = NULLABLE.get((kind, name))
                    if allowed is None or not allowed(row):
                        bad.append({"kind": kind, "field": name, "value": None,
                                    "missing": True, "handle": row.get("handle")})
                    continue
                if isinstance(value, bool) and (kind, name) in BOOLEAN_OUTCOMES:
                    continue  # a boolean outcome is 0 or 1 by the kernel's schema
                if not _bounded(value, 0.0, 1.0):
                    bad.append({"kind": kind, "field": name, "value": value,
                                "handle": row.get("handle")})
    for kind, fields in CAPPED_FIELDS.items():
        for row in rows_of(events, kind):
            for name in fields:
                # Every capped field of its kind is written by the kernel: a missing,
                # non-numeric, non-finite, negative or over-cap value fails, and each
                # one read is evidence (a penalty-only diary is checked, not unsupported).
                value = _lookup(row, name)
                value = None if value is ABSENT else value
                slack = 1e-12 if kind in WEIGHTED_PENALTY_KINDS else 0.0
                checked += 1
                if not _bounded(value, 0.0, ph.cap + slack):
                    bad.append({"kind": kind, "field": name, "value": value,
                                "cap": ph.cap, "handle": row.get("handle")})
    for row in rows_of(events, "immune.price_ratchet"):
        checked += 1
        if not _bounded(row.get("lambda_after"), 0.0, ph.lambda_max):  # min(lambda_max, …)
            bad.append({"kind": row["kind"], "card": row.get("card_id")})
    if not checked:
        return _unsupported("S4", "no row carries a score, a reward or a ratchet")
    return _result("S4", not bad, checked=checked, bad=bad[:5])


@criterion("S5")
def s5_neutral_imputation(events: list[Mapping], manifest: Mapping) -> Result:
    """S5: an abstention and a declined commission are credited by one formula:
    ``clip(neutral − penalty, 0, 1)`` on the router's own neutral (R9, D4), exactly as
    ``_priced_abstention`` computes it from the two values it ledgers."""
    rows = rows_of(events, "router.abstention_priced", "router.decline_priced")
    if not rows:
        return _unsupported("S5", "no abstention or decline was priced")
    bad = [row["handle"] for row in rows
           if float(row["reward"]) != min(1.0, max(0.0, float(row["neutral"])
                                                   - float(row["penalty"])))]
    return _result("S5", not bad, priced=len(rows), bad=bad[:5])


@criterion("S5b")
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
    bad, checked, untraced = [], 0, 0
    for row in events:
        kind = row.get("kind")
        if kind == "price.penalty":
            handle = row.get("handle")
            # ``raw`` is always written, None when unresolved (pricing.py).
            if need(row, "raw") is not None and seats.get(handle) != "NOOP":
                actor = actors.get(handle)
                if not isinstance(actor, str):
                    # A penalized handle with no draw (no decision.open, or no actor) is
                    # counted untraced, never read as a NOOP.
                    # A round no router drew (S1's rule: untraceable) is no router's mean.
                    untraced += 1
                    continue
                raws[actor].append(float(row["raw"]))
        elif kind in ("router.abstention_priced", "router.decline_priced"):
            router = need(row, "router")  # feedback.py: every priced abstention names it
            observed = raws.get(router) if isinstance(router, str) else None
            if observed:
                checked += 1
                mean = math.fsum(observed) / len(observed)
                if abs(float(row["neutral"]) - mean) > 1e-9:
                    bad.append({"handle": row["handle"], "neutral": row["neutral"],
                                "observed_mean": mean, "rounds": len(observed)})
    if not checked:
        return _unsupported("S5b", "no abstention was priced after a settled round",
                            untraced=untraced)
    return _result("S5b", not bad, checked=checked, mismatched=len(bad), example=bad[:3],
                   untraced=untraced)


@criterion("S7")
def s7_gain_targets(events: list[Mapping], manifest: Mapping | None = None) -> Result:
    """S7: gain rows name only the kernel's own routers, never a seat-registered learner."""
    gains = rows_of(events, "immune.gain")
    if not gains:
        return _unsupported("S7", "no gain row")
    bad = [row["router"] for row in gains if not str(row.get("router")).startswith("router:")]
    return _result("S7", not bad, gains=len(gains), bad=bad[:5])


@criterion("S8")
def s8_gain_rows_uniform(events: list[Mapping], manifest: Mapping) -> Result:
    """S8 (ledger half): each gain act moves every exploration row of a router by one
    common step, within ``[seed, gamma_max]``: γ is a scalar over all arms, so the act
    redistributes uniformly and cannot favour an arm (Astra C-2). The instrumented half
    (weights untouched; per-arm change symmetric) is ``gain_neutral``."""
    ph = physics(manifest)
    gains = rows_of(events, "immune.gain")
    if not gains:
        return _unsupported("S8", "no gain row")
    # A router's seed γ, where the diary shows it: its first gain row raised γ from the
    # seed (a lowering needs an earlier raise, and γ never goes below the seed).
    seeds: dict[str, list[float]] = {}
    for row in gains:
        if row["router"] not in seeds:
            seeds[row["router"]] = list(row["gamma_before"]) if raises(row) else []
    bad, unverified = [], []
    for row in gains:
        pairs = list(zip(row["gamma_before"], row["gamma_after"], strict=True))
        steps = {b - a for a, b in pairs}
        seed = seeds[row["router"]]
        # ``immune._gain``'s own step, exactly (immune.py:145-148): up is
        # max(old, min(gamma_max, old + gain_step)); down is
        # min(old, max(seed_gamma, old − gain_step)), a partial step only onto the seed.
        if need(row, "pathology") == "stable_failure":
            wrong = [b for a, b in pairs if b != max(a, min(ph.gamma_max, a + ph.gain_step))]
        else:
            wrong = []
            for i, (a, b) in enumerate(pairs):
                if seed:
                    if b != min(a, max(seed[i], a - ph.gain_step)):
                        wrong.append(b)
                elif b != a - ph.gain_step:
                    if a - ph.gain_step < b < a:
                        unverified.append({"router": row["router"], "window": row["window"]})
                    else:
                        wrong.append(b)
        # γ is an exploration rate: below 0 is never a γ, whatever the seed.
        below = [b for b in row["gamma_after"] if not _bounded(b, 0.0, ph.gamma_max)]
        if not seed and not wrong and not below:
            # The seed is the floor a lowering stops at; with it out of the diary the
            # lower bound of this row is not verified.
            if need(row, "pathology") != "stable_failure":
                unverified.append({"router": row["router"], "window": row["window"]})
        if len(steps) != 1 or wrong or below:
            bad.append({"router": row["router"], "window": row["window"],
                        "steps": sorted(steps), "wrong": wrong[:3]})
    if not bad and unverified:
        # A lowering stops at the seed; with the seed not in the diary a lowering's lower
        # bound (and a partial step onto it) cannot be verified.
        return _unsupported("S8", "a lowering with the router's seed unknown",
                            unverified=unverified[:5])
    return _result("S8", not bad, gains=len(gains), bad=bad[:5])


@criterion("S8-instrumented")
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
    if not rows_b:
        # (B): no base, no act to read.
        return _unsupported("S8-instrumented", "the router saved no base")
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
    # An EXP3 base's state always holds its actions and log weights
    # (learners/exp3.py ``EXP3.state``).
    raw = need(base, "log_weights")
    if isinstance(raw, Mapping):
        actions = list(need(base, "actions"))
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
    "TH-1b-antiwindup": th1b2_frozen,
    "TH-1c": th1c_movement,
    "TH-1d": th1d_frontier,
    "TH-1f": th1f_priority,
    "TH-3": th3_governance_gap,
    "OF-1a": of1a_outside_the_loop,
    "OF-2d": of2d_authorship,
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

#: Per-card criteria: ``replay`` runs each over every card the diary names
#: (``diary_cards``), the thrash price's own ``pathology:thrash`` included.
PER_CARD: dict[str, Callable[..., Result]] = {
    "SF-1a": sf1a_detection,
    "SF-1c": sf1c_anti_windup,
    "SF-1d": sf1d_escalation,
    "SF-2b": sf2b_order_blind,
}
#: Per-loop criteria: ``replay`` runs each over every loop a refactor names
#: (``diary_loops``).
PER_LOOP: dict[str, Callable[..., Result]] = {
    "TH-2": th2_short_lived,
}
#: Criteria ``replay`` cannot run, each with why: it needs an input the diary does not
#: hold (a scripted stimulus's timing, a synthetic null, the population's seat roles,
#: or the learners' instrumented states). Their gate tests supply it
#: (tests/gauntlet/populations.py). Every other criterion is registered above, and
#: tests/gauntlet/test_criteria_schema.py fails on one that is neither.
POPULATION_ONLY: dict[str, str] = {
    "th1a_detection": "cycle_start: the window the population's scripted cycle began",
    "th1e_release": "steady_from: the window the population's scripted stimulus stopped",
    "th4_null": "synthetic: the detector's own null rate, from a separate synthetic run",
    "sf2_gradient": "relievers and holders: the seat roles the population scripted",
    "of2c_holdout_bites": "seats and after_window: the population's holdout script",
    "gain_neutral": "the learners' states before and after a gain act, instrumented "
                    "in the run; the diary holds only the gain rows (S8 reads those)",
}
#: Criteria ``replay`` runs by their own rule rather than a registry above.
REPLAY_DIRECT: dict[str, str] = {
    "sf0_relation": "SF-0, from the manifest and every region the diary measured",
}


def replay(events: list[Mapping], manifest: Mapping | None = None, *,
           world: str | None = None, seed: int | None = None) -> list[Result]:
    """Every generic and per-card criterion over one diary, in a stable order.

    The diary is bound first (``bind_diary``): the criteria read the physics it
    launched under, and a claimed world, seed or manifest that is not the diary's
    raises ``DiaryInvalid`` before any criterion runs. The binding is the first result.
    """
    manifest, binding = bind_diary(events, world=world, seed=seed, manifest=manifest)
    results = [Result("BIND", PASS, binding), Result("SF-0", *_sf0_parts(manifest, events))]
    results += [fn(events, manifest) for fn in GENERIC.values()]
    for name, fn in PER_CARD.items():
        for card in diary_cards(events):
            result = fn(events, manifest, card=card)
            results.append(Result(f"{name}[{card}]", result.status, result.evidence))
    for name, fn in PER_LOOP.items():
        for loop in diary_loops(events):
            result = fn(events, manifest, loop=loop)
            results.append(Result(f"{name}[{loop}]", result.status, result.evidence))
    return results


def _sf0_parts(manifest: Mapping, events: list[Mapping]) -> tuple[str, dict]:
    regions = {}
    for row in card_windows(events):
        regions.update(need(row, "regions"))
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
    rp.add_argument("--world", help="the world the diary claims (its launched name)")
    rp.add_argument("--seed", type=int, help="the seed the diary claims")
    rp.add_argument("--manifest", help="a world TOML or manifest JSON whose physics must "
                    "be the diary's")
    rp.add_argument("--json", action="store_true", help="print JSON lines")
    sw = sub.add_parser("sweep", help="run a gauntlet population over several seeds")
    sw.add_argument("--population", required=True)
    sw.add_argument("--seeds", default="1,2,3")
    args = parser.parse_args(argv)
    if args.command == "replay":
        manifest = _manifest_from(args.manifest) if args.manifest else None
        try:
            events = load_events(args.diary)
            results = replay(events, manifest, world=args.world, seed=args.seed)
        except DiaryInvalid as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        for result in results:
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

    factory = getattr(populations, args.population, None)
    if factory is None or args.population not in populations.SWEEPABLE:
        print(f"no population {args.population!r}: one of {sorted(populations.SWEEPABLE)}",
              file=sys.stderr)
        return 2
    for seed in (int(s) for s in args.seeds.split(",")):
        run = populations.run(*factory(), seed=seed)
        # The diary each seed's criteria read is bound to that seed and world.
        try:
            results = replay(run.events, run.manifest, world=run.manifest.get("name"),
                             seed=seed)
        except DiaryInvalid as exc:
            print(f"refused: seed={seed}: {exc}", file=sys.stderr)
            return 2
        for result in results:
            print(f"seed={seed} {result.status:12} {result.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
