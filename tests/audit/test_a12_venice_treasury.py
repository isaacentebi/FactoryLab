"""A12: Venice credit is a journaled population transfer with a persistent budget."""

import base64
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from eth_account import Account

from factorylab.runtime.cli import main
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import WORLDS_DIR, manifest_from_dict
from factorylab.world.evm import BASE, event_topic, word_address
from factorylab.world.venice import prepare_top_up, top_up
from factorylab.world.x402 import HTTPResponse
from tests.runtime.test_fidelity import runtime
from tests.world.test_treasury_rails import setup as rail_setup
from tests.world.test_x402 import quote as quote_fixture


def test_a12_fake_venice_leg_holds_then_moves_five_and_preserves_budget_on_resume():
    rt = runtime()
    treasury = rt.treasury
    treasury.open_window(1)
    treasury.transfer("to_reserve", "20", handle="fund-reserve", now_ns=1)
    treasury.tick(2)
    balance = rt.wallet.balance
    reserve = treasury.pots()["reserve"]
    assert treasury.transfer("to_venice", "6", handle="bad", now_ns=3)["status"] == "refused"
    result = treasury.transfer("to_venice", "5", handle="population", now_ns=3)
    assert result["status"] == "submitted"
    assert rt.wallet.available == balance - 5_000_000
    assert treasury.pots()["sellers"]["venice"] == 0
    restored = runtime()
    restore_runtime(restored, runtime_state(rt))
    treasury = restored.treasury
    assert treasury.tick(4)[0]["status"] == "confirmed"
    assert treasury.pots()["reserve"] == reserve - 5_000_000
    assert treasury.pots()["sellers"]["venice"] == 5_000_000
    assert restored.wallet.balance == balance
    assert treasury.tick(5) == []
    treasury.transfer("to_venice", "5", handle="population-again", now_ns=5)
    treasury.tick(6)
    assert treasury.transfer("to_venice", "5", handle="over-budget", now_ns=7)[
        "status"] == "refused"
    treasury.open_window(1)
    assert treasury.venice_spent == 10_000_000
    treasury.open_window(2)
    assert treasury.transfer("to_venice", "5", handle="next-window", now_ns=8)[
        "status"] == "submitted"
    assert restored.wallet.check_conservation()


def test_a12_topup_cli_refuses_existing_world_before_credentials_or_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "world.jsonl").write_text('{"format":1,"genesis_hash":"fixture"}\n')

    def forbidden(*a, **kw):
        raise AssertionError("seed topup reached credentials or payment client")

    monkeypatch.setattr("factorylab.runtime.cli._load_dotenv", forbidden)
    monkeypatch.setattr("factorylab.world.x402.X402Client", forbidden)
    assert main(["reserve", "topup", "--usd", "5"]) != 0


def test_a12_retries_use_the_journaled_unsigned_authorization():
    account = Account.create()  # Ephemeral test signer; no credential files or network.
    submissions = []

    def request(method, path, body, **headers):
        assert (method, path, body) == ("POST", "/x402/top-up", {})
        if not headers:
            return HTTPResponse(402, quote_fixture.__wrapped__())
        submissions.append(json.loads(base64.b64decode(headers["X-402-Payment"])))
        return HTTPResponse(200, {"success": True, "payer": account.address})

    client = SimpleNamespace(address=account.address, _account=account, _request=request,
                             usdc_balance=lambda: 10_000_000, venice_balance=lambda: 1_000_000)
    reference = prepare_top_up(client, now_s=100, nonce=bytes(32))
    saved = deepcopy(reference)
    assert not submissions and "signature" not in json.dumps(reference)
    client.venice_balance = lambda: 6_000_000
    assert top_up(client, reference)["credit_after_micro"] == 6_000_000
    top_up(client, reference)
    assert reference == saved
    assert submissions[0]["payload"] == submissions[1]["payload"]
    assert submissions[0]["payload"]["authorization"]["nonce"] == "0x" + "00" * 32


@pytest.mark.parametrize("missing", ["debit", "credit", "canonical", "authorization", None])
def test_a12_confirmation_requires_exact_canonical_debit_and_venice_credit(missing):
    """Confirmation follows the canonical debit; a short credit balance is recorded, not refused.

    Edition 2 (C5, cold audit F2): the balance is a stock and cannot prove the flow.
    A missing credit therefore confirms with the shortfall in the evidence, while
    every missing piece of the on-chain debit still refuses.
    """
    rail = rail_setup()
    rail.testnet = False
    rail.base.chain = BASE
    nonce = "0x" + "ab" * 32
    topics = [event_topic("AuthorizationUsed(address,bytes32)"),
              "0x" + word_address(rail.reserve_address).hex(), nonce]
    event = {"address": BASE.usdc, "topics": topics, "transactionHash": "0xtransaction"}
    rail.base.log_rows = [event]
    rail.base.proved = {"status": "0x1", "blockHash": "0xblock", "logs": [event]}
    rail.base.credit_transfer = missing != "debit"
    if missing == "canonical":
        rail.base.proved = None
    if missing == "authorization":
        rail.base.proved["logs"] = []
    state = {
        "amount_micro": 5_000_000,
        "reference": {"authorization": {"nonce": nonce, "to": "0xrecipient"},
                      "start_block": 99, "network": "eip155:8453", "credit_before_micro": 1},
        "route_data": {"submission": {"credit_after_micro": 1 if missing == "credit"
                                     else 5_000_001}},
    }
    result = rail._venice_receipt(state)
    assert (result is None) == (missing not in (None, "credit"))
    if result:
        assert result["fee_micro"] == 0 and result["received_micro"] == 5_000_000
        evidence = result["evidence"]
        assert evidence["credit_before_micro"] == 1 and evidence["amount_micro"] == 5_000_000
        assert evidence["observed_micro"] == (1 if missing == "credit" else 5_000_001)
        assert evidence["balance_shortfall_micro"] == (5_000_000 if missing == "credit" else 0)


@pytest.mark.parametrize("value", [True, 10.0, -1, "-1", "0.0000001"])
def test_a12_manifest_budget_refuses_inexact_or_negative_money(value):
    import tomllib

    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw.setdefault("treasury", {})["max_venice_per_window"] = value
    with pytest.raises(ValueError):
        manifest_from_dict(raw)
