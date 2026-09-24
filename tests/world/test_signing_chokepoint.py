"""Every EIP-3009 signature made with a reserve key passes one chokepoint.

``x402.sign_transfer_authorization`` signs only after its guard wrote the authorization,
durably, to the reserve's write-ahead record under the reserve's lock. Each signer is
tried here: with no guard, with the lock held elsewhere (a capital-loop run alive), and
with the lock free, when the nonce must be in the record before the payment leaves.
The lock directory is the test's own (tests/conftest.py); every key is a fixture.
"""

import base64
import json
from copy import deepcopy

import pytest
from eth_account import Account

from factorylab.runtime.capital_loop import (
    CapitalLoopRefused,
    ReserveGuard,
    ReserveLock,
    read_authorizations,
)
from factorylab.world.evm import RailError
from factorylab.world.models import ModelRequest
from factorylab.world.x402 import (
    AuthorizationNotRecorded,
    HTTPResponse,
    X402Error,
    authorization_typed_data,
    parse_quote,
    payment_header,
    sign_transfer_authorization,
)
from tests.world.test_market import MODEL, SellerHTTP
from tests.world.test_x402 import quote as quote_fixture

TEST_KEY = "0x" + "11" * 32  # Deliberately public test fixture, never a real wallet.
ACCOUNT = Account.from_key(TEST_KEY)


def record_nonces(address=ACCOUNT.address):
    from factorylab.runtime import capital_loop

    # The test's own lock directory (the autouse fixture replaces the default).
    path = capital_loop.default_lock_dir() / f"{address.lower()}.authorizations.jsonl"
    if not path.exists():
        return []
    return [(e["nonce"], e.get("origin")) for e in read_authorizations(path)]


def typed():
    accepted = parse_quote(HTTPResponse(402, quote_fixture.__wrapped__())).accepted
    return authorization_typed_data(accepted, ACCOUNT.address, now=1_000, nonce=bytes(32))


def test_the_chokepoint_signs_nothing_without_a_guard_that_recorded_it():
    with pytest.raises(AuthorizationNotRecorded, match="no write-ahead guard"):
        sign_transfer_authorization(ACCOUNT, typed(), guard=None, head=lambda: 1)

    def refusing(message, start_block):
        raise CapitalLoopRefused("capital_loop_reserve_locked")

    with pytest.raises(AuthorizationNotRecorded, match="capital_loop_reserve_locked"):
        sign_transfer_authorization(ACCOUNT, typed(), guard=refusing, head=lambda: 1)
    other = deepcopy(typed())
    other["message"]["from"] = Account.create().address
    with pytest.raises(AuthorizationNotRecorded, match="payer"):
        sign_transfer_authorization(ACCOUNT, other, guard=ReserveGuard("test"),
                                    head=lambda: 1)
    assert record_nonces() == []  # nothing refused was recorded, nothing was signed
    selected = parse_quote(HTTPResponse(402, quote_fixture.__wrapped__()))
    with pytest.raises(AuthorizationNotRecorded):
        payment_header(ACCOUNT, selected)  # the x402 envelope has no way around it


def test_an_x402_purchase_refuses_while_the_reserve_is_held_and_records_first():
    from factorylab.world.market import X402Provider

    seen = []

    class Watching(SellerHTTP):
        def __call__(self, method, url, payload, headers):
            # The Base head the record takes as start_block, read through the same wire.
            if payload and payload.get("method") == "eth_chainId":
                return HTTPResponse(200, {"id": 1, "result": hex(8453)})
            if payload and payload.get("method") == "eth_blockNumber":
                return HTTPResponse(200, {"id": 1, "result": hex(4_242)})
            if "PAYMENT-SIGNATURE" in headers:
                # The moment the payment leaves, its nonce is already in the record.
                payment = json.loads(base64.b64decode(headers["PAYMENT-SIGNATURE"]))
                seen.append((payment["payload"]["authorization"]["nonce"], record_nonces()))
            return super().__call__(method, url, payload, headers)

    fake = Watching()
    provider = X402Provider(private_key=TEST_KEY, transport=fake,
                            guard=ReserveGuard("x402_purchase"))
    provider.register(MODEL, 2000)
    with ReserveLock(ACCOUNT.address):  # a capital-loop run holds the reserve
        with pytest.raises(X402Error):
            provider.complete(ModelRequest(MODEL, "", ()))
    assert fake.payments == [] and record_nonces() == []
    unguarded = X402Provider(private_key=TEST_KEY, transport=SellerHTTP())
    unguarded.register(MODEL, 2000)
    with pytest.raises(X402Error, match="no write-ahead guard"):
        unguarded.complete(ModelRequest(MODEL, "", ()))
    provider.complete(ModelRequest(MODEL, "", ()))
    [(nonce, recorded)] = seen
    assert recorded == [(nonce, "x402_purchase")]
    from factorylab.runtime import capital_loop

    path = capital_loop.default_lock_dir() / f"{ACCOUNT.address.lower()}.authorizations.jsonl"
    assert [e["start_block"] for e in read_authorizations(path)] == [4_242]


def test_the_world_binds_a_guard_to_its_market_and_its_rail():
    from tests.conftest import make_runtime

    rt = make_runtime()
    guard = rt.market.target.guard
    assert isinstance(guard, ReserveGuard) and guard.origin == "x402_purchase"


def test_the_ordinary_and_hybrid_rails_sign_nothing_unbound_or_while_held(monkeypatch):
    from tests.world.test_venice_hybrid import live, top_up_state

    rail = live(monkeypatch)
    state = top_up_state(rail)
    rail.authorization_log = None
    with pytest.raises(RailError, match="no write-ahead authorization record"):
        rail.send("venice_top_up", state["reference"])
    rail.authorization_log = ReserveGuard("treasury")
    with ReserveLock(rail.reserve_address):
        with pytest.raises(RailError, match="capital_loop_reserve_locked"):
            rail.send("venice_top_up", state["reference"])
    assert [r for r in rail._x402.requests if "X-402-Payment" in r[2]] == []
    from factorylab.world.treasury_rails import LiveRail

    assert LiveRail.authorization_log is None  # unbound unless the runtime binds it


def test_a_real_validbefore_is_stamped_by_the_wall_clock_not_the_worlds():
    # The cold review: the ordinary rail stamped a mainnet authorization's validBefore
    # from the world's clock (started_ns), which may be virtual.
    import time

    from factorylab.world.evm import BASE
    from factorylab.world.treasury_rails import LiveRail
    from tests.world.test_treasury_rails import Chain
    from tests.world.test_venice_hybrid import Client

    rail = LiveRail.__new__(LiveRail)
    rail.testnet, rail.base = False, Chain(BASE)
    client = Client(Account.create())
    rail._venice_client = lambda: client
    reference = rail.prepare("venice_top_up", {"started_ns": 0, "received_micro": 5_000_000},
                             {})
    assert abs(reference["created_s"] - time.time()) < 5  # not 0, the virtual start
