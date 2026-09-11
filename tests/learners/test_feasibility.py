import pytest

from factorylab.learners.feasibility import filter


def test_exact_reasons_and_input_order_never_ranked():
    calls = []

    def predicate(action):
        calls.append(action)
        return action in ("z", "b"), f"budget:{action}"

    result = filter(["z", "x", "b", "a"], predicate)
    assert calls == ["z", "x", "b", "a"]
    assert result.feasible == ["z", "b"]
    assert result.excluded == [("x", "budget:x"), ("a", "budget:a")]
    assert filter([], predicate).feasible == []


def test_non_string_reason_fails():
    with pytest.raises(TypeError):
        filter(["a"], lambda _: (False, None))
