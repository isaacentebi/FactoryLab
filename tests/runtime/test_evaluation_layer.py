"""Wave 5a: the evaluation layer built out (essay II.III; evaluations C1, C7, M1-M3, P5, P6).

A judge never reads work authored on its own foundation family, and a share of
returns is read by two judges on different families (ensemble disagreement). The
tiers read a meaningful share of the tier below, and recursion deepens past two. The
kernel refuses a world whose evaluators are a minority or a monoculture. The
adversarial layer holds an adversarial judge paid only against realized
consequence, antagonists of both learner types centred on their judges' ordinary
misses, and a chaos actuator whose faults are real and cannot move money. The
early-warning statistics are live and are the evaluators', never a producer's.
"""

from __future__ import annotations

import tomllib
from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.families import model_family
from factorylab.runtime.feedback import counter_score, exposure_score
from factorylab.runtime.shared import CH_COUNTER, NOOP
from factorylab.runtime.worlds import AssemblySeed, load_manifest, manifest_from_dict
from factorylab.settlement import Observer, WindowFacts
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime
from tests.runtime.test_reward_chain import (
    Population,
    _horizon,
    _judge,
    _mids,
    _rows,
)

# --- families ----------------------------------------------------------------------


@pytest.mark.parametrize(("a", "b"), [
    ("venice:z-ai-glm-5-3-flash", "z-ai/glm-5.3-flash"),
    ("openai/gpt-5.6-sol", "openai/gpt-5.6-luna"),
    ("openai/gpt-5.6-luna:online", "venice:openai-gpt-56-luna"),
    ("venice:qwen-3-8-flash", "qwen/qwen3.8-flash"),
    ("venice:deepseek-v4-1-flash", "deepseek/deepseek-v4.1-flash"),
])
def test_a_provider_change_or_a_size_is_not_a_model_change(a, b):
    assert model_family(a) == model_family(b)


def test_families_differ_across_foundations_and_every_double_is_its_own():
    families = {model_family(m) for m in ("z-ai/glm-5.3-flash", "openai/gpt-5.6-luna",
                                          "qwen/qwen3.8-flash", "deepseek/deepseek-v4.1-flash")}
    assert families == {"glm", "gpt", "qwen", "deepseek"}
    assert len({model_family(m) for m in ("fake-haiku", "fake-opus", "fake-sonnet")}) == 3


# --- the evaluator population is manifest physics (C1, M3) ---------------------------


def _edition6() -> dict:
    with open("worlds/edition6-testnet-rehearsal.toml", "rb") as fh:
        return tomllib.load(fh)


def _without(raw: dict, *seats: str) -> dict:
    return {**raw, "assemblies": [a for a in raw["assemblies"] if a["id"] not in seats]}


def test_a_world_whose_evaluators_are_the_minority_is_refused():
    raw = _without(_edition6(), "judge-mechanics", "judge-base-rate", "meta-audit")
    with pytest.raises(ValueError, match=r"evaluator seats \(5\) do not outnumber producer "
                                         r"seats \(6\)"):
        manifest_from_dict(raw)
    # A tie is not a majority (the #132 review, item 2: producers are "the minority").
    tied = _without(_edition6(), "judge-mechanics", "judge-base-rate")
    with pytest.raises(ValueError, match=r"evaluator seats \(6\) do not outnumber"):
        manifest_from_dict(tied)


def test_an_evaluator_monoculture_is_refused_and_a_provider_change_does_not_help():
    raw = _edition6()
    for seat in raw["assemblies"]:
        if seat["role"] in ("evaluator", "meta", "adversary"):
            seat["model_id"] = ("venice:z-ai-glm-5-3-flash" if seat["id"].endswith("a")
                                else "z-ai/glm-5.3-flash")
    with pytest.raises(ValueError, match="1 model families serve the evaluator tier"):
        manifest_from_dict(raw)


def test_too_few_judge_families_for_two_judges_a_return_is_refused_only_with_a_share():
    raw = _edition6()
    for seat in raw["assemblies"]:
        if seat["id"] in ("judge-fidelity", "judge-mechanics"):
            seat["model_id"] = "openai/gpt-6-luna"
    with pytest.raises(ValueError, match="opportunity's ProducerReturn is accepted by judges "
                                         "on 1 families other than its own"):
        manifest_from_dict(raw)
    raw["evaluation"]["multi_judge_share"] = 0.0
    assert manifest_from_dict(raw).evaluation.multi_judge_share == 0.0


def test_a_world_that_seeds_no_judging_is_not_bound():
    raw = _edition6()
    raw["assemblies"] = [a for a in raw["assemblies"] if a["role"] == "producer"]
    assert manifest_from_dict(raw).evaluator_population_problems() == []


