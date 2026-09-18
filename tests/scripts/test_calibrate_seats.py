"""Offline tests for the seat calibration harness (edition 2, C13): zero spend, no network."""

from pathlib import Path
from urllib import request

import pytest

from factorylab.runtime.worlds import load_manifest
from factorylab.world.metering import Infeasible
from scripts.calibrate_seats import (
    SCENARIOS,
    BudgetExhausted,
    BudgetGuard,
    CalibrationProvider,
    calibrate,
)

WORLD = "worlds/scripted.toml"
CANDIDATE = "fake-haiku"
ORIGINAL_READ_TEXT = Path.read_text


def guarded_read_text(path, *args, **kwargs):
    assert path.suffix != ".key" and path.name != ".env", "must not read credentials"
    return ORIGINAL_READ_TEXT(path, *args, **kwargs)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(request.OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(Path, "read_text", guarded_read_text)


@pytest.fixture
def manifest():
    return load_manifest(WORLD)


@pytest.mark.gate  # measured over 0.9 s: a subprocess, a jail timeout or a long loop
def test_budget_guard_refuses_over_cap_calls(manifest):
    guard = BudgetGuard(budget_micro=1000)
    guard.admit(1000)
    with pytest.raises(BudgetExhausted) as refused:
        guard.admit(1001)
    assert isinstance(refused.value, Infeasible)
    guard.charge(600)
    with pytest.raises(BudgetExhausted):
        guard.admit(401)
    guard.admit(400)
    assert guard.refusals == 2

    # End to end: a cap below the cheapest ceiling starts no call and spends nothing.
    report = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                       seed=3, budget_micro=10, long_context_bytes=90_000)
    rows = report["trees"][CANDIDATE]
    assert len(rows) == len(SCENARIOS)
    assert all(r["status"] == "failed" and r["budget_refused"] for r in rows)
    assert all(r["cost_micro"] == 0 for r in rows)
    assert report["budget"]["spent_micro"] == 0 == report["wallet_spent_micro"]
    assert report["budget"]["refused_calls"] == len(SCENARIOS)
    assert report["candidates"][CANDIDATE]["trees"] == 0

    # A cap that covers some trees stops exactly where the meter says it is spent.
    cheap = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                      seed=3, budget_micro=None, long_context_bytes=90_000)
    first = cheap["trees"][CANDIDATE][0]["cost_micro"]
    ceiling = cheap["planned_first_call_ceiling_micro"][CANDIDATE] // len(SCENARIOS)
    partial = calibrate(manifest, [CANDIDATE], provider=CalibrationProvider(), repeats=1,
                        seed=3, budget_micro=first + ceiling, long_context_bytes=90_000)
    ran = [r for r in partial["trees"][CANDIDATE] if not r["budget_refused"]]
    assert 1 <= len(ran) < len(SCENARIOS)
    assert partial["budget"]["spent_micro"] <= first + ceiling
    assert partial["budget"]["spent_micro"] == partial["wallet_spent_micro"]
