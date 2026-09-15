"""Edition 3, C2: the seat decides when to think.

The acceptance the plan states, verbatim: with two producers, one deferring six
ticks, the ledger shows no invocation for that seat in that span, one on the
seventh, and an immediate wake on a fill in between. Beside it: a watcher that
fires on a scripted price cross and wakes a deferred owner, the contents of a
coalesced update over a scripted price path, a subscription change that
survives a checkpoint and a restore, and the quiet tick — a draw that reaches
nobody and is nothing rather than an abstention.
"""

from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal, Rejected, parse_proposals
from factorylab.cortex.request import Return
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.subscriptions import (
    Subscription,
    parse_subscribe,
    validate_trigger,
)
from tests.conftest import make_runtime

WATCHER_CODE = "import sys\nprint('{\"action\": \"hold\"}')\n"


#: The scripted world's own two producers, given the common work contract of
#: edition 3: one coalesced update, with the fills and the watcher's news going to
#: the seat that owns the position.
SEAT_A, SEAT_B = "seed-observer", "seed-decider"


def producers(rt, *, b_accepts=("WorldUpdate", "Fill", "WatcherFired")):
    """Put both seed producers on the coalesced update, keeping their entitlements."""
    for seat, accepts in ((SEAT_A, ("WorldUpdate",)), (SEAT_B, tuple(b_accepts))):
        rt._instantiate(replace(rt.assemblies[seat].spec, accepts=frozenset(accepts)))
    for kind in ("WorldUpdate", "Fill", "WatcherFired"):
        rt._open_epoch(kind)
    return rt


def world_update(rt, *, coin="BTC", mid="100"):
    """One coalesced update event, as the loop emits it after a tick."""
    rt.emitted += 1
    return Event(f"wu-{rt.emitted}", EventKind.WORLD_UPDATE, rt.clock.now_ns,
                 {"tick": rt.tick_index,
                  "since_you_last_woke": {"coins": {coin: {"last": mid, "prints": 1}}}},
                 "runtime")


def market_mid(rt, coin, mid, *, seconds=0):
    rt.emitted += 1
    return Event(f"mid-{rt.emitted}", EventKind.MARKET_MID,
                 rt.clock.now_ns + seconds * 1_000_000_000,
                 {"coin": coin, "mid": mid}, "exchange")


def funding(rt, coin, rate, *, seconds=0):
    rt.emitted += 1
    return Event(f"fund-{rt.emitted}", EventKind.FUNDING,
                 rt.clock.now_ns + seconds * 1_000_000_000,
                 {"coin": coin, "rate": rate}, "exchange")


def fill(rt, coin="BTC"):
    rt.emitted += 1
    return Event(f"fill-{rt.emitted}", EventKind.FILL, rt.clock.now_ns,
                 {"coin": coin, "side": "buy", "size": "0.01", "price": "100"}, "exchange")


def entries(rt, monkeypatch):
    """Every ledger item this test writes, in order."""
    written = []
    append = rt.ledger.append

    def record(entry):
        written.append(entry)
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", record)
    return written


def route_until(rt, factory, seat, *, attempts=30):
    """Route fresh events of one kind until the named seat is woken; True if it was.

    The router's draw always keeps NOOP feasible, so one event is not a promise
    that an eligible seat is sampled. Every attempt is one more chance at the
    same tick: a seat that is asleep is not reached by any number of them, and
    an awake one is reached.
    """
    before = rt.stats.invocations_by_assembly.get(seat, 0)
    for _ in range(attempts):
        rt._route(factory())
        if rt.stats.invocations_by_assembly.get(seat, 0) > before:
            return True
    return False


def answer(rt, seat, outputs):
    """Apply one seat's own answer fields (subscribe, defer) as the producer path does."""
    handle = f"answer-{seat}-{rt.n}"
    rt._apply_thinking(handle, seat, Return(handle, outputs, 0, "ok"))
    return handle


# ---- the acceptance ---------------------------------------------------------


