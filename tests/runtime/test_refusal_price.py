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


def _learned(rt, r, p):
    """Ruling R10-g: every learner learns (r + cap - p) / (1 + cap), no clip."""
    cap = rt.m.prices.penalty_cap
    return (r + cap - p) / (1 + cap)


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
    _outside_the_niche(rt)
    return rt


def _outside_the_niche(rt):
    """A decision taken in the unhistoried niche bears no card penalty (wave 16, R-E as
    amended); these tests price the decisions outside it, so none is read as niche."""
    rt._is_niche = lambda *_a, **_k: False


def _drawn(rt, chosen, channel="verdict"):
    """One Tick-router decision that drew ``chosen``, at the router's own odds."""
    state = rt.routers["Tick"][0]
    feasible = lambda a: (a == chosen, "")  # noqa: E731 - only this arm may be woken
    sample = next(s for s in (state.router.route("Tick", feasible, random.Random(i))
                              for i in range(200)) if s.chosen == chosen)
    handle = rt.queue.open(actor=state.learner.id, event_id=f"tick-{chosen}",
                           propensity=rt._propensity(sample), channel=channel,
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
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    # Priced: never credited above a NOOP drawn in the same window, and below the
    # unpriced neutral a censored refusal used to be credited.
    noop_reward, _noop_penalty = rt._priced_abstention(noop, state.neutral())
    assert priced["reward"] <= noop_reward
    assert priced["reward"] < _learned(rt, state.neutral(), 0.0)


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
    # Its card's share is a count of the window's decisions: it settles at the close
    # (wave 16, D5, ruling R-I).
    assert not rt.queue.history(refused) and refused in rt.deferred_settlements
    rt._close_price_window()
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


def test_a_requested_childs_refusal_is_priced_as_an_abstention_on_its_request_router(
        monkeypatch):
    """A producer drawn by ``request:ProducerReturn`` that answers ``cannot`` is not
    consumed ok, so no requester holds it: unjudged, it settles declined, never free."""
    from factorylab.cortex.request import ChildRequest
    from factorylab.runtime.shared import request_router_key
    from tests.runtime.test_child_requests import parent_request

    rt = _priced_runtime(monkeypatch)
    rt._instantiate(replace(rt.assemblies["seed-decider"].spec, id="helper-a"))
    req = parent_request(rt)
    rt.handle_to_assembly[req.handle] = "seed-decider"
    task = ChildRequest("ProducerReturn", "helper task", {"q": 1}, {"type": "object"})
    rt._invoke_child("seed-decider", req, task, req.cost_ceiling)
    (child,) = _rows(rt, "request.child")
    handle = child["handle"]
    state = rt.routers[request_router_key("ProducerReturn")][0]
    assert rt.queue.get(handle).actor == state.learner.id
    (invocation,) = _rows(rt, "invocation", handle=handle)
    assert invocation["status"] == "refused"
    assert rt.pending[handle].declined == REASON and rt.pending[handle].requester is None
    updates = []
    rt.assembly_learners[child["target"]] = SimpleNamespace(
        update_for=lambda h, fb: updates.append((h, fb)), discard_for=lambda h: None)
    rt.assembly_rounds[handle] = child["target"]
    _noop_state, noop = _drawn(rt, NOOP)
    rt._contribution(noop, "producer")
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    _past_the_verdict_timeout(rt)
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["router"] == state.learner.id and priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    noop_reward, _noop_penalty = rt._priced_abstention(noop, state.neutral())
    assert priced["reward"] <= noop_reward
    ((learned, fb),) = updates
    expected, penalty = rt._priced_abstention(handle, NEUTRAL_REWARD)
    assert learned == handle and penalty > 0 and fb.reward == pytest.approx(expected)


def _forecast_desk(rt):
    from tests.audit.test_r3_j_runtime import register_work

    register_work(rt)  # a custom kind with reward shape "forecast", accepting Tick
    return "weather-desk"


def _mixed_contract(rt):
    from factorylab.cortex.registration import AssemblyProposal

    rt._register("author", AssemblyProposal(
        "dual", "producer", "fake-haiku", "Reply with JSON.", ("Tick",), 128, "low",
        ("ProducerReturn", "Verdict"), {}))
    return "dual"


@pytest.mark.parametrize(("seat", "channel"), [
    ("antagonist-a", "exposure"),      # Exposure: settles once no judge was scored on it
    ("forecast", "consequence"),       # a forecast-shaped custom kind: nothing to score
    ("mixed", "verdict"),              # several emits kinds, none bound by a refusal
])
def test_an_ungraded_refusal_on_any_producing_channel_settles_declined_at_the_price(
        monkeypatch, seat, channel):
    """Every producing contract's refusal, through the real ``_invoke``: none settles
    censored at a free neutral; each is priced on its router as an abstention."""
    rt = _priced_runtime(monkeypatch)
    seat = {"forecast": _forecast_desk, "mixed": _mixed_contract}.get(
        seat, lambda _rt: seat)(rt)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()  # a violation measured, and priced, before the refusal
    state, handle = _drawn(rt, seat, channel)
    rt.n += 1
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=seat), rt.queue.get(handle).deadline_ns)
    (invocation,) = _rows(rt, "invocation", handle=handle)
    assert invocation["status"] == "refused"
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._settle_exposures()
    rt._censor_stale_judgements()
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    _noop_state, noop = _drawn(rt, NOOP, channel)
    rt._contribution(noop, rt.window.decisions[handle]["role"])
    noop_reward, _noop_penalty = rt._priced_abstention(noop, state.neutral())
    assert priced["reward"] <= noop_reward


