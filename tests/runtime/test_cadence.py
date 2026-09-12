import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.cadence import GovernanceCadence


def cadence(*, sample=200, ratio=3, backstop=200, min_support=30):
    ledger = Ledger(clock_ns=lambda: 0)
    return ledger, GovernanceCadence(ledger, sample=sample, min_ratio=ratio, backstop=backstop,
                                     min_support=min_support)


def record(gate, latency, index=0):
    gate.record(handle=f"forecast-{index}", predicate_id="return_paid_off",
                opened_event=index, settled_event=index + latency,
                opened_ns=100, settled_ns=100 + latency, status="settled")


def test_no_data_uses_backstop_cap_and_launch_anchor():
    _, gate = cadence()
    gate.launch(1_000)
    assert gate.slowest_period_ns(10) == 2_000
    assert gate.earliest_ns(10) == 7_000
    assert not gate.ready(now_ns=6_999, tick_interval_ns=10, window=1)
    gate.advance(600)
    assert gate.ready(now_ns=7_000, tick_interval_ns=10, window=1)
    assert gate.slowest_period_ns(2) == 400  # the current interval owns the cap


def test_nearest_rank_p90_and_bounded_history_fall_with_faster_settlements():
    _, gate = cadence(sample=10, backstop=1, min_support=10)
    for i, latency in enumerate(range(10, 110, 10)):
        record(gate, latency, i)
    assert gate.slowest_period_ns(10) == 900
    for i in range(10):
        record(gate, 5, i + 10)
    assert gate.slowest_period_ns(10) == 50
    record(gate, 1_000_000, 20)
    assert gate.slowest_period_ns(10) == 50  # one outlier does not determine p90
    record(gate, 1_000_000, 21)
    assert gate.slowest_period_ns(10) == 10_000_000  # slow evidence may exceed the backstop


def test_zero_event_latency_retains_the_backstop_floor():
    _, gate = cadence()
    record(gate, 0)
    assert gate.slowest_period_ns(10) == 2_000
    assert gate.earliest_ns(10) == 6_000


def test_deferral_once_per_window_and_separate_activation_spacing(monkeypatch):
    ledger, gate = cadence(backstop=10)
    entries = []
    append = ledger.append

    def capture(entry):
        result = append(entry)
        entries.append(dict(entry))
        return result

    monkeypatch.setattr(ledger, "append", capture)
    gate.approve("one")
    gate.approve("two")
    for _ in range(3):
        assert not gate.ready(now_ns=29, tick_interval_ns=1, window=1)
    assert not gate.ready(now_ns=29, tick_interval_ns=1, window=2)
    deferred = [i for i in entries if i["kind"] == "charter.deferred"]
    assert [(i["amendment_id"], i["window"]) for i in deferred] == [
        ("one", 1), ("two", 1), ("one", 2), ("two", 2),
    ]
    assert all(i["earliest_ns"] == 30 for i in deferred)
    gate.advance(30)
    assert gate.ready(now_ns=30, tick_interval_ns=1, window=3)
    gate.activated("one", 30, 1)
    assert not gate.ready(now_ns=30, tick_interval_ns=1, window=3)
    assert gate.world_block(1)["waiting"] == ["two"]
    assert gate.earliest_ns(1) == 60
    assert gate.world_block(1)["earliest_activation"] == "1970-01-01T00:00:00.000000060Z"
    assert gate.world_block(1)["slowest_period"] == "0.00000001s"


def test_mutations_require_successful_ledger_append(monkeypatch):
    ledger, gate = cadence()
    gate.approve("one")
    before = gate.world_block(1)

    def reject(_entry):
        assert gate.world_block(1) == before
        raise RuntimeError("unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", reject)
        for action in (
            lambda: record(gate, 10), lambda: gate.approve("two"),
            lambda: gate.activated("one", 600, 1), lambda: gate.launch(100),
            lambda: gate.ready(now_ns=0, tick_interval_ns=1, window=1),
        ):
            with pytest.raises(RuntimeError, match="unavailable"):
                action()
            assert gate.world_block(1) == before
    assert not gate.ready(now_ns=0, tick_interval_ns=1, window=1)
    assert gate.world_block(1) == before


@pytest.mark.parametrize("field", ["sample", "min_ratio", "backstop", "min_support"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_cadence_parameters(field, value):
    params = {"sample": 200, "min_ratio": 3, "backstop": 200, field: value}
    with pytest.raises(ValueError):
        GovernanceCadence(Ledger(clock_ns=lambda: 0), **params)
