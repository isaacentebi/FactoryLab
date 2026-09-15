"""Edition 2, W1: locked backing released on a schedule (C1), dormancy between releases (C2),
and notebook rent by byte-time (C3), in the scripted world with no network and no credentials."""

from dataclasses import replace

from factorylab.kernel.termination import DORMANT
from factorylab.runtime.loop import Runtime
from factorylab.runtime.notes import NS_PER_DAY, NotesSpec, accrue
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.wake import _Observatory, public_window_item
from factorylab.runtime.worlds import EndowmentSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.audit.test_r3_k_notes import put
from tests.runtime.test_connectors import decision, ledger_items

BASE = load_manifest("scripted")
TICK = BASE.tick_interval_ns
INITIAL = BASE.initial_balance_micro


def endowed(locked, releases, *, events=0, notes=None):
    manifest = replace(BASE, endowment=EndowmentSpec(locked, releases),
                       **({"notes": notes} if notes else {}))
    return Runtime(manifest, events=events, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=False, router_gamma=.1, exchange=FakeExchange(),
                   provider=ScriptedProvider())


def kinds(rt, *names):
    return [i for i in ledger_items(rt) if i.get("kind") in names]


def test_world_launches_dormant_waits_for_its_release_then_wakes_and_spends():
    offset = 3 * TICK + TICK // 2
    rt = endowed(INITIAL, ((offset, INITIAL),), events=8)
    summary = rt.run()
    items = ledger_items(rt)
    launch = next(i for i in items if i.get("kind") == "event" and i["event"]["kind"] == "Launch")
    assert rt.wallet.launch_ns == launch["ts"]
    anchors = kinds(rt, "wallet.anchor")
    assert len(anchors) == 1 and anchors[0]["launch_ns"] == launch["ts"]
    # Dormancy entered before any event and left once the tranche landed, both ledgered.
    dormant = kinds(rt, "dormant")
    assert [(d["state"], d.get("trigger")) for d in dormant] == [("entered", "wallet"),
                                                                  ("exited", None)]
    assert dormant[0]["ts"] == launch["ts"]
    assert dormant[0]["next_release_ns"] == launch["ts"] + offset
    releases = kinds(rt, "release")
    assert [(r["amount"], r["due_ns"], r["locked_after"]) for r in releases] == [
        (INITIAL, launch["ts"] + offset, 0)]
    assert releases[0]["ts"] >= launch["ts"] + offset and dormant[1]["ts"] >= releases[0]["ts"]
    # No paid cognition while dormant; maintenance ran on every event; cognition after.
    exited_at = items.index(dormant[1])
    model_commits = [i for i in items if i.get("kind") == "wallet.commit"
                     and str(i.get("reason", "")).startswith("model:")]
    assert model_commits and all(items.index(c) > exited_at for c in model_commits)
    routed = [i for i in items if i.get("kind") == "compute.route"]
    assert routed and all(items.index(r) > exited_at for r in routed)
    done = [i for i in items if i.get("kind") == "runtime.event_done"]
    assert len(done) >= 8 and any(items.index(d) < exited_at for d in done)
    # The novelty reserve opened on unlocked money: nothing while everything was locked.
    assert kinds(rt, "novelty.window")[0]["budget"] == 0
    assert rt.wallet.locked == 0 and rt.dormancy is None and rt.wallet.check_conservation()
    assert not summary["terminated"] and summary["ledger_verify"]


def test_dormant_world_never_dies_by_budget_while_backing_and_a_release_remain():
    rt = endowed(INITIAL, ((NS_PER_DAY, INITIAL),), events=6)
    summary = rt.run()
    assert not summary["terminated"] and rt.dormancy is not None
    assert rt.termination.check(rt.wallet, rt.clock.now_ns,
                                cheapest_seat_micro=rt._cheapest_seat_micro()) == DORMANT
    assert not kinds(rt, "compute.route") and not kinds(rt, "release")
    assert len(kinds(rt, "runtime.event_done")) >= 6  # every world and internal event ran
    assert rt.wallet.unlocked == 0 and rt.wallet.balance == INITIAL
    # A world with no backing at all dies at once, exactly as before.
    dead = Runtime(BASE, events=6, seed=1, initial_balance_micro=0, ledger_path=None,
                   drip=False, router_gamma=.1, exchange=FakeExchange(),
                   provider=ScriptedProvider())
    assert dead.run()["termination_reason"] == "balance_zero"


