"""NOOP is not free (ruling R9; versioning P4, primitive F1).

A router may draw "wake nobody", because not spending is a real choice. Its credit
was an architect constant no charter price touched, so a woken seat had to beat
that constant plus its card penalty and the frontier drifted to abstention. The
abstention now bears the same charter prices a woken decision of its role bears in
the window it was drawn in.
"""

from dataclasses import replace

import pytest

from factorylab.charter.measurement import CardSamples
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import measured_role
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime import pricing
from factorylab.runtime.observations import window_facts
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import NOOP
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_attributable_blame import _card, _commitments, _decision, _runtime
from tests.runtime.test_learning_signal import _drawn, _router, _settle


def _learned(rt, r, p, *, router=True):
    """Ruling R10-l: every learner learns (r + B - P) / (1 + B), no clip, B = 2 * cap for
    a router (card share plus thrash) and cap for a seat's own learner."""
    bound = rt.m.prices.penalty_cap * (2 if router else 1)
    return (r + bound - p) / (1 + bound)


def _abstention(rt, role="evaluator"):
    handle = rt.queue.open(
        actor="test-router", event_id="noop", channel="conformity", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0, "test-router", "state"),
    )
    rt._contribution(handle, role)
    return handle


def test_an_abstention_bears_the_price_a_woken_decision_of_its_role_bears(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    woken = [_decision(rt, seat) for seat in ("eval-a", "eval-b")]
    noop = _abstention(rt)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    penalty = rt._penalty_for("evaluator", woken[0])
    assert penalty > 0
    assert rt._penalty_for("evaluator", noop) == pytest.approx(penalty)
    charged = rt._priced_abstention(noop)
    assert charged == pytest.approx(penalty)
    # A woken seat that delivered what an abstention is worth is no worse off than it:
    # the same raw value and the same charge, on the same map.
    assert rt._penalty_for("evaluator", woken[1]) == pytest.approx(charged)


def test_an_abstention_the_window_never_recorded_is_credited_unpriced():
    rt = _runtime(_card(per=None))
    handle = rt.queue.open(
        actor="test-router", event_id="noop", channel="conformity", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0, "test-router", "state"),
    )
    assert rt._priced_abstention(handle) == 0.0


def test_a_mixed_menu_abstention_is_priced_as_the_draw_would_have_woken(monkeypatch):
    """A judge router whose menu also holds a producing seat (a population seat that
    accepts ProducerReturn) stood in for both roles: its abstention bears each role's
    price in the proportion the draw would have woken them, not the price of the
    contract a mixed-menu NOOP is filed under."""
    from factorylab.learners.router import Sample

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    sample = Sample(("eval-a", "eval-b", "seed-decider", NOOP), (0.3, 0.3, 0.2, 0.2), NOOP,
                    0, "router:ProducerReturn", "h", ())
    roles = rt._abstention_roles(sample)
    assert roles == pytest.approx({"evaluator": 0.75, "producer": 0.25})
    noop = _abstention(rt)
    rt.window.decisions[noop]["menu_roles"] = roles
    for seat in ("eval-a", "eval-b"):
        _decision(rt, seat)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    expected = (0.75 * rt._penalty_for("evaluator", noop)
                + 0.25 * rt._penalty_for("producer", noop))
    assert expected > 0
    assert rt._priced_abstention(noop) == pytest.approx(expected)
    # A one-role menu is that role alone, and an empty draw weighs its seats equally.
    judges = Sample(("eval-a", "eval-b", NOOP), (0.0, 0.0, 1.0), NOOP, 0, "r", "h", ())
    assert rt._abstention_roles(judges) == {"evaluator": 1.0}


def _gap_runtime(monkeypatch):
    # The price owner is under test, not the immune organ or a full world loop.
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    card = replace(_card(per=None), observation="verdict_mean", answers_for="evaluator",
                   window=MetricWindow("windows", 1, None),
                   acceptable_region="at least 0.8")
    return _runtime(card), card


def _next_gap(rt):
    rt.window = pricing.MeasureWindow(rt.window.index + 1, rt._equity_micro())
    rt.price_windows[rt.window.index] = rt.window
    rt.card_samples.values.clear()


