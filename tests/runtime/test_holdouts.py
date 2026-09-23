"""The evaluatory layer appends holdouts to cards through the amendment path (charter audit M3).

Chapter II §IV.a: the factory's consequential reward structure assesses when its
proxies are insufficiently descriptive of their norms "by permitting its evaluatory
layer to continuously add holdout test criteria to a given charter". A holdout is a
registered predicate a closed window must also satisfy; appending one is a motion
with a trial, like a metric challenge, proposed by an evaluator or antagonist seat.
"""

import pytest

from factorylab.charter.charter import MetricCard, holdout_violation
from factorylab.charter.controller import CardRegion, PriceController
from factorylab.cortex.request import Return
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord
from tests.conftest import make_runtime

HOLDS = "def resolve(facts):\n    return facts['ok'] >= facts['invocations']\n"


class InProcessPredicates:
    """The jail's contract for a predicate, run in-process for a test."""

    def run(self, code, facts):
        namespace: dict = {}
        exec(code, namespace)  # noqa: S102 - test code, the population's contract
        return bool(namespace["resolve"](facts)), None


def _handle(rt, seat):
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id=f"from-{seat}", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


def _runtime(monkeypatch, *, ok=1, invocations=1):
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.predicate_runner = InProcessPredicates()
    rt.predicates.register("all-well-formed", "every invocation was well formed", HOLDS,
                           facts={"ok": 1, "invocations": 1}, persist=lambda _p: None)
    rt.window.ok, rt.window.invocations = ok, invocations
    rt.card_samples.closed(rt.window)
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "seed-observer": "producer", "seed-decider": "producer", "eval-a": "evaluator",
        "eval-b": "evaluator", "antagonist-a": "antagonist", "meta-a": "meta"})
    return rt


def _motion(**holdout):
    return {"kind": "amendment", "id": "hold-well-formed",
            "holdout": {"card_id": "well_formed_rate", "predicate": "all-well-formed",
                        "evidence": "a seat's malformed returns hide inside the pooled rate",
                        "trial_windows": 1, **holdout},
            "predicted_effect": {"card_id": "well_formed_rate", "direction": "increase",
                                 "window": 1}}


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _register(rt, seat, item):
    handle = _handle(rt, seat)
    rt._apply_registrations(handle, Return(handle, {"register": [item]}, 0, "ok"))


def test_an_evaluator_appends_a_holdout_after_a_trial_and_a_committee_vote(monkeypatch):
    rt = _runtime(monkeypatch)
    _register(rt, "eval-a", _motion())
    proposed, = _items(rt, "holdout.proposed")
    assert proposed["holdout"] == "all-well-formed@1"
    assert proposed["replacement"]["holdout"] == ["all-well-formed@1"]
    # Nothing reaches the agenda until the trial has measured its windows.
    assert rt.charter_book.agenda() == []
    rt._close_challenge_window(rt.window.index)
    trial, = _items(rt, "challenge.window")
    assert trial["holdout"] == {"predicate": "all-well-formed@1", "held": True}
    rt._ballot_due_challenges()
    assert [am.id for am in rt.charter_book.agenda()] == ["hold-well-formed"]
    ballot = rt._challenge_ballot_inputs("hold-well-formed")
    assert ballot["holdout"] == "all-well-formed@1"
    assert ballot["series"][0]["holdout"]["held"] is True
    rt.clock.now_ns = rt.cadence.earliest_ns(rt.tick_clock)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    card = next(c for c in rt.charter.cards if c.id == "well_formed_rate")
    assert card.holdout == ("all-well-formed@1",)
    assert "holdout: all-well-formed@1" in rt.charter.render()


@pytest.mark.parametrize(("seat", "item", "reason"), [
    ("seed-decider", _motion(), "evaluator or antagonist"),
    ("eval-a", _motion(predicate="wallet_up"), "registered predicate"),
    ("eval-a", _motion(card_id="no-such-card"), "current card"),
    ("eval-a", {**_motion(), "remove": ["cost_per_return"]}, "holdout alone"),
    ("eval-a", _motion(trial_windows=0), "trial_windows"),
])
def test_a_holdout_motion_is_refused_with_its_reason(monkeypatch, seat, item, reason):
    rt = _runtime(monkeypatch)
    _register(rt, seat, item)
    rejected, = _items(rt, "registration.rejected")
    assert reason in rejected["reason"] and _items(rt, "holdout.proposed") == []


def test_a_cards_motion_cannot_smuggle_a_holdout_in(monkeypatch):
    rt = _runtime(monkeypatch)
    card = next(c for c in rt.charter.cards if c.id == "well_formed_rate")
    _register(rt, "eval-a", {
        "kind": "amendment", "id": "sneak", "replace": [{
            "id": card.id, "norm": card.norm, "description": card.description,
            "units": card.units, "window": {"kind": "returns", "n": 100, "per": "role"},
            "region": card.rule.as_dict(), "observation": card.observation,
            "answers_for": card.answers_for, "holdout": ["all-well-formed@1"]}],
        "predicted_effect": {"card_id": card.id, "direction": "increase", "window": 1}})
    rejected, = _items(rt, "registration.rejected")
    assert "holdout is appended by a holdout motion" in rejected["reason"]


