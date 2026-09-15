"""A14: revisions are real and cascades keep attribution (seat 2, finding 4; seat 6, 8b)."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.charter import Charter
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.observations import observation_for
from factorylab.runtime.shared import CH_FAST
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_fidelity import runtime
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


class Producer(ScriptedProvider):
    """Twenty well-formed holds; the seventh carries a proposal that is refused."""

    def __init__(self, register_at=7, proposal=None):
        super().__init__()
        self.calls, self.register_at = 0, register_at
        self.proposal = proposal or {"kind": "model", "openrouter_id": "nobody/no-such-model"}

    def _produce(self, desc, inputs):
        self.calls += 1
        reply = {"action": "hold"}
        if self.calls == self.register_at:
            reply["register"] = [self.proposal]
        return reply


def test_one_rejected_registration_per_twenty_returns_leaves_revision_at_zero():
    rt = _consequence_runtime(provider=Producer())
    rt._manage_reserve_window()
    for _ in range(20):
        _consequence_produce(rt, "seed-decider")
    assert rt.window.producer_returns == 20 and rt.stats.registrations_rejected == 1
    assert rt.window.revision_returns == 0
    assert observation_for("revision_rate").measure(rt.window) == 0.0


def test_an_accepted_registration_and_an_activated_amendment_are_revisions():
    good = {"kind": "assembly", "id": "new-part", "model_id": "fake-haiku", "role": "producer",
            "accepts": ["Tick"], "system_prompt": "Answer the request."}
    rt = _consequence_runtime(provider=Producer(register_at=1, proposal=good))
    rt._manage_reserve_window()
    _consequence_produce(rt, "seed-decider")
    assert "new-part" in rt.assemblies
    assert rt.window.revision_returns == 1 and rt.stats.registrations_accepted == 1
    charter = rt.charter
    edition = Charter(charter.edition + 1, charter.norms, charter.cards)
    activations = iter([edition, None])
    rt._next_charter_activation = lambda: next(activations)
    rt.charter_book.activated_amendment = lambda _e: SimpleNamespace(
        id="am", proposed_prices=(), tick_interval=None, proposer_handle="seed-decider",
    )
    rt._activate_charter_if_due()
    assert rt.charter.edition == charter.edition + 1
    assert rt.window.revision_returns == 2 and rt.window.amendments_activated == 1


def test_cascade_siblings_settle_at_the_sibling_share_with_ledger_evidence():
    from tests.runtime.test_loop import _pending_meta, _recursive_runtime

    rt = _recursive_runtime(events=0)
    rt.m = replace(rt.m, timing=replace(rt.m.timing, jitter_fraction=0))
    handles = [_pending_meta(rt) for _ in range(3)]
    events = [Event(f"meta-{i}", EventKind.META_VERDICT, 0,
                    {"by": h, "about": "lower", "tier": 2, "score": 0.5}, "runtime")
              for i, h in enumerate(handles)]
    assert all(rt._cascade_arrival(e) is None for e in events[:2])
    assert rt._cascade_arrival(events[2]) is not None
    rt._deliver_meta_verdict(Event("top", EventKind.META_VERDICT, 0, {
        "by": "judge-3", "about": handles[2], "tier": 3, "score": 0.8}, "runtime"))
    assert rt.queue.history(handles[2])[0].score == 0.8
    assert [rt.queue.history(h)[0].score for h in handles[:2]] == [0.4, 0.4]
    siblings = [i for i in rt.ledger._recovery_items() if i["kind"] == "cascade.sibling"]
    assert [(i["handle"], i["representative"], i["share"]) for i in siblings] == [
        (handles[0], handles[2], 0.5), (handles[1], handles[2], 0.5),
    ]


class Judge(ScriptedProvider):
    def __init__(self, payoff):
        super().__init__()
        self.payoff = payoff

    def _evaluate(self, req, inputs):
        return {"verdict": 0.5, "payoff": self.payoff, "rationale": "j", "forecasts": []}

    def _meta(self, inputs):
        return {"conformity": 0.8, "rationale": "m"}


def _meta_step(rt, verdict_event):
    handle = _consequence_decision(rt, "meta-a", CH_FAST)
    rt.n += 1
    rt._meta_step(verdict_event, handle, SimpleNamespace(chosen="meta-a"),
                  rt.queue.get(handle).deadline_ns)
    return handle


def test_top_meta_is_graded_by_brier_against_the_verdicts_consequence():
    # Known outcome: the judge blessed a no-fill return (payoff 0.9 against y = 0) and
    # scored below the baseline, so a conformity of 0.8 was wrong: 1 - 0.8^2 = 0.36.
    rt = _consequence_runtime(provider=Judge(0.9))
    _, event = _consequence_produce(rt, "NOOP")
    _consequence_judge(rt, event, "eval-a")
    verdict = next(e for e in rt.internal if e.kind is EventKind.VERDICT)
    meta = _meta_step(rt, verdict)
    outcome = rt.queue.history(meta)[0]
    assert outcome.status is SettleStatus.SETTLED and outcome.score == pytest.approx(0.36)
    assert outcome.definition_version == "meta-consequence-v1"
    item = next(i for i in rt.ledger._recovery_items() if i["kind"] == "meta.consequence")
    assert item["y"] == 0 and item["conformity"] == 0.8 and item["handle"] == meta
    assert rt.stats.fast_settlements == 1
    # Pending outcome: the meta waits for the judge's payoff forecast, then settles on it
    # once the hold's window has also judged the verdict (T16: right on both counts).
    rt = _consequence_runtime(provider=Judge(0.0))
    rt._manage_reserve_window()  # window 1 opens; the hold below is made in it
    _, event = _consequence_produce(rt, "seed-decider")  # a hold
    about = event.payload["about_handle"]
    # Reopen the hold's account so its payoff is still pending when the meta judges.
    rt.consequences.table = rt.consequences.table._accounts({
        about: replace(rt.consequences.table.account(about), payoff=None, cost_micro=None),
    })
    _consequence_judge(rt, event, "eval-a")
    verdict = next(e for e in rt.internal if e.kind is EventKind.VERDICT)
    meta = _meta_step(rt, verdict)
    assert rt.queue.get(meta).status is SettleStatus.PENDING
    assert any(i["kind"] == "meta.awaiting_consequence" for i in rt.ledger._recovery_items())
    rt.consequences.finish(about, 0)
    rt._settle_due_forecasts()
    assert rt.queue.get(meta).status is SettleStatus.PENDING  # the payoff fact alone: right
    rt.clock.now_ns += rt.m.novelty.window_ns
    rt._manage_reserve_window()  # the hold's window closes and does not blame it
    rt._settle_due_forecasts()
    outcome = rt.queue.history(meta)[0]
    assert outcome.status is SettleStatus.SETTLED
    assert outcome.score == pytest.approx(0.96)  # y = 1
    assert rt.pending_meta == {}


def test_top_meta_decisions_live_as_long_as_the_backstop():
    rt = _consequence_runtime(provider=Judge(0.5))
    _, event = _consequence_produce(rt, "NOOP")
    _consequence_judge(rt, event, "eval-a")
    verdict = next(e for e in rt.internal if e.kind is EventKind.VERDICT)
    state = rt.routers["Verdict"][0]
    rt.n += 1
    rt._route_with(state, verdict)
    opened = [d for d in rt.queue.state()["decisions"].values() if d.event_id == verdict.id]
    horizon = (rt.ev.consequence_backstop_events + 2) * rt.tick_clock.interval_ns * 4
    assert opened[-1].channel == CH_FAST
    assert opened[-1].deadline_ns == rt.clock.now_ns + horizon


def _close(rt, verdict, skill):
    rt.stats.last_window_values = {"verdict_mean": verdict, "forecast_skill": skill}
    rt.stats.reserve_windows += 1
    rt._sampling_actuator()


def test_divergence_raises_the_consequence_mix_and_steps_back_without_it():
    rt = runtime()
    assert rt.consequence_mix == rt.ev.consequence_share == 0.3
    for verdict, skill in ((0.2, 0.3), (0.5, 0.1)):
        _close(rt, verdict, skill)
    assert rt.consequence_mix == 0.3  # two windows cannot support a slope of k = 3
    _close(rt, 0.8, -0.2)
    assert rt.consequence_mix == 0.4
    raised = next(i for i in rt.ledger._recovery_items() if i["kind"] == "sampling.raise")
    assert raised["verdict_slope"] > 0 > raised["outcome_slope"]
    assert (raised["mix_before"], raised["mix_after"]) == (0.3, 0.4)
    assert rt._world_block()["scoring"]["payoff_standing"].startswith("mean Brier")
    # The weight in force is still disclosed, but out of the prompt's stable prefix,
    # which a live adaptation must not invalidate: scoring names where it is published.
    block = rt._world_block()
    assert "world.adaptive_scoring.consequence_mix" in block["scoring"]["payoff_standing"]
    assert block["adaptive_scoring"]["consequence_mix"] == 0.4
    for i in range(6):  # the divergence persists: the mix ratchets to the cap
        _close(rt, 0.8 + 0.01 * (i + 1), -0.2 - 0.01 * (i + 1))
    assert rt.consequence_mix == rt.ev.sampling_cap == 0.7
    for _ in range(3):
        _close(rt, 0.9, 0.5)  # skill recovered: no divergence, the mix steps back
    lowered = [i for i in rt.ledger._recovery_items() if i["kind"] == "sampling.lower"]
    assert rt.consequence_mix == 0.4 and len(lowered) == 3
    dist = {"eval-a": 0.5, "eval-c": 0.5}
    rt.standing.set_requested("eval-a", 1)
    rt.standing.record("eval-a", 1.0, 0.5)  # eval-a has skill, eval-c is unknown
    mixed = rt._mix_with_standing(dist)
    assert mixed["eval-a"] > dist["eval-a"]


def test_scripted_windows_with_divergence_produce_a_sampling_raise_item(monkeypatch):
    base = runtime()
    manifest = replace(base.m, novelty=replace(base.m.novelty, window_ns=3_000_000_000))
    from factorylab.runtime.loop import Runtime

    rt = Runtime(manifest, events=24, seed=1, initial_balance_micro=None, ledger_path=None,
                 drip=False, router_gamma=0.1, kill_at_end=True)
    series = iter([(0.2, 0.3), (0.4, 0.1), (0.6, -0.1), (0.8, -0.2), (0.9, -0.3)])

    def diverging_close():
        verdict, skill = next(series, (0.9, -0.3))
        rt.stats.last_window_values = {"verdict_mean": verdict, "forecast_skill": skill}

    monkeypatch.setattr(rt, "_close_price_window", diverging_close)
    summary = rt.run()
    assert summary["ledger_verify"]
    items = []
    while True:
        item = rt.ledger.decrypt_item(len(items))
        items.append(item)
        if item["kind"] == "event" and item["event"]["kind"] == "Terminated":
            break
    raises = [i for i in items if i["kind"] == "sampling.raise"]
    assert raises and raises[0]["mix_after"] == 0.4
    assert rt.consequence_mix > rt.ev.consequence_share
    snapshot = next(i for i in items if i["kind"] == "snapshot"
                    and i["boundary"] == "reserve_window" and i["seq"] > raises[0]["seq"])
    runtime_fields = dict(snapshot["state"]["runtime"]["$map"])
    assert runtime_fields["consequence_mix"] == {"$float": repr(raises[0]["mix_after"])}


def test_the_only_recursive_meta_is_terminal_on_the_tier_it_judges():
    """Codex finding: one assembly accepting MetaVerdict made every meta decision
    non-terminal, that assembly's own included — but nothing judges its own output (A9),
    so its decision waited on the conformity channel for a verdict no one could give and
    timed out. A terminal meta is graded by its conformity Brier against the judged
    verdict's consequence (A14)."""
    from factorylab.runtime.shared import CH_CONFORMITY

    rt = _consequence_runtime(provider=Judge(0.9))
    spec = next(a.spec for a in rt.assemblies.values() if a.spec.role == "meta")
    rt._instantiate(replace(spec, id="recursive-meta", accepts=frozenset({"MetaVerdict"})))
    # C10: a seat instantiated past registration has no entitlement and is infeasible
    # for routing until credited. It is endowed from the unallocated pool with the
    # 2 USD trial the recursive-meta fixture in tests/runtime/test_loop.py uses, so
    # its fake-opus ceiling is covered and the router can draw it.
    rt.budget.grant("recursive-meta", 2_000_000, "fixture: the recursive tier's trial")
    rt._open_epoch("MetaVerdict")
    _, produced = _consequence_produce(rt, "NOOP")
    _consequence_judge(rt, produced, "eval-a")
    verdict = next(e for e in rt.internal if e.kind is EventKind.VERDICT)
    rt.n += 1
    rt._route_with(rt.routers["Verdict"][0], verdict)
    decisions = rt.queue.state()["decisions"]
    lower = next(d for d in decisions.values() if d.event_id == verdict.id)
    assert lower.propensity.chosen != "recursive-meta"
    assert lower.channel == CH_CONFORMITY  # the recursive tier can judge this one
    meta_event = next(e for e in rt.internal if e.kind is EventKind.META_VERDICT)
    for _ in range(20):  # the router may draw NOOP; the terminal decision is the one judged
        rt.n += 1
        opened = set(rt.queue.state()["decisions"])
        rt._route_with(rt.routers["MetaVerdict"][0], meta_event)
        top = next(d for h, d in rt.queue.state()["decisions"].items() if h not in opened)
        if top.propensity.chosen == "recursive-meta":
            break
    assert top.propensity.chosen == "recursive-meta"
    assert top.channel == CH_FAST
    # The tier above it is empty: its own meta verdict can only be routed to NOOP.
    emitted = next(e for e in rt.internal if e.kind is EventKind.META_VERDICT
                   and e.payload["by"] == top.handle)
    assert rt._universe_for("MetaVerdict", emitted) == ["NOOP"]
    assert rt._higher_tier_universe("recursive-meta") == []
    outcome = rt.queue.history(top.handle)[0]
    assert outcome.status is SettleStatus.SETTLED
    assert outcome.definition_version == "meta-consequence-v1"
    assert outcome.score == pytest.approx(0.36)  # conformity 0.8 against y = 0