@pytest.mark.parametrize(("seat", "channel"), [("seed-decider", "verdict"),
                                             ("antagonist-a", "exposure")])
def test_a_refusal_cut_off_before_its_settlement_check_still_settles_declined(
        monkeypatch, seat, channel):
    """The cutoff is the other door: a refusal the queue cuts off before any settlement
    check reads it settles declined there, never timed out at the unpriced neutral."""
    rt = _priced_runtime(monkeypatch)
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    state, handle = _drawn(rt, seat, channel)
    rt.n += 1
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen=seat), rt.queue.get(handle).deadline_ns)
    assert _rows(rt, "invocation", handle=handle)[0]["status"] == "refused"
    # Force the cutoff one tick out, well before the verdict timeout any settlement
    # check waits for, and reach it.
    rt.decision_ticks[handle] = [rt.ticks_consumed, rt.ticks_consumed + 1]
    rt.ticks_consumed += 1
    assert rt.ticks_consumed <= rt.ev.verdict_timeout_ticks
    rt._settle_exposures()
    rt._censor_stale_judgements()
    assert rt.queue.get(handle).status is SettleStatus.PENDING  # no check has read it
    assert handle not in rt.queue.expire_due()
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    # The settlement checks that come later find it closed and change nothing.
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._settle_exposures()
    rt._censor_stale_judgements()
    rt._deliver_returns()
    assert len(rt.queue.history(handle)) == 1
    assert len(_rows(rt, "router.decline_priced", handle=handle)) == 1


class SelectThenDecline(ScriptedProvider):
    """A seat that selects Exposure and reads in its first round, then declines."""

    def _produce(self, desc, inputs):
        if "tool_results" in inputs:
            return {"status": "cannot", "reason": REASON}
        return {"emits": "Exposure", "action": "hold", "tool_calls": [
            {"tool": "catalogue.search", "args": {"substring": "fake", "limit": 1}}]}


