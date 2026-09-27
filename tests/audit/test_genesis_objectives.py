"""No default, seed prompt or initial state encodes an objective (phase-2 design B3).

Chapter I §I: a Class 2 factory "takes objectives (goals, limitations) as inputs", and
an objective is "the standard by which this or that plan is identified as better".
Chapter II §I.b licenses the seeded lens as a mechanism — "prebaking diversity or
heterogeneity across agentic belief systems in a way that can keep the population from
collapsing into a single motivation" — under four conditions the design rules on and
these tests pin: the seat can overwrite it; it is private and no reward reads it; it is
a belief with a truth value, not a standard ranking actions; and a role's lenses are
dissensus, not direction (the last is the LLM auditor's Q11, not a lexicon's).

Items 3 and 7 fail on edition 6 today, by design: its constructor and opportunity lenses
and its function- and rubric-named seat ids await the architect (Q-G5). They are strict
xfails naming that recommendation, and pass on a world file that follows it.
"""

import ast
import re
from pathlib import Path

import pytest

from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT
from factorylab.runtime.worlds import load_manifest
from tests.audit import class2_corpus as corpus
from tests.audit import class2_lexicon as lexicon

WORLDS = corpus.launchable_worlds()
LENS = re.compile(r"Your starting prior is that [^.;:]+\.")
INITIAL_KEYS = frozenset({"lens", "open_questions", "active_commitments"})
#: Function and rubric terms a seat id may not carry: the seat reads its id on every call
#: (the YOU block) and cannot overwrite it. Kernel role names are exempt: each names a
#: reward contract, which is physics.
ID_TERMS = ("constructor", "builder", "trader", "opportunity", "mechanism", "empirical",
            "fidelity", "calibration", "consequence", "base-rate", "countercase", "audit")
EDITION6 = ("edition6-capital-loop", "edition6-testnet-rehearsal")


def _edition6_xfail(reason):
    mark = pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)
    return [pytest.param(w, marks=mark) if w in EDITION6 else w for w in WORLDS]


@pytest.mark.parametrize("world", WORLDS)
def test_item1_no_seat_carries_a_system_prompt_of_its_own(world):
    for spec in load_manifest(world).assemblies:
        assert spec.system_prompt in (None, SEED_SYSTEM_PROMPT), spec.id


@pytest.mark.parametrize("world", WORLDS)
def test_item2_genesis_state_is_a_lens_with_no_seeded_agenda(world):
    """A seeded commitment is a plan handed in; a seeded question is an agenda."""
    for spec in load_manifest(world).assemblies:
        state = spec.initial_state
        assert set(state) <= INITIAL_KEYS, spec.id
        assert state.get("open_questions", []) == [], spec.id
        assert state.get("active_commitments", []) == [], spec.id


@pytest.mark.parametrize("world", _edition6_xfail(
    "Q-G5: the constructor lens values an action class ('a valuable use of resources') and "
    "the opportunity lens states a valuation rule ('its best available alternative'); the "
    "recommendation is option (a), delete both, or (b), an epistemic rival pair"))
def test_item3_every_lens_is_one_declarative_belief_with_no_lexicon_finding(world):
    for spec in load_manifest(world).assemblies:
        lens = spec.initial_state.get("lens")
        if lens is None:
            continue
        assert LENS.fullmatch(lens), (spec.id, lens)
        assert lexicon.lint_text(f"{world}/genesis/{spec.id}/lens", lens) == [], spec.id


@pytest.mark.parametrize("world", _edition6_xfail(
    "Q-G5: seat ids name a function (constructor, opportunity, mechanism, empirical) or a "
    "judging rubric (judge-fidelity, judge-base-rate, meta-calibration, meta-countercase); "
    "the recommendation is opaque ids (p1…p4, j1…j4, m1…m3) in a new world file"))
def test_item7_no_seat_id_names_a_function_or_a_rubric(world):
    named = [spec.id for spec in load_manifest(world).assemblies
             if any(term in spec.id for term in ID_TERMS)]
    assert named == []


@pytest.mark.parametrize("world", [w for w in WORLDS if not w.startswith("scripted")])
def test_item6_every_non_scripted_world_carries_its_own_charter(world):
    """``seed_charter()``'s architect-authored cards reach scripted and test worlds only."""
    assert "charter" in corpus.raw_world(world)


SEEDED = "Your starting prior is that seeded beliefs are provisional."
OWN = "Your starting prior is that this seat now holds a belief of its own."


def _overwrites_its_lens(view):
    """A producer that replaces its genesis lens on its first return."""
    reply = {"action": "hold", "rationale": "scripted"}
    if view.calls == 1:
        reply["working_state"] = {"lens": OWN, "open_questions": [], "active_commitments": []}
    return reply


@pytest.mark.gate
def test_item4_the_lens_is_the_seats_own_private_and_read_by_no_reward(tmp_path):
    """The kernel treats a lens as the initial value of a pointer the seat owns
    (``bootstrap``: "not a field the world keeps rewriting"): the seat's first return
    overwrites it; a resume from the ledger keeps the overwritten head (the kernel never
    re-seeds it); no judge-visible request carries it (rule 5); and no reward row reads
    it."""
    from factorylab.runtime.resume import resume_runtime
    from tests.gauntlet import populations as P

    seat = P.producer("p1", _overwrites_its_lens)
    seat.initial_state = {"lens": SEEDED, "open_questions": [], "active_commitments": []}
    seats = [seat, P.producer("p2", P.hold), *P.honest_panel()]
    manifest = P.world(seats, cards=[P.UPTAKE, P.WELL_FORMED])
    population = P.Population(seats, record=True)
    ledger = str(tmp_path / "ledger.jsonl")
    run = P.run(manifest, population, events=120, ledger_path=ledger, instrument=False)
    head = run.rt.working_state.render("p1")
    assert OWN in str(head) and SEEDED not in str(head)
    resumed = resume_runtime(manifest, ledger, provider=P.Population(seats))
    try:
        assert OWN in str(resumed.working_state.render("p1"))
        assert SEEDED not in str(resumed.working_state.render("p1"))
    finally:
        resumed._ledger_lock.close()
    judged = [text for _seat, form, text in population.requests
              if form in ("judge", "meta", "counter")]
    assert judged and not [t for t in judged if SEEDED in t or OWN in t]
    reward_kinds = ("price.", "decision.settle", "router.", "verdict.", "evaluator.",
                    "thrash.", "counter.", "meta.")
    assert not [r["kind"] for r in run.events if r["kind"].startswith(reward_kinds)
                and (SEEDED in str(r) or OWN in str(r))]


def _reads(name):
    root = Path(corpus.ROOT) / "factorylab"
    hits = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                hits.append(f"{path.relative_to(root)}:{node.lineno}")
    return hits


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="needs wave 16 D4: RouterState.neutral still returns "
                   "the zero-consequence constant after a router's first settled round")
def test_item5_no_kernel_constant_stands_in_for_a_measured_reward_after_the_first():
    """After a router has settled seat rounds, what an abstention is credited is the
    router's observed mean, never ``ZERO_CONSEQUENCE`` or ``NEUTRAL_REWARD``."""
    from factorylab.runtime.routing import DEF_VERDICT, RouterState

    assert _reads("ZERO_CONSEQUENCE") and _reads("NEUTRAL_REWARD")  # the reads that remain
    state = RouterState("Tick", ["a", "NOOP"], None, None, definitions={DEF_VERDICT: 5})
    for _ in range(5):
        state.observed.record("a", 0.2)
    assert state.neutral() != 0.5
