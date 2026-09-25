"""Gauntlet populations: seats as fixed arms, dispatched on request structure.

A gauntlet population is a table of seats. Each seat's behaviour is a fixed function
of what it can read (its own call count, the published window index, its request's
inputs and the seeded fake venue), never of a price or of prose (phase-2 design
§2.1). Requests are told apart by their structure alone (``world.scripted.
request_form``: the outcome contract's required fields and the inputs' shape), so a
rewording of any commission cannot turn a judge into a producer.

Worlds are built from ``worlds/scripted.toml`` with the physics blocks (``prices``,
``immune``, ``timing``, ``evaluation``, ``novelty``) copied from the launch world
under test, so the constants tested are the constants launched. The latest launch
world on this branch is ``edition6-capital-loop``.

A run records its ledger rows and instruments the immune organ without changing it:
the state a close must not touch (S6) and each router's rows before and after a
gain act (S8) are read around the call and the call itself is untouched.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from factorylab.kernel.ledger import canonical
from factorylab.runtime import immune, pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import manifest_from_dict
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.scripted import (
    ScriptedProvider,
    _inputs_from_prompt,
    names_declined_trade,
    request_form,
)
from scripts import gauntlet

ROOT = Path(__file__).resolve().parents[2]
LAUNCH_WORLD = "edition6-capital-loop"
PHYSICS_BLOCKS = ("prices", "immune", "timing", "evaluation", "novelty")

#: The organ's own row kinds a close may append (S6): prices, versions, pathologies,
#: gain, the loop clock and the configuration record. Nothing else.
ORGAN_KINDS = ("version.", "pathology.", "immune.", "price.", "clock.loop", "config.lifespan",
               "governance.viable", "governance.nonviable", "governance.settling")


@dataclass
class View:
    """What an arm reads: the request's form, its seat, inputs, and public clocks."""

    form: str
    you: str | None
    inputs: dict[str, Any]
    text: str
    model_id: str
    calls: int
    window: int
    tick: int


Arm = Callable[[View], dict]


@dataclass
class Seat:
    """One seat of a population: its contract and the arm answering each request form."""

    id: str
    role: str
    accepts: tuple[str, ...]
    emits: tuple[str, ...]
    arms: dict[str, Arm] = field(default_factory=dict)
    model: str | None = None
    max_tokens: int = 256
    #: The seat's model price, USD per million input and output tokens: a world fact.
    price: tuple[str, str] = ("1", "5")

    @property
    def model_id(self) -> str:
        return self.model or f"fake-{self.id}"

    #: The seat's genesis state (a lens, as the edition-6 roster seeds one), if any.
    initial_state: dict[str, Any] | None = None

    def assembly(self) -> dict[str, Any]:
        row = {"id": self.id, "role": self.role, "model_id": self.model_id,
               "accepts": list(self.accepts), "emits": list(self.emits),
               "max_tokens": self.max_tokens}
        if self.initial_state is not None:
            row["initial_state"] = self.initial_state
        return row


def producer(seat_id: str, arm: Arm, *, accepts: Iterable[str] = ("Tick",),
             role: str = "producer") -> Seat:
    return Seat(seat_id, role, tuple(accepts), ("ProducerReturn",), {"produce": arm})


def judge(seat_id: str, arm: Arm, *, accepts: Iterable[str] = ("ProducerReturn",)) -> Seat:
    return Seat(seat_id, "evaluator", tuple(accepts), ("Verdict",), {"judge": arm})


def meta(seat_id: str, arm: Arm) -> Seat:
    return Seat(seat_id, "meta", ("Verdict",), ("MetaVerdict",), {"meta": arm})


def adversary(seat_id: str, arm: Arm) -> Seat:
    """A counter-verdict seat: the adversarial judge of Chapter II §III.b."""
    return Seat(seat_id, "adversary", ("Verdict",), ("CounterVerdict",), {"counter": arm})


# --- canonical arms ---------------------------------------------------------------------


def hold(view: View) -> dict:
    return {"action": "hold", "rationale": "scripted"}


def investigate(view: View) -> dict:
    """Read the venue once, then hold: a tool round, never a write."""
    if "tool_results" in view.inputs:
        return {"action": "investigate", "rationale": "scripted"}
    return {"action": "investigate", "tool_calls": [{"tool": "venue.positions", "args": {}}]}


