"""A12: Venice credit is a journaled population transfer with a persistent budget."""

import base64
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from eth_account import Account

from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.world.venice import prepare_top_up, top_up
from factorylab.world.x402 import HTTPResponse
from tests.runtime.test_fidelity import runtime
from tests.world.test_x402 import quote as quote_fixture

# Every signature here goes through the production chokepoint, with a real
# ReserveGuard in this test's temporary lock directory (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("write_ahead")



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
    # Delivered provider credit is spending authority (financing, never income):
    # the factory's own capital became thinking money it may now spend.
    assert restored.wallet.balance == balance + 5_000_000
    owed = treasury.collect_financing()
    assert [(o["handle"], o["micro"]) for o in owed] == [("population", 5_000_000)]
    assert treasury.collect_financing() == []  # handed over exactly once
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


def test_a12_retries_use_the_journaled_unsigned_authorization():
    account = Account.create()  # Ephemeral test signer; no credential files or network.
    submissions = []

    def request(method, path, body, **headers):
        assert (method, path, body) == ("POST", "/x402/top-up", {})
        if not headers:
            return HTTPResponse(402, quote_fixture.__wrapped__())
        submissions.append(json.loads(base64.b64decode(headers["X-402-Payment"])))
        return HTTPResponse(200, {"success": True, "payer": account.address})

    from factorylab.runtime.capital_loop import ReserveGuard

    client = SimpleNamespace(address=account.address, _account=account, _request=request,
                             usdc_balance=lambda: 10_000_000, venice_balance=lambda: 1_000_000,
                             guard=ReserveGuard("test"), chain_head=lambda: 1)
    reference = prepare_top_up(client, now_s=100, nonce=bytes(32))
    saved = deepcopy(reference)
    assert not submissions and "signature" not in json.dumps(reference)
    client.venice_balance = lambda: 6_000_000
    assert top_up(client, reference)["credit_after_micro"] == 6_000_000
    top_up(client, reference)
    assert reference == saved
    assert submissions[0]["payload"] == submissions[1]["payload"]
    assert submissions[0]["payload"]["authorization"]["nonce"] == "0x" + "00" * 32
