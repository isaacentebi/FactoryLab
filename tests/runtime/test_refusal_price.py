"""A producer's refusal is priced as the abstention it is (ruling R9; essay II.I.a, II.III.b).

A seat may answer ``{"status": "cannot", "reason": ...}`` to the event it was woken
for, and it still may: the kernel never chooses a seat's action for it. The refusal
is published and any judge may grade it like any other return. What changed is the
refusal nobody grades. It used to settle censored, and a censored decision is
credited the router's zero-consequence reward unpriced, while a NOOP draw bears its
role's charter price and a judged hold bears its card penalty: declining was the
one way out of paying for abstaining. It now settles as a declined commission,
which its router and its own learner credit as an abstention, less the price.
"""

from __future__ import annotations

import json
import random
import tomllib
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.learners.base import NEUTRAL_REWARD
from factorylab.runtime import pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_CONFORMITY, CH_COUNTER, CH_FAST, NOOP
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.settlement.vocabulary import DECLINED_DEFINITION
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import (
    ScriptedProvider,
    _description_from_prompt,
    _inputs_from_prompt,
)
from tests.runtime.test_attributable_blame import _card, _commitments

REASON = "no concrete task was given"


class Refuser(ScriptedProvider):
    """Every producer declines the event it was woken for."""

    def _produce(self, desc, inputs):
        return {"status": "cannot", "reason": REASON}


def _rows(rt, kind, **match):
    return [i for i in rt.ledger._recovery_items() if i.get("kind") == kind
            and all(i.get(k) == v for k, v in match.items())]


def _priced_runtime(monkeypatch):
    """A scripted world with one violated card at a positive price, and refusing producers."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    card = _card(per=None)
    seed = load_manifest("scripted")
    manifest = replace(seed, charter=replace(seed.charter, cards=(card,)))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, provider=Refuser())
    rt._manage_reserve_window()
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    return rt


def _drawn(rt, chosen):
    """One Tick-router decision that drew ``chosen``, at the router's own odds."""
    state = rt.routers["Tick"][0]
    feasible = lambda a: (a == chosen, "")  # noqa: E731 - only this arm may be woken
    sample = next(s for s in (state.router.route("Tick", feasible, random.Random(i))
                              for i in range(200)) if s.chosen == chosen)
    handle = rt.queue.open(actor=state.learner.id, event_id=f"tick-{chosen}",
                           propensity=rt._propensity(sample), channel="verdict",
                           deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=rt.wallet.available)
    return state, handle


def _refuse(rt):
    state, handle = _drawn(rt, "seed-decider")
    rt.n += 1
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="seed-decider"), rt.queue.get(handle).deadline_ns)
    return state, handle


def _past_the_verdict_timeout(rt):
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._censor_stale_judgements()


def test_a_refusal_is_recorded_published_and_left_to_the_judges(monkeypatch):
    rt = _priced_runtime(monkeypatch)
    _state, handle = _refuse(rt)
    (invocation,) = _rows(rt, "invocation", handle=handle)
    assert invocation["status"] == "refused" and invocation["cost"] > 0
    assert rt.queue.declared_propensity(handle).chosen == "declined"
    assert rt.pending[handle].declined == REASON
    # Published like any return: judges may grade it (II.III.b).
    (event,) = [e for e in rt.internal if str(e.kind) == str(EventKind.PRODUCER_RETURN)
                and e.payload["about_handle"] == handle]
    assert event.payload["status"] == "refused"
    assert event.payload["outputs"] == {"status": "cannot", "reason": REASON}


