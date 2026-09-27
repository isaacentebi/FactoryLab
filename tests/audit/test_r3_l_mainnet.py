"""A fake adapter cannot bypass the mainnet world-name restriction.

Mainnet admission is also bound, in the load path and not only in a script, to a
client namespace and to the exact ratified charter and roster the population voted.
"""

import re
import tomllib
from pathlib import Path
from uuid import uuid4

import pytest

from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict
from tests.runtime.test_manifests import _base

RATIFIED = Path(__file__).resolve().parents[2] / "docs/charter/edition1-short-ratified.toml"
ROSTER = "testnet-10m-roster"


def _ratified() -> tuple[dict, dict[str, str]]:
    """The exported artifact and the hashes it recorded, read the way the script reads them."""
    text = RATIFIED.read_text()
    metadata = dict(re.findall(r"^# (roster_sha256|charter_sha256) = ([0-9a-f]{64})$",
                               text, re.MULTILINE))
    return tomllib.loads(text)["charter"], metadata


def _current_roster_sha256() -> str:
    """The roster digest of the drafting roster as it stands today."""
    from factorylab.charter.provenance import roster_hash

    return roster_hash(manifest_from_dict(
        tomllib.loads((WORLDS_DIR / f"{ROSTER}.toml").read_text())))


def _funded(roster_sha256: str | None = None) -> dict:
    """The drafting roster as a funded mainnet manifest carrying its ratified provenance.

    W4 (primitive audit F12) edited this roster: its judges now accept Exposure. The
    edition-1 ratification recorded the roster before that edit, so it no longer
    admits this roster (``test_the_edition1_ratification_refuses_the_edited_roster``);
    the admission path is exercised here against the charter it ratified and the
    roster digest a re-ratification of today's roster would record.
    """
    raw = tomllib.loads((WORLDS_DIR / f"{ROSTER}.toml").read_text())
    charter, metadata = _ratified()
    raw["name"] = "funded"
    raw["exchange"] = {**raw["exchange"], "mainnet": True, "client_namespace": uuid4().hex}
    raw["charter"] = {**charter, "ratified_sha256": metadata["charter_sha256"],
                      "roster_sha256": roster_sha256 or _current_roster_sha256()}
    return raw


def test_the_edition1_ratification_refuses_the_edited_roster():
    """A roster edit is a new world (R8): the roster the population voted on is gone."""
    with pytest.raises(ValueError, match="mainnet roster differs"):
        manifest_from_dict(_funded(_ratified()[1]["roster_sha256"]))


def test_fake_mainnet_outside_funded_is_refused():
    data = _base()
    data["exchange"] = {"kind": "fake", "mainnet": True}
    with pytest.raises(ValueError, match="mainnet is only allowed"):
        manifest_from_dict(data)


def test_mainnet_requires_a_client_namespace():
    raw = _funded()
    raw["exchange"].pop("client_namespace")
    with pytest.raises(ValueError, match="client_namespace"):
        manifest_from_dict(raw)


def test_the_ratified_charter_and_its_roster_admit_the_funded_manifest():
    manifest = manifest_from_dict(_funded())
    assert manifest.exchange.mainnet is True
    assert len(manifest.charter.cards) == len(_ratified()[0]["cards"])


def test_one_edited_card_refuses_the_funded_manifest():
    raw = _funded()
    raw["charter"]["cards"][0] = {**raw["charter"]["cards"][0],
                                  "description": "Edited after ratification."}
    with pytest.raises(ValueError, match="ratified"):
        manifest_from_dict(raw)


def test_a_changed_roster_refuses_the_funded_manifest():
    raw = _funded()
    raw["assemblies"][0] = {**raw["assemblies"][0], "max_tokens": 4096}
    with pytest.raises(ValueError, match="roster"):
        manifest_from_dict(raw)


def test_mainnet_without_recorded_hashes_is_refused():
    raw = _funded()
    raw["charter"].pop("ratified_sha256")
    with pytest.raises(ValueError, match="ratified"):
        manifest_from_dict(raw)
    raw = _funded()
    raw["charter"].pop("roster_sha256")
    with pytest.raises(ValueError, match="roster"):
        manifest_from_dict(raw)