@pytest.mark.parametrize(("section", "key", "value"), [
    ("evaluation", "multi_judge_share", 1.5), ("evaluation", "multi_judge_count", 1),
    ("evaluation", "multi_judge_count", 9), ("evaluation", "meta_read_share", 0.0),
    ("chaos", "venue_unavailable", 0.6), ("chaos", "stale_mids", -0.1),
])
def test_the_new_keys_are_bounded(section, key, value):
    raw = _edition6()
    raw.setdefault(section, {})[key] = value
    with pytest.raises(ValueError, match=f"{section}.{key}"):
        manifest_from_dict(raw)


def test_an_unknown_chaos_key_is_refused():
    raw = _edition6()
    raw["chaos"]["fake_fills"] = 0.1
    with pytest.raises(ValueError, match="chaos accepts only"):
        manifest_from_dict(raw)


# --- routing: never the author's family; two judges on a share -----------------------


def _authors_and_readers(rt):
    """(reader seat, the authors of the two nearest links it read) for every judgement."""
    pairs = []
    for handle, about in rt.decision_subjects.items():
        reader = rt.handle_to_assembly.get(handle)
        authors = [rt.handle_to_assembly.get(h)
                   for h in (about, rt.decision_subjects.get(about)) if h is not None]
        authors = [a for a in authors if a is not None]
        if reader is None or not authors or reader not in rt.assemblies:
            continue
        if rt._judging(reader):
            pairs.append((reader, authors))
    return pairs


