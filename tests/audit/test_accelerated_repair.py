"""Accelerated-run failures retain evidence without inventing execution or proposals."""

import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.cortex.schematics import _is_registration_feedback
from factorylab.world.exchange import HyperliquidExchange
from factorylab.world.models import ModelRequest
from factorylab.world.openrouter import OpenRouterProvider
from tests.audit.test_r3_b_authority import _producing_decision
from tests.conftest import make_runtime


def test_unknown_submission_retains_safe_diagnostics_and_never_resubmits():
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address = "synthetic"
    exchange._info = SimpleNamespace(query_order_by_cloid=lambda *args: {"status": "unknownOid"})
    calls = []

    def submit():
        calls.append(1)
        raise TimeoutError("secret signing material must never reach the journal")

    result = exchange._submit("identity", submit)
    assert result.status == "uncertain"
    assert "submit exception: TimeoutError" in result.error
    assert "order not observed" in result.error
    assert "secret" not in result.error
    assert exchange._submit("identity", submit).status == "uncertain"
    assert calls == [1]
    exchange._info.query_order_by_cloid = lambda *args: {"status": "order", "order": {
        "status": "filled", "order": {"oid": 123, "origSz": "0.01", "sz": "0"}}}
    result = exchange._submit("identity", submit)
    assert result.status == "filled" and result.filled_size == Decimal("0.01")
    assert calls == [1]


def test_failed_lookup_exposes_exception_class_without_exception_payload():
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address = "synthetic"

    def lookup(*args):
        raise ValueError("Bearer secret")

    exchange._info = SimpleNamespace(query_order_by_cloid=lookup)
    assert exchange.lookup("identity").error == "lookup exception: ValueError"


