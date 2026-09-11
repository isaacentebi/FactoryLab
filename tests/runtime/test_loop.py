from collections import Counter
from dataclasses import replace

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.loop import (
    EVALUATOR_CARDS,
    PendingJudgement,
    Runtime,
    ScriptedProvider,
    run_world,
)
from factorylab.runtime.worlds import load_manifest


def _covered_evaluators(standing: dict) -> list[str]:
    per = standing.get("evaluators", standing)
    return [e for e, v in per.items() if isinstance(v, dict) and v.get("settled", 0) > 0]


def test_scripted_world_phase2_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=400, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # producers are judged, evaluators are judged, forecasts are sealed and settled
    assert st["verdicts"] >= 20 and st["conformities"] >= 3
    assert st["forecasts_sealed"] >= 20 and st["forecasts_settled"] >= 20
    assert st["max_settlement_latency_events"] >= 1
    # consequence standing exists with coverage for at least two evaluators
    assert len(_covered_evaluators(s["standing"])) >= 2
    # a scripted proposal registered a new assembly, opened an epoch, and it was invoked
    assert st["registrations_accepted"] >= 1 and st["epochs"] >= 1
    assert st["registrations_rejected"] >= 1  # the model proposal has no catalogue here
    counts = s["aggregates"]["invocations_by_assembly"]["counts"]
    assert counts.get("funding-watcher", 0) >= 1
    assert st["routers_replaced"] >= 1
    assert s["evaluation_boundary"] == "producer → evaluator → meta"
    assert st["sample_propensity"] is not None
    assert sum(s["aggregates"]["spend_by_capability"]["spend"].values()) > 0


def test_every_producer_decision_is_judged_or_censored() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=120, seed=4)
    st = s["stats"]
    judged = st["verdicts"] + st["censored"] + st["exposures_settled"]
    assert judged + s["outstanding_decisions"] >= st["producer_returns"]


def test_scripted_world_starves_but_cannot_die_from_compute_alone() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=600, seed=2, initial_balance_micro=200_000, drip=False)
    assert s["terminated"] is False and 0 < s["wallet_balance_micro"] < 200_000
    assert s["stats"]["exclusions"] > 0


def test_crash_world_dies_and_releases_seal_spec_condition_3() -> None:
    m = load_manifest("scripted-crash")
    s = run_world(m, events=600, seed=2)
    assert s["terminated"] is True and s["termination_reason"] == "balance_zero"
    assert s["seal_key_released"] is True
    assert s["wallet_balance_micro"] <= 0
    assert s["wallet_conservation"] is True


def test_determinism_same_seed_same_summary() -> None:
    m = load_manifest("scripted")
    a = run_world(m, events=60, seed=7)
    b = run_world(m, events=60, seed=7)
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b


def test_scripted_world_phase3_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=500, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # tool calls executed and results returned to the calling assembly
    assert st["tool_calls"] >= 10
    assert st["tool_call_failures"] < st["tool_calls"]
    # a population tool was registered and then called
    assert st["population_tools_registered"] >= 1 and "spread-check" in s["tools"]
    # an online variant is registered as a purchasable and an assembly was built on it
    assert "fake-haiku:online" in s["aggregates"]["invocations_by_assembly"]["counts"] or (
        "web-observer" in s["aggregates"]["invocations_by_assembly"]["counts"]
    )
    # an amendment was proposed, voted, passed and activated: evaluators now see edition 2
    assert st["amendments_proposed"] >= 1 and st["votes_cast"] >= 3
    assert st["amendments_passed"] >= 1 and st["amendments_activated"] >= 1
    assert s["charter_edition"] >= 2
    # prices (spec v0.6 section 8.1): a 2-minute window over 500 one-second ticks closes four
    # windows, and every closed window hands the well_formed_rate card one observation
    cards = s["prices"]["cards"]
    closed = st["reserve_windows"] - 1
    assert closed >= 3 and st["price_updates"] >= 2 and st["price_skipped"] == 0
    assert cards["well_formed_rate"]["updates"] == closed
    assert cards["well_formed_rate"]["lambda"] == 0.0  # scripted returns are all well formed
    assert st["last_window_values"]["well_formed_rate"] == 1.0
    # the amendment's turnover card ("below 5", ratio units) is registered once edition 2 is
    # live and is violated by two orders of magnitude every window, so its price saturates at
    # lambda_max and every producer verdict settles at 0 while it stands; evaluators pay for
    # forecast skill below zero. This run therefore shows penalized settlements, not a world
    # with no violated card.
    assert "turnover" in cards and cards["turnover"]["lambda"] == 1.0
    assert cards["turnover"]["saturations"] >= 1
    assert st["last_window_values"]["turnover"] > 5
    assert st["penalized_settlements"] >= 1
    assert cards["cost_per_return"]["updates"] == closed - 1  # no median before the first window