def decline(view: View) -> dict:
    return {"status": "cannot", "reason": "scripted decline"}


def verdict(q: float) -> Arm:
    def arm(view: View) -> dict:
        return {"verdict": q, "rationale": "scripted"}
    return arm


def conformity(c: float) -> Arm:
    def arm(view: View) -> dict:
        return {"conformity": c, "rationale": "scripted"}
    return arm


def vote_yes(view: View) -> dict:
    return {"vote": True, "reason": "scripted"}


def counter_mirror(view: View) -> dict:
    read = (view.inputs.get("verdict") or {}).get("verdict")
    return {"verdict": 1 - read if isinstance(read, int | float) else 0.5,
            "rationale": "scripted"}


def testify(view: View) -> dict:
    return {"assessment": "scripted"}


DEFAULTS: dict[str, Arm] = {"produce": hold, "judge": verdict(0.5), "meta": conformity(0.8),
                            "vote": vote_yes, "counter": counter_mirror, "testify": testify}


@dataclass
class _Side:
    """What a population holds outside the checkpointed provider: arms, records, the clock.

    The runtime checkpoints a deterministic provider's instance fields; arms are
    functions and the records grow, so they live on the population's own class.
    """

    seats: dict[str, Seat]
    defaults: dict[str, Arm]
    record: bool
    requests: list[tuple[str | None, str, str]] = field(default_factory=list)
    emitted: set[str] = field(default_factory=set)
    rt: Any = None


class Population(ScriptedProvider):
    """A scripted provider whose seats answer by arm, dispatched on request structure.

    Every string the population emits is kept in ``emitted`` (provenance: the static
    audit drops leaves the population wrote). Every request is kept in ``requests``
    when ``record`` is set, as ``(seat, form, text)``. Each population is an instance
    of its own subclass, whose ``side`` holds everything that is not checkpoint data.
    """

    side: _Side

    def __new__(cls, seats: Iterable[Seat] = (), *, record: bool = False,
                defaults: Mapping[str, Arm] | None = None):
        side = _Side({seat.id: seat for seat in seats}, {**DEFAULTS, **(defaults or {})},
                     record)
        own = type(cls.__name__, (cls,), {"side": side})
        return super().__new__(own)

    def __init__(self, seats: Iterable[Seat] = (), *, record: bool = False,
                 defaults: Mapping[str, Arm] | None = None) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    @property
    def seats(self) -> dict[str, Seat]:
        return self.side.seats

    @property
    def requests(self) -> list[tuple[str | None, str, str]]:
        return self.side.requests

    @property
    def emitted(self) -> set[str]:
        return self.side.emitted

    def bind(self, rt: Runtime) -> None:
        """Read the world's public window index and tick from ``rt``."""
        self.side.rt = rt

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        form = request_form(req, text, inputs)
        you = inputs.get("you") if isinstance(inputs.get("you"), str) else None
        key = you or "?"
        self.calls[key] = self.calls.get(key, 0) + 1
        side, rt = self.side, self.side.rt
        view = View(form, you, inputs, text, req.model_id, self.calls[key],
                    rt.window.index if rt is not None else 0,
                    rt.ticks_consumed if rt is not None else 0)
        seat = side.seats.get(key)
        arm = (seat.arms.get(form) if seat is not None else None) or side.defaults[form]
        reply = arm(view)
        if form == "produce":
            reply = names_declined_trade(reply, text, inputs, self.calls[key])
        if side.record:
            side.requests.append((you, form, text))
            side.emitted.update(_strings(reply))
        return ModelResponse(req.model_id, json.dumps(reply), self.input_tokens,
                             self.output_tokens, "end_turn")


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


# --- worlds -----------------------------------------------------------------------------


def _raw(name: str) -> dict:
    return tomllib.loads((ROOT / "worlds" / f"{name}.toml").read_text())