def test_dormant_state_and_locked_backing_restore_identically_in_memory():
    rt = endowed(INITIAL, ((NS_PER_DAY, INITIAL),), events=3)
    rt.run()
    assert rt.dormancy is not None
    restored = endowed(INITIAL, ((NS_PER_DAY, INITIAL),), events=3)
    restore_runtime(restored, runtime_state(rt))
    assert restored.dormancy == rt.dormancy
    assert restored.wallet.state() == rt.wallet.state()
    assert restored.wallet.next_release_ns == rt.wallet.next_release_ns
    assert runtime_state(restored) == runtime_state(rt)


def test_wake_shows_the_endowment_and_the_pause():
    rt = endowed(60_000_000, ((NS_PER_DAY, 60_000_000),))
    rt.wallet.launch(0)
    rt._manage_reserve_window()
    assert kinds(rt, "novelty.window")[-1]["budget"] == 40_000_000 == rt.wallet.unlocked
    pots = public_window_item(rt, window=rt.window.index, event=rt.n)["pots"]
    assert (pots["locked_micro"], pots["unlocked_micro"], pots["dormant"]) == (
        60_000_000, 40_000_000, False)
    assert pots["next_release_ns"] == NS_PER_DAY
    rt._enter_dormancy(5, trigger="wallet")
    assert public_window_item(rt, window=rt.window.index, event=rt.n)["pots"]["dormant"]
    observatory = _Observatory()
    for item in kinds(rt, "dormant"):
        observatory.feed(item)
    dormancy = observatory.result(rt.m)["pots"]["dormancy"]
    assert dormancy == [{"state": "entered", "ts_ns": 5, "locked_micro": 60_000_000,
                         "next_release_ns": NS_PER_DAY}]


def test_rent_is_bytes_times_time_at_the_manifest_rate_with_an_exact_remainder():
    spec = NotesSpec()
    cap = {"bytes": 262144, "rent_ns": 0, "rent_carry": 0}
    day_micro, day_carry = accrue(cap, NS_PER_DAY, spec)
    assert day_micro == 10485 and day_carry > 0  # about one cent a day at the cap
    # Collecting every hour charges exactly what collecting once a day charges.
    hourly = dict(cap)
    total = 0
    for hour in range(1, 25):
        micro, carry = accrue(hourly, hour * NS_PER_DAY // 24, spec)
        hourly["rent_ns"], hourly["rent_carry"] = hour * NS_PER_DAY // 24, carry
        total += micro
    assert (total, hourly["rent_carry"]) == (day_micro, day_carry)
    # A small note over a two-minute window owes less than a micro-USD: carried, not rounded up.
    small = {"bytes": 15, "rent_ns": 0, "rent_carry": 0}
    assert accrue(small, 120 * 10**9, spec) == (0, 15 * 120 * 10**9 * 1)
    assert NotesSpec(micro_per_byte_day="720").rate() == (1, 120 * 10**9)  # 1 per byte-window


def test_rent_is_collected_at_window_boundaries_by_elapsed_time_not_by_window_count():
    rt = endowed(0, ())
    rt._manage_reserve_window()
    handle = decision(rt)
    text = "x" * (262144 - 4)
    # The flat call price: the transfer toll is gone (C4) and rent is what follows.
    assert put(rt, handle, text=text)[1] == 1
    assert rt.notes["fact"]["bytes"] == 262144
    before = rt.wallet.balance
    rt.clock.now_ns += NS_PER_DAY
    rt._manage_reserve_window()
    rt.clock.now_ns += NS_PER_DAY
    rt._manage_reserve_window()
    rents = kinds(rt, "note.rent")
    assert [r["cost"] for r in rents] == [10485, 10486]  # the .76 remainder carries over
    assert rt.wallet.balance == before - 10485 - 10486
    assert rt.notes["fact"]["rent_ns"] == rt.clock.now_ns and rt.notes["fact"]["rent_due"] == 0
    # An overwrite inherits the open interval: rewriting forgives nothing.
    rt.clock.now_ns += NS_PER_DAY // 2
    result, cost = put(rt, handle, text="short")
    assert "error" not in result and cost == 1
    assert rt.notes["fact"]["rent_ns"] == rt.clock.now_ns - NS_PER_DAY // 2