def test_scripted_amendment_lambda_is_voted_adopted_and_visible(monkeypatch):
    from factorylab.runtime.loop import Runtime, ScriptedProvider, _inputs_from_prompt

    requests = []

    class RecordingProvider(ScriptedProvider):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            requests.append(_inputs_from_prompt(text))
            return super().complete(req)

    rt = Runtime(
        load_manifest("scripted"),
        events=260,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        drip=True,
        router_gamma=0.1,
        provider=RecordingProvider(),
    )
    entries = []
    append = rt.ledger.append

    def capture(entry):
        result = append(entry)
        entries.append(dict(entry))
        return result

    monkeypatch.setattr(rt.ledger, "append", capture)
    result = rt.run()
    assert result["ledger_verify"] and result["wallet_conservation"]
    votes = [req["amendment"] for req in requests if "amendment" in req]
    assert votes and all(am["add"][0]["lambda"] == 0.6 for am in votes)
    proposed = [(i, e) for i, e in enumerate(entries) if e["kind"] == "price.proposed"]
    assert len(proposed) == 1
    index, item = proposed[0]
    assert item["card_id"] == "turnover" and item["amendment_id"] == "turnover-card"
    assert item["lambda_after"] == 0.6
    assert any(e["kind"] == "price.region" and e["card_id"] == "turnover" for e in entries[:index])
    worlds = [req["world"] for req in requests if req.get("world", {}).get("charter_edition") == 2]
    first = next(c for c in worlds[0]["card_prices"] if c["card_id"] == "turnover")
    assert first["lambda"] == 0.6
    updates = [
        e
        for e in entries[index + 1 :]
        if e["kind"] == "price.update" and e["card_id"] == "turnover"
    ]
    assert updates and updates[0]["lambda_before"] == 0.6


# Recursive depth is introduced by a population return, never by changing the seeds.


class RecursiveMetaProvider(ScriptedProvider):
    recursive_ids = ("recursive-meta",)

    def _produce(self, desc, inputs):
        self._producer_calls += 1
        reply = {"action": "hold"}
        if self._producer_calls == 8:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": aid,
                    "role": "meta",
                    "model_id": inputs["world"]["models"][0]["id"],
                    "system_prompt": "Assess the supplied judgement against the charter.",
                    "accepts": ["MetaVerdict"],
                    "max_tokens": 128,
                }
                for aid in self.recursive_ids
            ]
        return reply


def _recursive_runtime(*, events=100, provider=None):
    return Runtime(
        load_manifest("scripted"),
        events=events,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        drip=True,
        router_gamma=0.1,
        provider=provider or RecursiveMetaProvider(),
    )


def _diary(runtime):
    runtime.termination.kill("test audit")
    items = []
    while True:
        try:
            items.append(runtime.ledger.decrypt_item(len(items)))
        except IndexError:
            return items


