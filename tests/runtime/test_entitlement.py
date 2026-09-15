"""C10 in the runtime: genesis split, routing feasibility, manifest field, resume."""

import tomllib
from dataclasses import replace

import pytest

from factorylab.kernel.budget import unlocked_balance
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, resume_runtime, runtime_state
from factorylab.runtime.wake import public_window_item
from factorylab.runtime.worlds import WORLDS_DIR, load_manifest, manifest_from_dict
from tests.conftest import make_runtime


class ProcessDeath(BaseException):
    pass


def stop_after(rt, predicate):
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if predicate(rt, event):
            raise ProcessDeath
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


def test_genesis_splits_base_share_equally_across_seeded_seats():
    rt = make_runtime(balance=90_000_000)
    seats = [a.id for a in rt.m.assemblies]
    assert len(seats) == 9
    assert rt.budget.entitlements() == {seat: 8_000_000 for seat in seats}
    assert rt.budget.unallocated() == 18_000_000 and invariant(rt)
    genesis = [i for i in rt.ledger.ledger._recovery_items()
               if i["kind"] == "budget" and i["op"] == "genesis"]
    assert len(genesis) == 1 and genesis[0]["amount"] == 90_000_000


def test_manifest_base_share_is_optional_validated_and_hash_neutral_by_default():
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    default = manifest_from_dict(raw)
    assert default.endowment.base_share == 0.8
    assert "endowment" not in default.canonical_json()
    assert default.manifest_hash() == load_manifest("scripted").manifest_hash()
    explicit = manifest_from_dict({**raw, "endowment": {"base_share": 0.8}})
    assert explicit.manifest_hash() == default.manifest_hash()
    changed = manifest_from_dict({**raw, "endowment": {"base_share": 0.5}})
    assert changed.manifest_hash() != default.manifest_hash()
    with pytest.raises(ValueError):
        manifest_from_dict({**raw, "endowment": {"base_share": 0}})
    with pytest.raises(ValueError):
        manifest_from_dict({**raw, "endowment": {"base_share": 1.5}})
    with pytest.raises(ValueError):
        manifest_from_dict({**raw, "endowment": {"unknown": 1}})
    rt = Runtime(changed, events=0, seed=1, initial_balance_micro=90_000_000, ledger_path=None,
                 drip=False, router_gamma=.1)
    assert set(rt.budget.entitlements().values()) == {5_000_000}


def test_exhausted_entitlement_is_infeasible_for_routing_not_insolvency():
    rt = make_runtime()
    seat, other = "seed-decider", "antagonist-a"
    assert rt._is_feasible(seat)[0] and rt._is_feasible(other)[0]
    rt.budget.debit(seat, rt.budget.entitlement(seat), "test")
    # a seed is protected until its first settled record: its trial calls may draw
    # on the unallocated pool, so routing still admits it
    assert rt._is_feasible(seat)[0]
    rt.budget.grant(other, rt.budget.unallocated(), "test")  # the pool is spent
    feasible, reason = rt._is_feasible(seat)
    assert not feasible and reason.startswith("entitlement:") and rt._is_feasible(other)[0]
    assert not rt.wallet.dead and invariant(rt)
    rt.budget.transfer(other, seat, 1_000_000, "test")
    assert rt._is_feasible(seat)[0]
    # a historied seat spends only its own: the pool does not admit it
    rt.budget.debit(seat, rt.budget.entitlement(seat), "test")
    rt.budget.debit(other, 1_000_000, "test")
    rt._unhistoried = lambda action_id: False
    feasible, reason = rt._is_feasible(seat)
    assert not feasible and reason.startswith("entitlement:")


def test_public_window_item_carries_entitlements_per_seat():
    rt = make_runtime(balance=90_000_000)
    item = public_window_item(rt, window=0, event=0)
    assert item["entitlements"]["unallocated_micro"] == 18_000_000
    rows = {row["id"]: row for row in item["entitlements"]["seats"]}
    assert rows["seed-decider"] == {"id": "seed-decider", "kind": "producer", "micro": 8_000_000}
    assert len(rows) == 9


def test_entitlements_restore_exactly_after_a_crash(tmp_path):
    base = load_manifest("scripted")
    m = replace(base, novelty=replace(base.novelty, window_ns=2 * base.tick_interval_ns))
    path = tmp_path / "entitlement.jsonl"
    rt = Runtime(m, events=140, seed=1, initial_balance_micro=None, ledger_path=str(path),
                 drip=True, router_gamma=.1)
    rt.events_budget = 8
    stop_after(rt, lambda r, e: r.ticks_consumed == 5 and str(e.kind) == "Tick")
    before = rt.budget.state()
    assert any(before["entitlements"][seat] < 8_888_888 for seat in before["entitlements"])
    assert invariant(rt)
    restored = resume_runtime(m, str(path))
    assert restored.budget.state() == before and invariant(restored)
    restored.stats.resumes = 0
    assert runtime_state(restored)["budget"] == runtime_state(rt)["budget"]
    assert restored.run()["ledger_verify"]


