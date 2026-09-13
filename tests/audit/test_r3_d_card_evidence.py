"""T12 and T13 retain the same selected evidence through pricing and diagnosis."""

import json
from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.cortex.request import Return
from factorylab.kernel.ledger import canonical
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.versioning.series import windows
from factorylab.versioning.versions import diagnose
from tests.audit.test_v3_seat4_boundaries import _decision
from tests.conftest import make_runtime


def cost_runtime(role="evaluator", *, kind="returns", n=2, per="role"):
    rt = make_runtime()
    card = MetricCard("cost", rt.charter.norms[1], "Selected response cost", "micro-USD",
                      {"kind": kind, "n": n, "per": per}, "at most 500",
                      "cost_per_return", role)
    rt.charter = replace(rt.charter, cards=(card,))
    rt._manage_reserve_window()
    return rt


def returned(rt, assembly, role, cost, *, status="ok"):
    handle = _decision(rt, assembly)
    ret = Return(handle, {}, cost, status)
    rt.card_samples.returned(handle=handle, assembly=assembly, role=role,
                             window=rt.window.index, ret=ret)
    rt._contribution(handle, role).update(cost=cost, ok=int(status == "ok"), invocations=1)
    if role == "producer" and status == "ok":
        rt.window.costs.append(cost)
    return handle


@pytest.mark.parametrize("role,assembly", [("evaluator", "eval-a"), ("meta", "meta-a")])
def test_cost_shares_exclude_unselected_failed_and_other_role_returns(role, assembly):
    rt = cost_runtime(role, n=3)
    stale = returned(rt, assembly, role, 9_000)
    cheap = returned(rt, assembly, role, 1_000)
    malformed = returned(rt, assembly, role, 50_000, status="malformed")
    expensive = returned(rt, assembly, role, 3_000)
    unrelated = returned(rt, "seed-decider", "producer", 100_000)
    rt.n = 10
    rt._close_price_window()
    assert rt.window.closed_values == {"cost": 2_000}
    for handle in (stale, malformed, unrelated):
        assert rt._penalty_for(role, handle) == 0
    assert rt._penalty_for(role, cheap) == pytest.approx(0.125)
    assert rt._penalty_for(role, expensive) == pytest.approx(0.375)


def test_cost_scope_means_keep_equal_weight_with_unequal_success_support():
    rt = cost_runtime(per="assembly", n=2)
    a = returned(rt, "eval-a", "evaluator", 1_000)
    failed = returned(rt, "eval-a", "evaluator", 20_000, status="malformed")
    b = returned(rt, "eval-b", "evaluator", 1_000)
    c = returned(rt, "eval-b", "evaluator", 1_000)
    unsupported = returned(rt, "meta-a", "evaluator", 9_000)
    rt.n = 10
    rt._close_price_window()
    shares = rt.window.closed_shares[0]["shares"]
    assert shares == {a: 0.5, b: 0.25, c: 0.25}
    assert failed not in shares and unsupported not in shares


@pytest.mark.parametrize("per", [None, "role", "assembly"])
def test_cost_window_selector_retains_earlier_contributors_after_resume(per):
    rt = cost_runtime("producer", kind="windows", n=2, per=per)
    first = returned(rt, "seed-decider", "producer", 1_000)
    rt.n = 10
    rt._close_price_window()
    assert rt.window.closed_values == {}
    rt.window = MeasureWindow(2, rt.wallet.balance)
    second = returned(rt, "seed-decider", "producer", 3_000)
    rt.n = 20
    rt._close_price_window()
    assert rt.window.closed_values == {"cost": 2_000}
    assert rt.window.closed_shares[0]["shares"] == {first: 0.25, second: 0.75}
    before = rt._penalty_for("producer", second)
    rt.window = MeasureWindow(3, rt.wallet.balance)
    returned(rt, "seed-decider", "producer", 1_000_000)
    rt.card_samples.returns.clear()  # Frozen ownership does not depend on retained rows.
    restored = cost_runtime("producer", kind="windows", n=2, per=per)
    restore_runtime(restored, runtime_state(rt))
    assert before == pytest.approx(0.375)
    assert restored._penalty_for("producer", second) == before
    assert restored.price_windows[2].closed_shares == rt.price_windows[2].closed_shares


def test_contributions_record_emitted_role_instead_of_callers_label(monkeypatch):
    rt = cost_runtime()
    handle = _decision(rt, "eval-a")
    entries = []
    append = rt.ledger.append

    def capture(item):
        entries.append(item)
        return append(item)

    monkeypatch.setattr(rt.ledger, "append", capture)
    monkeypatch.setattr(rt, "_invoke_compute", lambda *args: Return(handle, {}, 2_000, "ok"))
    req = rt._request(handle, "ProducerReturn", {}, {"type": "object"}, 10**15, "conformity")
    rt._invoke("eval-a", req, "producer")
    assert rt.window.decisions[handle]["role"] == "evaluator"
    assert rt.card_samples.returns[-1]["role"] == "evaluator"
    contribution = next(i for i in entries if i["kind"] == "price.contribution")
    assert contribution["role"] == "evaluator"


@pytest.mark.parametrize("costs,raw,expected", [([1_000] * 99 + [100], 100, True),
                                               ([100] * 99 + [1_000], 1_000, False),
                                               ([1_000], 1_000, False)])
def test_live_and_offline_diagnose_typed_cost_support(costs, raw, expected, monkeypatch):
    rt = cost_runtime("producer", n=100)
    entries = []
    append = rt.ledger.append

    def capture(item):
        seq = append(item)
        entries.append(json.loads(canonical(dict(item, seq=seq))))
        return seq

    monkeypatch.setattr(rt.ledger, "append", capture)
    for cost in costs:
        returned(rt, "seed-decider", "producer", cost)
    for index in range(1, rt.m.immune.k + 1):
        rt.window = MeasureWindow(index, rt.wallet.balance, costs=[raw], registrations=1)
        rt.n = index * 10
        rt._close_price_window()
        price = next(i for i in reversed(entries) if i["kind"] == "price.window")
        immune = next(i for i in reversed(entries) if i["kind"] == "immune.window")
        assert immune["profile"]["card:cost"] == price["values"].get("cost")
        assert immune["regions"]["card:cost"] == price["regions"]["cost"]
    offline = windows(entries)
    assert [w["profile"] for w in rt.stats.immune_windows] == [
        {k: w["profile"][k] for k in rt.stats.immune_windows[0]["profile"]} for w in offline
    ]
    flags = diagnose(offline, k=rt.m.immune.k,
                     registration_bins=rt.m.immune.registration_bins,
                     revision_bins=rt.m.immune.revision_bins)["flags"]
    assert flags == rt.stats.pathologies
    assert flags["stable_failure"] is expected


def test_immune_uses_frozen_regions_after_live_region_changes():
    rt = cost_runtime("producer", n=1)
    returned(rt, "seed-decider", "producer", 1_000)
    rt._close_price_window()
    frozen = rt.stats.immune_windows[-1]["regions"]
    rt.regions["cost"] = replace(rt.regions["cost"], hi=2_000)
    from factorylab.runtime.immune import close_window

    close_window(rt, {"cost_per_return": 0})
    assert rt.stats.immune_windows[-1]["regions"] == frozen
    assert rt.stats.immune_windows[-1]["profile"]["card:cost"] == 1_000