def test_population_registers_recursive_meta_and_settles_higher_tiers():
    runtime = _recursive_runtime()
    summary = runtime.run()
    assert "MetaVerdict" in runtime._world_block()["event_kinds"]
    assert any(
        a["id"] == "recursive-meta" and a["accepts"] == ["MetaVerdict"]
        for a in runtime._world_block()["assemblies"]
    )
    assert summary["stats"]["meta_verdicts"][3] > 0
    assert summary["wallet_conservation"] and summary["ledger_verify"]
    items = _diary(runtime)
    opens = {i["handle"]: i for i in items if i["kind"] == "decision.open"}
    events = {i["event"]["id"]: i["event"] for i in items if i["kind"] == "event"}
    meta_events = [e for e in events.values() if e["kind"] == "MetaVerdict"]
    returns = [i["return"] for i in items if i["kind"] == "decision.settle"]
    registration = next(
        i["seq"]
        for i in items
        if i["kind"] == "event"
        and i["event"]["kind"] == "Registered"
        and "recursive-meta" in str(i["event"]["payload"])
    )
    meta_opens = [
        o
        for o in opens.values()
        if events.get(o["event_id"], {}).get("kind") in ("Verdict", "MetaVerdict")
    ]
    assert any(o["seq"] < registration and o["channel"] == "fast" for o in meta_opens)
    assert all(o["channel"] == "conformity" for o in meta_opens if o["seq"] > registration)
    judged_metas = []
    for event in meta_events:
        p = event["payload"]
        assert p["by"] in opens and p["about"] in opens
        origin = events[opens[p["by"]]["event_id"]]
        expected = origin["payload"]["tier"] + 1 if origin["kind"] == "MetaVerdict" else 2
        assert p["tier"] == expected
        if p["tier"] == 3:
            judged_metas.extend(
                r
                for r in returns
                if r["handle"] == p["about"]
                and r["sampling_ref"] == p["by"]
                and r["channel"] == "conformity"
                and r["status"] == "settled"
            )
    assert judged_metas
    for tier, count in summary["stats"]["meta_verdicts"].items():
        assert count == sum(e["payload"]["tier"] == tier for e in meta_events)
    releases = [i for i in items if i["kind"] == "cascade.release"]
    released_ids = {i["event_id"] for i in releases}
    assert all(o["event_id"] in released_ids for o in meta_opens)
    for opened in meta_opens:
        event = events[opened["event_id"]]
        if event["kind"] == "MetaVerdict":
            judged = opens[event["payload"]["by"]]["propensity"]["chosen"]
            assert judged not in opened["propensity"]["action_ids"]
    assert any(
        r["status"] == "censored"
        and r["channel"] == "conformity"
        and opens[r["handle"]]["propensity"]["chosen"] == "recursive-meta"
        for r in returns
    )
    # The sole recursive judge cannot sample itself when its tier-3 return is delivered.
    self_routes = [
        o
        for o in meta_opens
        if events[o["event_id"]]["kind"] == "MetaVerdict"
        and events[o["event_id"]]["payload"]["tier"] == 3
    ]
    assert self_routes
    assert all(o["propensity"]["action_ids"] == ["NOOP"] for o in self_routes)
    assert all(o["propensity"]["probs"] == [1.0] for o in self_routes)


class TwoRecursiveMetaProvider(RecursiveMetaProvider):
    recursive_ids = ("recursive-meta", "recursive-meta-two")

    def __init__(self):
        super().__init__()
        self.meta_inputs = []

    def _meta(self, inputs):
        self.meta_inputs.append(inputs)
        return super()._meta(inputs)


