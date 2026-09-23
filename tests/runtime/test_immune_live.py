"""Wave 5b: live versions, the priced pathologies, the niche, entrainment and uptake.

Essay II.II and II.II.b: versions are read live from the transfer operator; stable
failure is priced by its duration and thrash by its volatility, both through the one
PID law; learning death is prevented "as a fact about the world": a share of compute
and write access usable only by unhistoried actions. Essay II.IV.b-c: thrash is also
a loop whose period exceeds its configuration's lifespan, entrainment is priced as
dependency concentration and broken by jitter, and exploration is paid by
anticipatory settlement.
"""

from dataclasses import replace
from math import ceil
from statistics import fmean

import pytest

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime import immune, pricing
from factorylab.runtime.observations import observation_for, window_facts
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.shared import NOOP
from factorylab.runtime.subscriptions import SubscriptionBook
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from tests.conftest import make_runtime
from tests.runtime.test_abstention_price import _abstention
from tests.runtime.test_attributable_blame import _card, _commitments, _runtime
from tests.runtime.test_immune_ratchet import _close, _items
from tests.runtime.test_immune_ratchet import _runtime as _organ


def _open(rt, seat, *, parent=None, channel="verdict"):
    handle = rt.queue.open(
        actor="test-router", event_id="test", channel=channel, deadline_ns=10**18,
        parent_handle=parent, cost_ceiling=0,
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, "test-router", "state"))
    rt.handle_to_assembly[handle] = seat
    return handle


def _settle(rt, handle, channel="verdict"):
    rt.queue.settle(handle, channel=channel, score=0.5, status=SettleStatus.SETTLED,
                    definition_version="verdict-v1", sampling_ref=None)


# --- live versioning (versioning M1, M2, P6; time T7) ---------------------------------


def test_every_closed_window_is_read_into_live_versions_and_settling_reaches_governance():
    rt = _organ()
    for _ in range(10):
        _close(rt, 1.0, registrations=0, organ_due=False)
    state = rt.stats.versions
    assert state["version"] == 1 and state["gap"] == 1.0 and state["settled_tick"] is not None
    assert [s["settled"] for s in _items(rt, "version.settled")] == [True]
    # The launch settles by the factory's own dynamics: governance measures revisions.
    assert not _items(rt, "governance.settling")
    rt.charter = replace(rt.charter, edition=rt.charter.edition + 1)
    for _ in range(5):
        _close(rt, 1.0, registrations=0, organ_due=False)
    settled = [s for s in _items(rt, "version.settled") if s["cause"] == "charter"]
    assert [s["settled"] for s in settled] == [True]
    governance = _items(rt, "governance.settling")
    assert governance[-1]["settling_ticks"] == settled[0]["ticks"] > 0
    assert rt.cadence.slowest_period_events() >= settled[0]["ticks"]
    assert len(rt.stats.immune_windows) == rt.m.timing.min_ratio * rt.m.immune.k
    window = _items(rt, "immune.window")[-1]
    assert {"gap", "rolling_gap", "card_gap", "volatility", "tick", "terms",
            "frontier_invocation", "thrash"} <= set(window)


def test_a_charter_edition_and_a_change_of_terms_each_open_a_version():
    """Essay II.II: "If a revision to the input at the level of the charter happens, the
    version has changed. If an external force changes the terms ... the version has
    changed" (versioning M3)."""
    rt = _organ()
    for _ in range(3):
        _close(rt, 1.0, registrations=0, organ_due=False)
    rt.charter = replace(rt.charter, edition=rt.charter.edition + 1)
    _close(rt, 1.0, registrations=0, organ_due=False)
    rt.tool_specs["venue.new_surface"] = {"kind": "venue", "price_micro_per_call": 5}
    _close(rt, 1.0, registrations=0, organ_due=False)
    assert [b["cause"] for b in _items(rt, "version.boundary")] == ["charter", "terms"]
    # The population's own tools are not the world's terms.
    rt.population_tools["own-tool"] = object()
    rt.tool_specs["own-tool"] = {"kind": "population", "price_micro_per_call": 1}
    _close(rt, 1.0, registrations=0, organ_due=False)
    assert len(_items(rt, "version.boundary")) == 2