def test_a_deferring_seat_is_absent_for_six_ticks_and_woken_by_a_fill(monkeypatch):
    rt = producers(make_runtime())
    rt._manage_reserve_window()
    written = entries(rt, monkeypatch)
    answer(rt, SEAT_B, {"action": "defer", "defer": 6})
    deferred = [e for e in written if e["kind"] == "seat.deferred"]
    assert deferred and deferred[-1]["ticks"] == 6 and deferred[-1]["until_tick"] == 6

    for tick in range(1, 7):
        rt.ticks_consumed = tick
        assert rt._asleep(SEAT_B, world_update(rt)).startswith("asleep: deferred")
        assert not route_until(rt, lambda: world_update(rt), SEAT_B, attempts=8)
    assert SEAT_B not in rt.stats.invocations_by_assembly
    assert rt.stats.invocations_by_assembly.get(SEAT_A, 0) > 0

    # A fill is money and safety: it reaches the seat whatever it deferred, and
    # buying it does not end the sleep it paid for.
    rt.ticks_consumed = 3
    assert rt._asleep(SEAT_B, fill(rt)) == ""
    assert route_until(rt, lambda: fill(rt), SEAT_B)
    assert rt._asleep(SEAT_B, world_update(rt)).startswith("asleep: deferred")

    # The seventh tick is the seat's own again.
    rt.ticks_consumed = 7
    assert rt._asleep(SEAT_B, world_update(rt)) == ""
    before = rt.stats.invocations_by_assembly.get(SEAT_B, 0)
    assert route_until(rt, lambda: world_update(rt), SEAT_B)
    assert rt.stats.invocations_by_assembly[SEAT_B] == before + 1
    assert rt.subscription_book.last_wake[SEAT_B] == 7
    assert SEAT_B not in rt.subscription_book.deferred_until


def test_a_quiet_tick_is_nothing_rather_than_an_abstention(monkeypatch):
    rt = producers(make_runtime())
    rt._manage_reserve_window()
    written = entries(rt, monkeypatch)
    answer(rt, SEAT_A, {"action": "defer", "defer": 4})
    answer(rt, SEAT_B, {"action": "defer", "defer": 4})
    decisions, noops = rt.stats.decisions, rt.stats.noops
    rt.ticks_consumed = 2
    rt._route(world_update(rt))
    assert rt.stats.decisions == decisions and rt.stats.noops == noops
    assert not rt._compute_routed
    quiet = [e for e in written if e["kind"] == "tick.quiet"]
    assert len(quiet) == 1
    assert quiet[0]["absent"] == 2
    assert set(quiet[0]["why"]) == {SEAT_A, SEAT_B}
    assert all(why.startswith("asleep: deferred") for why in quiet[0]["why"].values())


def test_the_coalesced_update_folds_a_scripted_price_path(monkeypatch):
    rt = producers(make_runtime())
    for seconds, (coin, mid) in enumerate(
            [("BTC", "100"), ("BTC", "104"), ("ETH", "20"), ("BTC", "97"), ("BTC", "101")]):
        rt._fold_world_event(market_mid(rt, coin, mid, seconds=seconds))
    rt._fold_world_event(funding(rt, "BTC", "0.0001", seconds=6))
    rt._fold_world_event(funding(rt, "BTC", "-0.0002", seconds=7))
    rt.ticks_consumed = 1
    block = rt.subscription_book.peek(SEAT_A, now=rt.tick_index)
    assert block["prints"] == 5
    assert block["events"] == {"MarketMid": 5, "Funding": 2}
    assert block["coins"]["BTC"] == {
        "first": "100", "last": "101", "high": "104", "low": "97", "prints": 4,
        "first_t_s": block["coins"]["BTC"]["first_t_s"],
        "last_t_s": block["coins"]["BTC"]["last_t_s"],
    }
    assert block["coins"]["ETH"]["first"] == block["coins"]["ETH"]["last"] == "20"
    assert [p["rate"] for p in block["funding"]["BTC"]] == ["0.0001", "-0.0002"]
    assert block["coins"]["BTC"]["last_t_s"] > block["coins"]["BTC"]["first_t_s"]
    # One update per seat per tick: taking it starts the next one from here.
    taken = rt.subscription_book.take(SEAT_A, now=rt.tick_index)
    assert taken == block
    assert rt.subscription_book.peek(SEAT_A, now=rt.tick_index)["prints"] == 0
    # The other seat's fold is its own and still unread.
    assert rt.subscription_book.peek(SEAT_B, now=rt.tick_index)["prints"] == 5