def test_a_refusal_no_judge_graded_is_priced_as_an_abstention_never_credited_free(
        monkeypatch):
    """The escape: refuse, let no judge grade it, and collect the neutral unpriced."""
    rt = _priced_runtime(monkeypatch)
    state, refused = _refuse(rt)
    _noop_state, noop = _drawn(rt, NOOP)
    rt._contribution(noop, "producer")
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    _past_the_verdict_timeout(rt)
    (settled,) = rt.queue.history(refused)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    assert not _rows(rt, "evaluation.censored", handle=refused)
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=refused)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(state.neutral() - priced["penalty"])
    # It no longer pays: the refusal earns no more than a NOOP drawn in the same window,
    # and strictly less than the unpriced neutral a censored refusal used to earn.
    noop_reward, _noop_penalty = rt._priced_abstention(noop, state.neutral())
    assert priced["reward"] <= noop_reward
    assert priced["reward"] < state.neutral()


def test_a_refusal_a_judge_graded_settles_on_its_verdict_like_any_return(monkeypatch):
    rt = _priced_runtime(monkeypatch)
    _state, refused = _refuse(rt)
    (event,) = [e for e in rt.internal if str(e.kind) == str(EventKind.PRODUCER_RETURN)
                and e.payload["about_handle"] == refused]
    judge = rt.queue.open(actor="test-router", event_id="judge", channel=CH_CONFORMITY,
                          propensity=PropensityRecord(("eval-a",), (1.0,), "eval-a", 0,
                                                      "test-router", "state"),
                          deadline_ns=10**18, parent_handle=None,
                          cost_ceiling=rt.wallet.available)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"), 10**18,
                       returned=Return(judge, {"verdict": 0.2, "rationale": "r"}, 0, "ok"))
    rt._settle_arrived_verdicts()
    _past_the_verdict_timeout(rt)
    (settled,) = rt.queue.history(refused)
    assert settled.status is SettleStatus.SETTLED and settled.definition_version == "verdict-v1"
    (row,) = _rows(rt, "price.penalty", handle=refused)
    assert row["raw"] == pytest.approx(0.2)


def test_the_refusing_seats_own_learner_is_priced_as_its_router_is(monkeypatch):
    rt = _priced_runtime(monkeypatch)
    _state, refused = _refuse(rt)
    updates = []
    rt.assembly_learners["seed-decider"] = SimpleNamespace(
        update_for=lambda handle, fb: updates.append((handle, fb)),
        discard_for=lambda handle: None)
    rt.assembly_rounds[refused] = "seed-decider"
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    _past_the_verdict_timeout(rt)
    rt._close_assembly_rounds()
    ((handle, fb),) = updates
    expected, penalty = rt._priced_abstention(refused, NEUTRAL_REWARD)
    assert handle == refused and fb.action == "declined"
    assert penalty > 0 and fb.reward == pytest.approx(expected)
    assert fb.reward < NEUTRAL_REWARD


# --- a judge, a meta and a counter-judge decline their commissions ---------------------


class Decliner(ScriptedProvider):
    """Producers answer as scripted; every judging seat declines, in the real form."""

    def complete(self, req):
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        if _description_from_prompt(text).startswith(("Give verdict", "Assess",
                                                      "Give your own verdict")):
            return ModelResponse(req.model_id, json.dumps({"status": "cannot",
                                                           "reason": "no view"}),
                                 self.input_tokens, self.output_tokens, "end_turn")
        return super().complete(req)


def _declining_runtime(monkeypatch):
    """The priced world above, with an adversarial seat, where every judge declines."""
    from factorylab.runtime.worlds import AssemblySeed

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    card = _card(per=None)
    seed = load_manifest("scripted")
    adversary = AssemblySeed(id="adv-a", model_id="fake-sonnet", accepts=("Verdict",),
                             role="adversary", max_tokens=128)
    manifest = replace(seed, assemblies=(*seed.assemblies, adversary),
                       charter=replace(seed.charter, cards=(card,)))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, provider=Decliner())
    rt._manage_reserve_window()
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    return rt


