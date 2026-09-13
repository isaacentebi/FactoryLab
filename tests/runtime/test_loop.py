from collections import Counter
from dataclasses import replace

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider


def _covered_evaluators(standing: dict) -> list[str]:
    per = standing.get("evaluators", standing)
    return [e for e, v in per.items() if isinstance(v, dict) and v.get("settled", 0) > 0]


def _short_cadence_manifest():
    base = load_manifest("scripted")
    return replace(base, evaluation=replace(base.evaluation, consequence_backstop_events=20))


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
    # A1: the boundary is the registered contract, not a fixed three-tier pipeline.
    assert s["evaluation_boundary"] == "registered accepts → selected emits → return channel"
    assert st["sample_propensity"] is not None
    assert sum(s["aggregates"]["spend_by_capability"]["spend"].values()) > 0
    # Verdicts themselves now answer to the judged return's FIFO consequence.
    assert st["lots_opened"] > 0 and st["lots_closed"] > 0
    # backstop marking is asserted in tests/settlement/test_consequence.py; this scripted
    # trajectory closes every lot before the 200-event backstop
    assert st["paid_off"] > 0 and st["not_paid_off"] > 0 and st["marked"] >= 0
    assert s["standing"]["eval-c"]["weight"] > s["standing"]["eval-a"]["weight"]


def test_every_producer_decision_is_judged_or_censored() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=120, seed=4)
    st = s["stats"]
    judged = st["verdicts"] + st["censored"] + st["exposures_settled"]
    assert judged + s["outstanding_decisions"] >= st["producer_returns"]


def test_scripted_world_compute_starvation_is_final_under_phase4() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=600, seed=2, initial_balance_micro=1, drip=False)
    assert s["terminated"] and s["termination_reason"] == "insolvency:compute"
    assert s["seal_key_released"] and s["wallet_balance_micro"] == 1
    assert s["stats"]["exclusions"] > 0


def test_crash_world_dies_and_releases_seal_spec_condition_3() -> None:
    m = load_manifest("scripted-crash")
    s = run_world(m, events=600, seed=2)
    assert s["terminated"] is True and s["termination_reason"] == "balance_zero"
    assert s["seal_key_released"] is True
    assert s["wallet_balance_micro"] <= 0
    assert s["wallet_conservation"] is True


def test_determinism_same_seed_same_summary() -> None:
    base = load_manifest("scripted")
    m = replace(base, novelty=replace(base.novelty, window_ns=20_000_000_000))
    a = run_world(m, events=60, seed=7)
    b = run_world(m, events=60, seed=7)
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b


