import pytest

from factorylab.kernel.events import Bus, Event, EventKind
from factorylab.kernel.termination import Termination


def test_ledger_first_subscription_order_and_reentrant_fifo(ledger, clock):
    bus = Bus(ledger)
    seen = []

    def first(event):
        assert ledger.verify()
        seen.append((event.id, "first"))
        if event.id == "outer":
            bus.publish(Event("nested-1", EventKind.TICK, 101, {}, "kernel"))
            bus.publish(Event("nested-2", EventKind.TICK, 102, {}, "kernel"))
            assert seen == [("outer", "first")]

    bus.subscribe("Tick", first)
    bus.subscribe(EventKind.TICK, lambda event: seen.append((event.id, "second")))
    bus.publish(Event("outer", "Tick", 100, {}, "world"))
    assert seen == [
        (id, subscriber)
        for id in ("outer", "nested-1", "nested-2")
        for subscriber in ("first", "second")
    ]
    Termination(ledger=ledger, bus=bus, clock_ns=clock).kill("audit")
    assert [ledger.decrypt_item(seq)["event"]["id"] for seq in range(3)] == [
        "outer",
        "nested-1",
        "nested-2",
    ]


def test_ledger_failure_prevents_delivery(ledger, monkeypatch):
    bus = Bus(ledger)
    seen = []
    bus.subscribe("Tick", seen.append)

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append", fail)
    with pytest.raises(OSError):
        bus.publish(Event("a", "Tick", 0, {}, "kernel"))
    assert seen == []


def test_callback_errors_do_not_drop_subscribers_or_queued_events(ledger):
    bus = Bus(ledger)
    seen = []

    def broken(event):
        if event.id == "outer":
            bus.publish(Event("nested", "Tick", 1, {}, "kernel"))
            raise ValueError("subscriber failed")

    bus.subscribe("Tick", broken)
    bus.subscribe("Tick", lambda event: seen.append(event.id))
    with pytest.raises(ExceptionGroup) as errors:
        bus.publish(Event("outer", "Tick", 0, {}, "kernel"))
    assert len(errors.value.exceptions) == 1
    assert seen == ["outer", "nested"]
    bus.publish(Event("after", "Tick", 2, {}, "kernel"))
    assert seen[-1] == "after"


def test_event_payload_cannot_change_between_subscribers(ledger):
    source = {"nested": ["original"]}
    event = Event("a", "Fill", 1, source, "exchange")
    source["nested"].append("mutation")
    assert event.payload["nested"] == ("original",)
    with pytest.raises(TypeError):
        event.payload["private"] = "author"
    bus = Bus(ledger)
    with pytest.raises(PermissionError):
        bus.publish(Event("forged", "Terminated", 2, {}, "kernel"))


def test_subscription_changes_take_effect_for_next_event(ledger):
    bus = Bus(ledger)
    seen = []

    def subscribe_during_delivery(event):
        seen.append(event.id)
        if event.id == "one":
            bus.subscribe("Tick", lambda _: seen.append("new"))

    bus.subscribe("Tick", subscribe_during_delivery)
    bus.publish(Event("one", "Tick", 1, {}, "world"))
    assert seen == ["one"]
    bus.publish(Event("two", "Tick", 2, {}, "world"))
    assert seen == ["one", "two", "new"]


def test_event_kinds_match_spec():
    # v0.4 section 4.5 plus the v0.5 section 1 additions
    assert {kind.value for kind in EventKind} == {
        "Launch",
        "Tick",
        "Drip",
        "MarketMid",
        "Funding",
        "Fill",
        "OrderRejected",
        "Verdict",
        "MetaVerdict",
        "ProducerReturn",
        "ForecastSettled",
        "Registered",
        "RouterReplaced",
        "Reconciled",
        "TransferIntent",
        "WalletChanged",
        "ReserveWindowOpened",
        "Terminated",
    }
    with pytest.raises(ValueError):
        Event("a", "Unknown", 0, {}, "kernel")


@pytest.mark.parametrize("override", [
    {"about": ""}, {"about": 1}, {"by": None}, {"tier": 1}, {"tier": True},
    {"tier": 2.0}, {"score": True}, {"score": -0.1}, {"score": 1.1},
    {"score": float("nan")}, {"score": float("inf")},
])
def test_meta_verdict_rejects_invalid_payload(override):
    with pytest.raises(ValueError):
        Event("meta", "MetaVerdict", 0,
              {"about": "decision-1", "by": "decision-2", "tier": 2, "score": 0.8,
               **override}, "runtime")


def test_meta_verdict_is_immutable_and_ledger_first(ledger):
    bus = Bus(ledger)
    event = Event("meta", "MetaVerdict", 0,
                  {"about": "decision-1", "by": "decision-2", "tier": 3, "score": 0.8},
                  "runtime")
    seen = []
    bus.subscribe("MetaVerdict", lambda e: seen.append((e, ledger.verify())))
    bus.publish(event)
    assert seen == [(event, True)]
    with pytest.raises(TypeError):
        event.payload["by"] = "assembly-id"