def test_routing_need_is_the_last_real_ceiling_repriced_for_world_growth():
    rt = make_runtime()
    seat = "seed-decider"
    assert rt._seat_need(seat) == 0  # no call yet, no hold yet
    now = rt._current_world_chars()
    assert now > 0
    rt.seat_ceilings[seat] = {"ceiling": 400_000, "world_chars": now}
    assert rt._seat_need(seat) == 400_000
    rt.seat_ceilings[seat] = {"ceiling": 400_000, "world_chars": now - 10_000}
    price = rt.prices.price(rt.assemblies[seat].spec.model_id)
    grown = 400_000 + price.cost(15_000, 0)  # 10 000 characters at the meter's 1.5 slack
    assert rt._seat_need(seat) == grown > 400_000
    rt.seat_ceilings[seat] = {"ceiling": 400_000, "world_chars": now + 10_000}
    assert rt._seat_need(seat) == 400_000  # a shrunken world never lowers the evidence
    # the need is what routing compares against the seat's cover
    rt._unhistoried = lambda action_id: False
    rt.budget.debit(seat, rt.budget.entitlement(seat) - grown + 1, "test")
    rt.seat_ceilings[seat] = {"ceiling": 400_000, "world_chars": now - 10_000}
    assert not rt._is_feasible(seat)[0]
    rt.seat_ceilings[seat] = {"ceiling": 400_000, "world_chars": now}
    assert rt._is_feasible(seat)[0]


def test_a_stale_routing_estimate_is_bridged_by_the_pool_never_a_failed_return():
    rt = Runtime(load_manifest("scripted"), events=30, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=.1)
    items = []
    append = rt.ledger.append

    def capture(item):
        items.append(dict(item))
        return append(item)

    rt.ledger.append = capture
    stop_after(rt, lambda r, e: r.n == 12)
    seat = "seed-observer"
    real = rt.seat_ceilings[seat]["ceiling"]
    assert real > 0
    # the seat is historied, its recorded ceiling is stale by a lot, and its entitlement
    # covers the stale figure but not the rendered request
    rt._unhistoried = lambda action_id: False
    rt.seat_ceilings[seat] = {"ceiling": 1, "world_chars": rt._current_world_chars()}
    rt.budget.debit(seat, rt.budget.entitlement(seat) - real // 2, "test")
    assert rt._is_feasible(seat)[0]
    rt.run()
    bridges = [i for i in items if i.get("kind") == "budget" and i.get("op") == "bridge"
               and i["assembly_id"] == seat]
    assert bridges and all(b["backed"] == b["amount"] > 0 for b in bridges)
    assert bridges[0]["reason"] == "routing estimate"
    invocations = [i for i in items if i.get("kind") == "invocation"
                   and i["assembly_id"] == seat and items.index(i) > items.index(bridges[0])]
    assert invocations and all(i["status"] == "ok" for i in invocations)
    assert rt.seat_ceilings[seat]["ceiling"] >= real  # the evidence is refreshed
    assert rt.budget.holds() == 0 and invariant(rt)


def test_an_unhistoried_seat_with_nothing_is_fed_by_the_protected_share_until_it_is_spent():
    from factorylab.kernel.wallet import Infeasible
    from tests.runtime.test_fidelity import decision

    rt = make_runtime()
    seat = "seed-observer"  # a seed is unhistoried until its first settled record
    rt.budget.debit(seat, rt.budget.entitlement(seat), "test")
    rt.budget.grant("seed-decider", rt.budget.unallocated(), "test")  # the pool is spent
    assert rt.budget.cover(seat, rt._protected_share(seat)) == 0
    assert not rt._is_feasible(seat)[0]
    # this window's novelty share is money the wallet withholds from ordinary calls:
    # it feeds the seat's trial whatever the pool holds
    rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
    share = rt.reserve.remaining()
    assert share > 0 and rt._protected_share(seat) == share
    assert rt.budget.cover(seat, rt._protected_share(seat)) == share
    assert rt._is_feasible(seat)[0]
    # the reservation enforces the same figure routing read
    handle = decision(rt, seat)
    wallet = rt._seat_wallet(seat)
    reason = f"model:{rt.assemblies[seat].spec.model_id}"
    with pytest.raises(Infeasible):
        wallet.reserve(share + 1, handle, reason)
    hold = wallet.reserve(share, handle, reason)
    assert rt._protected_share(seat) == 0 and rt.budget.holds() == share
    wallet.release(hold)
    assert rt._protected_share(seat) == share and rt.budget.holds() == 0
    # once the share is spent the seat is infeasible, for routing and for the meter alike
    rt.reserve._NoveltyReserve__remaining = 0
    assert rt._protected_share(seat) == 0 and not rt._is_feasible(seat)[0]
    with pytest.raises(Infeasible):
        wallet.reserve(1, handle, reason)
    # a historied seat never draws on it
    rt.reserve.open_window(rt.clock.now_ns + rt.m.novelty.window_ns, rt.wallet.balance)
    rt._unhistoried = lambda action_id: False
    assert rt._protected_share(seat) == 0 and not rt._is_feasible(seat)[0]
    assert invariant(rt)


def test_snapshot_codec_carries_the_book_and_older_snapshots_keep_genesis():
    rt = make_runtime()
    rt.budget.transfer("seed-decider", "eval-a", 1_000, "test")
    state = runtime_state(rt)
    fresh = make_runtime()
    restore_runtime(fresh, state)
    assert fresh.budget.state() == rt.budget.state()
    without = {k: v for k, v in state.items() if k != "budget"}
    older = make_runtime()
    restore_runtime(older, without)
    assert older.budget.entitlement("eval-a") == 8_888_888  # genesis stands