def test_scripted_world_phase3_spec_condition_2() -> None:
    m = _short_cadence_manifest()
    s = run_world(m, events=500, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # tool calls executed and results returned to the calling assembly
    assert st["tool_calls"] >= 10
    assert st["tool_call_failures"] < st["tool_calls"]
    # a population tool was registered and then called
    from factorylab.cortex.sandbox import jail_available

    if jail_available():
        assert st["population_tools_registered"] >= 1 and "spread-check" in s["tools"]
    else:
        assert st["population_tools_registered"] == 0 and "spread-check" not in s["tools"]
    # an online variant is registered as a purchasable and an assembly was built on it
    assert "fake-haiku:online" in s["aggregates"]["invocations_by_assembly"]["counts"] or (
        "web-observer" in s["aggregates"]["invocations_by_assembly"]["counts"]
    )
    # an amendment was proposed, voted, passed and activated: evaluators now see edition 2
    assert st["amendments_proposed"] >= 1 and st["votes_cast"] >= 3
    assert st["amendments_passed"] >= 1 and st["amendments_activated"] >= 1
    assert s["charter_edition"] >= 2
    # prices: a 2-minute window over 500 one-second ticks closes four
    # windows, and every closed window hands the well_formed_rate card one observation
    cards = s["prices"]["cards"]
    closed = st["reserve_windows"] - 1
    assert closed >= 3 and st["price_updates"] >= 2 and st["price_skipped"] == 0
    assert cards["well_formed_rate"]["updates"] == closed
    assert cards["well_formed_rate"]["lambda"] == 0.0  # scripted returns are all well formed
    assert st["last_window_values"]["well_formed_rate"] == 1.0
    # the amendment's turnover card ("below 5", ratio units) is registered once edition 2 is
    # live and is violated by two orders of magnitude every window. Its price saturates,
    # while the bounded, attributed penalty preserves positive settled producer rewards.
    assert "turnover" in cards and cards["turnover"]["lambda"] == 1.0
    assert cards["turnover"]["saturations"] >= 1
    assert st["last_window_values"]["turnover"] > 5
    assert st["penalized_settlements"] >= 1
    assert cards["cost_per_return"]["updates"] == closed - 1  # no median before the first window


def test_scripted_amendment_lambda_is_voted_adopted_and_visible(monkeypatch):
    from factorylab.runtime.loop import Runtime
    from factorylab.world.scripted import ScriptedProvider, _inputs_from_prompt

    requests = []

    class RecordingProvider(ScriptedProvider):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            requests.append(_inputs_from_prompt(text))
            return super().complete(req)

    rt = Runtime(
        _short_cadence_manifest(),
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
    from factorylab.charter.measurement import measurement_catalogue as catalogue

    assert all(w["observations"] == catalogue() for w in worlds)
    assert all(am["add"][0]["observation"] == "turnover" for am in votes)
    windows = [e for e in entries if e["kind"] == "price.window"]
    assert windows and all("revision_rate" in e["observations"] for e in windows)
    assert any(e["observations"].get("consequence_paid_off_rate") is not None for e in windows)
    for observation, stat in (
        ("registrations", "registrations_accepted"),
        ("registration_rejections", "registrations_rejected"),
        ("amendments_proposed", "amendments_proposed"),
        ("amendments_activated", "amendments_activated"),
        ("fills", "fills"), ("tool_calls", "tool_calls"),
    ):
        measured = sum(e["observations"][observation] for e in windows)
        measured += getattr(rt.window, observation)
        assert measured == result["stats"][stat]
    assert sum(e["observations"]["realized_pnl_usd"] for e in windows) == pytest.approx(
        (rt.realized_to_date - rt.window.realized_pnl_micro) / 1_000_000
    )


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
        a["event_kind"] == "MetaVerdict" and a["count"] > 0
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
    # Once the recursive tier exists, a meta it can judge opens on conformity; the
    # recursive judge itself is terminal (nothing judges its own output) and opens on
    # the consequence-graded channel instead of waiting for a verdict no one can give.
    # A1 gives NOOP no judging contract when the entire judging menu is excluded.
    later = [o for o in meta_opens if o["seq"] > registration
             and o["propensity"]["chosen"] != "NOOP"]
    assert later and any(o["propensity"]["chosen"] == "recursive-meta" for o in later)
    assert all(
        o["channel"] == ("fast" if o["propensity"]["chosen"] == "recursive-meta"
                         else "conformity")
        for o in later
    )
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
    # The sole recursive meta is graded by its conformity Brier against the judged
    # verdict's consequence (A14): it is never left to time out on a tier above it.
    graded = [
        r for r in returns
        if opens[r["handle"]]["propensity"]["chosen"] == "recursive-meta"
    ]
    assert graded and all(
        r["status"] == "settled" and r["definition_version"] == "meta-consequence-v1"
        for r in graded
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


def test_meta_score_settles_the_representative_and_siblings_at_the_sibling_share():
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
        assert h not in runtime.pending
    assert runtime.queue.history(handles[2])[0].score == 0.25
    share = runtime.ev.sibling_share
    assert [runtime.queue.history(h)[0].score for h in handles[:2]] == [0.25 * share] * 2
    assert handles[2] not in runtime.cascade_windows


def test_producer_verdict_delivery_is_immediate_before_any_cascade_release():
    runtime = _recursive_runtime(events=1)
    runtime.run()
    items = _diary(runtime)
    verdicts = [i for i in items if i["kind"] == "event" and i["event"]["kind"] == "Verdict"]
    assert verdicts
    releases = [i["seq"] for i in items if i["kind"] == "cascade.release"]
    for verdict in verdicts:
        # A verdict never waits on the conformity cascade that grades its evaluator.
        assert all(seq > verdict["seq"] for seq in releases)
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

    def penalty(cards, handle=None):
        assert cards == "meta"
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
    assert calls[0]["cards"] == "meta"
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


def _consequence_runtime(*, provider=None, exchange=None, manifest=None):
    from factorylab.runtime.loop import Runtime

    return Runtime(
        manifest or load_manifest("scripted"),
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        drip=False,
        router_gamma=0.2,
        provider=provider,
        exchange=exchange,
    )


def _consequence_decision(runtime, action, channel):
    from factorylab.kernel.queue import PropensityRecord

    return runtime.queue.open(
        actor="test-router",
        event_id=f"test-{runtime.n}",
        channel=channel,
        propensity=PropensityRecord((action,), (1.0,), action, 0, "test-router", "state"),
        deadline_ns=runtime.clock.now_ns + 100_000_000_000,
        parent_handle=None,
        cost_ceiling=runtime.wallet.available,
    )


def _consequence_produce(runtime, action="seed-decider", channel="verdict"):
    from types import SimpleNamespace

    from factorylab.kernel.events import Event, EventKind

    runtime.n += 1
    handle = _consequence_decision(runtime, action, channel)
    runtime._producer_step(
        Event(f"tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test"),
        handle,
        SimpleNamespace(chosen=action),
        runtime.queue.get(handle).deadline_ns,
    )
    event = next(
        e
        for e in runtime.internal
        if e.kind == EventKind.PRODUCER_RETURN and e.payload["about_handle"] == handle
    )
    runtime._settle_due_forecasts()
    return handle, event


def _consequence_judge(runtime, event, judge):
    from types import SimpleNamespace

    runtime.n += 1
    handle = _consequence_decision(runtime, judge, "conformity")
    runtime._evaluator_step(
        event,
        handle,
        SimpleNamespace(chosen=judge),
        runtime.queue.get(handle).deadline_ns,
    )
    runtime._settle_due_forecasts()
    return handle


def _consequence_diary(runtime):
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    return [runtime.ledger.decrypt_item(i) for i in range(marker)]


def test_delivered_verdicts_seal_raw_q_and_noop_skeptic_beats_noop_blesser():
    runtime = _consequence_runtime()
    about, event = _consequence_produce(runtime, "NOOP")
    first = _consequence_judge(runtime, event, "eval-a")
    second = _consequence_judge(runtime, event, "eval-c")
    assert runtime.standing.weight("eval-c") > runtime.standing.weight("eval-a")
    assert runtime.standing.skill("eval-a") < 0
    items = _consequence_diary(runtime)
    seals = [
        i for i in items if i["kind"] == "forecast.seal" and i["predicate_id"] == "return_paid_off"
    ]
    assert len(seals) == 2
    assert [(s["evaluator_id"], s["q"]) for s in seals] == [("eval-a", 0.9), ("eval-c", 0.1)]
    assert all(s["about_handle"] == about for s in seals)
    assert [runtime.queue.get(s["handle"]).parent_handle for s in seals] == [first, second]
    outcomes = [i for i in items if i["kind"] == "forecast.consequence"]
    assert len(outcomes) == 2 and all(i["y"] == 0 and not i["marked"] for i in outcomes)
    assert runtime.ledger.verify()


def test_tool_order_and_close_belong_to_calling_returns_and_tool_charge_decides_payoff():
    import json
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange
    from factorylab.world.models import ModelResponse

    class Provider:
        def __init__(self):
            self.calls = 0

        def complete(self, req):
            replies = [
                {
                    "action": "hold",
                    "tool_calls": [
                        {
                            "tool": "venue.place_market",
                            "args": {
                                "coin": "BTC",
                                "side": "buy",
                                "size": "1",
                            },
                        }
                    ],
                },
                {"action": "noop"},
                {
                    "action": "hold",
                    "tool_calls": [
                        {
                            "tool": "venue.close",
                            "args": {
                                "coin": "BTC",
                            },
                        }
                    ],
                },
                {"action": "noop"},
            ]
            reply = replies[self.calls]
            self.calls += 1
            return ModelResponse(req.model_id, json.dumps(reply), 300, 40, "end_turn")

    exchange = FakeExchange(
        coins=("BTC",),
        start_prices={"BTC": Decimal("100")},
        price_path={"BTC": [Decimal("100.005010")]},
        fee_bps=Decimal(0),
        spread_bps=Decimal(0),
    )
    runtime = _consequence_runtime(provider=Provider(), exchange=exchange)
    runtime.tool_specs["venue.place_market"]["price_micro_per_call"] = 11
    opener, _ = _consequence_produce(runtime)
    assert runtime.consequences.payoff(opener) is None
    assert runtime.consequences.table.account(opener).cost_micro == 5011
    runtime._settle_exchange_effects(exchange.advance(1_000_000_000))
    closer, _ = _consequence_produce(runtime)
    payoff = runtime.consequences.payoff(opener)
    assert payoff.net_micro == 5010 and payoff.cost_micro == 5011 and payoff.y == 0
    assert not payoff.marked
    closed = runtime.consequences.payoff(closer)
    assert closed.net_micro == 5010 and closed.cost_micro == 5000 and closed.y == 1  # credited
    assert runtime.consequences.table.lots == ()
    assert runtime.window.producer_returns == runtime.window.noop_returns == 2
    assert runtime.window.revision_returns == 0  # Tool calls are not revisions (A14).
    assert runtime.window.tool_calls == runtime.window.fills == 2
    items = _consequence_diary(runtime)
    commits = [i for i in items if i["kind"] == "wallet.commit" and i["handle"] == opener]
    assert sum(i["amount"] for i in commits) == payoff.cost_micro
    orders = [i for i in items if i["kind"] == "consequence.order"]
    assert [i["handle"] for i in orders] == [opener, closer]
    assert runtime.wallet.check_conservation() and runtime.ledger.verify()


def test_antagonist_exposure_waits_past_verdict_timeout_for_marked_verdict_consequence():
    from dataclasses import replace

    from factorylab.world.scripted import ScriptedProvider

    class Provider(ScriptedProvider):
        def _produce(self, desc, inputs):
            return {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001",
                    "payoff": 0.0}

        @staticmethod
        def _evaluate(req, inputs):
            return {"verdict": 1.0, "payoff": 1.0, "rationale": "test", "forecasts": []}

    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        evaluation=replace(
            manifest.evaluation,
            consequence_backstop_events=30,
            verdict_timeout_events=2,
        ),
    )
    runtime = _consequence_runtime(provider=Provider(), manifest=manifest)
    about, event = _consequence_produce(runtime, "antagonist-a", "exposure")
    _consequence_judge(runtime, event, "eval-a")
    runtime.n = 10
    runtime._settle_due_forecasts()
    assert about in runtime.pending_exposure
    runtime._settle_exchange_effects(runtime.exchange.advance(1_000_000_000))
    runtime.n = 31
    runtime._settle_due_forecasts()
    assert about not in runtime.pending_exposure
    assert runtime.queue.history(about)[-1].score == 1.0
    assert runtime.consequences.payoff(about).marked
    assert runtime.standing.skill("eval-a") < 0


def test_population_cannot_propose_extra_kernel_forecasts():
    runtime = _consequence_runtime()
    parent = _consequence_decision(runtime, "eval-a", "conformity")
    runtime._open_forecasts(
        parent,
        "eval-a",
        "unused",
        [
            {
                "predicate": "return_paid_off",
                "params": {"horizon_events": 1},
                "q": 0.0,
            }
        ],
    )
    assert runtime.book.outstanding() == 0
    assert "return_paid_off" not in str(runtime._forecast_schema())
    world = runtime._world_block()
    # Scoring is public; describing the kernel predicate does not make it proposable.
    assert "return_paid_off" in world["scoring"]
    assert "return_paid_off" not in str(world["proposal_shapes"])
    assert "return_paid_off" not in str(world["a_return_may_include"])


def test_consequence_backstop_manifest_default_override_and_validation():
    import json
    import tomllib

    import pytest

    from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict

    assert load_manifest("scripted").evaluation.consequence_backstop_events == 200
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw["evaluation"]["consequence_backstop_events"] = 7
    manifest = manifest_from_dict(raw)
    assert manifest.evaluation.consequence_backstop_events == 7
    assert json.loads(manifest.canonical_json())["evaluation"]["consequence_backstop_events"] == 7
    for value in (0, -1, 1.5, True, "7"):
        raw["evaluation"]["consequence_backstop_events"] = value
        with pytest.raises(ValueError, match="consequence_backstop_events"):
            manifest_from_dict(raw)


def test_self_crossing_limit_tools_cannot_manufacture_paid_off_return():
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange

    runtime = _consequence_runtime(
        exchange=FakeExchange(
            coins=("BTC",),
            start_prices={"BTC": Decimal("100")},
        )
    )
    wash = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(wash, 0)
    for side in ("buy", "sell"):
        result, _ = runtime._run_tool(
            "seed-decider",
            wash,
            {
                "tool": "venue.place_limit",
                "args": {"coin": "BTC", "side": side, "size": "1", "price": "100"},
            },
            slot=f"test:{side}",
        )
        assert result["status"] == "filled"
    runtime.consequences.finish(wash, 500)
    runtime.consequences.resolve(0)
    payoff = runtime.consequences.payoff(wash)
    assert payoff.net_micro == -70_000 and payoff.y == 0
    assert runtime.wallet.balance == runtime.initial - 70_000


def test_resting_limit_fill_and_reduce_only_tool_keep_original_return_attribution():
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange

    exchange = FakeExchange(
        coins=("BTC",),
        start_prices={"BTC": Decimal("101")},
        price_path={"BTC": [Decimal("100"), Decimal("110")]},
    )
    runtime = _consequence_runtime(exchange=exchange)
    limit = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(limit, 0)
    result, _ = runtime._run_tool(
        "seed-decider",
        limit,
        {
            "tool": "venue.place_limit",
            "args": {"coin": "BTC", "side": "buy", "size": "1", "price": "100"},
        },
    )
    assert result["status"] == "resting"
    runtime.consequences.finish(limit, 500)
    runtime.consequences.resolve(0)
    assert runtime.consequences.payoff(limit) is None
    runtime._settle_exchange_effects(exchange.advance(1_000_000_000))
    assert runtime.consequences.table.lots[0].handle == limit
    runtime._settle_exchange_effects(exchange.advance(2_000_000_000))
    reduce = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(reduce, 1)
    result, _ = runtime._run_tool(
        "seed-decider",
        reduce,
        {
            "tool": "venue.place_market",
            "args": {"coin": "BTC", "side": "sell", "size": "2", "reduce_only": True},
        },
    )
    assert result["filled_size"] == "1"
    runtime.consequences.finish(reduce, 500)
    runtime.consequences.resolve(1)
    assert runtime.consequences.payoff(limit).y == 1
    assert runtime.consequences.payoff(reduce).y == 1  # the closer is credited (A16)
    assert runtime.consequences.table.lots == ()


@pytest.fixture
def market_http(monkeypatch):
    from urllib import request

    from tests.world.test_market import SellerHTTP

    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(request.OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    return SellerHTTP(balance=100_000)


def _market_runtime(market_http, *, provider=None, events=10, treasury=None, seed_price="0"):
    from factorylab.runtime.worlds import manifest_from_dict
    from factorylab.world.market import X402Provider
    from tests.world.test_market import TEST_KEY

    manifest = manifest_from_dict({
        "name": "market-scripted", "seed": 1, "initial_balance_usd": "0.1",
        "models": [{"id": "fake-model", "provider": "fake",
                    "input_usd_per_mtok": seed_price, "output_usd_per_mtok": seed_price}],
        "assemblies": [{"id": "seed-market", "model_id": "fake-model", "accepts": ["Tick"]}],
        "evaluation": {"trial_amount_usd": "0.001"},
        "novelty": {"share": 0.5},
        "treasury": treasury or {"insolvency_events": 3},
    })
    return Runtime(
        manifest, events=events, seed=1, initial_balance_micro=None, ledger_path=None,
        drip=False, router_gamma=0.2, provider=provider or ScriptedProvider(),
        market=X402Provider(private_key=TEST_KEY, transport=market_http),
    )


def test_population_registers_x402_seller_through_scripted_returns(market_http):
    from tests.world.test_market import MODEL

    class Proposer(ScriptedProvider):
        def _produce(self, description, inputs):
            self._producer_calls += 1
            if self._producer_calls == 1:
                return {"action": "hold", "register": [{"kind": "model", "openrouter_id": MODEL}]}
            if self._producer_calls == 2:
                return {"action": "hold", "register": [{
                    "kind": "assembly", "id": "market-buyer", "model_id": MODEL,
                    "system_prompt": "Return a JSON action.", "accepts": ["Tick"],
                    "max_tokens": 16,
                }]}
            return {"action": "hold"}

    runtime = _market_runtime(market_http, provider=Proposer(), events=20)
    summary = runtime.run()
    assert summary["stats"]["registrations_accepted"] == 2
    assert runtime.prices.price(MODEL).per_request_micro == 1734
    assert dict(runtime.registry.get("model:" + MODEL).price.units) == {
        "input_token": 0, "output_token": 0, "request": 1734,
    }
    assert runtime._is_feasible("market-buyer") == (True, "")
    assert runtime._world_block()["sellers"][0]["per_request_micro"] == 1734
    assert market_http.payments
    assert runtime.window.market_purchases == len(market_http.payments)
    items = _diary(runtime)
    assert any(i["kind"] == "event" and i["event"]["kind"] == "Registered"
               and i["event"]["payload"]["id"] == MODEL
               for i in items)
    payment = next(i for i in items if i["kind"] == "x402.result")
    commit = next(i for i in items if i["kind"] == "wallet.commit"
                  and i["handle"] == payment["handle"])
    assert payment["seq"] < commit["seq"]
    invocation = next(i for i in items if i["kind"] == "invocation"
                      and i["handle"] == payment["handle"])
    assert commit["seq"] < invocation["seq"] and invocation["cost"] == 1734
    assert summary["wallet_conservation"] and summary["ledger_verify"]


def _register_test_seller(runtime):
    from factorylab.cortex.registration import AssemblyProposal, ModelProposal
    from tests.world.test_market import MODEL

    runtime._manage_reserve_window()
    runtime._register("proposal", ModelProposal(MODEL))
    runtime._register("proposal", AssemblyProposal(
        "market-buyer", "producer", MODEL, "Return JSON.", ("Tick",), 16, "low",
    ))


def test_x402_feasibility_uses_one_fixed_request_and_on_chain_reserve(market_http):
    runtime = _market_runtime(market_http)
    _register_test_seller(runtime)
    runtime.wallet.settle(1734 - runtime.wallet.balance, "test", "exchange_pnl")
    market_http.balance = 1734
    assert runtime._is_feasible("market-buyer") == (True, "")
    market_http.balance = 1733
    assert not runtime._is_feasible("market-buyer")[0]
    assert not market_http.payments  # registration and feasibility never authorize payments


def test_market_discovery_tool_is_priced_and_debited_before_return(market_http, monkeypatch):
    from factorylab.world.x402 import HTTPResponse
    from tests.world.test_market import resource

    runtime = _market_runtime(market_http)
    calls = []

    def fake(method, url, payload, headers):
        calls.append(url)
        return HTTPResponse(200, {"items": [resource("https://seller.test/chat")],
                                 "pagination": {"offset": 0, "limit": 100, "total": 1}})

    monkeypatch.setattr(runtime.market, "_transport", fake)
    initial = runtime.wallet.balance
    result, cost = runtime._run_tool("seed-market", "discovery", {
        "tool": "market.discover", "args": {"url_substring": "chat"},
    })
    assert cost == runtime.m.tools.population_tool_micro_per_call
    assert runtime.wallet.balance == initial - cost
    assert result["sellers"][0]["resource"] == "https://seller.test/chat"
    assert runtime.tool_specs["market.discover"]["kind"] == "market" and len(calls) == 1


def test_insolvency_terminates_scripted_world_when_seller_demands_unaffordable_payment(market_http):
    from factorylab.world.x402 import InsufficientReserve

    class Unaffordable(ScriptedProvider):
        def complete(self, req):
            raise InsufficientReserve("Reserve cannot cover the quoted Base USDC payment")

    runtime = _market_runtime(market_http, provider=Unaffordable(), events=200)
    _register_test_seller(runtime)
    # The reserve can be drained after feasibility, between the read and the payment quote.
    def demand(req, *, record=None, quoted=None):
        raise InsufficientReserve("Reserve cannot cover the quoted Base USDC payment")

    runtime.market.complete = demand
    # A single x402 route removes seeded alternatives while preserving ordinary router sampling.
    del runtime.assemblies["seed-market"]
    runtime._build_router("Tick", "exp3", 0.2)
    result = runtime.run()
    assert result["terminated"] and result["termination_reason"] == "insolvency:compute"
    assert result["seal_key_released"] and result["wallet_balance_micro"] > 0
    assert not market_http.payments
    events = [i for i in _diary(runtime) if i["kind"] == "treasury.insolvency"]
    assert [e["consecutive_events"] for e in events[-3:]] == [1, 2, 3]


def test_insolvency_no_affordable_provider_counts_once_per_routed_event(market_http):
    runtime = _market_runtime(market_http, seed_price="1000")
    result = runtime.run()
    assert result["termination_reason"] == "insolvency:compute"
    assert result["seal_key_released"] and result["wallet_balance_micro"] == 100_000
    items = _diary(runtime)
    counted = [i for i in items if i["kind"] == "treasury.insolvency"]
    assert [i["consecutive_events"] for i in counted] == [1, 2, 3]
    assert len({i["event_id"] for i in counted}) == 3
    assert len([i for i in items if i["kind"] == "event"
                and i["event"]["kind"] == "Terminated"]) == 1


def test_insolvency_streak_reset_noop_and_unrouted_events(market_http):
    runtime = _market_runtime(market_http)
    event = Event("routed", EventKind.TICK, 1, {}, "test")
    runtime._compute_routed = True
    runtime._compute_unaffordable = True
    runtime._record_insolvency_event(event)
    assert runtime.insolvency_count == 1
    runtime._compute_routed = False
    runtime._record_insolvency_event(Event("unrouted", EventKind.REGISTERED, 2, {}, "test"))
    assert runtime.insolvency_count == 1
    # Affordable NOOP choices are not insolvency; no invocation is needed to reset.
    runtime._compute_routed = True
    runtime._compute_unaffordable = False
    runtime._record_insolvency_event(event)
    assert runtime.insolvency_count == 0


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "20"])
def test_treasury_insolvency_threshold_must_be_positive_integer(market_http, value):
    with pytest.raises(ValueError, match="insolvency_events"):
        _market_runtime(market_http, treasury={"insolvency_events": value})


def test_treasury_defaults_are_hashed_and_can_select_an_index(market_http):
    from factorylab.runtime.worlds import TreasurySpec
    from factorylab.world.market import DISCOVERY_URL

    assert TreasurySpec().insolvency_events == 20 and TreasurySpec().discovery_url == DISCOVERY_URL
    runtime = _market_runtime(market_http, treasury={
        "insolvency_events": 5, "discovery_url": "https://index.test/resources",
    })
    assert runtime.m.treasury.insolvency_events == 5
    assert runtime.m.manifest_hash() != replace(runtime.m, treasury=TreasurySpec()).manifest_hash()


def test_scripted_clock_amendment_changes_next_tick_deterministically(monkeypatch):
    import json

    from factorylab.world.scripted import _inputs_from_prompt

    def run():
        requests = []

        class ClockProvider(ScriptedProvider):
            def complete(self, req):
                text = "\n".join(str(m.get("content", "")) for m in req.messages)
                requests.append(_inputs_from_prompt(text))
                response = super().complete(req)
                body = json.loads(response.text)
                for proposal in body.get("register", []):
                    if proposal.get("kind") == "amendment":
                        proposal["tick_interval"] = "2s"
                return replace(response, text=json.dumps(body))

        rt = Runtime(_short_cadence_manifest(), events=260, seed=1,
                     initial_balance_micro=None, ledger_path=None, drip=True,
                     router_gamma=0.1, provider=ClockProvider())
        entries = []
        append = rt.ledger.append

        def capture(entry):
            entries.append(dict(entry))
            return append(entry)

        monkeypatch.setattr(rt.ledger, "append", capture)
        summary = rt.run()
        votes = [r["amendment"] for r in requests if "amendment" in r]
        assert votes and all(v["tick_interval"] == "2s" for v in votes)
        assert summary["stats"]["clock_changes"] == 1
        changed = next(i for i, e in enumerate(entries) if e["kind"] == "clock.changed")
        ticks_before = [e for e in entries[:changed]
                        if e["kind"] == "event" and e["event"].kind == "Tick"]
        ticks_after = [e for e in entries[changed:]
                       if e["kind"] == "event" and e["event"].kind == "Tick"]
        assert ticks_after[0]["ts"] - ticks_before[-1]["ts"] == 2_000_000_000
        assert all(b["ts"] - a["ts"] == 2_000_000_000
                   for a, b in zip(ticks_after, ticks_after[1:], strict=False))
        assert rt._world_block()["clock"]["tick_interval"] == "2s"
        return summary, entries

    first, entries = run()
    second, replay = run()
    assert first == second
    assert entries == replay


def test_scripted_governance_waits_for_measured_periods_and_ledgers_both_forecast_types():
    from dataclasses import asdict
    from math import ceil

    base = load_manifest("scripted")
    manifest = replace(
        base, timing=replace(base.timing, cadence_sample=20, min_support=2),
        evaluation=replace(base.evaluation, consequence_backstop_events=4),
        novelty=replace(base.novelty, window_ns=3_000_000_000),
    )
    manifest.validate()

    def run():
        rt = Runtime(manifest, events=26, seed=1, initial_balance_micro=None,
                     ledger_path=None, drip=False, router_gamma=0.1, kill_at_end=True)
        # Cadence is tested with an experienced electorate; fresh seeds have no seats.
        from tests.runtime.test_fidelity import decision

        for _ in range(manifest.committee.min_settled):
            decision(rt, "eval-a", settled=True)
        about = None

        def route(event):
            nonlocal about
            if event.kind != EventKind.TICK:
                return
            if event.payload["index"] == 0:
                about = _consequence_decision(rt, "seed-decider", "verdict")
                rt.consequences.start(about, rt.n)
                parent = _consequence_decision(rt, "eval-a", "conformity")
                rt.consequences.seal_verdict(
                    rt.book, rt.queue, evaluator_handle=parent, evaluator_id="eval-a",
                    about=about, payoff=0.5, event=rt.n, now_ns=rt.clock.now_ns,
                    tick_ns=rt.tick_clock.interval_ns,
                )
                rt._open_forecasts(parent, "eval-a", about, [{
                    "predicate": "wallet_up", "q": 0.5, "params": {"horizon_events": 4},
                }])
                for amendment_id in ("cadence-one", "cadence-two"):
                    rt._propose_amendment(about, {
                        "id": amendment_id,
                        "replace": [{**asdict(rt.charter.cards[1]), "description": amendment_id}],
                        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1},
                    })
            elif event.payload["index"] == 4:
                rt.consequences.finish(about, 0)

        rt._route = route
        rt._settle_exchange_effects = lambda events: None
        snapshots = []
        original = rt._activate_charter_if_due

        def activate():
            original()
            snapshots.append(rt._world_block()["governance"])

        rt._activate_charter_if_due = activate
        summary = rt.run()
        # kill_at_end releases the key; the known ledger length is not needed.
        items = []
        while True:
            item = rt.ledger.decrypt_item(len(items))
            items.append(item)
            if item["kind"] == "event" and item["event"]["kind"] == "Terminated":
                break
        return summary, items, snapshots

    summary, items, snapshots = run()
    assert summary["ledger_verify"] and summary["wallet_conservation"]
    latencies = []
    approvals = set()
    last_activation = 0
    deferred = []
    activations = []
    opens = {i["handle"]: i for i in items if i["kind"] == "forecast.seal"}
    settled_handles = {
        i["return"]["handle"] for i in items
        if i["kind"] == "decision.settle" and i["return"]["channel"] == "consequence"
        and i["return"]["handle"] in opens
    }
    samples = [i for i in items if i["kind"] == "cadence.settlement"]
    assert {i["handle"] for i in samples} == settled_handles
    assert len(samples) == len(settled_handles)
    assert "return_paid_off" in {i["predicate_id"] for i in samples}
    assert any(i["predicate_id"] != "return_paid_off" for i in samples)
    for item in items:
        if item["kind"] == "cadence.settlement":
            assert item["opened_event"] == opens[item["handle"]]["made_at_event"]
            assert item["latency_ns"] == item["settled_ns"] - item["opened_ns"]
            assert item["latency_events"] == item["settled_event"] - item["opened_event"]
            latencies = (latencies + [item["latency_events"]])[-20:]
        elif item["kind"] == "charter.approved":
            approvals.add(item["amendment_id"])
        elif item["kind"] == "charter.deferred":
            assert item["amendment_id"] in approvals
            assert item["ts"] < item["earliest_ns"] or item["n"] < item["earliest_event"]
            deferred.append((item["amendment_id"], item["window"]))
        elif item["kind"] == "charter.cadence":
            measured = sorted(latencies)[ceil(0.9 * len(latencies)) - 1]
            period = max(measured, manifest.evaluation.consequence_backstop_events) * 10**9
            assert item["slowest_period_ns"] == period
            assert item["activation_ns"] - last_activation >= manifest.timing.min_ratio * period
            assert item["previous_activation_ns"] == last_activation
            last_activation = item["activation_ns"]
            activations.append(item)
    assert deferred and len(deferred) == len(set(deferred))
    assert len(activations) == 2 and any(s["waiting"] for s in snapshots)
    assert all(i["activation_event"] - i["previous_activation_event"]
               >= manifest.timing.min_ratio * i["slowest_period_events"] for i in activations)
    assert not snapshots[-1]["waiting"]
    assert (summary, items, snapshots) == run()