def test_held_abstention_prices_are_causal_and_not_ordinary_act_imputations(monkeypatch):
    """R16c-3: neither refusal nor late credit can erase or replace a supported fact."""
    rt, card = _gap_runtime(monkeypatch)
    warmup = _abstention(rt)
    rt._close_price_window()
    assert rt._priced_abstention(warmup) == 0
    _next_gap(rt)
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    source = rt.window.index
    _next_gap(rt)
    noop = _abstention(rt)
    decline = _decision(rt, "eval-a")
    ordinary = _decision(rt, "eval-b")
    rt._close_price_window()
    # Settle after close, before the price owner releases completed attribution.
    rt._settle_declined(decline, "declined")
    _settle(rt, ordinary, SettleStatus.CENSORED)
    terms = rt._abstention_price_terms(noop)
    assert len(terms) == 1
    term = terms[0]
    assert term["held"] is True and term["source_window"] == source
    assert term["window"] == source + 1
    assert term["violation"] == pytest.approx(0.75)
    expected = min(rt.m.prices.penalty_cap, term["lambda"] * 0.75) / 3
    assert expected > 0
    assert rt._priced_abstention(noop) == pytest.approx(expected)
    assert rt._priced_abstention(decline) == pytest.approx(expected)
    assert rt._priced_abstention(ordinary) == 0
    assert rt._penalty_for("evaluator", ordinary) == 0
    assert rt._penalty_for("evaluator", noop) == 0  # no implicit fallback
    assert rt._abstention_price_terms(ordinary) == []
    _next_gap(rt)
    rt.window.verdicts = {"subject": {"eval-a": [0.8]}}
    current = _abstention(rt)
    rt._close_price_window()
    assert rt._priced_abstention(current) == 0
    assert not any(t.get("held") for t in rt._abstention_price_terms(current))
    assert rt._priced_abstention(noop) == pytest.approx(expected)
    assert rt._priced_abstention(warmup) == 0  # no future-window leakage
    _next_gap(rt)
    after_recovery = _abstention(rt)
    rt._close_price_window()
    assert rt._priced_abstention(after_recovery) == 0
    assert rt._abstention_price_terms(after_recovery)[0]["source_window"] == source + 2


def test_held_evidence_never_carries_holdout_owners_or_becomes_an_observation(monkeypatch):
    """A supported proxy is reusable; another decision's holdout debt is not."""
    rt, card = _gap_runtime(monkeypatch)
    rt.predicates.register(
        "one-call", "Invocation count",
        "def resolve(facts):\n    return facts['invocations'] > 0\n",
        facts={"invocations": 0}, persist=lambda _p: None,
    )
    rt.charter = replace(rt.charter, cards=(replace(card, holdout=("one-call@1",)),))
    rt._derive_regions()
    # A failing predicate without a supported card is not a supported predecessor.
    warmup = _abstention(rt)
    rt._close_price_window()
    assert rt._priced_abstention(warmup) == 0
    assert not rt.window.closed_held
    _next_gap(rt)
    owner = _decision(rt, "eval-a")
    rt.window.decisions[owner]["invocations"] = 0
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    assert rt.window.closed_holdouts[card.id] > 0
    assert rt.window.closed_holdout_attribution[card.id]["shares"] == {owner: 1.0}
    _next_gap(rt)
    noop = _abstention(rt)
    rt._close_price_window()
    term, = rt._abstention_price_terms(noop)
    assert term["violation"] == pytest.approx(0.75)
    assert term["holdout_violation"] == term["attributed_holdout_violation"] == 0
    assert term["holdout_attributees"] == {} and term["holdout_share"] == 0
    assert rt.window.closed_held  # the excluded field is not empty
    assert "closed_held" not in window_facts(rt.window)
    samples = CardSamples()
    samples.closed(rt.window)
    assert "closed_held" not in samples.windows[-1]


@pytest.mark.parametrize("change", ["observation", "role", "kind", "scope", "remove"])
def test_a_redefined_or_removed_metric_cannot_inherit_held_prices(monkeypatch, change):
    """A reused id is not evidence for different rows, roles or scopes."""
    rt, card = _gap_runtime(monkeypatch)
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    replacements = {
        "observation": replace(card, observation="verdict_std"),
        "role": replace(card, answers_for="producer"),
        "kind": replace(card, window=MetricWindow("returns", 1, None)),
        "scope": replace(card, window=MetricWindow("windows", 1, "role")),
        "remove": card,
    }
    if change == "remove":
        rt.charter = replace(rt.charter, cards=())
        rt._derive_regions()
    changed = replacements[change]
    rt.charter = replace(rt.charter, cards=(changed,))
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    _next_gap(rt)
    noop = _abstention(rt, role=changed.answers_for)
    rt._close_price_window()
    assert rt._priced_abstention(noop) == 0
    assert not any(t.get("held") for t in rt._abstention_price_terms(noop))


