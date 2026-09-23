"""The factory's clock (Chapter II §IV.b-c; time audit T1-T13, versioning P5).

Loop periods are ratios, not constants; every loop counts world ticks consumed,
and wall time converts only for display and money rails. Each test pins one
relation: an outer loop at least ``min_ratio`` times the measured loop it
commands, with its own continuous jitter; a cutoff in ticks; a price moved only
by new evidence; exploration protected for its measured consequence period; the
novelty share as a flow; a speed limit on refactoring; governance's settling time
and viability; the capital loop measured; a model call bounded by the tick.
"""

from dataclasses import replace
from fractions import Fraction
from math import ceil
from types import SimpleNamespace

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.cadence import GovernanceCadence
from factorylab.runtime.clockwork import (
    Clockwork,
    deadline_ticks,
    derived_period,
    jitter_draw,
    tick_ns,
    ticks_for,
)
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _open(rt, *, horizon=None, owner="seed-decider", **timing):
    """One decision the way a router opens one, its cutoff stated in ticks."""
    return rt.queue.open(
        actor=owner, event_id=f"clock-{rt.stats.decisions}",
        propensity=PropensityRecord((owner,), (1.0,), owner, 0, owner, "t"),
        channel="verdict", parent_handle=None, cost_ceiling=0,
        **({"horizon_ticks": horizon} if horizon is not None else timing))


# --- the relations themselves --------------------------------------------------------


def test_a_derived_period_is_a_floor_of_min_ratio_with_continuous_jitter_of_its_own():
    draws = [jitter_draw(7, "price", n) for n in range(1, 200)]
    assert all(0 <= d < 1 for d in draws) and len(set(draws)) == len(draws)
    assert draws != [jitter_draw(7, "immune", n) for n in range(1, 200)]  # its own stream
    assert draws == [jitter_draw(7, "price", n) for n in range(1, 200)]  # reproducible
    periods = [derived_period(3, 0.2, 4, d) for d in draws]
    assert min(periods) >= 12 and max(periods) <= 14.4 and len(set(periods)) > 100
    for bad in ((2, 0.2, 4, 0.5), (3, -1, 4, 0.5), (3, 0.2, 0, 0.5), (3, 0.2, 4, 1.0)):
        with pytest.raises(ValueError):
            derived_period(*bad)


def test_an_outer_loop_waits_for_an_inner_loop_that_slowed_after_its_draw():
    """Time audit T2, the runtime half: the ratio is checked when the loop fires."""
    clock = Clockwork(min_ratio=3, jitter_fraction=0.0, seed=1)
    assert clock.due("price", 0, 1)  # a loop that never fired opens its first period
    schedule = clock.fire("price", 0, 2)
    assert schedule["due"] == 6 and clock.period("price") == 6
    assert not clock.due("price", 5, 2) and clock.due("price", 6, 2)
    assert not clock.due("price", 6, 3)  # the inner loop is slower now: 3 x 3 = 9
    assert clock.due("price", 9, 3)


def test_a_cutoff_is_its_horizon_plus_a_ratio_slack_and_ticks_convert_at_the_slower_gap():
    assert [deadline_ticks(h, 3) for h in (0, 1, 3, 20, 60, 200)] == [1, 2, 4, 27, 80, 267]
    declared = SimpleNamespace(interval_ns=10)
    assert tick_ns(declared) == 10 and ticks_for(25, declared) == 3
    slow = SimpleNamespace(interval_ns=10, measured_interval_ns=lambda: 25)
    fast = SimpleNamespace(interval_ns=10, measured_interval_ns=lambda: 4)
    assert tick_ns(slow) == 25 and tick_ns(fast) == 10  # measurement only slows it
    assert ticks_for(0, slow) == 1