def test_governance_live_time_anchor_does_not_treat_epoch_time_as_elapsed():
    rt = _consequence_runtime()
    rt.live = True
    rt.clock.now_ns = 1_800_000_000_000_000_000
    rt._manage_reserve_window()
    assert rt.cadence.earliest_ns(rt.tick_clock.interval_ns) == (
        rt.clock.now_ns
        + rt.m.timing.min_ratio * rt.ev.consequence_backstop_events * rt.tick_clock.interval_ns
    )


@pytest.mark.parametrize(("observation", "region", "unknown"), [
    ("unavailable", "below 5", ["observation"]),
    ("turnover", "roughly stable", ["region"]),
    ("unavailable", "roughly stable", ["region", "observation"]),
])
def test_unpriced_cards_report_unknown_field_once(monkeypatch, observation, region, unknown):
    from factorylab.charter.charter import Charter, MetricCard

    rt = _recursive_runtime(events=0)
    rt.charter = Charter(1, rt.charter.norms, (
        MetricCard("turnover", rt.charter.norms[0], "d", "ratio",
                   MetricWindow("windows", 1, None), region, observation, "all"),
    ))
    entries = []
    append = rt.ledger.append

    def capture(item):
        result = append(item)
        entries.append(dict(item))
        return result

    monkeypatch.setattr(rt.ledger, "append", capture)
    rt._derive_regions()
    rt._derive_regions()
    assert not rt.regions and not rt.priced
    unparsed = [e for e in entries if e["kind"] == "price.unparsed"]
    assert len(unparsed) == 1 and unparsed[0]["unparsed"] == unknown
    assert all(field in unparsed[0]["reason"] for field in unknown)
    rt.n = 100
    rt._close_price_window()
    assert not any(e["kind"] == "price.update" for e in entries)