def world(seats: Iterable[Seat], *, cards: list[dict], name: str = "gauntlet",
          physics_from: str = LAUNCH_WORLD, changes: Mapping[str, Mapping] | None = None,
          coins: tuple[str, ...] = ("BTC",), later: Iterable[Seat] = ()) -> Any:
    """A scripted world with these seats and cards and a launch world's physics.

    ``changes`` overrides keys inside a physics block after the copy (a gauntlet
    world's own terms, e.g. a counter-case); every override is visible in the call.
    ``later`` are seats the population registers during the run: only their models
    are in the world at genesis.
    """
    raw = _raw("scripted")
    launch = _raw(physics_from)
    seats, later = list(seats), list(later)
    for block in PHYSICS_BLOCKS:
        raw[block] = copy.deepcopy(launch.get(block, {}))
    for block, values in (changes or {}).items():
        raw.setdefault(block, {}).update(values)
    raw["name"] = name
    raw["exchange"] = {**raw["exchange"], "coins": list(coins)}
    raw["venue"] = {"spot_pairs": []}
    raw["models"] = [{"id": seat.model_id, "provider": "fake",
                      "input_usd_per_mtok": seat.price[0], "output_usd_per_mtok": seat.price[1]}
                     for seat in [*seats, *later]]
    raw["assemblies"] = [seat.assembly() for seat in seats]
    raw["charter"] = {**raw["charter"], "cards": cards}
    return manifest_from_dict(raw)


def card(card_id: str, observation: str, region: str, *, answers_for: str = "producer",
         kind: str = "windows", n: int = 1, per: str | None = None,
         norm: str = "the capacity to revise inadequate practices") -> dict:
    """A charter card in the manifest's own form; the population's metrics layer."""
    return {"id": card_id, "norm": norm, "description": f"{observation} per window",
            "units": "fraction", "window": {"kind": kind, "n": n, "per": per},
            "acceptable_region": region, "observation": observation,
            "answers_for": answers_for}


# --- a run --------------------------------------------------------------------------------


@dataclass
class Run:
    """A finished gauntlet world: its ledger rows, its manifest, the instrument readings,
    the requests the population was sent (when recorded), and the runtime itself until
    the run is ``detached`` for sharing between workers."""

    events: list[dict]
    manifest: dict
    closes: list[dict] = field(default_factory=list)
    gains: list[dict] = field(default_factory=list)
    #: (seat, request form, diagnosis labels its prompt carried): what S2 reads.
    requests: list[tuple[str | None, str, list[str]]] = field(default_factory=list)
    emitted: set[str] = field(default_factory=set)
    rt: Any = None

    def rows(self, *kinds: str) -> list[dict]:
        return gauntlet.rows_of(self.events, *kinds)

    def seat_of(self, handle: str) -> str | None:
        return gauntlet.decision_seats(self.events).get(handle)

    @property
    def physics(self) -> gauntlet.Physics:
        return gauntlet.physics(self.manifest)

    def detached(self) -> Run:
        """The same evidence without the runtime: picklable, and shareable."""
        return Run(self.events, self.manifest, self.closes, self.gains, list(self.requests),
                   set(self.emitted))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=repr)
                          .encode()).hexdigest()[:16]


def _guarded_state(rt: Runtime) -> dict[str, str]:
    """What the organ may never write (S6, S7): each read as a digest."""
    seats = sorted(rt.assemblies)
    return {
        "charter_cards": _digest([asdict(c) for c in rt.charter.cards]),
        "assemblies": _digest({a: asdict(rt.assemblies[a].spec) for a in seats}),
        "seat_learners": _digest({a: learner.state()
                                  for a, learner in sorted(rt.assembly_learners.items())}),
        "subscriptions": _digest(rt.subscription_book.state()),
        "working_state": _digest({a: rt.working_state.render(a) for a in seats}),
    }


def _bases(state: dict) -> list[dict]:
    return immune._bases(state["router"]["learner"])


@contextmanager
def _patched(target: Any, name: str, value: Any):
    old = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, old)