def test_a_cutoff_counts_ticks_and_never_wall_time():
    """Time audit T3: a stalled loop or a slow tick does not expire a decision."""
    rt = make_runtime()
    handle = _open(rt, horizon=5)
    assert rt.queue.deadline_tick(handle) == rt.ticks_consumed + 7
    assert rt.queue.get(handle).deadline_ns == rt.clock.now_ns + 7 * rt.tick_clock.interval_ns
    rt.clock.now_ns += 10**15  # eleven days of wall time, no tick
    assert rt.queue.expire_due() == []
    rt.ticks_consumed += 6
    assert rt.queue.expire_due() == []
    rt.ticks_consumed += 1
    assert rt.queue.expire_due() == [handle]
    assert rt.queue.get(handle).status is SettleStatus.TIMED_OUT
    # A wall-clock deadline a caller states is converted once, at the delivered tick.
    other = _open(rt, deadline_ns=rt.clock.now_ns + 25 * rt.tick_clock.interval_ns // 10)
    assert rt.queue.deadline_tick(other) == rt.ticks_consumed + 3


def test_a_settled_decision_feeds_its_roles_settle_loop_and_a_scored_one_its_scored_loop():
    rt = make_runtime()
    scored, censored = _open(rt, horizon=5), _open(rt, horizon=5)
    rt.ticks_consumed += 4
    rt.queue.settle(scored, channel="verdict", score=0.7, status=SettleStatus.SETTLED,
                    definition_version="t", sampling_ref=None)
    rt.ticks_consumed += 2
    rt.queue.settle(censored, channel="verdict", score=0.0, status=SettleStatus.CENSORED,
                    definition_version="t", sampling_ref=None)
    assert rt.clockwork.latencies["settle:producer"] == [4, 6]
    assert rt.clockwork.latencies["scored:producer"] == [4]
    rt.queue.forget_ticks()
    assert scored not in rt.decision_ticks and censored not in rt.decision_ticks


# --- prices (T2) -----------------------------------------------------------------------


def _returns_runtime():
    """The scripted charter with every card over its latest single response per scope."""
    seed = load_manifest("scripted")
    window = MetricWindow("returns", 1, "role")
    charter = replace(seed.charter, cards=tuple(replace(c, window=window)
                                               for c in seed.charter.cards))
    rt = Runtime(replace(seed, charter=charter), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt._derive_regions()
    return rt


def _close(rt, *, ticks, responses=1):
    """Close one window ``ticks`` after the last, holding ``responses`` new responses."""
    from factorylab.cortex.request import Return

    rt.n += 10
    rt.ticks_consumed += ticks
    rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=responses, ok=0)
    for i in range(responses):
        handle = f"h-{rt.n}-{i}"
        rt.card_samples.returned(handle=handle, assembly="seed-decider", role="producer",
                                 window=rt.window.index, ret=Return(handle, {}, 10, "failed"))
    rt._close_price_window()


def _updates(rt, card):
    return [u for u in _items(rt, "price.update") if u["card_id"] == card]


def test_a_price_moves_only_on_a_new_sample_and_no_faster_than_its_sample_loop():
    rt = _returns_runtime()
    card = "well_formed_rate"
    _close(rt, ticks=3)
    assert len(_updates(rt, card)) == 1
    _close(rt, ticks=1, responses=0)  # measured on the old response, nothing new in scope
    skipped = [s for s in _items(rt, "price.skipped") if s["card_id"] == card]
    assert skipped[-1]["reason"] == "no_new_sample" and len(_updates(rt, card)) == 1
    _close(rt, ticks=1)  # new evidence, but less than min_ratio sample loops since the move
    skipped = [s for s in _items(rt, "price.skipped") if s["card_id"] == card]
    assert skipped[-1]["reason"] == "ratio" and len(_updates(rt, card)) == 1
    _close(rt, ticks=rt.m.timing.min_ratio)
    assert len(_updates(rt, card)) == 2
    assert rt.card_clock[card] == rt.ticks_consumed


def test_the_measurement_window_is_the_price_loops_derived_period_in_ticks():
    """Time audit T1, T11: no window is cast. The launch opens the price loop and every
    outer loop's first period, each on its own schedule, none sharing a boundary."""
    rt = make_runtime()
    rt._manage_reserve_window()
    loops = {i["loop"]: i for i in _items(rt, "clock.loop")}
    assert set(loops) == {"price", "immune", "sampling"}
    assert loops["price"]["period_ticks"] >= rt.m.timing.min_ratio
    assert loops["immune"]["period_ticks"] >= rt.m.timing.min_ratio * loops["immune"][
        "inner_ticks"] and loops["immune"]["inner_ticks"] == ceil(loops["price"]["period_ticks"])
    assert loops["sampling"]["inner_ticks"] == rt.cadence.consequence_period_events()
    assert len({i["due_tick"] for i in loops.values()}) == 3
    index = rt.window.index
    rt.clock.now_ns += 10**15  # wall time alone closes nothing
    rt._manage_reserve_window()
    assert rt.window.index == index
    rt.ticks_consumed = rt.window.due_tick
    rt.clock.now_ns += 1
    rt._manage_reserve_window()
    assert rt.window.index == index + 1


# --- exploration (T5, T6) --------------------------------------------------------------


def test_the_novelty_share_is_a_flow_per_measured_consequence_period():
    rt = make_runtime()
    rt._manage_reserve_window()
    first = _items(rt, "novelty.window")[-1]
    period = rt.cadence.consequence_period_events()
    drawn = ceil(_items(rt, "clock.loop")[0]["period_ticks"])
    assert Fraction(first["accrued"]) == Fraction(min(drawn, period), period)
    assert first["amount"] == first["cap"] * min(drawn, period) // period
    for _ in range(20):  # however many windows open, never more than one period's share
        rt.ticks_consumed = rt.window.due_tick
        rt.clock.now_ns += 1
        rt._manage_reserve_window()
        assert rt.reserve.remaining() <= _items(rt, "novelty.window")[-1]["cap"]


def test_a_trial_is_protected_for_min_ratio_measured_consequence_periods_in_ticks(
        monkeypatch):
    rt = make_runtime()
    monkeypatch.setattr(rt.registry, "get", lambda _aid: SimpleNamespace(provenance="agent"))
    rt.stats.registered_tick["newcomer"] = rt.ticks_consumed
    patience = rt._patience()
    assert patience == rt.m.timing.min_ratio * rt.cadence.consequence_period_events()
    rt.ticks_consumed += patience - 1
    rt.stats.reserve_windows += 1000  # windows are not the unit: nothing ends here
    assert rt._unhistoried("newcomer")
    rt.ticks_consumed += 1
    assert not rt._unhistoried("newcomer")


def test_a_learning_death_grant_lives_its_patience_and_is_not_reissued_while_it_lives():
    rt = make_runtime()
    rt.stats.pathologies = {"learning_death": True}
    rt._issue_novelty_grant()
    grant = rt.novelty_grant
    assert grant["until_tick"] == rt.ticks_consumed + rt._patience()
    rt.ticks_consumed += 5
    rt._issue_novelty_grant()
    assert len(_items(rt, "novelty.grant")) == 1 and rt.novelty_grant is grant
    assert rt._novelty_grant_open("seed-decider")
    rt.ticks_consumed = grant["until_tick"]
    assert not rt._novelty_grant_open("seed-decider")
    rt._issue_novelty_grant()  # lapsed and still flagged: the next one
    assert len(_items(rt, "novelty.grant")) == 2


def test_a_grown_menu_waits_one_measured_round_period_for_its_epoch(monkeypatch):
    """Time audit T6: a speed limit on refactoring; a registration joins a routed kind
    at most once per period of that router's own rounds."""
    rt = make_runtime()
    kind = "Tick"
    state = rt.routers[kind][0]
    rt.clockwork.record(f"router:{kind}", 5)
    rt.clockwork.loops[f"epoch:{kind}"] = {"opened": rt.ticks_consumed, "due": 0,
                                           "period": 1.0, "inner": 5, "fires": 1}
    grown = [*state.universe, "newcomer"]
    monkeypatch.setattr(rt, "_universe_for", lambda k, ev=None: list(grown))
    rt._open_epoch(kind)
    assert "newcomer" not in state.universe and kind in rt.pending_epochs
    assert _items(rt, "epoch.deferred")[-1]["inner_ticks"] == 5
    rt.ticks_consumed += 4
    rt._open_pending_epochs()
    assert "newcomer" not in rt.routers[kind][0].universe
    rt.ticks_consumed += 1
    rt._open_pending_epochs()
    assert "newcomer" in rt.routers[kind][0].universe and kind not in rt.pending_epochs


# --- governance (T2, T7) ---------------------------------------------------------------


def _cadence(backstop=4):
    ledger = Ledger()
    return ledger, GovernanceCadence(ledger, sample=50, min_ratio=3, backstop=backstop,
                                     min_support=1)


def test_settling_time_is_measured_after_an_activation_and_joins_the_slowest_loop():
    ledger, cadence = _cadence()
    for tick, value in enumerate((0.50, 0.52, 0.51), start=1):
        cadence.advance(tick)
        cadence.observe_scores({"card": value})
    cadence.advance(4)
    cadence.activated("am", 0, 1)
    for tick, value in ((6, 0.9), (9, 0.70), (12, 0.71), (15, 0.70)):
        cadence.advance(tick)
        cadence.observe_scores({"card": value})
    settling = [i for i in ledger._recovery_items() if i["kind"] == "governance.settling"]
    assert settling and settling[-1]["settled"] and settling[-1]["settling_ticks"] == 11
    assert cadence.slowest_period_events() == 11  # above the four-tick backstop


def test_an_unsettled_revision_holds_governance_only_min_ratio_consequence_periods():
    ledger, cadence = _cadence()
    for tick, value in enumerate((0.50, 0.50, 0.50), start=1):
        cadence.advance(tick)
        cadence.observe_scores({"card": value})
    cadence.activated("am", 0, 1)
    cadence.advance(10)
    assert cadence.slowest_period_events() == 7  # its age: unfinished is not fast
    for tick in range(11, 30):
        cadence.advance(tick)
        cadence.observe_scores({"card": float(tick % 2)})  # never settles
    settling = [i for i in ledger._recovery_items() if i["kind"] == "governance.settling"]
    assert settling and settling[0]["settled"] is False
    assert settling[0]["settling_ticks"] >= 3 * cadence.consequence_period_events()


def test_governance_is_ledgered_nonviable_when_its_period_outlasts_the_run_or_the_world():
    manifest = load_manifest("scripted")  # three backstops of 20 ticks: 60 ticks
    short = Runtime(manifest, events=50, seed=1, initial_balance_micro=None, ledger_path=None,
                    router_gamma=0.1, exchange=FakeExchange(), provider=ScriptedProvider())
    short._manage_reserve_window()
    nonviable, = _items(short, "governance.nonviable")
    assert nonviable["needed_ticks"] == 60 and nonviable["run_ticks"] == 50
    long = Runtime(manifest, events=500, seed=1, initial_balance_micro=None, ledger_path=None,
                   router_gamma=0.1, exchange=FakeExchange(), provider=ScriptedProvider())
    long._manage_reserve_window()
    assert not _items(long, "governance.nonviable") and long.governance_viable
    lagging = replace(manifest, timing=replace(manifest.timing, world_repricing_ns=59 * 10**9))
    world = Runtime(lagging, events=500, seed=1, initial_balance_micro=None, ledger_path=None,
                    router_gamma=0.1, exchange=FakeExchange(), provider=ScriptedProvider())
    world._manage_reserve_window()
    assert _items(world, "governance.nonviable")[-1]["world_ticks"] == 59


def test_a_promise_is_graded_no_sooner_than_min_ratio_consequence_periods():
    rt = make_runtime()
    assert rt._policy_floor() == rt.m.timing.min_ratio * rt.cadence.consequence_period_events()


# --- money rails (T1, T13) -------------------------------------------------------------


def test_the_capital_loop_is_measured_and_no_conversion_strands_faster_than_it(monkeypatch):
    rt = make_runtime()
    floor = rt.m.treasury.forward_wait_ticks
    latency = (floor + 40) * rt.tick_clock.interval_ns
    results = iter([[{"status": "confirmed", "transfer_id": "t-0", "latency_ns": latency}],
                    []])
    monkeypatch.setattr(rt.treasury, "tick", lambda now: next(results))
    rt._tick_treasury()
    capital, = _items(rt, "cadence.capital")
    assert capital["latency_ticks"] == floor + 40
    assert rt.cadence.slowest_period_events() >= floor + 40
    assert rt.clockwork.latencies["capital"] == [floor + 40]
    rt._tick_treasury()
    assert rt.treasury.forward_wait_ticks == floor + 40
    assert rt.treasury.tick_index == rt.ticks_consumed


def test_the_treasury_caps_count_their_own_wall_clock_window_not_the_pricing_window():
    rt = make_runtime()
    span = rt.m.treasury.cap_window_ns
    start = rt.clock.now_ns
    assert rt._cap_window() == 1
    rt.stats.reserve_windows += 100  # the pricing window renews nothing
    rt.clock.now_ns = start + span - 1
    assert rt._cap_window() == 1
    rt.clock.now_ns = start + span
    assert rt._cap_window() == 2
    legacy = make_runtime()
    legacy.treasury.open_window(5)  # a checkpoint from before the anchor
    assert legacy._cap_window() == 5


# --- requisite velocity (T8) -----------------------------------------------------------


class _Expiring:
    name = "expiring"

    def __init__(self):
        self.seen = []

    def complete(self, req):
        from factorylab.world.openai_wire import CALL_EXPIRED
        from factorylab.world.openrouter import OpenRouterError

        self.seen.append(req.timeout_s)
        raise OpenRouterError(None, CALL_EXPIRED)


def test_a_call_is_bounded_by_the_delivered_tick_and_an_expired_one_times_its_decision_out():
    from factorylab.runtime.compute import _ObservedMeteredModel
    from factorylab.world.metering import BillingUncertain, Meter
    from factorylab.world.models import ModelRequest

    rt = make_runtime()
    assert rt._call_deadline_s() == rt.m.timing.min_ratio * rt.tick_clock.interval_ns / 1e9
    handle = _open(rt, horizon=20)
    provider = _Expiring()
    passes = []
    model = _ObservedMeteredModel(provider, rt.prices, Meter(rt.wallet), record=lambda e: None,
                                  before_call=lambda: passes.append(1),
                                  deadline_s=rt._call_deadline_s, expired=rt._call_expired)
    with pytest.raises(BillingUncertain):
        model.complete(ModelRequest("fake-haiku", "s", ({"role": "user", "content": "x"},),
                                    max_tokens=10), handle=handle)
    assert passes == [1] and provider.seen == [rt._call_deadline_s()]
    assert rt.queue.get(handle).status is SettleStatus.TIMED_OUT
    expired, = _items(rt, "decision.call_expired")
    assert expired["handle"] == handle and expired["tick"] == rt.ticks_consumed


def test_the_safety_pass_runs_between_calls_only_once_a_delivered_tick_has_passed():
    from factorylab.runtime.live import LiveClock

    class FakeTime:
        t = 10**12

        def now_ns(self):
            return self.t

    ft = FakeTime()
    rt = make_runtime(live=True, clock_source=LiveClock(interval_ns=10**9, count=5,
                                                        now_ns=ft.now_ns, sleep=lambda s: None))
    calls = []
    rt._reconcile_orders = lambda **_: calls.append("reconcile")
    rt._evaluate_watchers = lambda **kw: calls.append(kw.get("sweep"))
    rt._settle_exchange_effects = lambda fills, **_: calls.append(("fills", len(fills)))
    rt.consequence_fills.poll = lambda _exchange: []
    rt._safety_ns = ft.t
    ft.t += 10**9 - 1
    rt._safety_pass()
    assert calls == [] and not _items(rt, "safety.pass")  # less than a tick: nothing
    ft.t += 1
    rt._safety_pass()
    assert calls == [("fills", 0), "reconcile", f"safety-{ft.t}"]
    assert _items(rt, "safety.pass")[-1]["ts"] == ft.t and rt.clock.now_ns >= ft.t
    rt._safety_pass()
    assert len(_items(rt, "safety.pass")) == 1  # the clock restarted at that pass
    simulated = make_runtime()
    simulated._safety_pass()
    assert not _items(simulated, "safety.pass")  # a simulated clock never moves mid-event