@pytest.mark.parametrize("declines", [False, True])
def test_mid_gap_checkpoint_resumes_actual_held_credit_charges(monkeypatch, declines):
    """Checkpointing the gap preserves emitted prices and exactly-once learner credits."""
    rt, card = _gap_runtime(monkeypatch)
    rt.window.verdicts = {"subject": {"eval-a": [0.2]}}
    rt._close_price_window()
    _next_gap(rt)
    rt._close_price_window()  # an entire gap before the checkpoint
    _next_gap(rt)
    router, _ = _router(rt)
    router.latency = [5, 1]
    arm = next(a for a in router.universe if a != NOOP) if declines else NOOP
    handle = _drawn(rt, router, arm)
    rt._contribution(handle, "evaluator")
    if declines:
        rt._settle_declined(handle, "declined")
    else:
        _settle(rt, handle, SettleStatus.INAPPLICABLE)
    rt._deliver_returns()
    restored = _runtime(card)
    restore_runtime(restored, runtime_state(rt))
    rows = []
    for branch in (rt, restored):
        branch._close_price_window()
        branch.ticks_consumed += 100
        branch._deliver_returns()
        kind = "router.decline_priced" if declines else "router.abstention_priced"
        credits = [row for row in branch.ledger._recovery_items()
                   if row.get("kind") == kind and row.get("handle") == handle]
        assert len(credits) == 1
        credit = credits[0]
        assert credit["penalty"] > 0
        assert credit["terms"][0]["held"] is True
        assert credit["terms"][0]["source_window"] == 0
        branch._deliver_returns()
        assert len([row for row in branch.ledger._recovery_items()
                    if row.get("kind") == kind and row.get("handle") == handle]) == 1
        rows.append(({key: value for key, value in credit.items()
                      if key not in ("hash", "prev_hash", "seq")},
                     _router(branch)[0].learner.state()))
    assert rows[0] == rows[1]


def test_every_abstention_a_world_draws_is_priced_on_the_roles_of_its_menu(
        scripted_runtime_run):
    manifest = load_manifest("scripted")
    record = scripted_runtime_run(manifest, 100, 1)  # the shared uninterrupted run
    rt, items = record.runtime(manifest), record.entries
    opened = {i["handle"]: i for i in items if i.get("kind") == "decision.open"
              and i["propensity"]["chosen"] == NOOP and i.get("parent_handle") is None}
    assert opened
    # The window still open holds each abstention drawn in it, weighted over the roles
    # of the seats its draw could have woken, and filed under the heaviest of them.
    held = {h: d for h, d in rt.window.decisions.items() if h in opened}
    assert held
    for handle, row in held.items():
        prop = opened[handle]["propensity"]
        seats = [(a, p) for a, p in zip(prop["action_ids"], prop["probs"], strict=True)
                 if a != NOOP and a in rt.assemblies]
        if not seats:
            # A draw that could wake nobody stood in for the seats it excluded, equally.
            router = next(st for st in [*rt._all_router_states(),
                                        *rt.retired_routers.values()]
                          if st.learner.id == opened[handle]["actor"])
            seats = [(a, 1.0) for a in router.universe if a != NOOP and a in rt.assemblies]
        total = sum(p for _a, p in seats)
        expected: dict[str, float] = {}
        for seat, p in seats:
            role = measured_role(rt.assemblies[seat].spec.emits)
            expected[role] = expected.get(role, 0.0) + p / total
        assert row["menu_roles"] == pytest.approx(expected)
        assert row["role"] == max(sorted(expected), key=expected.get)
    priced = [i for i in items if i.get("kind") == "router.abstention_priced"]
    assert priced and {row["handle"] for row in priced} <= set(opened)
    for row in priced:
        assert row["reward"] == pytest.approx(_learned(rt, row["neutral"], row["penalty"]))