def _draw(rt, seat, channel):
    """One decision the router holding ``seat`` drew for it (or for NOOP), at its odds."""
    state = next(s for s in rt._all_router_states() if seat in s.universe
                 and not s.kind.startswith("request:"))
    feasible = lambda a: (a == seat, "")  # noqa: E731 - only this arm may be woken
    sample = next(s for s in (state.router.route(state.kind, feasible, random.Random(i))
                              for i in range(500)) if s.chosen == seat)
    handle = rt.queue.open(actor=state.learner.id, event_id=f"draw-{seat}-{rt.n}",
                           propensity=rt._propensity(sample), channel=channel,
                           deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=rt.wallet.available)
    return state, handle


def _produced(rt):
    rt.n += 1
    _state, handle = _drawn(rt, "seed-decider")
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="seed-decider"), rt.queue.get(handle).deadline_ns)
    return next(e for e in rt.internal if str(e.kind) == str(EventKind.PRODUCER_RETURN)
                and e.payload["about_handle"] == handle)


def _verdict(rt):
    """A first-tier verdict event, from a judge whose answer is handed in."""
    event = _produced(rt)
    judge = rt.queue.open(actor="test-router", event_id="judge", channel=CH_CONFORMITY,
                          propensity=PropensityRecord(("eval-c",), (1.0,), "eval-c", 0,
                                                      "test-router", "state"),
                          deadline_ns=10**18, parent_handle=None,
                          cost_ceiling=rt.wallet.available)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-c"), 10**18,
                       returned=Return(judge, {"verdict": 0.6, "rationale": "r"}, 0, "ok"))
    return rt.return_events[judge]


def _decline(rt, step, seat, channel):
    if step == "evaluator":
        event, run = _produced(rt), rt._evaluator_step
    elif step == "meta":
        event, run = _verdict(rt), rt._meta_step
    else:
        event, run = _verdict(rt), rt._counter_step
    state, handle = _draw(rt, seat, channel)
    run(event, handle, SimpleNamespace(chosen=seat), rt.queue.get(handle).deadline_ns)
    return state, handle


@pytest.mark.parametrize(("step", "seat", "channel"), [
    ("evaluator", "eval-a", CH_CONFORMITY),
    ("meta", "meta-a", CH_FAST),
    ("counter", "adv-a", CH_COUNTER),
])
def test_a_judging_seat_that_declines_is_priced_as_an_abstention_never_censored_free(
        monkeypatch, step, seat, channel):
    """The real decline, as ``_invoke`` hands it on (status ``refused``), is a decline:
    the published schematic says its router is credited as for an abstention."""
    rt = _declining_runtime(monkeypatch)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()  # a violation measured, and priced, before the decline
    state, handle = _decline(rt, step, seat, channel)
    (invocation,) = _rows(rt, "invocation", handle=handle)
    assert invocation["status"] == "refused" and invocation["cost"] > 0
    (declined,) = _rows(rt, "evaluation.declined", handle=handle)
    assert declined["channel"] == channel and declined["reason"] == "no view"
    assert not _rows(rt, "evaluation.censored", handle=handle)
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    # Learned in the window it was declined in, as the loop learns it.
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(state.neutral() - priced["penalty"])
    assert priced["reward"] < state.neutral()
    # Never above a NOOP the same router drew in the same window.
    _noop_state, noop = _draw(rt, NOOP, channel)
    rt._contribution(noop, rt.window.decisions[handle]["role"])
    noop_reward, _noop_penalty = rt._priced_abstention(noop, state.neutral())
    assert priced["reward"] <= noop_reward


def test_a_model_refusal_with_no_decline_is_still_censored(monkeypatch):
    """A provider's own refusal says nothing a seat chose: it stays a form failure."""
    rt = _declining_runtime(monkeypatch)
    event = _produced(rt)
    _state, handle = _draw(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"), 10**18,
                       returned=Return(handle, {"reason": "refused"}, 5, "refused"))
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.CENSORED
    assert not _rows(rt, "evaluation.declined", handle=handle)


# --- the harness: one seat always refuses, one always holds -----------------------------


REFUSER, HOLDER = "seat-refuse", "seat-hold"


