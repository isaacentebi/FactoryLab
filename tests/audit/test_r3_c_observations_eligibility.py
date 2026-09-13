"""Registered observations and router-selected consequence experience reach governance."""

from dataclasses import replace
from types import SimpleNamespace

from factorylab.charter.amendment import Amendment
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import DEF_CONFORMITY
from tests.audit.test_a15_liability import boundary
from tests.audit.test_r3_c_liability import committee
from tests.audit.test_v3_seat4_boundaries import _decision
from tests.conftest import make_runtime
from tests.runtime.test_fidelity import decision


def test_registered_observation_reaches_vote_activation_price_and_frozen_liability(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    rt.tool_jail_available = True
    rt.observation_runner = SimpleNamespace(
        run=lambda code, facts: (0.9 if "0.9" in code else 0.5, None)
    )
    rt.window = MeasureWindow(1, rt.wallet.balance, costs=[100], invocations=1, ok=1)
    rt._close_price_window()
    committee(rt, monkeypatch)
    author = decision(rt, "seed-decider")
    observation = {
        "kind": "observation",
        "id": "fresh-measure",
        "description": "Evidence",
        "unit": "fraction",
        "range": [0, 1],
        "code": "def observe(facts): return 0.5",
    }
    rt._apply_registrations(author, Return(author, {"register": [observation]}, 0, "ok"))
    assert rt.registry.get("observation:fresh-measure").version == 1
    card = {
        "id": "fresh-card",
        "norm": "useful inquiry",
        "description": "New evidence",
        "units": "fraction",
        "window": {"kind": "windows", "n": 1, "per": None},
        "acceptable_region": "at least 0.8",
        "observation": "fresh-measure",
        "answers_for": "producer",
    }
    rt._propose_amendment(
        author,
        {
            "id": "fresh-policy",
            "add": [card],
            "predicted_effect": {"card_id": "fresh-card", "direction": "increase", "window": 2},
        },
    )
    votes = list(rt.pending_votes)
    assert len(votes) == 3
    assert all(
        v["observation_id"] == "fresh-measure" and v["observation_version"] == 1 for v in votes
    )
    proposed = next(i for i in rt.ledger._recovery_items() if i["kind"] == "charter.propose")
    assert proposed["observation_bindings"]["fresh-card"] == {"id": "fresh-measure", "version": 1}
    boundary(rt, 2)
    rt._close_price_window()
    assert rt.window.closed_values["fresh-card"] == 0.5
    assert rt.controller.price("fresh-card") > 0
    rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
    observation["code"] = "def observe(facts): return 0.9"
    rt._apply_registrations(author, Return(author, {"register": [observation]}, 0, "ok"))
    assert rt.registry.get("observation:fresh-measure").version == 2, rt.registration_feedback
    saved = runtime_state(rt)
    restored = make_runtime()
    restored.observation_runner = rt.observation_runner
    restore_runtime(restored, saved)
    for current in (rt, restored):
        current.window = MeasureWindow(3, current.wallet.balance, costs=[100], invocations=1, ok=1)
        current.n += 1
        current._close_price_window()
        assert current.window.closed_values["fresh-card"] == 0.9
        for vote in votes:
            result = current.queue.history(vote["handle"])[-1]
            assert result.status is SettleStatus.SETTLED
            assert result.score == (0 if vote["vote"] else 1)
        # The runtime-bound book also validates against the restored registration map.
        fresh = next(c for c in current.charter.cards if c.id == "fresh-card")
        amendment = Amendment(
            "restored-check",
            author,
            current.charter.edition,
            (),
            (replace(fresh, description="Restated"),),
            (),
            {"card_id": fresh.id, "direction": "increase", "window": 1},
        )
        current.charter_book.validate(amendment)
        current.charter_book.propose(amendment)
    assert runtime_state(rt) == runtime_state(restored)


def test_conformity_and_consequence_evidence_count_each_original_once():
    rt = make_runtime()
    assembly = "meta-a"
    for index in range(rt.m.committee.min_settled):
        handle = _decision(rt, assembly, channel="conformity")
        rt.queue.settle(
            handle,
            channel="conformity",
            score=0.8,
            status=SettleStatus.SETTLED,
            definition_version=DEF_CONFORMITY,
            sampling_ref="higher-tier",
        )
        assert (assembly in rt._committee_eligible()) == (index + 1 == rt.m.committee.min_settled)
        for number in range(3):
            child = rt.queue.open(
                actor=assembly,
                event_id=f"forecast-{index}-{number}",
                propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, assembly, "fixture"),
                channel="consequence",
                parent_handle=handle,
                deadline_ns=10**12,
                cost_ceiling=0,
            )
            rt.queue.settle(
                child,
                channel="consequence",
                score=0.8,
                status=SettleStatus.SETTLED,
                definition_version="fixture",
                sampling_ref=None,
            )
        assert (assembly in rt._committee_eligible()) == (index + 1 == rt.m.committee.min_settled)


def test_plain_fast_scores_never_manufacture_committee_experience():
    rt = make_runtime()
    for _ in range(rt.m.committee.min_settled):
        handle = _decision(rt, "meta-a", channel="fast")
        rt.queue.settle(
            handle,
            channel="fast",
            score=1,
            status=SettleStatus.SETTLED,
            definition_version="fast-v1",
            sampling_ref=None,
        )
    assert "meta-a" not in rt._committee_eligible()
