"""Fill checkpoint validation precedes destination mutation (§II.b, §III.b)."""

import pytest

from factorylab.runtime.resume import ResumeError, decode, encode, restore_runtime, runtime_state
from tests.conftest import make_runtime


def test_genuine_three_field_legacy_cursor_keeps_live_constructor_defaults():
    source = make_runtime(live=True)
    state = runtime_state(source)
    components = decode(state["components"])
    saved = components["consequence_fills"]
    components["consequence_fills"] = {key: saved[key] for key in
                                      ("since_ns", "seen", "through_ns")}
    state["components"] = encode(components)
    target = make_runtime(live=True)
    restore_runtime(target, state)
    cursor = target.consequence_fills
    assert cursor.since_ns == saved["since_ns"]
    assert cursor.seen == saved["seen"]
    assert cursor.through_ns == saved["through_ns"]
    assert cursor.launch_ns == saved["since_ns"]
    assert cursor.read_ns is None
    assert cursor.measured is True
    assert cursor.propagation_bound_ns is None
    assert cursor.observation_complete is True


@pytest.mark.parametrize("field,value", [("since_ns", "bad"), ("seen", []),
                                         ("measured", 1), ("propagation_bound_ns", -1)])
def test_invalid_fill_component_refuses_before_any_runtime_mutation(field, value):
    source = make_runtime(live=True)
    source.clock.now_ns += 123
    state = runtime_state(source)
    components = decode(state["components"])
    components["consequence_fills"][field] = value
    state["components"] = encode(components)
    target = make_runtime(live=True)
    before = runtime_state(target)
    with pytest.raises(ResumeError, match="fill cursor"):
        restore_runtime(target, state)
    assert runtime_state(target) == before