class Standoff(ScriptedProvider):
    """One producer always refuses, one always holds; judges grade holds 0.5 and decline
    to grade a refusal, as live judges did ("no return to judge")."""

    def complete(self, req):
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _inputs_from_prompt(text)
        desc = _description_from_prompt(text)
        if desc.startswith("Respond to event"):
            reply = ({"status": "cannot", "reason": REASON} if inputs.get("you") == REFUSER
                     else {"action": "hold"})
        elif desc.startswith("Give verdict"):
            reply = ({"verdict": 0.5, "rationale": "r"}
                     if inputs.get("producer", {}).get("status") == "ok"
                     else {"status": "cannot", "reason": "no return to judge"})
        else:
            return super().complete(req)
        return ModelResponse(req.model_id, json.dumps(reply), self.input_tokens,
                             self.output_tokens, "end_turn")


def _standoff_manifest():
    raw = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    raw["models"] += [{"id": f"fake-{seat}", "provider": "fake", "input_usd_per_mtok": "1",
                       "output_usd_per_mtok": "5"} for seat in ("refuse", "hold")]
    raw["assemblies"] = [
        {"id": seat, "role": "producer", "model_id": f"fake-{seat.removeprefix('seat-')}",
         "accepts": ["Tick"], "emits": ["ProducerReturn"], "max_tokens": 256}
        for seat in (REFUSER, HOLDER)
    ] + [a for a in raw["assemblies"] if a["role"] in ("evaluator", "meta")]
    return manifest_from_dict(raw)


@pytest.mark.gate
def test_in_a_world_a_seat_that_always_refuses_earns_less_than_one_that_holds_or_a_noop():
    rounds = []

    class Recording(Runtime):
        def _thrash_charged(self, state, handle, reward):
            if state.kind == "Tick":
                rounds.append((self.queue.get(handle).propensity.chosen, reward))
            return super()._thrash_charged(state, handle, reward)

    rt = Recording(_standoff_manifest(), events=300, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1, provider=Standoff())
    rt.run()
    tick = {s.learner.id for s in rt._all_router_states() if s.kind == "Tick"}
    drawn = {i["handle"]: i["propensity"]["chosen"] for i in rt.ledger._recovery_items()
             if i.get("kind") == "decision.open" and i.get("actor") in tick}
    outcomes = Counter((drawn[h], lr.status, lr.definition_version)
                       for h in drawn for lr in rt.queue.history(h))
    # No refusal escapes into a censored, unpriced settlement.
    assert not any(seat == REFUSER and status is SettleStatus.CENSORED
                   for seat, status, _d in outcomes)
    assert outcomes[(REFUSER, SettleStatus.INAPPLICABLE, DECLINED_DEFINITION)] > 0
    priced = _rows(rt, "router.decline_priced")
    assert priced and any(row["penalty"] > 0 for row in priced)
    for row in priced:
        assert row["reward"] == pytest.approx(max(0.0, row["neutral"] - row["penalty"]))
    rewards: dict[str, list[float]] = {}
    for seat, reward in rounds:
        rewards.setdefault(seat, []).append(reward)
    mean = {seat: sum(r) / len(r) for seat, r in rewards.items()}
    assert mean[REFUSER] < mean[NOOP] and mean[REFUSER] < mean[HOLDER]
    # The judges who declined to grade a refusal ("no return to judge") declined too,
    # in the form ``_invoke`` hands on: none is censored at a free neutral, each is
    # priced on its router as an abstention, and some of those prices bite.
    judge_routers = {s.learner.id for s in rt._all_router_states()
                     if s.kind == "ProducerReturn"}
    assert not _rows(rt, "evaluation.censored")
    declined = {row["handle"] for row in _rows(rt, "evaluation.declined",
                                                channel=CH_CONFORMITY)}
    judged = [row for row in priced if row["router"] in judge_routers]
    assert declined and {row["handle"] for row in judged} == declined
    assert any(row["penalty"] > 0 for row in judged)
    assert all(row["reward"] <= row["neutral"] for row in judged)