def test_two_recursive_metas_terminate_by_cadence_and_receive_representatives():
    provider = TwoRecursiveMetaProvider()
    runtime = _recursive_runtime(events=160, provider=provider)
    summary = runtime.run()
    assert not runtime.internal
    assert not summary["terminated"]
    assert summary["wallet_balance_micro"] > runtime.m.initial_balance_micro // 2
    assert summary["stats"]["meta_verdicts"].get(4, 0) > 0
    items = _diary(runtime)
    events = {i["event"]["id"]: i["event"] for i in items if i["kind"] == "event"}
    releases = [i for i in items if i["kind"] == "cascade.release"]
    arrivals = Counter(
        1 if e["kind"] == "Verdict" else e["payload"]["tier"]
        for e in events.values()
        if e["kind"] in ("Verdict", "MetaVerdict")
    )
    for tier, count in arrivals.items():
        assert arrivals[tier + 1] <= count // runtime.m.timing.min_ratio
    assert sum(n for tier, n in arrivals.items() if tier > 1) <= arrivals[1] // 2
    assert len(provider.meta_inputs) <= arrivals[1] // 2
    assert provider.meta_inputs
    assert all("window" in inputs for inputs in provider.meta_inputs)
    assert all(
        "released representative" in inputs["world"]["meta_input"]
        for inputs in provider.meta_inputs
    )
    opens = {i["handle"]: i for i in items if i["kind"] == "decision.open"}
    settlements = {i["return"]["handle"]: i for i in items if i["kind"] == "decision.settle"}
    for release in releases:
        window = release["window"]
        assert 3 <= window["count"] <= 4
        event = events[release["event_id"]]
        handle_key = "evaluator_handle" if release["tier"] == 1 else "by"
        assert window["handles"][-1] == event["payload"][handle_key]
        for handle in window["handles"][:-1]:
            assert handle not in runtime.pending
            result = settlements[handle]
            assert result["return"]["status"] in ("settled", "censored")
            if result["return"]["definition_version"] == "fast-v1":
                assert result["return"]["score"] == 1.0
                if opens[handle]["channel"] == "conformity":
                    assert result["seq"] > release["seq"]


def test_cascade_release_is_ledger_first_and_fast_fallback_keeps_timeout(monkeypatch):
    runtime = _recursive_runtime(events=0)
    runtime.m = replace(runtime.m, timing=replace(runtime.m.timing, jitter_fraction=0))
    handles = [_pending_meta(runtime) for _ in range(3)]
    events = [
        Event(
            f"meta-{i}",
            EventKind.META_VERDICT,
            0,
            {"by": h, "about": "lower", "tier": 2, "score": i / 2},
            "runtime",
        )
        for i, h in enumerate(handles)
    ]
    for event in events[:2]:
        assert runtime._cascade_arrival(event) is None
    before = runtime.cascade[2]
    rng_before = runtime.rng.getstate()
    append = runtime.ledger.append

    def reject_release(item):
        if item["kind"] == "cascade.release":
            raise RuntimeError("ledger unavailable")
        return append(item)

    monkeypatch.setattr(runtime.ledger, "append", reject_release)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        runtime._cascade_arrival(events[2])
    assert runtime.cascade[2] is before
    assert runtime.rng.getstate() == rng_before
    assert all(runtime.queue.get(h).status is SettleStatus.PENDING for h in handles)
    monkeypatch.setattr(runtime.ledger, "append", append)
    runtime.n = runtime.ev.verdict_timeout_events + 1
    runtime.pending[handles[1]].opened_at_event = runtime.n
    runtime.pending[handles[2]].opened_at_event = runtime.n
    released = runtime._cascade_arrival(events[2])
    assert released.id == events[2].id
    # Nothing settles at release: the window's siblings wait for the meta's score.
    assert all(runtime.queue.get(h).status is SettleStatus.PENDING for h in handles)
    assert runtime.cascade_windows[handles[2]] == handles[:2]
    runtime._censor_stale_judgements()
    assert runtime.queue.history(handles[0])[0].status is SettleStatus.CENSORED
    assert handles[1] in runtime.pending and handles[2] in runtime.pending


