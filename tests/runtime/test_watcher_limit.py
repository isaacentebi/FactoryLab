"""Watcher work is bounded by a hard limit on the world's own time (essay II.II.b).

A watcher's predicate runs in the world's own process and pays no one, so it moves
no money; its cost is time, which is a limit. Every sweep reads the world once and
settles at most ``[subscriptions] max_watcher_evaluations_per_sweep`` watchers
against that one snapshot, in a rotating order by id, so none waits more than
ceil(n / limit) sweeps, whoever registered first.
"""

import math
from dataclasses import replace

import pytest

from factorylab.runtime.worlds import SubscriptionsSpec, load_manifest, manifest_from_dict
from tests.conftest import make_runtime
from tests.runtime.test_connectors import ledger_items
from tests.runtime.test_real_flows import _world

TRIGGER = {"kind": "price_cross", "coin": "BTC", "level": "1"}


def _watchers(rt, n):
    """``n`` live watchers, registered in reverse id order (so fairness cannot lean
    on who registered first)."""
    seat = rt.assemblies["seed-observer"]
    for i in reversed(range(n)):
        rt.assemblies[f"w{i:03d}"] = seat
        rt.subscription_book.watch(f"w{i:03d}", owner="seed-decider", trigger=TRIGGER)
    return sorted(f"w{i:03d}" for i in range(n))


def _count_reads(rt):
    reads = []
    target = rt.exchange.target if hasattr(rt.exchange, "target") else rt.exchange
    for method in ("mids", "account"):
        inner = getattr(target, method)

        def counted(*a, _inner=inner, _method=method, **k):
            reads.append(_method)
            return _inner(*a, **k)

        setattr(target, method, counted)
    return reads


def test_two_hundred_watchers_are_settled_against_one_read_of_the_world_a_sweep():
    rt = make_runtime()
    rt._manage_reserve_window()
    limit = rt.m.subscriptions.max_watcher_evaluations_per_sweep
    ids = _watchers(rt, 200)
    reads = _count_reads(rt)
    sweeps = math.ceil(200 / limit)
    reached = []
    for sweep in range(sweeps):
        before = len(ledger_items(rt, "watcher.evaluated"))
        count = len(reads)
        rt._evaluate_watchers(sweep=f"s{sweep}")
        evaluated = ledger_items(rt, "watcher.evaluated")[before:]
        assert len(evaluated) <= limit
        assert sorted(reads[count:]) == ["account", "mids"]  # one snapshot, shared
        reached += [i["watcher"] for i in evaluated]
    # Every watcher reached within ceil(n / limit) sweeps, in id order round the
    # circle (the last sweep wraps to the start).
    assert set(reached) == set(ids) and reached[:200] == ids
    # The next sweep carries on round the circle from where the last one stopped.
    before = len(ledger_items(rt, "watcher.evaluated"))
    rt._evaluate_watchers()
    assert [i["watcher"] for i in ledger_items(rt, "watcher.evaluated")[before:]][0] == (
        ids[(sweeps * limit) % 200])


def test_a_retired_owner_s_watcher_is_not_evaluated_and_none_costs_anything():
    """The watcher itself stays live; only its owner retires. It is never evaluated
    again and takes no place in the rotation: with one evaluation a sweep, the sweeps
    go round the two others."""
    rt = make_runtime()
    rt._manage_reserve_window()
    ids = _watchers(rt, 3)
    rt.subscription_book.watchers[ids[1]]["owner"] = "owner-b"
    rt.retired_assemblies.add("owner-b")
    assert ids[1] not in rt.retired_assemblies and ids[1] in rt.assemblies
    balance = rt.wallet.balance
    rt._evaluate_watchers()
    evaluated = [i["watcher"] for i in ledger_items(rt, "watcher.evaluated")]
    assert evaluated == [ids[0], ids[2]]
    assert rt.wallet.balance == balance
    assert all(i["cost"] == 0 for i in ledger_items(rt, "watcher.evaluated"))
    rt.m = replace(rt.m, subscriptions=SubscriptionsSpec(1))
    before = len(ledger_items(rt, "watcher.evaluated"))
    for sweep in range(4):
        rt._evaluate_watchers(sweep=f"s{sweep}")
    assert [i["watcher"] for i in ledger_items(rt, "watcher.evaluated")[before:]] == [
        ids[0], ids[2], ids[0], ids[2]]


def test_no_watchers_read_nothing():
    rt = make_runtime()
    rt._manage_reserve_window()
    reads = _count_reads(rt)
    rt._evaluate_watchers()
    assert reads == []


def test_the_limit_is_a_manifest_constant_published_as_a_fact():
    rt = make_runtime()
    limit = rt.m.subscriptions.max_watcher_evaluations_per_sweep
    assert limit == SubscriptionsSpec().max_watcher_evaluations_per_sweep == 32
    published = rt._world_block()["watchers"]
    assert published["max_watcher_evaluations_per_sweep"] == limit
    assert "ceil(n / max_watcher_evaluations_per_sweep)" in published["rule"]
    assert '"max_watcher_evaluations_per_sweep":32' in load_manifest("scripted").canonical_json()
    small = replace(load_manifest("scripted"), subscriptions=SubscriptionsSpec(5))
    assert small.subscriptions.max_watcher_evaluations_per_sweep == 5
    for bad in (0, -1, "5", True):
        with pytest.raises(ValueError, match="max_watcher_evaluations_per_sweep"):
            manifest_from_dict(_world(subscriptions={"max_watcher_evaluations_per_sweep": bad}))
    with pytest.raises(ValueError, match="unknown subscriptions manifest key"):
        manifest_from_dict(_world(subscriptions={"max_watchers": 5}))


def test_the_rotation_survives_a_checkpoint():
    rt = make_runtime()
    rt._manage_reserve_window()
    _watchers(rt, 40)
    rt._evaluate_watchers()
    state = rt.subscription_book.state()
    assert state["watch_cursor"] == rt.subscription_book.watch_cursor is not None
    restored = make_runtime().subscription_book
    restored.restore(state)
    assert restored.watch_cursor == rt.subscription_book.watch_cursor