def test_two_roles_measure_same_observation_with_independent_bounds():
    from factorylab.charter.charter import Charter, MetricCard
    from factorylab.runtime.pricing import MeasureWindow

    rt = _recursive_runtime(events=0)
    rt.charter = Charter(1, rt.charter.norms, (
        MetricCard("low", rt.charter.norms[0], "d", "fraction",
                   MetricWindow("windows", 1, None), "below 0.2",
                   " NOOP_SHARE ", "producer"),
        MetricCard("high", rt.charter.norms[0], "d", "fraction",
                   MetricWindow("windows", 1, None), "below 0.9",
                   "noop_share", "evaluator"),
        MetricCard("cost-alias", rt.charter.norms[0], "d", "micro-USD",
                   MetricWindow("windows", 1, None),
                   "below the median of the previous window", "cost_per_return", "producer"),
    ))
    rt._derive_regions()
    rt.window = MeasureWindow(1, 100, producer_returns=4, noop_returns=2, costs=[100, 200, 900])
    rt.n = 100
    rt._close_price_window()
    cards = rt.controller.snapshot()["cards"]
    assert cards["low"]["updates"] == cards["high"]["updates"] == 1
    assert cards["low"]["lambda"] > 0 and cards["high"]["lambda"] == 0
    assert rt.stats.last_window_values["noop_share"] == 0.5
    assert rt._penalty_for("producer") > 0
    rt._derive_regions()
    assert rt.regions["cost-alias"].hi == 200.0
    items = _diary(rt)
    window = next(e for e in items if e["kind"] == "price.window")
    assert window["observations"]["noop_share"] == 0.5
    assert window["values"] == {"low": 0.5, "high": 0.5}
    from factorylab.versioning.report import summary

    report = summary(items, **{
        name: getattr(rt.m.immune, name) for name in (
            "bins", "k", "tv_threshold", "gap_threshold", "registration_bins", "revision_bins"
        )
    })
    assert "card:low" in report["operator"]["dimensions"]
    assert report["windows"][0]["profile"]["card:low"] == 0.5


