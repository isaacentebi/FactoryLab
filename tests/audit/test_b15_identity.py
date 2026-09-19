"""B15: a manifest file cannot borrow another world's declared identity."""


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