def test_a_polymorphic_decline_settles_on_the_kind_it_selected_at_its_cutoff(monkeypatch):
    """A multi-kind decision that selected Exposure in a tool round and then declined
    waits on the exposure channel; at its cutoff it settles declined there. The kernel's
    own routing channel for it is ``emits``, and a settlement naming that is refused."""
    from factorylab.cortex.registration import AssemblyProposal

    rt = _priced_runtime(monkeypatch)
    rt.provider.target.__class__ = SelectThenDecline
    rt._register("author", AssemblyProposal(
        "dual-x", "producer", "fake-haiku", "Reply with JSON.", ("Tick",), 128, "low",
        ("ProducerReturn", "Exposure"), {}))
    assert "dual-x" in rt.assemblies
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    state = rt.routers["Tick"][0]
    feasible = lambda a: (a == "dual-x", "")  # noqa: E731 - only this arm may be woken
    sample = next(s for s in (state.router.route("Tick", feasible, random.Random(i))
                              for i in range(200)) if s.chosen == "dual-x")
    handle = rt.queue.open(actor=state.learner.id, event_id="tick-dual",
                           propensity=rt._propensity(sample), channel="verdict",
                           return_channels=rt._return_channels("dual-x"),
                           deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=rt.wallet.available)
    assert rt.queue.queue.get(handle).channel == "emits"
    rt.n += 1
    rt._producer_step(
        Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0}, "test"),
        handle, SimpleNamespace(chosen="dual-x"), rt.queue.get(handle).deadline_ns)
    assert [r["status"] for r in _rows(rt, "invocation", handle=handle)] == ["refused"]
    assert rt.return_kinds[handle] == "Exposure"
    assert rt.queue.get(handle).channel == "exposure"
    rt.decision_ticks[handle] = [rt.ticks_consumed, rt.ticks_consumed + 1]
    rt.ticks_consumed += 1
    assert handle not in rt.queue.expire_due()
    (settled,) = rt.queue.history(handle)
    assert settled.status is SettleStatus.INAPPLICABLE
    assert settled.definition_version == DECLINED_DEFINITION
    (declined,) = _rows(rt, "evaluation.declined", handle=handle)
    assert declined["channel"] == "exposure"
    rt._deliver_returns()
    (priced,) = _rows(rt, "router.decline_priced", handle=handle)
    assert priced["penalty"] > 0
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))


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
    _outside_the_niche(rt)
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
    assert priced["reward"] == pytest.approx(_learned(rt, state.neutral(), priced["penalty"]))
    assert priced["reward"] < _learned(rt, state.neutral(), 0.0)
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
def test_in_a_world_every_unjudged_refusal_and_decline_settles_at_the_abstention_price():
    """Pricing physics only: what each refusal and decline settles as, and at what credit.
    No assertion here says which action a seat should prefer, or where routing shares
    should move (AGENTS.md rule 2); the harness only reads the ledger."""
    rt = Runtime(_standoff_manifest(), events=300, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, provider=Standoff())
    rt.run()
    tick = {s.learner.id for s in rt._all_router_states() if s.kind == "Tick"}
    items = rt.ledger._recovery_items()
    drawn = {i["handle"]: i["propensity"]["chosen"] for i in items
             if i.get("kind") == "decision.open" and i.get("actor") in tick}
    # Every return each decision received, from the diary: a decision fully settled is
    # released from the queue (wave 17b), and the diary is the record.
    outcomes = Counter((drawn[i["return"]["handle"]], SettleStatus(i["return"]["status"]),
                        i["return"]["definition_version"]) for i in items
                       if i.get("kind") in ("decision.settle", "decision.timeout")
                       and i["return"]["handle"] in drawn)
    # No refusal escapes into a censored, unpriced settlement.
    assert not any(seat == REFUSER and status is SettleStatus.CENSORED
                   for seat, status, _d in outcomes)
    assert outcomes[(REFUSER, SettleStatus.INAPPLICABLE, DECLINED_DEFINITION)] > 0
    priced = _rows(rt, "router.decline_priced")
    assert priced
    for row in priced:
        assert row["reward"] == pytest.approx(_learned(rt, row["neutral"], row["penalty"]))
    # A seat that only refuses, once past its trial, bears its penalty share (ruling
    # R10-b: its priced declines are a reward trail, and a seed's trial ends at its
    # patience); inside the niche nothing is charged.
    niche = {row["handle"] for row in _rows(rt, "price.contribution") if row.get("niche")}
    refused = [row for row in priced if drawn.get(row["handle"]) == REFUSER]
    assert any(row["penalty"] > 0 for row in refused if row["handle"] not in niche)
    assert all(row["penalty"] == 0 for row in priced if row["handle"] in niche)
    late = [h for h in drawn if drawn[h] == REFUSER
            and rt.queue.opened_tick(h) is not None
            and rt.queue.opened_tick(h) >= rt._patience()]
    assert late and not set(late) & niche  # past the trial, never in the niche
    # The judges who declined to grade a refusal ("no return to judge") declined too,
    # in the form ``_invoke`` hands on: none is censored at a free neutral, each is
    # priced on its router as an abstention, and some of those prices bite.
    judge_routers = {s.learner.id for s in rt._all_router_states()
                     if s.kind == "ProducerReturn"}
    assert not _rows(rt, "evaluation.censored")
    declined = {row["handle"] for row in _rows(rt, "evaluation.declined",
                                                channel=CH_CONFORMITY)}
    judged = [row for row in priced if row["router"] in judge_routers]
    # Every decline is priced, except one still owed at the run's end: a decline
    # outside the niche is priced on its window's close (D5), which the run may end
    # before.
    owed = {h for h in declined if h in rt.noop_credits}
    assert declined and {row["handle"] for row in judged} == declined - owed
    assert all(not rt._is_niche(h) for h in owed)
    assert all(row["reward"] <= _learned(rt, row["neutral"], 0.0) for row in judged)
    assert all(row["penalty"] == 0 for row in judged if row["handle"] in niche)


def test_a_seat_that_declines_every_round_leaves_the_niche_when_its_trial_ends(monkeypatch):
    """Ruling R10-b, the violation attempt: declining is a reward trail, and a seed's
    trial ends at its patience from the world's first tick, so a seat that declines
    every round is protected inside the trial and charged after it."""
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    card = _card(per=None)
    seed = load_manifest("scripted")
    rt = Runtime(replace(seed, charter=replace(seed.charter, cards=(card,))), events=0,
                 seed=1, initial_balance_micro=None, ledger_path=None, router_gamma=0.1,
                 provider=Refuser())
    rt._manage_reserve_window()
    rt._derive_regions()
    rt.controller.set_price(card.id, 0.8, amendment_id="test")
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()  # a violation measured, and priced

    def decline_penalty():
        _state, handle = _refuse(rt)
        _commitments(rt, "eval-a", censored=4)
        rt._close_price_window()
        _past_the_verdict_timeout(rt)
        rt._deliver_returns()
        return handle, rt._is_niche(handle), rt._priced_abstention(handle, 0.5)[1]

    inside = decline_penalty()
    assert inside[1] and inside[2] == 0.0  # the trial: protected, charged nothing
    assert rt.queue.has_history("seed-decider")  # its priced decline is a reward trail
    rt.ticks_consumed = max(rt.ticks_consumed, rt._patience())
    assert not rt._unhistoried("seed-decider")
    after = decline_penalty()
    assert not after[1] and after[2] > 0  # out of the niche: it bears its share
