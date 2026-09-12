import pytest

from factorylab.charter.charter import Charter, MetricCard, seed_charter


def test_seed_charter_renders_norms_and_cards() -> None:
    c = seed_charter()
    text = c.render()
    assert text.startswith("CHARTER (edition 1)")
    for n in c.norms:
        assert f"- {n}" in text
    assert "forecast_skill" in text and "acceptable: above zero" in text


def test_charter_validation() -> None:
    card = MetricCard("x", "nope", "d", "u", "w", "a", "o")
    with pytest.raises(ValueError):
        Charter(1, ("a norm",), (card,))
    with pytest.raises(ValueError):
        Charter(0, ("a norm",), ())
    with pytest.raises(ValueError):
        MetricCard("", "n", "d", "u", "w", "a", "o")
    ok = MetricCard("x", "a norm", "d", "u", "w", "a", "o")
    with pytest.raises(ValueError):
        Charter(1, ("a norm",), (ok, ok))


def test_seed_cards_name_runtime_observations():
    assert {c.observation for c in seed_charter().cards} == {
        "cost_per_return", "well_formed_rate", "forecast_skill",
    }


def test_duplicate_card_error_names_id():
    card = seed_charter().cards[0]
    with pytest.raises(ValueError, match="cost_per_return.*id"):
        Charter(1, seed_charter().norms, (card, card))
