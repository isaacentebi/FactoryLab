"""B15: a manifest file cannot borrow another world's declared identity."""

from pathlib import Path

import pytest

from factorylab.runtime.worlds import WORLDS_DIR, load_manifest


def test_loading_by_name_rejects_a_different_declared_identity(tmp_path, monkeypatch):
    raw = (WORLDS_DIR / "scripted.toml").read_text()
    (tmp_path / "alias.toml").write_text(raw)
    monkeypatch.setattr("factorylab.runtime.worlds.WORLDS_DIR", tmp_path)
    with pytest.raises(ValueError, match="file stem"):
        load_manifest("alias")
    with pytest.raises(ValueError, match="file stem"):
        load_manifest(str(tmp_path / "alias.toml"))


def test_edition_example_has_one_identity_and_one_card_per_observation():
    m = load_manifest("edition1-example")
    assert m.name == Path("edition1-example.toml").stem
    observations = [card.observation for card in m.charter.cards]
    assert len(observations) == len(set(observations))
    assert "model_cost_efficiency" in {card.id for card in m.charter.cards}