# --- thrash, priced (versioning C2) ----------------------------------------------------


def test_the_thrash_price_integrates_the_duration_of_volatility_and_leaks_after():
    rt = make_runtime()
    prices = []
    for _ in range(5):
        rt.n += 10
        rt.stats.versions = {"volatility": 0.6}
        prices.append(immune.thrash_penalty(rt))
    lambdas = [p["lambda"] for p in prices]
    assert lambdas == sorted(lambdas) and lambdas[-1] > lambdas[0]
    assert prices[-1]["penalty"] == pytest.approx(
        min(lambdas[-1] * 0.4, rt.m.prices.penalty_cap))
    rt.n += 10
    rt.stats.versions = {"volatility": 0.1}  # inside the bound: settled
    settled = immune.thrash_penalty(rt)
    assert settled["penalty"] == 0 and settled["lambda"] < lambdas[-1]
    rt.stats.versions = {"volatility": None}
    assert immune.thrash_penalty(rt)["penalty"] == 0


def test_the_thrash_price_charges_the_core_and_its_abstentions_never_the_frontier():
    rt = make_runtime()
    rt.m = replace(rt.m, evaluation=replace(rt.m.evaluation, no_swap_regret_kinds=("Tick",)))
    rt.window.thrash_penalty = 0.2
    core, frontier = rt.routers["Tick"][0], rt.routers["MarketMid"][0]
    assert rt._thrash_charged(core, "no-origin", 0.7) == pytest.approx(0.5)
    assert rt._thrash_charged(core, "no-origin", 0.1) == 0.0
    assert rt._thrash_charged(frontier, "no-origin", 0.7) == 0.7
    charged = len(_items(rt, "thrash.charged"))
    handle = rt.queue.open(
        actor=core.learner.id, event_id="noop", channel="verdict", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((NOOP,), (1.0,), NOOP, 0, core.learner.id, "state"))
    rt.noop_credits[handle] = {"router": core.learner.id, "due_tick": rt.ticks_consumed,
                               "p": None, "executed": None}
    rt._credit_abstentions()
    abstained = _items(rt, "thrash.charged")[charged:]
    assert [i["handle"] for i in abstained] == [handle]  # waking nobody pays it too


def test_immune_decay_is_gone_and_a_manifest_naming_it_is_refused():
    rt = make_runtime()
    assert not hasattr(rt.controller, "set_decay") and not hasattr(immune,
                                                                   "ImmunePriceController")
    assert not hasattr(rt.m.immune, "decay_step")
    raw = manifest_from_dict  # the loader refuses the removed key by name
    with pytest.raises(ValueError, match="decay_step was removed"):
        from factorylab.runtime.worlds import _manifest_immune

        _manifest_immune({"price_step": 0.05, "decay_step": 0.1})
    assert callable(raw)


# --- stable failure, priced by its duration (versioning P3, P4) -----------------------


def test_a_stable_failures_duration_price_reaches_abstention(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per=None))
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    rt.window = MeasureWindow(rt.window.index + 1, rt.wallet.balance)
    noop = _abstention(rt)
    before = rt._priced_abstention(noop, 0.9)[1]
    rt.controller.set_price("censorship-bound", 0.2, amendment_id="lower")
    lowered = rt._priced_abstention(noop, 0.9)[1]
    for window in range(3):
        rt.controller.ratchet("censorship-bound", window=window, step=0.1)
    ratcheted = rt._priced_abstention(noop, 0.9)[1]
    assert lowered < before and ratcheted > lowered
    assert ratcheted <= rt.m.prices.penalty_cap


