"""The sampling actuator is blind without consequence, never calmed by it (wave 16, R-B).

Realized consequence is sparse: a window that scored no consequence at all has no
consequence reading. The actuator raises the consequence mix when the verdict mean
rises while consequence skill falls, and steps back otherwise; with fewer than
``immune.k`` supported readings it can see neither, so it holds the mix where it is and
says so (``sampling.blind``, published in ``world.adaptive_scoring``). Absence of
consequence is not evidence of calm.
"""

from __future__ import annotations

from tests.conftest import make_runtime


def _fire(rt, verdict: float, skill: float | None, scored: int) -> None:
    """Close one window with these readings and let the actuator act on it."""
    rt.stats.last_window_values = {"verdict_mean": verdict}
    rt.last_window_consequences = scored
    rt._evaluator_skill = lambda: skill
    rt.clockwork.loops.pop("sampling", None)  # due: the actuator's own loop has come round
    rt._sampling_actuator()


def _rows(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i.get("kind") == kind]


def test_a_window_without_consequence_never_steps_the_mix_back():
    rt = make_runtime()
    k = rt.m.immune.k
    rt.consequence_mix = raised = rt.ev.consequence_share + rt.ev.sampling_step
    # A proxy climbing while the world issues no consequence at all (a quiet venue: every
    # key uninformative). The cumulative skill figure still exists, but no window has a
    # reading of it.
    for i in range(2 * k):
        _fire(rt, 0.5 + 0.05 * i, 0.1, scored=0)
    assert rt.consequence_mix == raised
    assert not _rows(rt, "sampling.lower") and not _rows(rt, "sampling.raise")
    blind = _rows(rt, "sampling.blind")
    assert len(blind) == 2 * k and blind[-1]["supported"] == 0 and blind[-1]["needed"] == k
    assert rt._adaptive_scoring_block()["sampling_blind"]["supported"] == 0


def test_the_actuator_sees_again_once_k_windows_have_readings():
    rt = make_runtime()
    k = rt.m.immune.k
    _fire(rt, 0.5, 0.1, scored=0)
    for i in range(k):
        _fire(rt, 0.5 + 0.05 * i, 0.1 - 0.02 * i, scored=3)
    # The proxy rose while realized consequence fell over k supported windows: raise.
    assert _rows(rt, "sampling.raise")
    assert rt._adaptive_scoring_block()["sampling_blind"] is None