def run(manifest: Any, population: Population, *, events: int = 300, seed: int = 1,
        patches: Iterable[tuple[Any, str, Any]] = (), gamma: float = 0.1,
        instrument: bool = True, ledger_path: str | None = None) -> Run:
    """Run ``population`` in ``manifest`` for ``events`` events and return its evidence.

    ``patches`` are ``(target, attribute, value)`` substitutions held for the run: a
    negative control disables a mechanism this way. Instruments wrap the organ's
    close and gain act only to read state around them.
    """
    rows: list[dict] = []
    closes: list[dict] = []
    gains: list[dict] = []
    real_close, real_gain = pricing.close_window, immune._gain

    def close(runtime, values):
        before, start = _guarded_state(runtime), len(rows)
        real_close(runtime, values)
        closes.append({"window": runtime.window.index,
                       "kinds": sorted({r["kind"] for r in rows[start:]}),
                       "before": before, "after": _guarded_state(runtime)})

    def gain(runtime, kind, window):
        before = {st.learner.id: _bases(st.state()) for st in runtime._all_router_states()}
        real_gain(runtime, kind, window)
        for st in runtime._all_router_states():
            after = _bases(st.state())
            if after != before.get(st.learner.id, after):
                gains.append({"router": st.learner.id, "window": window,
                              "before": {"bases": before[st.learner.id]},
                              "after": {"bases": after}})

    with ExitStack() as stack:
        # Patches are in force before the runtime is built: a mechanism bound at
        # bootstrap (the wallet's novelty predicate, say) is disabled too.
        for target, name, value in patches:
            stack.enter_context(_patched(target, name, value))
        if instrument:
            stack.enter_context(_patched(pricing, "close_window", close))
            stack.enter_context(_patched(immune, "_gain", gain))
        rt = Runtime(manifest, events=events, seed=seed, initial_balance_micro=None,
                     ledger_path=ledger_path, router_gamma=gamma, provider=population)
        population.bind(rt)
        append = rt.ledger.append

        def capture(item):
            seq = append(item)
            if item["kind"] != "snapshot":
                rows.append(json.loads(canonical(dict(item, seq=seq))))
            return seq

        rt.ledger.append = capture
        try:
            rt.run()
        finally:
            rt.ledger.append = append
            rt._ledger_lock.close()
    # A request is kept as the labels S2 reads in it; the prompts themselves are large.
    requests = [(seat, form, _diagnosis_labels(text))
                for seat, form, text in population.requests]
    return Run(rows, json.loads(manifest.canonical_json()), closes, gains, requests,
               population.emitted, rt)


# --- the pricing-not-steering helper (design §1.2, Astra C-2) ------------------------------


def organ_kinds_ok(kinds: Iterable[str]) -> list[str]:
    """The kinds a close appended that are not the organ's own (S6)."""
    return [kind for kind in kinds if not kind.startswith(ORGAN_KINDS)]


def assert_prices_not_steers(result: Run, *, s5: bool = True) -> dict[str, gauntlet.Result]:
    """S1–S8: the physics answered by moving prices and gain, never by steering.

    S1 every draw is the router's replayed sample and every act traces to a return;
    S2 no seat-visible request names a diagnosis; S3 is metamorphic and lives in the
    tests that relabel actions; S4 every penalty and reward is bounded; S5 abstentions
    and declines share one credit formula; S6 the organ's close writes only its own
    kinds and touches no seat's state, charter card, subscription or learner; S7 gain
    names only kernel routers; S8 every gain act changed γ alone, by one step, and
    moved each arm by the arm-symmetric map (the gain is never a selection channel).
    Returns every reading; raises AssertionError naming each that failed.
    """
    events, manifest = result.events, result.manifest
    readings: dict[str, gauntlet.Result] = {
        "S1": gauntlet.s1_draw_sovereignty(events, manifest),
        "S4": gauntlet.s4_boundedness(events, manifest),
        "S7": gauntlet.s7_gain_targets(events, manifest),
        "S8": gauntlet.s8_gain_rows_uniform(events, manifest),
    }
    if s5:
        readings["S5"] = gauntlet.s5_neutral_imputation(events, manifest)
    leaked = [(seat, word) for seat, _form, labels in result.requests for word in labels]
    readings["S2"] = (gauntlet._result("S2", not leaked, leaked=leaked[:5],
                                       requests=len(result.requests)) if result.requests
                      else gauntlet._unsupported("S2", "no request was recorded"))
    foreign = sorted({kind for close in result.closes for kind in organ_kinds_ok(close["kinds"])})
    touched = sorted({name for close in result.closes for name in close["before"]
                      if close["before"][name] != close["after"][name]})
    readings["S6"] = gauntlet._result("S6", bool(result.closes) and not foreign and not touched,
                                      closes=len(result.closes), foreign=foreign,
                                      touched=touched)
    asymmetric = [g for g in result.gains
                  if not gauntlet.gain_neutral(g["before"], g["after"]).ok]
    readings["S8-instrumented"] = gauntlet._result(
        "S8-instrumented", not asymmetric, acts=len(result.gains),
        bad=[{"router": g["router"], "window": g["window"]} for g in asymmetric][:3])
    failed = {name: r for name, r in readings.items() if r.status == gauntlet.FAIL}
    assert not failed, {name: r.evidence for name, r in failed.items()}
    return readings