def test_one_registration_does_not_reset_the_ratchet():
    rt = _organ(lambda_max=10.0)
    durations = []
    for i in range(8):
        _close(rt, 0.2, registrations=3 if i == 4 else 0)
        durations.append(rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"])
    assert durations[-1] == max(durations) >= 5
    assert not _items(rt, "immune.price_ratchet_ended")


# --- learning death, read from the frontier (versioning P1, C1) -----------------------


def test_an_unmeasured_card_alone_is_not_learning_death():
    rt = _organ()
    rt.charter = replace(rt.charter, cards=())  # nothing measured, nothing vouched for
    for _ in range(6):
        _close(rt, 1.0, registrations=0, organ_due=False)
    assert not _items(rt, "pathology.learning_death")


def test_access_is_read_from_the_reserve_window_still_open_at_the_close():
    rt = make_runtime()
    rt._manage_reserve_window()
    remaining = rt.reserve.remaining()
    assert remaining > 0
    rt.ticks_consumed = rt.window.due_tick
    rt.clock.now_ns += 1
    rt._manage_reserve_window()
    window = _items(rt, "immune.window")[-1]
    assert window["profile"]["access:registration_route"] == 1.0
    assert _items(rt, "novelty.window")[-1]["carried"] == remaining


# --- the niche (ruling R5; versioning P2) ---------------------------------------------


def test_an_unhistoried_action_of_a_historied_seat_may_spend_the_niche():
    rt = make_runtime()
    rt._manage_reserve_window()
    seat, tool = "seed-decider", "venue.positions"
    _settle(rt, _open(rt, seat))
    assert not rt._unhistoried(seat)  # past its first record: no seat trial
    handle = _open(rt, seat)
    reason, model = f"tool:{tool}", f"model:{rt.assemblies[seat].spec.model_id}"
    assert rt._novelty_compute(handle, reason)
    assert rt._novelty_protection(handle, reason) == rt.reserve.remaining() > 0
    assert rt.wallet.available_for(handle, reason) == rt.wallet.unhistoried_available
    assert not rt._novelty_compute(handle, model)  # its ordinary compute is not the niche
    rt.niche_rounds[handle] = rt._tool_action(tool)
    assert rt._novelty_compute(handle, model)  # the round that reads the result is
    rt.niche_rounds.clear()
    # A requested child is its parent's subcontracting.
    assert not rt._novelty_compute(_open(rt, "eval-a", parent=handle), reason)
    rt.queue.record_actions(handle, {rt._tool_action(tool)})
    _settle(rt, handle)
    assert not rt._novelty_compute(_open(rt, seat), reason)  # it has a reward trail now


def test_the_learning_death_grant_is_gone():
    rt = make_runtime()
    assert not hasattr(rt, "novelty_grant") and not hasattr(rt, "_novelty_grant_open")
    assert not hasattr(rt, "_issue_novelty_grant")


# --- thrash as lifespan against latency (time T14) -------------------------------------


def test_a_configuration_replaced_before_its_correction_lands_is_thrash():
    rt = _organ()
    for _ in range(3):
        _close(rt, 1.0, registrations=0, organ_due=False)
    immune.configuration_changed(rt, "router:Tick", 10)  # the first: no lifespan yet
    assert not _items(rt, "config.lifespan")
    rt.ticks_consumed += 3
    immune.configuration_changed(rt, "router:Tick", 10)
    (row,) = _items(rt, "config.lifespan")
    assert row["lifespan_ticks"] == 3 and row["ratio"] == pytest.approx(0.3)
    _close(rt, 1.0, registrations=0, organ_due=False)
    (flag,) = _items(rt, "pathology.thrash")
    assert flag["short_lived"][0]["loop"] == "router:Tick"


# --- entrainment (time T15) ------------------------------------------------------------


def test_dependency_concentration_is_an_observation_a_card_can_price():
    w = MeasureWindow(1, 0, calls_by_provider={"openrouter": 3, "venice": 1},
                      calls_by_family={"glm": 2, "gpt": 2})
    assert observation_for("provider_concentration").measure(w) == 0.75
    assert observation_for("family_concentration").measure(w) == 0.5
    assert observation_for("provider_concentration").measure(MeasureWindow(1, 0)) is None
    assert "calls_by_provider" not in window_facts(w)  # the names stay private
    rt = make_runtime()
    rt._count_dependency("seed-decider")
    rt._count_dependency("eval-a")
    assert rt.window.calls_by_provider == {"fake": 2}
    assert sum(rt.window.calls_by_family.values()) == 2


def test_routine_wakes_are_jittered_per_seat_and_only_a_little():
    rt = make_runtime()
    jf = rt.clockwork.jitter_fraction
    draws = [rt._wake_jitter("seat-x", t, 1) for t in range(4000)]
    assert set(draws) <= {0, 1} and fmean(draws) == pytest.approx(jf / 2, abs=0.02)
    assert rt._wake_jitter("seat-x", 7, 10) == rt._wake_jitter("seat-x", 7, 10)
    assert max(rt._wake_jitter("s", t, 10) for t in range(500)) <= ceil(10 * jf)
    assert [rt._wake_jitter("a", t, 1) for t in range(50)] != [
        rt._wake_jitter("b", t, 1) for t in range(50)]  # not one shared beat
    book = SubscriptionBook()
    book.woke("s", now=5)
    assert book.absent("s", "Tick", now=6, coins=frozenset(), jitter=lambda *_a: 1)
    assert book.absent("s", "Tick", now=6, coins=frozenset(), jitter=lambda *_a: 0) == ""
    assert book.absent("s", "Fill", now=6, coins=frozenset(), jitter=lambda *_a: 5) == ""


# --- anticipatory settlement (time T18) ------------------------------------------------


def test_a_registration_is_settled_early_on_judges_forecasts_and_corrected_on_uptake():
    rt = _organ()
    builder, judge = "seed-decider", "eval-a"
    rt._open_uptake("tool", "half-spread", builder)
    key = "tool:half-spread"
    record = rt.uptake[key]
    rt._post_uptake_forecasts(_open(rt, "seed-observer"), [{"registration": key, "q": 0.9}])
    rt._post_uptake_forecasts(_open(rt, judge), [{"registration": key, "q": 0.8},
                                                 {"registration": "tool:nope", "q": 0.5}])
    refused = [i["reason"] for i in _items(rt, "uptake_forecast.refused")]
    assert any("judging" in r for r in refused) and any("open" in r for r in refused)
    (forecast,) = record["forecasts"]
    _close(rt, 1.0, registrations=0, organ_due=False)
    early = rt.queue.history(record["decision"])[-1]
    assert (early.status, early.score, early.definition_version) == (
        SettleStatus.SETTLED, 0.8, "uptake-anticipated-v1")
    correction = record["early"]["handle"]
    rt._taken_up("tool", "half-spread", builder)  # its own lineage is not uptake
    assert not record["taken"]
    rt._taken_up("tool", "half-spread", "eval-b")
    _close(rt, 1.0, registrations=0, organ_due=False)
    assert key not in rt.uptake
    assert rt.queue.history(correction)[-1].score == pytest.approx(0.5 + 0.5 * (1 - 0.8))
    assert rt.queue.history(forecast["handle"])[-1].score == pytest.approx(1 - 0.2 ** 2)
    assert rt.uptake_standing[judge] == (1, pytest.approx(0.96))


def test_a_registration_nobody_forecast_settles_on_its_outcome():
    rt = _organ()
    rt._open_uptake("observation", "downside", "seed-decider")
    record = rt.uptake["observation:downside"]
    rt.ticks_consumed = record["until_tick"]
    _close(rt, 1.0, registrations=0, organ_due=False)
    last = rt.queue.history(record["decision"])[-1]
    assert (last.score, last.definition_version) == (0.0, "uptake-realized-v1")


def test_the_niche_is_published_as_a_schematic_and_nothing_more():
    rt = make_runtime()
    mechanics = rt._mechanics_block()
    assert "eligible" in mechanics["novelty"] and "thrash_price" in mechanics
    assert "uptake" in mechanics
    assert set(rt._adaptive_scoring_block()["thrash_price"]) == {"lambda", "penalty"}
    assert load_manifest("scripted").immune.gap_threshold > 0
