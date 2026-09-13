"""T27: insolvency evidence records transitions and the terminating count."""

from types import SimpleNamespace

from factorylab.runtime.compute import ComputeMixin


def test_insolvency_records_transitions_and_termination_count():
    recorded = []
    runtime = SimpleNamespace(
        _compute_routed=True, _compute_unaffordable=False, insolvency_count=0,
        m=SimpleNamespace(treasury=SimpleNamespace(insolvency_events=3)),
        _record_market=recorded.append,
    )
    for i, unaffordable in enumerate([False, False, True, True, False, False,
                                     True, True, True]):
        runtime._compute_unaffordable = unaffordable
        ComputeMixin._record_insolvency_event(runtime, SimpleNamespace(id=str(i)))
    assert [(item["event_id"], item["consecutive_events"], item["unaffordable"])
            for item in recorded] == [
        ("2", 1, True), ("4", 0, False), ("6", 1, True), ("8", 3, True),
    ]
    assert runtime.insolvency_count == 3
