"""The router's reward line means what it claims (defects 2, 3, 4 and 14).

A router learns from the decisions it sampled, once each, on the evidence the
world produced about them. It does not learn from a draw it never made, from a
decision twice, or from a missing fact read as a zero.
"""

from factorylab.kernel.events import Event, EventKind
from tests.conftest import make_runtime


def _tick(n: int = 1) -> Event:
    return Event(f"tick-{n}", EventKind.TICK, n, {}, "test")


def test_a_quiet_tick_keeps_no_snapshot_in_a_keyed_router():
    """Defect 14: a draw that reached nobody opens no round, so it freezes none."""
    rt = make_runtime()
    state = rt._build_router("Tick", "blum_mansour", 0.1)
    rt._asleep = lambda _seat, _ev: "asleep: test"
    rt._route_with(state, _tick())
    assert any(i["kind"] == "tick.quiet" for i in rt.ledger._recovery_items())
    assert state.learner.inner._snapshots == {}
