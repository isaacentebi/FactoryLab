import pytest

from factorylab.versioning.operator import (
    contraction,
    observed_gap,
    total_variation,
    transition_operator,
)
from factorylab.versioning.report import summary


def test_identity_has_no_gap_bound():
    assert contraction([[1.0, 0.0], [0.0, 1.0]]) == {"delta": 1.0, "gap_bound": 0.0}


def test_uniform_rows_have_unit_gap_bound():
    assert contraction([[0.5, 0.5], [0.5, 0.5]]) == {"delta": 0.0, "gap_bound": 1.0}
    op = transition_operator([(0,), (0,), (1,), (1,), (0,)])
    assert op["matrix"] == [[0.5, 0.5], [0.5, 0.5]]
    assert op["gap_bound"] == 1


def test_terminal_cell_and_short_runs():
    op = transition_operator([(0,), (1,)])
    assert op["matrix"] == [[0.0, 1.0], [0.0, 1.0]]
    assert op["unobserved_exits"] == [[1]]
    assert op["mixing"] == 1
    assert transition_operator([(0,)])["gap_bound"] is None
    assert transition_operator([])["mixing"] is None
    assert total_variation([(0,)], [(0,), (1,), (1,)]) == pytest.approx(2 / 3)


def test_fixed_cells_preserve_missing_dimensions_and_ignore_unpriced_channels(diary):
    report = summary(
        diary(
            [
                {"verdict": 0, "fast": 2, "exposure": 1, "cards": {"cost": 7},
                 "regions": {"cost": {"kind": "max", "hi": 6, "lo": None, "scale": 1}}},
                {"verdict": 0, "fast": 2, "cards": {"cost": 7}},
                {"verdict": 1, "cards": {}},
                {"verdict": 1, "cards": {"cost": 7}},
            ]
        ),
    )
    op = report["operator"]
    assert op["dimensions"] == ["cost", "registrations", "revision"]
    assert op["cuts"]["cost"] == [0, 1]
    assert report["windows"][0]["cell"] == [1, 1, 0]
    assert report["windows"][2]["cell"] == [-1, 1, 0]


def test_two_cell_alternation_has_no_gap_and_is_thrash(diary):
    rows = [{"cards": {"quality": i % 2}, "regions": {
        "quality": {"kind": "max", "hi": -1, "lo": None, "scale": 1},
    }} for i in range(12)]
    report = summary(diary(rows), k=3, tv_threshold=.2)
    assert report["operator"]["matrix"] == [[0, 1], [1, 0]]
    assert report["operator"]["delta"] == 1
    assert observed_gap([(0,), (1,)] * 6) == 0
    assert any(flag["kind"] == "thrash" for flag in report["pathologies"])


def test_the_gap_bound_reads_many_steps_and_imputes_no_second_attractor():
    """A drift that drains into one cell holds one attractor; one new reading at the end
    of a sample is not a second attractor; alternation never mixes."""
    assert observed_gap([(2,), (1,), (0,), (0,), (0,)]) == 1.0
    assert observed_gap([(0,)] * 8 + [(1,)]) > 0.95
    assert observed_gap([(0,)] * 4 + [(1,)] + [(0,)] * 4) == pytest.approx(6 / 7)
    assert observed_gap([(0,)] * 5 + [(1,)] * 5) == pytest.approx(0.2)
    assert observed_gap([(0,)]) is None and observed_gap([(0,), (0,)]) == 1.0


@pytest.mark.parametrize("matrix", [[[0.2, 0.2], [0.5, 0.5]], [[1, 0]], [[-1]], [[float("nan")]]])
def test_invalid_operator_is_rejected(matrix):
    with pytest.raises(ValueError):
        contraction(matrix)
