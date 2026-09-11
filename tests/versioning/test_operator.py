import pytest

from factorylab.versioning import summary
from factorylab.versioning.operator import contraction, total_variation, transition_operator


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


def test_quantiles_support_missing_dimensions_and_ties(diary):
    report = summary(
        diary(
            [
                {"verdict": 0, "fast": 2, "exposure": 1, "cards": {"cost": 7}},
                {"verdict": 0, "fast": 2, "cards": {"cost": 7}},
                {"verdict": 1, "cards": {}},
                {"verdict": 1, "cards": {"cost": 7}},
            ]
        ),
        bins=3,
    )
    op = report["operator"]
    assert op["dimensions"] == ["verdict", "fast", "cost"]
    assert op["cuts"]["fast"] == [2, 2]
    assert report["windows"][0]["cell"] == [0, 0, 0]
    assert report["windows"][2]["cell"] == [1, -1, -1]


def test_two_cell_alternation_is_thrash_candidate(diary):
    report = summary(diary([{"verdict": i % 2} for i in range(12)]), k=3, tv_threshold=0.2)
    assert report["operator"]["matrix"] == [[0, 1], [1, 0]]
    assert report["operator"]["delta"] == 1
    assert any(flag["kind"] == "thrash" for flag in report["pathologies"])
    # With odd k=3, an alternating pair has block TV 1/3, not above default 0.5.
    assert not any(
        flag["kind"] == "thrash"
        for flag in summary(diary([{"verdict": i % 2} for i in range(12)]))["pathologies"]
    )


@pytest.mark.parametrize("matrix", [[[0.2, 0.2], [0.5, 0.5]], [[1, 0]], [[-1]], [[float("nan")]]])
def test_invalid_operator_is_rejected(matrix):
    with pytest.raises(ValueError):
        contraction(matrix)