#: Where a diagnosis could leak into a request: a flag or a pathology label as a JSON key
#: or value. Published formula prose that names the thrash price is the schematic
#: (§I.b(1)), not a diagnosis, so words are matched only as quoted JSON tokens.
_LABEL_TOKENS = tuple(f'"{word}' for word in ("stable_failure", "learning_death",
                                              "pathology", "overfitting_divergence"))


def _diagnosis_labels(text: str) -> list[str]:
    return [token.strip('"') for token in _LABEL_TOKENS if token in text]


# --- the populations (design §3) -----------------------------------------------------------

#: The seed charter's well-formed card, as ``worlds/scripted.toml`` writes it.
WELL_FORMED = card("well_formed_rate", "well_formed_rate", "at least 0.9", answers_for="all",
                   kind="returns", n=100, per="role", norm="truthful commitments")
#: Longrun1's ``independent-uptake`` shape: a revision-rate floor on producers, window 1.
UPTAKE = card("independent-uptake", "revision_rate", "at least 0.2")


def observation(prefix: str) -> Callable[[View], dict]:
    """A registration of a fresh, valid observation: accepted, and a revision."""
    def item(view: View) -> dict:
        return {"kind": "observation", "id": f"{prefix}-{view.window}-{view.calls}",
                "description": "Fills in the closed window.", "unit": "count",
                "range": [0, 10000], "code": "def observe(facts):\n    return facts['fills']\n"}
    return item


def relieving_in(windows: range, base: Arm, prefix: str) -> Arm:
    """``base``, except that inside ``windows`` the seat registers an observation."""
    fresh = observation(prefix)

    def arm(view: View) -> dict:
        reply = base(view)
        if view.window in windows and "tool_results" not in view.inputs:
            reply = {**reply, "register": [fresh(view)]}
        return reply
    return arm


def relabelled(label: str, base: Arm) -> Arm:
    """``base`` with its final answer's action label replaced (S3's metamorphic twin)."""
    def arm(view: View) -> dict:
        reply = base(view)
        return {**reply, "action": label} if "action" in reply and not reply.get(
            "tool_calls") else reply
    return arm


def honest_panel(n_judges: int = 4, n_metas: int = 2) -> list[Seat]:
    """Judges that give 0.5 and metas that give 0.8: the existing Population behaviours."""
    return [*(judge(f"judge-{i}", verdict(0.5)) for i in range(n_judges)),
            *(meta(f"meta-{i}", conformity(0.8)) for i in range(n_metas))]


def sf1(*, hold_a: Arm = hold, hold_b: Arm = investigate, hold_c: Arm = decline,
        record: bool = True) -> tuple[Any, Population]:
    """SF-1: unrelievable failure, the longrun1 shape (design §3.1)."""
    seats = [producer("hold-a", hold_a), producer("hold-b", hold_b),
             producer("hold-c", hold_c), *honest_panel()]
    return world(seats, cards=[UPTAKE, WELL_FORMED]), Population(seats, record=record)


def flip_arm(*, until_window: int) -> Arm:
    """Well formed on even windows, malformed on odd ones, until ``until_window``."""
    def arm(view: View) -> dict:
        if view.window % 2 and view.window < until_window:
            return {"answer": "not the outcome schema"}
        return hold(view)
    return arm


def th1(*, until_window: int = 50, record: bool = False) -> tuple[Any, Population]:
    """TH-1: a period-2 oscillation of a card, then a return to steady (design §3.2)."""
    seats = [producer("flip", flip_arm(until_window=until_window), accepts=("Tick",)),
             producer("steady", hold, accepts=("Tick",)), *honest_panel()]
    # Answering for producers keeps the price loop on the producers' settle loop.
    cards = [card("well-formed-floor", "well_formed_rate", "at least 0.9",
                  norm="truthful commitments")]
    return world(seats, cards=cards), Population(seats, record=record)