def test_a_subscription_change_is_the_seats_own_and_survives_a_restore(monkeypatch):
    rt = producers(make_runtime())
    rt._manage_reserve_window()
    written = entries(rt, monkeypatch)
    rt.ticks_consumed = 3
    answer(rt, SEAT_B, {"action": "hold",
                          "subscribe": {"kinds": ["WorldUpdate", "Fill"], "coins": ["btc"],
                                        "cadence_floor": 9},
                          "defer": 2})
    changed = [e for e in written if e["kind"] == "subscription.changed"]
    assert changed[-1]["cadence_floor"] == 9 and changed[-1]["coins"] == ["BTC"]
    # No ballot: the seat's own money, adopted where it was answered.
    assert not any(e["kind"].startswith("proposal") for e in changed)

    state = runtime_state(rt)
    fresh = producers(make_runtime())
    restore_runtime(fresh, state)
    sub = fresh.subscription_book.subscription(SEAT_B)
    assert sub == Subscription(frozenset({"WorldUpdate", "Fill"}), frozenset({"BTC"}), 9)
    assert fresh.subscription_book.deferred_until[SEAT_B] == 5
    assert fresh.subscriptions == rt.subscriptions

    # The restored world is still asleep, and its cadence floor still narrows.
    fresh.ticks_consumed = 4
    assert fresh._asleep(SEAT_B, world_update(fresh)).startswith("asleep: deferred")
    fresh.ticks_consumed = 6
    assert fresh._asleep(SEAT_B, world_update(fresh)) == ""
    fresh.subscription_book.woke(SEAT_B, now=6)
    fresh.ticks_consumed = 8
    assert fresh._asleep(SEAT_B, world_update(fresh)) == "asleep: cadence floor 9 ticks"


def test_a_watcher_fires_on_a_price_cross_and_wakes_its_deferred_owner(monkeypatch):
    rt = producers(make_runtime())
    rt._manage_reserve_window()
    rt.tool_jail_available = True
    rt.handle_to_assembly["owner-handle"] = SEAT_B
    rt._register("owner-handle", replace(
        AssemblyProposal("btc-watch", "producer", "program", "program", ("Tick",), 512, "low"),
        code=WATCHER_CODE, trigger={"kind": "price_cross", "coin": "btc", "level": "100"}))
    watcher = rt.subscription_book.watchers["btc-watch"]
    assert watcher["owner"] == SEAT_B
    assert watcher["trigger"] == {"kind": "price_cross", "coin": "BTC", "level": "100"}
    assert rt.assemblies["btc-watch"].spec.trigger == watcher["trigger"]

    mids = {"BTC": "99"}
    monkeypatch.setattr(rt, "_observed_world",
                        lambda: {"mids": dict(mids), "funding": {}, "equity_usd": "100"})
    written = entries(rt, monkeypatch)
    answer(rt, SEAT_B, {"action": "defer", "defer": 6})
    calls = rt.stats.invocations_by_assembly.get("btc-watch", 0)

    rt.internal.clear()  # the Registered announcement is not what this test reads
    rt._evaluate_watchers()  # the first look only records where the world was
    assert not rt.internal
    mids["BTC"] = "101"  # the scripted cross
    rt._evaluate_watchers()
    evaluated = [e for e in written if e["kind"] == "watcher.evaluated"]
    assert [e["fired"] for e in evaluated] == [False, True]
    assert all(e["cost"] == rt.m.prices.program_micro_per_call for e in evaluated)
    # A watcher is the kernel's arithmetic, not a model call.
    assert rt.stats.invocations_by_assembly.get("btc-watch", 0) == calls

    fired = rt.internal.pop()
    assert fired.kind is EventKind.WATCHER_FIRED
    assert fired.payload["owner"] == SEAT_B
    assert fired.payload["trigger"] == {"kind": "price_cross", "coin": "BTC", "level": "100",
                                        "previous": "99", "observed": "101"}
    # The owner is deferred and is woken anyway; nobody else is offered the news.
    assert rt._asleep(SEAT_B, fired) == ""
    assert rt._asleep(SEAT_A, fired) == "asleep: this event is addressed to another seat"
    before = rt.stats.invocations_by_assembly.get(SEAT_B, 0)
    assert route_until(rt, lambda: fired, SEAT_B)
    assert rt.stats.invocations_by_assembly[SEAT_B] == before + 1


