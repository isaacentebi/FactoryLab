"""Public metadata preflight names unavailable markets before launch."""

import json
from dataclasses import replace
from io import BytesIO

import pytest

from factorylab.runtime.cli import main
from factorylab.runtime.worlds import load_manifest


@pytest.fixture
def metadata(monkeypatch):
    def response(request, timeout):
        assert timeout == 5
        kind = json.loads(request.data)["type"]
        value = {"universe": [{"name": "BTC"}]} if kind == "meta" else {
            "tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "PURR"}],
            "universe": [{"name": "@1", "tokens": [1, 0]}],
        }
        return BytesIO(json.dumps(value).encode())
    monkeypatch.setattr("urllib.request.urlopen", response)


def test_missing_pair_and_coin_are_named_by_manifest_command(metadata, monkeypatch, capsys):
    manifest = load_manifest("testnet")
    manifest = replace(manifest, exchange=replace(manifest.exchange,
                       coins=("BTC", "MISSING"), spot_pairs=("ETH/USDC",)))
    monkeypatch.setattr("factorylab.runtime.cli.load_manifest", lambda _: manifest)
    assert main(["manifest", "--world", "testnet"]) == 1
    report = json.loads(capsys.readouterr().out)["venue_validation"]
    assert report == {"status": "invalid", "missing_coins": ["MISSING"],
                      "missing_spot_pairs": ["ETH/USDC"]}


def test_metadata_uses_token_pair_names_not_wire_names(metadata):
    manifest = load_manifest("testnet")
    manifest = replace(manifest, exchange=replace(manifest.exchange,
                       coins=("BTC",), spot_pairs=("PURR/USDC",)))
    assert manifest.validate_venue_metadata() == {
        "status": "valid", "missing_coins": [], "missing_spot_pairs": []}


def test_offline_preflight_names_unverified_markets(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("offline")
    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    manifest = load_manifest("testnet")
    assert manifest.validate_venue_metadata() == {
        "status": "unavailable", "coins": list(manifest.exchange.coins),
        "spot_pairs": list(manifest.exchange.spot_pairs)}


@pytest.mark.network
def test_testnet_metadata_accepts_shipped_pairs():
    report = load_manifest("testnet").validate_venue_metadata()
    if report["status"] == "unavailable":
        pytest.skip("public venue metadata is unreachable")
    assert report == {"status": "valid", "missing_coins": [], "missing_spot_pairs": []}