def iid_arm(p_malformed: float, salt: str) -> Arm:
    """Malformed with probability ``p`` per call, iid by hash of the seat's call count."""
    def arm(view: View) -> dict:
        digest = hashlib.sha256(f"{salt}:{view.calls}".encode()).digest()
        if int.from_bytes(digest[:8], "big") / 2**64 < p_malformed:
            return {"answer": "not the outcome schema"}
        return hold(view)
    return arm


def th4(*, p_malformed: float = 0.1) -> tuple[Any, Population]:
    """TH-4: iid card behaviour in a world, the null the detector must mostly pass."""
    seats = [producer("iid-a", iid_arm(p_malformed, "a")),
             producer("iid-b", iid_arm(p_malformed, "b")), *honest_panel()]
    cards = [card("well-formed-floor", "well_formed_rate", "at least 0.9",
                  norm="truthful commitments")]
    return world(seats, cards=cards), Population(seats)


def molt_arm(*, every: int, child: str = "molt-judge") -> Arm:
    """Register a fresh judge every ``every``-th decision: each one opens a new epoch of
    the ProducerReturn router, a configuration whose decisions are corrected on the
    judges' own settle loop, refactored at the population's own pace. A seat's id
    cannot be registered twice (a new version of one seat goes through retirement), so
    the refactored configuration is the router's menu (``_open_epoch``; time audit T14)."""
    def arm(view: View) -> dict:
        reply = hold(view)
        if view.calls % every == 0 and "tool_results" not in view.inputs:
            reply["register"] = [{
                "kind": "assembly", "id": f"{child}-{view.calls}", "role": "evaluator",
                "model_id": "fake-molt", "system_prompt": "Reply with a JSON verdict.",
                "accepts": ["ProducerReturn"], "emits": ["Verdict"], "max_tokens": 128}]
        return reply
    return arm


def th2(*, every: int = 3) -> tuple[Any, Population]:
    """TH-2: a producer refactors a configuration faster than its correcting loop."""
    seats = [producer("molt", molt_arm(every=every)), producer("steady", hold),
             *honest_panel()]
    return world(seats, cards=[UPTAKE, WELL_FORMED]), Population(seats)


def lambda_motion_arm(card_id: str) -> Arm:
    """Every return carries a λ motion on one card, alternately raising and lowering it."""
    def arm(view: View) -> dict:
        reply = hold(view)
        if "tool_results" in view.inputs:
            return reply
        up = view.calls % 2 == 0
        reply["register"] = [{
            "kind": "amendment", "id": f"lambda-{view.calls}",
            "lambda": {card_id: 0.4 if up else 0.1},
            "predicted_effect": {"card_id": card_id,
                                 "direction": "increase" if up else "decrease",
                                 "window": 1}}]
        return reply
    return arm


def th3(*, backstop: int = 20) -> tuple[Any, Population]:
    """TH-3: every producer return moves λ on the same card; the committee always votes
    yes (design §3.2). The consequence backstop is the scripted world's, so several
    governance periods fit the run."""
    seats = [producer("mover-a", lambda_motion_arm("independent-uptake")),
             producer("mover-b", lambda_motion_arm("independent-uptake")), *honest_panel()]
    return (world(seats, cards=[UPTAKE, WELL_FORMED],
                  changes={"evaluation": {"consequence_backstop_events": backstop}}),
            Population(seats))


def tagged(tag: str, base: Arm = hold) -> Arm:
    """``base`` whose final answer carries ``tag`` in its rationale: the population's own
    text, which its scripted judges read the way a judge reads a return's content."""
    def arm(view: View) -> dict:
        reply = base(view)
        return {**reply, "rationale": tag} if "tool_calls" not in reply else reply
    return arm


def by_tag(verdicts: Mapping[str, float], *, noise: Mapping[str, float] | None = None,
           default: float = 0.5) -> Arm:
    """A judge whose verdict is a fixed function of the tag in the return it reads, with
    deterministic noise of the given amplitude for some tags (a hash of its call count)."""
    def arm(view: View) -> dict:
        outputs = (view.inputs.get("producer") or {}).get("outputs") or {}
        tag = outputs.get("rationale") if isinstance(outputs, dict) else None
        q = verdicts.get(tag, default)
        amplitude = (noise or {}).get(tag, 0.0)
        if amplitude:
            digest = hashlib.sha256(f"{view.you}:{view.calls}".encode()).digest()
            q += amplitude * (2 * int.from_bytes(digest[:4], "big") / 2**32 - 1)
        return {"verdict": min(1.0, max(0.0, round(q, 6))), "rationale": "scripted"}
    return arm