def test_meta_score_settles_every_handle_in_its_window():
    runtime = _recursive_runtime(events=0)
    runtime.m = replace(runtime.m, timing=replace(runtime.m.timing, jitter_fraction=0))
    handles = [_pending_meta(runtime) for _ in range(3)]
    events = [
        Event(
            f"meta-{i}",
            EventKind.META_VERDICT,
            0,
            {"by": h, "about": "lower", "tier": 2, "score": i / 2},
            "runtime",
        )
        for i, h in enumerate(handles)
    ]
    for event in events[:2]:
        assert runtime._cascade_arrival(event) is None
    released = runtime._cascade_arrival(events[2])
    assert released is not None
    judged = Event(
        "meta-top",
        EventKind.META_VERDICT,
        0,
        {"by": "judge-3", "about": handles[2], "tier": 3, "score": 0.25},
        "runtime",
    )
    runtime._deliver_meta_verdict(judged)
    for h in handles:
        assert runtime.queue.get(h).status is SettleStatus.SETTLED
        assert runtime.queue.history(h)[0].score == 0.25
        assert h not in runtime.pending
    assert handles[2] not in runtime.cascade_windows


def test_producer_verdict_delivery_is_immediate_before_any_cascade_release():
    runtime = _recursive_runtime(events=1)
    runtime.run()
    items = _diary(runtime)
    verdicts = [i for i in items if i["kind"] == "event" and i["event"]["kind"] == "Verdict"]
    assert verdicts
    assert not any(i["kind"] == "cascade.release" for i in items)
    for verdict in verdicts:
        handle = verdict["event"]["payload"]["about_handle"]
        settlement = next(
            i for i in items if i["kind"] == "decision.settle" and i["return"]["handle"] == handle
        )
        assert settlement["seq"] < verdict["seq"]
        assert settlement["return"]["channel"] == "verdict"
        assert settlement["return"]["status"] == "settled"


def _pending_meta(runtime):
    handle = runtime.queue.open(
        actor="test-router",
        event_id="test",
        channel="conformity",
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=0,
        propensity=PropensityRecord(("meta",), (1.0,), "meta", 0, "test-router", "state"),
    )
    runtime.pending[handle] = PendingJudgement(handle, "conformity", 0, tier=2)
    return handle


def test_meta_timeout_boundary_first_judgement_and_evaluator_prices(monkeypatch):
    runtime = _recursive_runtime(events=0)
    handle = _pending_meta(runtime)
    runtime.n = runtime.ev.verdict_timeout_events
    runtime._censor_stale_judgements()
    assert runtime.queue.get(handle).status is SettleStatus.PENDING
    calls = []
    settle = runtime._settle_priced

    def priced(*args, **kwargs):
        calls.append(kwargs)
        settle(*args, **kwargs)

    monkeypatch.setattr(runtime, "_settle_priced", priced)

    def penalty(cards):
        assert cards == EVALUATOR_CARDS
        return 0.25

    monkeypatch.setattr(runtime, "_penalty_for", penalty)
    for tier, score in [(2, 0.1), (3, 0.8), (4, 0.2)]:
        event = Event(
            f"judgement-{tier}",
            EventKind.META_VERDICT,
            0,
            {"about": handle, "by": "higher-handle", "tier": tier, "score": score},
            "runtime",
        )
        runtime.bus.publish(event)
        runtime._deliver_meta_verdict(event)
    assert len(calls) == 1
    assert calls[0]["cards"] == EVALUATOR_CARDS
    result = runtime.queue.returns_for("test-router")[0]
    assert result.score == 0.55 and result.sampling_ref == "higher-handle"


def test_late_meta_verdict_is_censored():
    runtime = _recursive_runtime(events=0)
    handle = _pending_meta(runtime)
    runtime.n = runtime.ev.verdict_timeout_events + 1
    event = Event(
        "late",
        EventKind.META_VERDICT,
        0,
        {"about": handle, "by": "late-handle", "tier": 3, "score": 1.0},
        "runtime",
    )
    runtime.bus.publish(event)
    runtime._deliver_meta_verdict(event)
    assert runtime.queue.get(handle).status is SettleStatus.PENDING
    runtime._censor_stale_judgements()
    assert handle not in runtime.pending
    result = runtime.queue.returns_for("test-router")[0]
    assert result.status is SettleStatus.CENSORED and result.score == 0
    assert result.sampling_ref is None
    assert runtime.stats.censored == 1