# ---- the vocabulary of what a seat may say ----------------------------------


def test_a_subscription_narrows_the_contract_and_says_why_when_it_cannot(monkeypatch):
    rt = producers(make_runtime())
    written = entries(rt, monkeypatch)
    answer(rt, SEAT_A, {"subscribe": {"kinds": ["Verdict"]}})
    refused = [e for e in written if e["kind"] == "subscription.refused"]
    assert refused and "outside your contract" in refused[-1]["reason"]
    assert rt.subscription_book.subscription(SEAT_A) == Subscription()
    assert rt.registration_feedback[-1]["kind"] == "subscription"
    answer(rt, SEAT_A, {"defer": "soon"})
    assert any("defer must be an integer" in e.get("reason", "")
               for e in written if e["kind"] == "subscription.refused")

    current = Subscription(frozenset({"WorldUpdate"}), None, 1)
    kept = parse_subscribe({"cadence_floor": 4}, current, frozenset({"WorldUpdate", "Fill"}))
    assert kept == Subscription(frozenset({"WorldUpdate"}), None, 4)
    assert parse_subscribe({"kinds": None}, kept, frozenset({"WorldUpdate"})).kinds is None
    for bad in ({"cadence_floor": 0}, {"coins": []}, {"unknown": 1}, "no"):
        with pytest.raises(ValueError):
            parse_subscribe(bad, current, frozenset({"WorldUpdate"}))


def test_a_trigger_is_a_program_seats_field_and_the_kernel_can_settle_it():
    assert validate_trigger({"kind": "equity_below", "level": 50}) == {
        "kind": "equity_below", "level": "50"}
    for bad in ({"kind": "vibes"}, {"kind": "price_cross"}, {"kind": "funding_sign"},
                {"kind": "equity_below"}, {"kind": "equity_below", "coin": "BTC", "level": 1},
                {"kind": "funding_sign", "coin": "BTC", "level": 1}):
        with pytest.raises(ValueError):
            validate_trigger(bad)
    accepted, rejected = parse_proposals(
        {"register": [
            {"kind": "assembly", "id": "watch-me", "role": "producer", "model_id": "fake-haiku",
             "system_prompt": "Look.", "accepts": ["Tick"],
             "trigger": {"kind": "funding_sign", "coin": "BTC"}}]},
        event_kinds=frozenset({"Tick"}), known_models=frozenset({"fake-haiku"}),
        known_assemblies=frozenset(), tool_jail=True)
    assert not accepted and isinstance(rejected[0], Rejected)
    assert "program seat" in rejected[0].reason


def test_the_action_vocabulary_names_investigation_construction_and_governance():
    from factorylab.runtime.propensity import ACTION_CLASSES, action_class, declared_record

    assert ACTION_CLASSES == ("hold", "investigate", "build", "govern", "defer", "order")
    assert action_class("hold", {"action": "hold"}) == "hold"
    assert action_class("hold", {"action": "hold"}, tool_calls=2) == "investigate"
    assert action_class("buy:BTC:xs", {"action": "order"}) == "order"
    assert action_class("hold", {"register": [{"kind": "assembly"}]}) == "build"
    assert action_class("hold", {"register": [{"kind": "challenge"}]}) == "govern"
    assert action_class("hold", {"action": "defer", "defer": 6}) == "defer"
    assert action_class("hold", {"register": [{"kind": "tool"}]}, tool_calls=1) == "build"
    assert action_class("verdict:0.8", {"verdict": 0.8}) == "verdict:0.8"
    assert action_class("malformed", {}) == "malformed"
    # A declaration over the six verbs names the action taken even when the label
    # it ends up carrying is the finer one.
    record, reason = declared_record(
        "buy:BTC:xs", {"hold": 0.7, "order": 0.3}, learner_id="assembly:x",
        state_hash="h", taken_class="order")
    assert reason is None and record.chosen == "order"
    assert dict(zip(record.action_ids, record.probs, strict=True))["order"] == pytest.approx(0.3)
    unusable, why = declared_record("buy:BTC:xs", {"hold": 1.0}, learner_id="assembly:x",
                                    state_hash="h", taken_class="order")
    assert unusable.chosen == "buy:BTC:xs" and "action class" in why