def test_each_failed_holdout_adds_one_bounded_step_and_passing_ones_dilute_nothing():
    """Cold review: k/n let always-true holdouts dilute a real failure, and 1/1 was a
    whole region of violation. Each failure now counts on its own, as one step."""
    step = 0.05
    assert holdout_violation([False], step) == pytest.approx(0.05)
    assert holdout_violation([False, True, True, True], step) == pytest.approx(0.05)
    assert holdout_violation([False, False, None], step) == pytest.approx(0.10)
    assert holdout_violation([None, None], step) == 0.0  # absent evidence is never a failure
    ledger = Ledger(None)
    controller = PriceController(ledger, eta=0.5, decay=0.1, lambda_max=1.0,
                                 min_window_events=1, kp=0.5)
    controller.register(CardRegion("c", "min", 0.9, None, 1.0))
    controller.observe("c", 0.95, 1)
    assert controller.price("c") == 0.0
    controller.observe("c", 0.95, 2, holdout=step)
    # One failing holdout inside the region: priced, and nowhere near lambda_max.
    assert controller.price("c") == pytest.approx(0.5 * step + 0.5 * step)
    update = [i for i in ledger._recovery_items() if i["kind"] == "price.update"][-1]
    assert update["holdout"] == step and update["violation"] == step
    # Outside the region the holdout adds to the region's violation.
    controller.observe("c", 0.85, 3, holdout=step)
    update = [i for i in ledger._recovery_items() if i["kind"] == "price.update"][-1]
    assert update["violation"] == pytest.approx(0.05 / 1.0 + step)
    with pytest.raises(ValueError, match="holdout"):
        controller.observe("c", 0.95, 4, holdout=-0.1)


def test_a_live_card_with_a_failing_holdout_is_priced_one_step_at_the_close(monkeypatch):
    from dataclasses import replace

    from factorylab.charter.charter import Charter
    from factorylab.runtime import pricing

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(monkeypatch, ok=1, invocations=2)
    card = next(c for c in rt.charter.cards if c.id == "well_formed_rate")
    held = MetricCard(card.id, card.norm, card.description, card.units,
                      replace(card.window, n=1), card.region, card.observation,
                      card.answers_for, holdout=("all-well-formed@1",))
    rt.charter = Charter(rt.charter.edition, rt.charter.norms, (held,))
    rt.card_samples.windows.clear()
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.4, amendment_id="test")
    handle = _handle(rt, "seed-decider")
    rt.card_samples.returned(handle=handle, assembly="seed-decider", role="producer",
                             window=rt.window.index, ret=Return(handle, {}, 1, "ok"))
    rt._close_price_window()
    step = rt.m.committee.promise_resolution * 1.0 / rt.regions[card.id].scale
    window, = [i for i in _items(rt, "price.window") if "holdouts" in i]
    assert window["values"][card.id] == 1.0  # inside "at least 0.9"
    assert window["holdouts"][card.id]["results"] == {"all-well-formed@1": False}
    assert window["holdouts"][card.id]["violation"] == pytest.approx(step)
    update = [i for i in _items(rt, "price.update") if i["card_id"] == card.id][-1]
    assert update["violation"] == pytest.approx(step)
    assert 0.4 < update["lambda_after"] < rt.m.prices.lambda_max
    term, = rt._penalty_terms("all", handle)
    assert term["violation"] == pytest.approx(step)


@pytest.mark.parametrize(("code", "reason"), [
    ("def resolve(facts):\n    return facts['index'] < 40\n", "not a behavioural fact"),
    ("def resolve(facts):\n    return len(facts['tick_timestamps_ns']) < 9\n",
     "not a behavioural fact"),
    ("def resolve(facts):\n    return facts['mids']['BTC'][-1][1] > 0\n",
     "not a behavioural fact"),
    ("import time\ndef resolve(facts):\n    return time.time() < 2e9 and facts['ok'] > 0\n",
     "imports only"),
    ("def resolve(facts):\n    k = 'index'\n    return facts[k] < 40\n", "literal key"),
    ("def resolve(facts):\n    return any(v for v in facts)\n", "subscript or .get"),
])
def test_a_holdout_on_the_clock_or_the_world_is_refused(monkeypatch, code, reason):
    """Cold review: a predicate on the window index passes preflight and trial, then
    fails forever. A holdout reads behavioural facts only."""
    rt = _runtime(monkeypatch)
    rt.predicates.register("calendar", "reads the calendar", code,
                           facts={"ok": 1, "invocations": 1, "index": 1,
                                  "tick_timestamps_ns": [], "mids": {"BTC": [[1, 1]]}},
                           persist=lambda _p: None)
    _register(rt, "eval-a", _motion(predicate="calendar"))
    rejected, = _items(rt, "registration.rejected")
    assert reason in rejected["reason"] and _items(rt, "holdout.proposed") == []


def test_a_behavioural_holdout_names_what_it_reads():
    from factorylab.charter.holdout import behavioural_reads

    assert behavioural_reads(HOLDS) == {"ok", "invocations"}
    assert behavioural_reads("import math\ndef resolve(f):\n    return math.isfinite("
                             "f.get('tool_calls', 0))\n") == {"tool_calls"}