def test_uncertain_order_blocks_same_coin_and_retains_first_diagnostic(monkeypatch):
    rt = make_runtime()
    h = _producing_decision(rt)
    exchange = rt.exchange.target
    calls = []

    def place(order):
        calls.append(order)
        raise TimeoutError("secret")

    monkeypatch.setattr(exchange, "place", place)
    result = rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert result["status"] == "uncertain"
    entries = [i for i in rt.ledger._recovery_items() if i["kind"] == "order.uncertain"]
    assert entries[0]["result"]["error"] == "write exception: TimeoutError"
    assert "secret" not in json.dumps(entries)
    h2 = _producing_decision(rt)
    refused = rt._venue_write(h2, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert refused["status"] == "rejected" and len(calls) == 1


def test_public_registration_feedback_contains_only_actual_proposal_failures():
    rt = make_runtime()
    rt._refuse_judgement("judge", "unknown handle")
    rt._refuse_order("trader", "uncertain prior order")
    rt._reject_registration("author", "unknown model", 0)
    world = rt._world_block()
    assert len(world["registration_feedback"]) == 1
    assert world["registration_feedback"][0]["kind"] == "registration.rejected"
    assert {f["kind"] for f in world["return_feedback"]} == {"judgement", "order.refused"}
    assert all("handle" not in f for f in rt.registration_feedback)
    assert rt.stats.registrations_rejected == 1


@pytest.mark.parametrize("reason,expected", [
    ("propensity: wrong action", False), ("judgement: closed", False),
    ("order: blocked", False), ("unknown model", True),
])
def test_old_checkpoint_feedback_is_classified_without_rewriting_history(reason, expected):
    assert _is_registration_feedback({"reason": reason}) is expected


def test_json_output_contract_is_sent_only_for_object_requests():
    payloads = []

    def transport(method, path, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "model": "fixture", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    provider = OpenRouterProvider(transport=transport)
    req = ModelRequest("fixture", "Reply with JSON", (), max_tokens=100)
    provider.complete(req)
    provider.complete(replace(req, json_object=True))
    assert "response_format" not in payloads[0]
    assert payloads[1]["response_format"] == {"type": "json_object"}


def test_rehearsal_refuses_charter_tampering_and_different_roster(tmp_path):
    from pathlib import Path

    from factorylab.runtime.worlds import load_manifest
    from scripts.draft_edition1 import charter_digest, render_toml, roster_hash
    from scripts.rehearsal import preflight, prepare

    base = Path("worlds/testnet-10m-roster.toml")
    manifest = load_manifest(str(base))
    body = render_toml([(manifest.charter.cards[1], None)], manifest.charter.norms)
    import tomllib

    digest = charter_digest(tomllib.loads(body)["charter"])
    charter = tmp_path / "charter.toml"
    charter.write_text(f"# roster_sha256 = {roster_hash(manifest)}\n"
                       f"# charter_sha256 = {digest}\n" + body)
    out = tmp_path / "testnet-check.toml"
    evidence = prepare(base, charter, out)
    assert evidence["charter_sha256"] == digest
    assert evidence["cards"][0]["window"]["n"] == 100
    original = out.read_text()
    out.write_text(original.replace('acceptable_region = "at least 0.9"',
                                    'acceptable_region = "at least 0.1"'))
    with pytest.raises(ValueError, match="exact voted charter"):
        preflight(out, charter)
    out.write_text(original.replace('max_tokens = 2000', 'max_tokens = 2100'))
    with pytest.raises(ValueError, match="roster differs"):
        preflight(out, charter)
    out.write_text(original)
    charter.write_text(charter.read_text().replace('at least 0.9', 'at least 0.1'))
    with pytest.raises(ValueError, match="survey export"):
        preflight(out, charter)


def test_rehearsal_will_not_run_an_implicit_seed_charter(tmp_path):
    import tomllib
    from pathlib import Path

    from factorylab.runtime.worlds import load_manifest
    from scripts.draft_edition1 import charter_digest, render_toml, roster_hash
    from scripts.rehearsal import preflight

    base = Path("worlds/testnet-10m-roster.toml")
    manifest = load_manifest(str(base))
    body = render_toml([(manifest.charter.cards[1], None)], manifest.charter.norms)
    digest = charter_digest(tomllib.loads(body)["charter"])
    charter = tmp_path / "charter.toml"
    charter.write_text(f"# roster_sha256 = {roster_hash(manifest)}\n"
                       f"# charter_sha256 = {digest}\n" + body)
    with pytest.raises(ValueError, match="exact voted charter"):
        preflight(base, charter)


def test_fresh_worlds_cannot_collide_with_each_other_or_historical_client_ids():
    import hashlib

    from factorylab.runtime.worlds import load_manifest
    from factorylab.world.exchange import live_exchange

    manifest = load_manifest("testnet-10m-roster")
    a_spec = replace(manifest.exchange, client_namespace="a" * 32)
    b_spec = replace(manifest.exchange, client_namespace="b" * 32)

    def factory(**kwargs):
        return HyperliquidExchange.__new__(HyperliquidExchange)

    a, b = live_exchange(a_spec, factory), live_exchange(b_spec, factory)
    again = live_exchange(a_spec, factory)
    legacy = live_exchange(manifest.exchange, factory)
    handle = "decision-17"
    assert a.client_id(handle).to_raw() != b.client_id(handle).to_raw()
    assert a.client_id(handle).to_raw() == again.client_id(handle).to_raw()
    assert legacy.client_id(handle).to_raw() == "0x" + hashlib.sha256(
        handle.encode()).hexdigest()[:32]
    assert a.client_id(handle).to_raw() != legacy.client_id(handle).to_raw()
    assert "client_namespace" not in json.loads(manifest.canonical_json())["exchange"]
    assert replace(manifest, exchange=a_spec).manifest_hash() != manifest.manifest_hash()


def test_committee_can_resolve_overlap_without_rewriting_any_card():
    from dataclasses import asdict

    from factorylab.charter.charter import seed_charter
    from scripts.ratify_charter import majority_subset, select_cards

    a = seed_charter().cards[1]
    b = replace(a, id="alternative", acceptable_region="at least 0.95")
    c = seed_charter().cards[2]
    raw = {"norms": list(seed_charter().norms), "cards": [asdict(x) for x in (a, b, c)]}
    with pytest.raises(ValueError, match="already named"):
        select_cards(raw, [a.id, b.id])
    with pytest.raises(ValueError, match="absent"):
        select_cards(raw, ["invented"])
    # The original five-seat majority is retained even if one seat abstains.
    assert majority_subset(raw, [[a.id], [a.id], None, None, None], 5) == []
    result = majority_subset(raw, [[a.id, c.id], [a.id, c.id], [a.id], [b.id], None], 5)
    assert result == [(a, None)]


def test_ratification_accepts_toml_omitted_null_scope():
    import tomllib
    from dataclasses import replace

    from factorylab.charter.charter import seed_charter
    from factorylab.charter.windows import MetricWindow
    from scripts.draft_edition1 import render_toml
    from scripts.ratify_charter import select_cards

    card = replace(seed_charter().cards[1], window=MetricWindow("returns", 10, None))
    raw = tomllib.loads(render_toml([(card, None)], seed_charter().norms))["charter"]
    assert "per" not in raw["cards"][0]["window"]
    assert select_cards(raw, [card.id]) == [(card, None)]


def test_rehearsal_retains_original_treasury_and_balance_floor():
    from factorylab.runtime.worlds import load_manifest

    original = load_manifest('testnet')
    rehearsal = load_manifest('testnet-10m-roster')
    assert rehearsal.treasury == original.treasury
    assert rehearsal.termination.balance_floor_micro == original.termination.balance_floor_micro