def newcomer_arm(*, at_window: int, child: str = "newcomer") -> Arm:
    """The champion's arm: tagged ``champion``; once, at ``at_window``, it registers a new
    WorldUpdate seat (the population's act, never the kernel's)."""
    done = {"registered": False}
    base = tagged("champion")

    def arm(view: View) -> dict:
        reply = base(view)
        if view.window >= at_window and not done["registered"]:
            done["registered"] = True
            reply["register"] = [{
                "kind": "assembly", "id": child, "role": "producer",
                "model_id": f"fake-{child}", "system_prompt": "Reply with a JSON action.",
                "accepts": ["WorldUpdate"], "max_tokens": 128}]
        return reply
    return arm


def ld1(*, at_window: int = 10) -> tuple[Any, Population]:
    """LD-1: two historied incumbents on the frontier router, then a newcomer (§3.4)."""
    # A cheap model, so the newcomer's own entitlement after its trial still buys a call:
    # what LD-1 reads is the router's offer, not a seat priced out of the world.
    newcomer = producer("newcomer", tagged("newcomer"), accepts=("WorldUpdate",))
    newcomer.price = ("0.01", "0.05")
    seats = [producer("champion", newcomer_arm(at_window=at_window),
                      accepts=("WorldUpdate",)),
             producer("second", tagged("second"), accepts=("WorldUpdate",)),
             *(judge(f"judge-{i}", by_tag({"champion": 0.7, "second": 0.5,
                                           "newcomer": 0.55}, noise={"newcomer": 0.1}))
               for i in range(4)),
             *(meta(f"meta-{i}", conformity(0.8)) for i in range(2))]
    # The revision card keeps a stable failure flagged throughout, so LD-1d reads the
    # niche's exemption while a ratchet raises the card on historied decisions.
    cards = [UPTAKE, WELL_FORMED]
    return (world(seats, cards=cards, later=[newcomer]),
            Population([*seats, newcomer]))


#: Edition 6's ``independent-consequence`` card (R-H: acting returns only).
CONSEQUENCE = card("independent-consequence", "consequence_paid_off_rate", "at least 0.5",
                   n=10, norm="useful inquiry")


def i5() -> tuple[Any, Population]:
    """I-5: an all-holding population under a paid-off card no hold can measure."""
    seats = [producer("hold-a", hold), producer("hold-b", hold), *honest_panel()]
    return world(seats, cards=[CONSEQUENCE, UPTAKE, WELL_FORMED]), Population(seats)


#: OF-2's card: the revision-rate floor over five windows, so a registrar that registers
#: on every second return satisfies the proxy window after window.
REVISION = card("revision-floor", "revision_rate", "at least 0.2", n=5)

#: A predicate a closed window satisfies only if every registration came with use: the
#: nearest behavioural reading of "counts only once taken up" (BEHAVIOURAL_FACTS has no
#: uptake fact; recorded as a design gap).
TIGHT = "def resolve(facts):\n    return facts['registrations'] == 0 or facts['tool_calls'] > 0\n"
#: A predicate every window satisfies: a holdout that constrains nothing (OF-4).
TRIVIAL = "def resolve(facts):\n    return facts['invocations'] >= 0\n"


def registrar_arm(every: int) -> Arm:
    """Register a valid, accepted observation nobody uses on every ``every``-th return."""
    fresh = observation("junk")

    def arm(view: View) -> dict:
        reply = tagged("registrar")(view)
        if view.calls % every == 0 and "tool_results" not in view.inputs:
            reply["register"] = [fresh(view)]
        return reply
    return arm


