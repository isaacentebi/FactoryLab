"""A checkpoint the ledger cannot encode is refused or raised exactly as before.

``_snapshot`` refuses a state holding a non-finite number or nested too deep
(``snapshot.refused``), and any other encoding error surfaces. The refusal outranks
the error when a state holds both, wherever in the state either sits.
"""

import pytest

from factorylab.runtime import loop
from tests.conftest import make_runtime


def _snapshot_of(monkeypatch, state):
    rt = make_runtime()
    monkeypatch.setattr(loop, "runtime_state", lambda _rt: state)
    return rt, rt._snapshot("test")


def _deep(depth):
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("state", [
    {"routers": [{"weights": [0.5, float("nan")]}]},
    {"assembly_learners": {"a": {"loss": float("inf")}}},
    {"runtime": {"$map": []}, "routers": [_deep(5000)]},
    # A key the ledger cannot encode, before and after the refused number.
    {"config": {1: "x"}, "routers": [{"w": float("-inf")}]},
    {"routers": [{"w": float("nan")}], "retired_routers": [{2: "x"}]},
    {"config": {1: "x"}, "routers": [_deep(5000)]},
])
def test_a_nonfinite_or_overdeep_state_is_refused(monkeypatch, state):
    rt, written = _snapshot_of(monkeypatch, state)
    assert written is False
    item = rt.ledger._recovery_items()[-1]
    assert item["kind"] == "snapshot.refused" and item["boundary"] == "test"


def test_any_other_encoding_error_surfaces(monkeypatch):
    with pytest.raises(TypeError):
        _snapshot_of(monkeypatch, {"config": {1: "x"}, "routers": [{"w": 0.5}]})