@pytest.mark.gate
def test_no_seat_ever_judges_a_chain_authored_on_its_own_family():
    """A judge avoids the producer's family; a meta the judge's and the producer's; a
    grader of a meta the meta's and the judge's (the #132 review, item 3)."""
    from factorylab.runtime.loop import Runtime

    rt = Runtime(load_manifest("scripted"), events=60, seed=2, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    pairs = _authors_and_readers(rt)
    assert len(pairs) > 20
    assert any(len(authors) == 2 for _reader, authors in pairs)
    for reader, authors in pairs:
        assert rt._family(reader) not in {rt._family(a) for a in authors}, (reader, authors)


@pytest.mark.gate
def test_a_multi_judged_return_is_read_by_judges_on_two_other_families():
    from factorylab.runtime.loop import Runtime

    base = load_manifest("scripted")
    # Every window is derived from the loop it commands (time audit T1).
    manifest = replace(base, evaluation=replace(base.evaluation, multi_judge_share=1.0))
    rt = Runtime(manifest, events=60, seed=2, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    assert any(i["kind"] == "route.multi_judge" for i in items)
    means = [i for i in items if i["kind"] == "verdict.mean"]
    assert means
    for row in means:
        judges = [rt.handle_to_assembly[h] for h in row["judges"]]
        author = rt.handle_to_assembly[row["handle"]]
        families = [rt._family(j) for j in judges]
        assert len(set(families)) == len(families) and rt._family(author) not in families
    # Ensemble disagreement exists once returns are multi-judged (essay II.III.a).
    windows = [i for i in items if i["kind"] == "price.window"]
    assert any(w["observations"].get("evaluator_disagreement") is not None for w in windows)


def test_a_zero_share_draws_nothing_from_the_stream():
    base = load_manifest("scripted")
    rt = _consequence_runtime(manifest=replace(base, evaluation=replace(
        base.evaluation, multi_judge_share=0.0)))
    state = rt.rng.getstate()
    ev = Event("p", EventKind.PRODUCER_RETURN, 0, {"about_handle": "x"}, "runtime")
    rt._draw_more_judges(ev, rt.routers["ProducerReturn"], ["eval-a"])
    assert rt.rng.getstate() == state


# --- the tiers above (C7) ------------------------------------------------------------


def _recursive(base):
    seats = []
    for seat in base.assemblies:
        if seat.role == "meta":
            seat = replace(seat, accepts=("Verdict", "MetaVerdict"))
        seats.append(seat)
    seats.append(AssemblySeed(id="meta-top", model_id="fake-sonnet", accepts=("MetaVerdict",),
                              role="meta", emits=("MetaVerdict",), max_tokens=128))
    return replace(base, assemblies=tuple(seats))


@pytest.mark.gate
def test_metas_are_graded_by_a_tier_above_and_the_tiers_read_a_share_of_each_window():
    from factorylab.runtime.loop import Runtime

    manifest = _recursive(load_manifest("scripted"))
    manifest.validate()
    # The tier-two window is min_ratio times the judges' scored loop (time audit T10):
    # a judge settles on its world outcome, about a backstop, so metas' grades reach the
    # tier above after a few of those loops, not in the first eighty ticks.
    rt = Runtime(manifest, events=200, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    grades = [i for i in items if i["kind"] == "evaluator.meta_grade"]
    assert any(i["tier"] >= 3 for i in grades), "no meta was graded from above"
    # A window releases its representative and companions beside it.
    assert any("companion_of" in i for i in items if i["kind"] == "cascade.release")
    settled = [i for i in items if i["kind"] == "evaluator.settled" and i["tier"] == 2]
    assert any(i["graded_by"] is not None for i in settled)


@pytest.mark.gate
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_the_grades_a_tier_above_delivers_count_and_none_vanishes(seed):
    """Essay II.III.b: evaluators are graded from above, tier upon tier; II.IV.c: a
    verdict rises a tier only after settling, through a window at least min_ratio times
    the loop beneath. A meta's judge settles no sooner than its own grade window, so a
    grade window of a fixed verdict_timeout_ticks closed before the tier above could
    read the meta: on these seeds 11 to 17 tier-three grades were delivered and 0 to 4
    counted. The grade window is now the read itself."""
    from factorylab.runtime.loop import Runtime

    rt = Runtime(_recursive(load_manifest("scripted")), events=200, seed=seed,
                 initial_balance_micro=None, ledger_path=None, router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    delivered = rt.stats.meta_verdicts
    assert delivered.get(3, 0) >= 5
    for tier, count in delivered.items():
        counted = [i for i in items if i["kind"] == "evaluator.meta_grade" and i["tier"] == tier]
        refused = [i for i in items if i["kind"] == "evaluator.grade_censored"
                   and i["tier"] == tier and "by" in i]
        # No delivered grade vanishes: it counts or is ledgered with its reason.
        assert len(counted) + len(refused) == count, tier
        # A clear majority of what the tier above delivers counts.
        assert 3 * len(counted) >= 2 * count, (tier, len(counted), count)
    # II.IV.c: a judgement unsettled at its window's release is withheld, not dropped.
    closed = [i["reason"] for i in items if i["kind"] == "evaluator.grade_censored"]
    assert not [r for r in closed if r.startswith("unsettled")]
    carried = {e for i in items if i["kind"] == "cascade.carry" for e in i["carried"]}
    risen = carried & {i["event_id"] for i in items if i["kind"] == "cascade.release"}
    assert carried and 2 * len(risen) >= len(carried), (len(risen), len(carried))


# --- the adversarial layer (M1, P5) --------------------------------------------------


@pytest.mark.parametrize(("judged", "p"), [(0.9, 0.2), (0.5, 0.5), (0.1, 0.95), (0.3, 0.0)])
def test_a_counter_verdict_is_scored_by_a_proper_rule_and_a_parrot_earns_half(judged, p):
    def expected(q):
        return p * counter_score(q, judged, 1.0) + (1 - p) * counter_score(q, judged, 0.0)

    grid = [i / 1000 for i in range(1001)]
    assert max(grid, key=expected) == pytest.approx(p, abs=1e-3)
    assert counter_score(judged, judged, 1.0) == counter_score(judged, judged, 0.0) == 0.5
    assert 0.0 <= counter_score(0.0, 1.0, 0.0) and counter_score(1.0, 0.0, 1.0) <= 1.0


def test_exposure_pays_only_a_miss_worse_than_the_judges_ordinary_misses():
    """The Wave 2 review, item 6: a merely miscalibrated judge earns an honest antagonist
    nothing extra; one fooled worse than usual pays it."""
    assert exposure_score([0.41], [0.41]) == pytest.approx(0.5)
    assert exposure_score([0.2], [0.41]) > 0.5 > exposure_score([0.6], [0.41])
    assert exposure_score([0.0], [1.0]) == 1.0 and exposure_score([1.0], [0.0]) == 0.0


def _adversarial_runtime(**provider):
    base = load_manifest("scripted")
    adversary = AssemblySeed(id="adv-a", model_id="fake-sonnet", accepts=("Verdict",),
                             role="adversary", max_tokens=128)
    manifest = replace(base, assemblies=(*base.assemblies, adversary))
    manifest.validate()
    rt = _consequence_runtime(provider=Population(**provider), manifest=manifest)
    rt._manage_reserve_window()
    return rt


def _produce_hold(rt):
    rt.n += 1
    handle = _consequence_decision(rt, "seed-decider", "verdict")
    rt._producer_step(Event(f"tick-{rt.n}", EventKind.TICK, rt.clock.now_ns, {"index": 0},
                            "test"), handle, SimpleNamespace(chosen="seed-decider"),
                      rt.queue.get(handle).deadline_ns)
    event = next(e for e in rt.internal if e.kind is EventKind.PRODUCER_RETURN
                 and e.payload["about_handle"] == handle)
    return handle, event


def _counter(rt, judge_handle, q):
    handle = _consequence_decision(rt, "adv-a", CH_COUNTER)
    rt._counter_step(rt.return_events[judge_handle], handle, SimpleNamespace(chosen="adv-a"),
                     rt.queue.get(handle).deadline_ns,
                     returned=Return(handle, {"verdict": q, "rationale": "counter"}, 0, "ok"))
    return handle


def test_an_adversarial_judge_is_paid_by_how_far_it_beat_the_verdict_it_read():
    rt = _adversarial_runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    _mids(rt, BTC="100")
    _producer, event = _produce_hold(rt)
    judge = _judge(rt, event, "eval-c")
    rt._settle_arrived_verdicts()
    counter = _counter(rt, judge, 0.1)
    (opened,) = _rows(rt, "counter.opened", handle=counter)
    assert opened["judge_q"] == 0.9 and opened["q"] == 0.1
    _horizon(rt, BTC="101")  # the declined buy would have paid: the verdict of 0.9 was wrong
    (judged,) = _rows(rt, "verdict.consequence", handle=judge)
    (settled,) = _rows(rt, "counter.settled", handle=counter)
    assert settled["y"] == judged["y"]
    assert settled["score"] == pytest.approx(counter_score(0.1, 0.9, judged["y"]))
    assert settled["score"] > 0.5
    (record,) = rt.queue.history(counter)
    assert record.definition_version == "counter-v1" and record.status is SettleStatus.SETTLED
    # The counter touched neither the judge's settlement nor the producer's.
    assert not _rows(rt, "evaluator.meta_grade", handle=judge)


def test_a_counter_on_a_return_the_world_never_measures_is_censored():
    rt = _adversarial_runtime(verdicts=(0.9,))
    _producer, event = _produce_hold(rt)  # a bare hold: no world outcome
    judge = _judge(rt, event, "eval-c")
    rt._settle_arrived_verdicts()
    assert judge not in rt.verdict_views  # no view is kept for a verdict nobody may counter
    counter = _counter(rt, judge, 0.2)
    assert not _rows(rt, "counter.opened", handle=counter)
    assert rt.queue.history(counter)[0].status is SettleStatus.CENSORED


def _capture_requests(rt):
    seen = []
    original = rt._request

    def record(handle, description, inputs, *args, **kwargs):
        seen.append((handle, inputs))
        return original(handle, description, inputs, *args, **kwargs)

    rt._request = record
    return seen


def test_a_counter_reads_the_world_the_judge_read_never_a_later_one():
    """The #132 review, item 1: the counter's edge may not be fresher information."""
    rt = _adversarial_runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    _mids(rt, BTC="100")
    _producer, event = _produce_hold(rt)
    seen = _capture_requests(rt)
    judge = _judge(rt, event, "eval-c")
    judged = next(inputs for handle, inputs in seen if handle == judge)
    frozen = rt.verdict_views[judge]
    # The world moves inside the tick: a print lands, a window's statistics change.
    _mids(rt, BTC="150")
    rt.stats.early_warning = {"window": 99, "series": {"verdict": []}}
    rt.clock.now_ns += 1
    counter_handle = _consequence_decision(rt, "adv-a", CH_COUNTER)
    rt._counter_step(rt.return_events[judge], counter_handle, SimpleNamespace(chosen="adv-a"),
                     rt.queue.get(counter_handle).deadline_ns)
    (_h, shown), = [(h, i) for h, i in seen if h == counter_handle]
    assert shown["early_warning"] == judged["early_warning"] == frozen["early_warning"]
    for key, value in judged["actor_context"].items():
        if key != "seats":
            assert shown["actor_context"][key] == value, key
    assert "150" not in str(shown["actor_context"])


def test_a_counter_made_after_its_verdicts_tick_is_censored():
    rt = _adversarial_runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    _mids(rt, BTC="100")
    _producer, event = _produce_hold(rt)
    judge = _judge(rt, event, "eval-c")
    rt.ticks_consumed += 1  # a tick later: the world has moved on since the verdict
    counter = _counter(rt, judge, 0.1)
    assert not _rows(rt, "counter.opened", handle=counter)
    (censored,) = _rows(rt, "evaluation.censored", handle=counter)
    assert "tick it was given" in censored["reason"]


@pytest.mark.gate
def test_adversarial_judges_are_drawn_when_the_verdict_is_given():
    from factorylab.runtime.loop import Runtime

    base = load_manifest("scripted")
    adversary = AssemblySeed(id="adv-a", model_id="fake-sonnet", accepts=("Verdict",),
                             role="adversary", max_tokens=128)
    manifest = replace(base, assemblies=(*base.assemblies, adversary),
                       evaluation=replace(base.evaluation, adversarial_share=0.5))
    rt = Runtime(manifest, events=60, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    rt.run()
    items = rt.ledger._recovery_items()
    opened = [i for i in items if i["kind"] == "counter.opened"]
    assert opened
    # Every counter the router drew read its verdict in the tick it was given, on the
    # judge's frozen view: none was censored for reading late.
    assert not [i for i in items if i["kind"] == "evaluation.censored"
                and "tick it was given" in i["reason"]]
    assert not rt.verdict_views  # every frozen view was consumed when its verdict was routed


def test_the_adversarial_share_caps_counters_and_no_requester_can_hire_one():
    rt = _adversarial_runtime()
    capped = rt._cap_adversarial({"adv-a": 0.9, "meta-a": 0.05, NOOP: 0.05})
    assert capped["adv-a"] == pytest.approx(rt.ev.adversarial_share)
    assert rt._commissioned_judge_refusal("adv-a") is not None
    assert "adv-a" not in rt._request_universe("CounterVerdict")


def test_the_edition6_antagonists_are_routed_by_both_learner_types():
    from factorylab.learners.exp3 import EXP3
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.routing import _KeyedLearner

    world = load_manifest("worlds/edition6-testnet-rehearsal.toml")
    # Offline: the fake venue, the OpenRouter menu only (no seat thinks on Venice), and a
    # fixed output allowance in place of the provider's own.
    world = replace(world, exchange=replace(world.exchange, kind="fake", spot_pairs=()),
                    models=tuple(replace(m, provider="fake") for m in world.models
                                 if m.provider != "venice"),
                    assemblies=tuple(replace(a, max_tokens=256) for a in world.assemblies))
    rt = Runtime(world, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    (tick,) = rt.routers["Tick"]
    (update,) = rt.routers["WorldUpdate"]
    assert isinstance(tick.learner, _KeyedLearner) and "antagonist-core" in tick.universe
    assert isinstance(update.learner, EXP3) and "antagonist" in update.universe
    assert all(isinstance(s.learner, EXP3) for s in rt.routers["ProducerReturn"])


# --- the chaos actuator: real faults that cannot move money --------------------------


def _chaotic(**rates):
    base = load_manifest("scripted")
    manifest = replace(base, chaos=replace(base.chaos, **rates))
    manifest.validate()
    rt = _consequence_runtime(manifest=manifest)
    rt._manage_reserve_window()
    return rt


def _money(rt):
    return (rt.wallet.balance, rt.wallet.available, rt.budget.unallocated(),
            {a: rt.budget.entitlement(a) for a in rt.assemblies})


def test_a_zero_rate_draws_nothing_and_injects_nothing():
    rt = _chaotic()
    state = rt.rng.getstate()
    rt.ticks_consumed += 1
    rt._chaos_tick()
    assert rt.rng.getstate() == state and set(rt.chaos_tick) == {"tick"}


def test_a_faulted_venue_read_answers_unavailable_before_anything_is_metered():
    rt = _chaotic(venue_unavailable=0.5)
    rt.ticks_consumed += 1
    rt.chaos_tick = {"tick": rt.ticks_consumed, "venue_unavailable": True}
    handle = _consequence_decision(rt, "seed-decider", "verdict")
    before, items = _money(rt), len(rt.ledger._recovery_items())
    result, cost = rt._run_tool("seed-decider", handle, {"tool": "venue.mids", "args": {}})
    assert result == {"status": "unavailable", "error": "VenueUnavailable"} and cost == 0
    assert _money(rt) == before
    new = [i["kind"] for i in rt.ledger._recovery_items()[items:]]
    assert new == ["chaos.applied"]


def test_a_venue_write_is_never_faulted():
    rt = _chaotic(venue_unavailable=0.5)
    rt.ticks_consumed += 1
    rt.chaos_tick = {"tick": rt.ticks_consumed, "venue_unavailable": True}
    spec = rt.tool_specs["venue.place_limit"]
    for tool in rt.CONSEQUENCE_WRITES:
        assert rt._chaos_tool_fault(tool, rt.tool_specs.get(tool, spec), "h") is None


def test_a_withheld_population_tool_result_is_neither_run_nor_charged(monkeypatch):
    rt = _chaotic(tool_withheld=0.5)
    rt.population_tools["half"] = object()
    rt.tool_specs["half"] = {"id": "half", "kind": "population", "price_micro_per_call": 50,
                             "args_schema": {"type": "object"}}
    monkeypatch.setattr(rt, "_allowed_tools", lambda _a: {"half"})
    monkeypatch.setattr(rt.rng, "random", lambda: 0.0)
    ran = []
    monkeypatch.setattr(rt.tool_runner, "run", lambda *a, **k: ran.append(a) or {"x": 1})
    handle = _consequence_decision(rt, "seed-decider", "verdict")
    before = _money(rt)
    result, cost = rt._run_tool("seed-decider", handle, {"tool": "half", "args": {}})
    assert result["error"] == "result withheld" and cost == 0 and not ran
    assert _money(rt) == before
    assert rt.stats.chaos_faults == {"tool_withheld": 1}


def test_a_connector_timeout_fetches_nothing_and_charges_nothing(monkeypatch):
    rt = _chaotic(connector_timeout=0.5)
    monkeypatch.setattr(rt.rng, "random", lambda: 0.0)
    monkeypatch.setattr(rt.registry, "get", lambda _cid: SimpleNamespace(
        kind="connector", input_schema={"origin": "https://example.org"}))
    monkeypatch.setattr(rt.connector_proxy, "validate", lambda *_a: None)
    fetched = []
    monkeypatch.setattr(rt.connector_proxy, "fetch", lambda *a: fetched.append(a))
    before = _money(rt)
    result, cost = rt._fetch_connector("seed-decider", "h", {"id": "c", "path": "/x"})
    assert result == {"error": "timeout"} and cost == 0 and not fetched
    assert _money(rt) == before and not rt.connector_calls


def test_stale_mids_age_only_what_seats_are_shown():
    rt = _chaotic(stale_mids=0.5)
    rt.recent_mids["BTC"] = deque([{"t_s": 1, "mid": "100"}, {"t_s": 5, "mid": "101"}],
                                  maxlen=20)
    rt.clock.now_ns = 5_000_000_000
    rt.ticks_consumed += 1
    rt.chaos_tick = {"tick": rt.ticks_consumed, "stale_mids": rt.clock.now_ns}
    assert [p["mid"] for p in rt._seat_recent_mids()["BTC"]] == ["100"]
    # The world's own record, which declined trades are priced from, keeps every print,
    # and the venue read behind the collateral check never consults the fault.
    from factorylab.runtime.grounded import latest_mids

    assert dict(latest_mids(rt))["BTC"] == "101"
    assert rt._tick_mids() == rt.exchange.mids()
    payload = {"mids": {"BTC": "101"}, "account": {"equity_usd": "1"}}
    rt._seat_tick_view(payload)
    assert payload["mids"] == {"BTC": "100"} and payload["account"] == {"equity_usd": "1"}


def test_the_chaos_module_reaches_no_money_path():
    """Physics, not policy: the actuator's source names no wallet, meter, budget,
    venue write, treasury, custody or consequence book."""
    import ast
    import inspect

    from factorylab.runtime import chaos

    names = {node.attr for node in ast.walk(ast.parse(inspect.getsource(chaos)))
             if isinstance(node, ast.Attribute)}
    forbidden = {"wallet", "meter", "budget", "exchange", "treasury", "consequences",
                 "_venue_write", "_order_collateral", "settle", "_settle_priced", "queue"}
    assert not names & forbidden, names & forbidden


@pytest.mark.gate
def test_a_world_under_heavy_chaos_conserves_money_and_records_every_fault():
    from factorylab.runtime.loop import run_world

    base = load_manifest("scripted")
    manifest = replace(base, chaos=replace(base.chaos, venue_unavailable=0.5, stale_mids=0.5,
                                           tool_withheld=0.5, connector_timeout=0.5))
    summary = run_world(manifest, events=60, seed=3)
    assert summary["wallet_conservation"] is True and summary["ledger_verify"] is True
    faults = summary["stats"]["chaos_faults"]
    assert faults.get("venue_unavailable", 0) > 5 and faults.get("stale_mids", 0) > 5


def test_a_fault_is_a_failure_a_forecast_can_settle_on():
    tick = WindowFacts(10, 10, 10, ({"kind": "Tick", "payload": {},
                                     "faults": [{"fault": "stale_mids", "seat": None}]},))
    call = WindowFacts(10, 10, 10, ({"kind": "Tick", "payload": {},
                                     "faults": [{"fault": "tool_withheld", "seat": "x"}]},))
    quiet = WindowFacts(10, 10, 10, ({"kind": "Tick", "payload": {}},))
    params = {"horizon_events": 5}
    assert Observer().observe("failure_within", params, tick) == 1
    # Without the runtime's count, only a tick's fault (no seat can draw it) is evidence.
    assert Observer().observe("failure_within", params, call) == 0
    assert Observer().observe("failure_within", params, quiet) == 0
    counted = replace(call, independent_failures=0)
    assert Observer().observe("failure_within", params, counted) == 0


def test_a_forecaster_cannot_manufacture_its_own_failure(monkeypatch):
    """The #132 review, item 4: a fault on the forecaster's own call, or its own
    lineage's rejected order, is not a failure it may be paid for predicting."""
    rt = _chaotic(tool_withheld=0.5)
    rt.n = len(rt.events_log) - 1
    rt.handle_to_assembly.update({"h-own": "eval-a", "h-other": "eval-b"})
    rt._chaos_fault("tool_withheld", handle="h-own", tool="t")
    own_only = (dict(rt.events_log[rt.n]),)
    assert rt._independent_failures("eval-a", own_only) == 0
    assert rt._independent_failures("eval-b", own_only) == 1
    rt._chaos_fault("tool_withheld", handle="h-other", tool="t")
    assert rt._independent_failures("eval-a", (dict(rt.events_log[rt.n]),)) == 1
    rt.events_log[rt.n]["faults"] = []
    rt._chaos_fault("stale_mids")
    tick_fault = (dict(rt.events_log[rt.n]),)
    assert rt._independent_failures("eval-a", tick_fault) == 1
    monkeypatch.setattr(rt, "_order_owner", lambda order_id: "h-own")
    rejected = ({"kind": "OrderRejected", "payload": {"order_id": "7"}},)
    assert rt._independent_failures("eval-a", rejected) == 0
    assert rt._independent_failures("eval-b", rejected) == 1


# --- early warning: live, and the evaluators' (R3, M2) -------------------------------


@pytest.mark.gate
def test_early_warning_is_computed_at_every_close_and_shown_only_to_evaluators():
    from factorylab.runtime.loop import Runtime

    base = load_manifest("scripted")
    manifest = replace(base, evaluation=replace(base.evaluation, multi_judge_share=1.0))
    rt = Runtime(manifest, events=150, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1)
    requests = []
    original = rt._request

    def record(handle, description, inputs, *args, **kwargs):
        requests.append((description, inputs))
        return original(handle, description, inputs, *args, **kwargs)

    rt._request = record
    rt.run()
    k = rt.m.immune.k
    closes = [i for i in rt.ledger._recovery_items() if i["kind"] == "ews.window"]
    assert len(closes) >= 4 * k
    table = rt.stats.early_warning
    assert table["spans_windows"] == [k, 2 * k, 4 * k]
    assert {"verdict", "conformity", "consequence", "disagreement", "balance"} <= set(
        table["series"])
    assert any(s["variance"] is not None for s in table["series"]["verdict"])
    judged = [inputs for description, inputs in requests if description.startswith("Give")]
    produced = [inputs for description, inputs in requests if description.startswith("Respond")]
    assert judged and all(inputs["early_warning"]["window"] is not None for inputs in judged[-3:])

    def shown(value, key):
        """Every value published under ``key`` anywhere in a request's inputs."""
        if isinstance(value, dict):
            return [v for k, v in value.items() if k == key] + [
                x for v in value.values() for x in shown(v, key)]
        if isinstance(value, list):
            return [x for v in value for x in shown(v, key)]
        return []

    assert produced
    for inputs in produced:
        assert not shown(inputs, "early_warning")
        for values in shown(inputs, "last_closed_window_values"):
            assert not set(values) & {"ews_variance", "ews_autocorrelation"}
    assert not set(rt._public_observations()["last_closed_window_values"]) & {
        "ews_variance", "ews_autocorrelation"}


class _ProcessDeath(BaseException):
    pass


@pytest.mark.slow
def test_a_world_under_chaos_resumes_mid_tick_and_replays_identically(tmp_path):
    """A fault is drawn from the checkpointed stream and the tick's faults are
    checkpointed: a process that dies right after one resumes to the same world."""
    from factorylab.runtime.loop import Runtime, run_world
    from factorylab.runtime.resume import resume_world

    base = load_manifest("scripted")
    manifest = replace(base, chaos=replace(base.chaos, venue_unavailable=0.4, stale_mids=0.4,
                                           tool_withheld=0.4, connector_timeout=0.4))
    path = tmp_path / "chaos.jsonl"
    rt = Runtime(manifest, events=20, seed=1, initial_balance_micro=None,
                 ledger_path=str(path), router_gamma=.1)
    append = rt.ledger.append
    seen = []

    def crash_after_a_fault(entry):
        seq = append(entry)
        if entry["kind"] == "chaos.fault" and len(seen) < 3:
            seen.append(entry)
            if len(seen) == 3:
                raise _ProcessDeath
        return seq

    rt.ledger.append = crash_after_a_fault
    with pytest.raises(_ProcessDeath):
        rt.run()
    resumed = resume_world(manifest, str(path))
    resumed["stats"]["resumes"] = 0
    assert resumed == run_world(manifest, events=20, seed=1)


# --- the #132 review: compute share, a lost majority, and whole chains -----------------


def test_evaluator_compute_share_is_an_observation_a_card_may_price():
    from factorylab.runtime.observations import observation_for
    from factorylab.runtime.pricing import MeasureWindow

    share = observation_for("evaluator_compute_share")
    assert share.measure(MeasureWindow(1, 100, compute_spend_micro=400,
                                       evaluator_spend_micro=300)) == 0.75
    assert share.measure(MeasureWindow(1, 100)) is None


@pytest.mark.gate
def test_evaluator_compute_share_is_measured_every_window_and_published():
    from factorylab.runtime.loop import Runtime

    base = load_manifest("scripted")
    manifest = base  # every window is derived from the loop it commands (time audit T1)
    rt = Runtime(manifest, events=60, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    windows = [i for i in rt.ledger._recovery_items() if i["kind"] == "price.window"]
    shares = [w["observations"].get("evaluator_compute_share") for w in windows]
    assert any(s is not None and 0 < s < 1 for s in shares)
    assert "evaluator_compute_share" in rt._public_observations()["last_closed_window_values"]


def test_a_lost_evaluator_majority_is_ledgered_never_refused():
    rt = _consequence_runtime()
    for seat in ("eval-a", "eval-b"):
        rt._retire_assembly(seat, "p")
    assert not _rows(rt, "population.evaluator_majority")
    rt._retire_assembly("eval-c", "p")  # three evaluators left against three producers
    (lost,) = _rows(rt, "population.evaluator_majority")
    assert lost["held"] is False and (lost["evaluators"], lost["producers"]) == (3, 3)
    rt._retire_assembly("eval-d", "p")
    assert len(_rows(rt, "population.evaluator_majority")) == 1  # once per change


def test_a_meta_avoids_the_judges_family_and_the_producers():
    rt = _consequence_runtime()
    rt.handle_to_assembly.update({"p": "seed-decider", "j": "eval-b"})  # opus, sonnet
    rt.decision_subjects["j"] = "p"
    verdict = Event("v", EventKind.VERDICT, 0, {"about_handle": "p", "evaluator_handle": "j"},
                    "runtime")
    assert rt._chain_families(verdict) == {"fake-opus", "fake-sonnet"}
    assert rt._chain_families(verdict, depth=1) == {"fake-sonnet"}
    # A meta about to grade that judge is graded above only off its own family and the
    # judge's: the tier above respects family too.
    higher = rt._higher_tier_universe("meta-a", verdict)
    assert all(rt._family(a) not in {"fake-haiku", "fake-sonnet"} for a in higher)


def test_a_roster_whose_metas_cannot_read_a_chain_is_refused():
    raw = _edition6()
    for seat in raw["assemblies"]:
        if seat["role"] == "meta":
            seat["model_id"] = "openai/gpt-6-luna"
    raw["assemblies"].append({**next(a for a in raw["assemblies"] if a["id"] == "meta-audit"),
                              "id": "meta-extra", "model_id": "xiaomi/mimo-v2.6-flash"})
    with pytest.raises(ValueError, match="no meta reads a Verdict by a xiaomi judge on a "
                                         "gpt return off both families"):
        manifest_from_dict(raw)


def test_an_adversary_that_can_read_no_chain_is_refused():
    base = load_manifest("scripted")
    blind = AssemblySeed(id="adv-a", model_id="fake-haiku", accepts=("Verdict",),
                         role="adversary", max_tokens=128)
    only_haiku = replace(base, assemblies=tuple(
        replace(a, model_id="fake-haiku") if a.role == "producer" else a
        for a in base.assemblies) + (blind,))
    problems = only_haiku.evaluator_population_problems()
    assert any("adversarial judge adv-a" in p for p in problems) or any(
        "fake-haiku" in p for p in problems)


@pytest.mark.gate
def test_a_draw_with_every_reader_barred_is_ledgered_and_counted():
    from factorylab.runtime.loop import Runtime

    rt = Runtime(load_manifest("scripted"), events=40, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    # The meta off every chain retires: verdicts whose chain holds the other meta's
    # family have no reader left, and the kernel says so.
    rt._retire_assembly("meta-b", "test")
    rt.run()
    barred = _rows(rt, "route.barred")
    assert barred and rt.stats.route_barred == len(barred)
    assert all(row["event_kind"] == "Verdict" for row in barred)


# --- the #132 Codex review: two routers of a judged kind; the adversary scope ----------


@pytest.mark.gate
def test_two_routers_of_a_judged_kind_never_draw_two_judges_of_one_family():
    from factorylab.runtime.loop import Runtime

    base = load_manifest("scripted")
    manifest = replace(base, evaluation=replace(base.evaluation, multi_judge_share=0.0))
    rt = Runtime(manifest, events=80, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.5)
    rt._build_router("ProducerReturn", "exp3", 0.5, replace=False)
    assert len(rt.routers["ProducerReturn"]) == 2
    rt.run()
    means = _rows(rt, "verdict.mean")
    assert means
    for row in means:
        judges = [rt.handle_to_assembly[h] for h in row["judges"]]
        families = [rt._family(j) for j in judges]
        assert len(set(judges)) == len(judges)
        assert len(set(families)) == len(families)
        assert rt._family(rt.handle_to_assembly[row["handle"]]) not in families


def test_counter_verdicts_are_measured_and_priced_in_the_adversary_scope(monkeypatch):
    from factorylab.charter.amendment import proposed_answers_for
    from factorylab.charter.charter import MetricCard, MetricWindow
    from factorylab.charter.measurement import preflight_measurement
    from factorylab.cortex.registration import measured_role

    assert measured_role("CounterVerdict") == "adversary"
    assert proposed_answers_for("adversary", "c") == "adversary"
    card = MetricCard(id="counter-form", norm="n", description="d", units="fraction",
                      window=MetricWindow("returns", 5, "role"),
                      acceptable_region="at least 0.9", observation="well_formed_rate",
                      answers_for="adversary")
    preflight_measurement(card)  # the scope has samples to measure: it is real
    rt = _adversarial_runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(0.9,))
    priced = []
    original = rt._penalty_for

    def record(cards, handle=None, **kwargs):
        priced.append((cards, handle))
        return original(cards, handle, **kwargs)

    monkeypatch.setattr(rt, "_penalty_for", record)
    _mids(rt, BTC="100")
    _producer, event = _produce_hold(rt)
    judge = _judge(rt, event, "eval-c")
    rt._settle_arrived_verdicts()
    counter = _counter(rt, judge, 0.1)
    _horizon(rt, BTC="101")
    assert ("adversary", counter) in priced