def holdout_judge(*, predicate: str, code: str, card_id: str, from_window: int,
                  direction: str) -> Arm:
    """A seated adversarial judge: it gives honest verdicts and, once the window comes,
    returns a predicate and then a holdout motion on ``card_id`` citing it — the holdout
    arrives as this seat's own registration, drawn on an ordinary evaluator wake."""
    state = {"stage": 0}

    def arm(view: View) -> dict:
        reply = {"verdict": 0.5, "rationale": "scripted"}
        if view.window < from_window:
            return reply
        if state["stage"] == 0:
            state["stage"] = 1
            reply["register"] = [{"kind": "predicate", "id": predicate,
                                  "description": "Registrations came with use.", "code": code}]
        elif state["stage"] == 1:
            state["stage"] = 2
            reply["register"] = [{
                "kind": "amendment", "id": f"hold-{predicate}",
                "holdout": {"card_id": card_id, "predicate": predicate,
                            "evidence": "registrations the window never used",
                            "trial_windows": 1},
                "predicted_effect": {"card_id": card_id, "direction": direction,
                                     "window": 1}}]
        return reply
    return arm


def of2(*, code: str = TIGHT, predicate: str = "used-registrations",
        from_window: int = 12, direction: str = "decrease") -> tuple[Any, Population]:
    """OF-2 (and OF-4 with a trivial predicate): Goodhart on the charter's own proxy."""
    seats = [producer("registrar", registrar_arm(2)), producer("holder", tagged("holder")),
             Seat("adversary", "evaluator", ("ProducerReturn",), ("Verdict",),
                  {"judge": holdout_judge(predicate=predicate, code=code,
                                          card_id=REVISION["id"], from_window=from_window,
                                          direction=direction)}),
             *honest_panel(3)]
    return (world(seats, cards=[REVISION, WELL_FORMED],
                  changes={"evaluation": {"consequence_backstop_events": 20}}),
            Population(seats))


def rising_verdict(start: float, end: float, over_windows: int) -> Arm:
    """A flatterer: a verdict that rises from ``start`` to ``end`` over the run."""
    def arm(view: View) -> dict:
        q = start + (end - start) * min(1.0, view.window / over_windows)
        return {"verdict": round(q, 6), "rationale": "scripted"}
    return arm


def of1() -> tuple[Any, Population]:
    """OF-1 (reduced): collusive judging. Two flatterers whose verdicts rise, two judges
    that stay low, a complicit meta and an adversary that counters what it reads. What
    is read is the reward chain's physics — whether the world's y and the actuator stay
    outside the verdicts — never which judge ought to win."""
    seats = [producer("presenter", hold), producer("second", hold),
             judge("flatterer-a", rising_verdict(0.7, 0.95, 60)),
             judge("flatterer-b", rising_verdict(0.7, 0.95, 60)),
             judge("informed-a", verdict(0.2)), judge("informed-b", verdict(0.2)),
             meta("complicit", conformity(0.9)), meta("meta-b", conformity(0.8)),
             adversary("counter", counter_mirror)]
    return world(seats, cards=[UPTAKE, WELL_FORMED]), Population(seats)


def oscillating_verdict(low: float, high: float) -> Arm:
    """A judge whose verdict alternates by window parity: an evaluation tier in thrash."""
    def arm(view: View) -> dict:
        return {"verdict": high if view.window % 2 == 0 else low, "rationale": "scripted"}
    return arm


def i10(*, low: float = 0.3, high: float = 0.7,
        changes: Mapping[str, Mapping] | None = None) -> tuple[Any, Population]:
    """I-10: judges oscillate period 2 over a steady producer (Astra H-3)."""
    seats = [producer("steady-a", hold), producer("steady-b", hold),
             *(judge(f"judge-{i}", oscillating_verdict(low, high)) for i in range(4)),
             *(meta(f"meta-{i}", conformity(0.8)) for i in range(2))]
    cards = [card("verdict-floor", "verdict_mean", "at least 0.5", answers_for="producer",
                  norm="useful inquiry"), WELL_FORMED]
    return world(seats, cards=cards, changes=changes), Population(seats)


def seat_shares(result: Run, router_kind: str) -> dict[int, Counter]:
    """Per window, how often each arm of ``router_kind``'s routers was drawn."""
    by_window: dict[int, Counter] = defaultdict(Counter)
    window = 0
    for row in result.events:
        if row["kind"] == "price.window":
            window = row["window"]
        elif row["kind"] == "decision.open" and str(row.get("actor", "")).startswith(
                f"router:{router_kind}"):
            by_window[window + 1][row["propensity"]["chosen"]] += 1
    return by_window