def test_position_peak_is_ledger_first_and_survives_flat_account(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    rt = _recursive_runtime(events=0)
    rt.window.equity_start_micro = 10_000_000
    positions = [SimpleNamespace(coin="BTC", size=Decimal("-2"))]
    monkeypatch.setattr(rt.exchange, "account", lambda: SimpleNamespace(positions=positions))
    monkeypatch.setattr(rt.exchange, "mids", lambda: {"BTC": Decimal("3")})
    append = rt.ledger.append

    def capture(item):
        if item["kind"] == "observation.position_peak":
            assert rt.window.max_position_notional_micro is None
        return append(item)

    monkeypatch.setattr(rt.ledger, "append", capture)
    rt._observe_positions()
    assert rt.window.max_position_notional_micro == 6_000_000
    positions.clear()
    rt._observe_positions()
    assert rt.window.max_position_notional_micro == 6_000_000


def test_manifest_charter_is_edition_one_and_seeds_prices():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict

    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw["charter"] = {
        "norms": ["population norm"],
        "cards": [
            {"id": card_id, "norm": "population norm", "description": "Population draft",
             "units": "fraction", "window": {"kind": "windows", "n": 1, "per": None},
             "acceptable_region": region,
             "observation": {"draft": "well_formed_rate", "deferred": "noop_share",
                             "default": "revision_rate"}[card_id], "answers_for": "all", **price}
            for card_id, region, price in [
                ("draft", "at least 0.9", {"lambda": 0.4}),
                ("deferred", "below the median of the previous window", {"lambda": 0.3}),
                ("default", "at least 0.9", {}),
            ]
        ],
    }
    manifest = manifest_from_dict(raw)
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=0.1)
    assert rt.charter == manifest.charter and rt.charter.edition == 1
    assert "Population draft" in rt.charter.render()
    assert "cost_per_return" not in rt.charter.render()
    rt._derive_regions()
    prices = rt.controller.snapshot()["cards"]
    assert prices["draft"]["lambda"] == 0.4
    assert prices["deferred"]["lambda"] == 0.3
    assert prices["default"]["lambda"] == 0
    rt.rolling["deferred_prev_median"] = 0.8
    rt._derive_regions()
    assert rt.controller.snapshot()["cards"]["deferred"]["lambda"] == 0.3
    assert rt.run()["ledger_verify"]
