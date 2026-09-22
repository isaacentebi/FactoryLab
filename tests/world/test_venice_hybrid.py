"""The hybrid capital-loop rehearsal: real Venice credit, paid for by the testnet pots.

Essay II.IV: "a continuous, reciprocal flow of capital is an objective requirement".
A testnet world cannot rehearse that flow with real Venice credit unless the profit
it spends is the profit it observes, so every hybrid conversion has two legs -- a
testnet shadow send out of the venue, then the real Base mainnet x402 top-up -- and
confirms only when both have. These tests hold the order, the failure matrix and the
accounting on scripted custodians and on the live rail's own code against fakes.
"""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_account import Account
from hyperliquid.utils.signing import USD_SEND_SIGN_TYPES, recover_user_from_user_signed_action

from factorylab.kernel.ledger import Ledger, canonical
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.runtime.resume import RecoveryJournal, encode
from factorylab.runtime.worlds import TreasurySpec, load_manifest
from factorylab.world.evm import BASE, BASE_SEPOLIA, Pending, RailError, event_topic, word_address
from factorylab.world.exchange import FakeExchange
from factorylab.world.treasury import (
    FAKE_MAINNET_RESERVE_MICRO,
    TOP_UP_WAIT_EXCEEDED,
    FakeTreasury,
)
from factorylab.world.treasury_rails import HybridRail
from factorylab.world.x402 import HTTPResponse
from tests.world.test_treasury_rails import Chain, Info
from tests.world.test_x402 import quote as quote_fixture

SINK = "0x000000000000000000000000000000000000dEaD"
FIVE = 5_000_000
S = 1_000_000_000


def hybrid(**kwargs):
    ledger = Ledger(clock_ns=lambda: 0)
    wallet = Wallet(100_000_000, ledger, clock_ns=lambda: 0)
    exchange = FakeExchange(start_cash_usd=Decimal("50"))
    treasury = FakeTreasury(ledger, wallet, exchange=exchange, venice_shadow_sink=SINK, **kwargs)
    wallet.bind_pots(treasury.pots)
    treasury.open_window(1)
    return treasury, wallet, exchange, ledger


def kinds(ledger, kind):
    return [i for i in ledger._recovery_items() if i["kind"] == kind]


def test_both_legs_confirm_and_the_wallet_pots_and_credit_agree():
    treasury, wallet, exchange, ledger = hybrid()
    books = treasury.rail.hybrid_books
    before, balance = treasury.pots(), wallet.balance
    drift = Reconciler.snapshot(balance, None, exchange, pots_view=before)["discrepancy_micro"]
    submitted = treasury.transfer("to_venice", "5", handle="seat-profit", now_ns=1)
    assert submitted["status"] == "submitted"
    # The shadow leg goes first; no real authorization exists until it has confirmed.
    assert books["shadow_sent"] == FIVE and books["authorizations"] == 0
    assert wallet.available == balance - FIVE  # the tranche is held, not yet spent
    assert treasury.tick(2) == []
    assert books["authorizations"] == 1 and len(books["submissions"]) == 1
    confirmed = treasury.tick(3)
    assert confirmed[0]["status"] == "confirmed"
    after = treasury.pots()
    # $5 of testnet profit left the observed venue and $5 of Venice credit arrived: the
    # observed total is unchanged, exactly as an ordinary reserve-paid conversion.
    assert after["venue"] == before["venue"] - FIVE
    assert after["sellers"]["venice"] == before["sellers"]["venice"] + FIVE
    assert after["total_micro"] == before["total_micro"] and after["complete"]
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO - FIVE  # outside the pots
    # Delivered credit is spending authority (financing, never income).
    assert wallet.balance == balance + FIVE and wallet.available == wallet.balance
    assert treasury.income["converted_from_principal_micro"] == FIVE
    assert treasury.income["earned_micro"] == 0
    assert [(o["handle"], o["micro"]) for o in treasury.collect_financing()] == [
        ("seat-profit", FIVE)]
    financing = kinds(ledger, "treasury.financing")
    assert [(f["source"], f["paid_from"], f["shadow_sink"]) for f in financing] == [
        ("venue_perps", "base_mainnet_reserve", SINK)]
    # The reconciler moves by the financing alone, as it does for any conversion.
    moved = Reconciler.snapshot(wallet.balance, None, exchange,
                                pots_view=after)["discrepancy_micro"] - drift
    assert moved == FIVE
    assert wallet.check_conservation() and not wallet.dead
    assert treasury.stranded == [] and not after["pending"]


def test_a_refused_shadow_leg_spends_nothing_real():
    treasury, wallet, exchange, ledger = hybrid()
    treasury.rail.script["reject_shadow"] = True
    cash, before = exchange._cash, treasury.pots()
    result = treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    assert result["status"] == "failed"
    books = treasury.rail.hybrid_books
    assert books["authorizations"] == 0 and books["submissions"] == []
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO
    assert exchange._cash == cash and treasury.pots()["total_micro"] == before["total_micro"]
    assert wallet.available == wallet.balance == 100_000_000
    assert not kinds(ledger, "treasury.financing") and treasury.collect_financing() == []
    treasury.rail.script["reject_shadow"] = False
    assert treasury.transfer("to_venice", "5", handle="h2", now_ns=2)["status"] == "submitted"


def test_a_top_up_that_fails_after_the_shadow_strands_recoverably_and_resumes_once():
    treasury, wallet, exchange, ledger = hybrid()
    treasury.rail.script["top_up_unavailable"] = True
    cash = exchange._cash
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    pots = treasury.pots()
    assert pots["pending"] and pots["pending_reason"] == "Venice top-up quote unavailable"
    treasury.tick(3)
    treasury.open_window(2)
    treasury.open_window(3)
    treasury.tick(4)
    # Past the bound the paid shadow leg parks with its hold and frees the slot.
    assert treasury.state["status"] == "stranded" and treasury.state["recoverable"]
    assert treasury.state["reason"] == TOP_UP_WAIT_EXCEEDED
    assert [s["stranded_micro"] for s in treasury.pots()["stranded"]] == [FIVE]
    assert not treasury.pots()["pending"]
    assert wallet.available == wallet.balance - FIVE
    # A kill here: the checkpoint carries the strand, its hold and the scripted books.
    saved = json.loads(json.dumps(treasury.snapshot()))
    restored = FakeTreasury(ledger, wallet, exchange=exchange, venice_shadow_sink=SINK)
    restored.restore(saved)
    assert restored.rail.hybrid_books["shadow_sent"] == FIVE
    assert restored.tick(5) == []  # recovery prepares a fresh top-up; the shadow is not resent
    assert restored.tick(6)[0]["status"] == "confirmed"
    books = restored.rail.hybrid_books
    assert exchange._cash == cash - Decimal(5)  # the venue paid once
    assert books["shadow_sent"] == FIVE and books["authorizations"] == 1
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO - FIVE  # and mainnet once
    assert restored.rail.venice == FIVE
    assert [o["micro"] for o in restored.collect_financing()] == [FIVE]
    assert wallet.available == wallet.balance == 100_000_000 + FIVE
    assert wallet.check_conservation()


def test_an_unknown_top_up_outcome_is_never_resubmitted_until_it_provably_expired():
    treasury, wallet, _, ledger = hybrid()
    books, script = treasury.rail.hybrid_books, treasury.rail.script
    script["top_up"] = "unknown_lost"
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    assert books["submissions"] == ["treasury-0:authorization-0"]
    for now in (70 * S, 140 * S, 600 * S):
        assert treasury.tick(now) == []
    # Poll-only: the retry loop never resubmits and no second authorization exists.
    assert books["submissions"] == ["treasury-0:authorization-0"]
    assert books["authorizations"] == 1 and not kinds(ledger, "treasury.retry")
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO
    # Only once the rail says the authorization can no longer execute is it retried,
    # through the recoverable strand and a new authorization.
    script.update(expire=True, top_up="settled")
    treasury.tick(700 * S)
    assert treasury.state["status"] == "stranded" and treasury.stranded
    treasury.tick(701 * S)
    assert treasury.tick(702 * S)[0]["status"] == "confirmed"
    assert books["submissions"] == ["treasury-0:authorization-0", "treasury-0:authorization-1"]
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO - FIVE
    assert books["shadow_sent"] == FIVE and wallet.check_conservation()


def test_an_unknown_outcome_that_landed_confirms_on_its_one_submission():
    treasury, _, _, _ = hybrid()
    books = treasury.rail.hybrid_books
    treasury.rail.script["top_up"] = "unknown_landed"
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    assert treasury.tick(3)[0]["status"] == "confirmed"
    assert len(books["submissions"]) == 1
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO - FIVE


def test_the_window_cap_admits_two_conversions_and_refuses_the_third():
    treasury, _, _, ledger = hybrid(max_venice_per_window=10_000_000)
    for n, now in enumerate((1, 4)):
        assert treasury.transfer("to_venice", "5", handle=f"h{n}", now_ns=now)[
            "status"] == "submitted"
        treasury.tick(now + 1)
        assert treasury.tick(now + 2)[0]["status"] == "confirmed"
    refused = treasury.transfer("to_venice", "5", handle="h2", now_ns=8)
    assert refused == {"status": "refused", "error": "treasury.max_venice_per_window exhausted"}
    assert treasury.rail.hybrid_books["shadow_sent"] == 2 * FIVE
    treasury.open_window(2)
    assert treasury.transfer("to_venice", "5", handle="h3", now_ns=9)["status"] == "submitted"


def test_a_hybrid_conversion_needs_the_venue_and_the_mainnet_reserve_to_cover_it():
    treasury, _, exchange, _ = hybrid()
    treasury.rail.hybrid_books["mainnet_reserve"] = FIVE - 1
    assert "mainnet reserve" in treasury.transfer("to_venice", "5", handle="h", now_ns=1)["error"]
    treasury.rail.hybrid_books["mainnet_reserve"] = FIVE
    exchange._cash = Decimal("4")
    assert "venue pot" in treasury.transfer("to_venice", "5", handle="h", now_ns=2)["error"]
    assert treasury.transfer("to_venice", "6", handle="h", now_ns=3)["status"] == "refused"


# ---- manifest


def capital_loop():
    return load_manifest("worlds/edition5-capital-loop.toml")


def test_the_mode_is_refused_on_mainnet_without_a_sink_and_off_its_one_network():
    world = capital_loop()
    world.validate()
    with pytest.raises(ValueError, match="testnet rehearsal mode"):
        replace(world, exchange=replace(world.exchange, mainnet=True)).validate()
    with pytest.raises(ValueError, match="requires treasury.venice_shadow_sink"):
        replace(world, treasury=replace(world.treasury, venice_shadow_sink=None)).validate()
    with pytest.raises(ValueError, match="nonzero EVM address"):
        replace(world, treasury=replace(world.treasury, venice_shadow_sink="0x" + "0" * 40)
                ).validate()
    with pytest.raises(ValueError, match="must be base-mainnet"):
        replace(world, treasury=replace(world.treasury, venice_network="base-sepolia")).validate()
    with pytest.raises(ValueError, match="requires treasury.venice_network"):
        replace(world, treasury=replace(world.treasury, venice_network=None)).validate()


def test_the_keys_are_hash_neutral_at_their_defaults():
    import glob

    seen = 0
    for path in sorted(glob.glob("worlds/*.toml")):
        treasury = json.loads(load_manifest(path).canonical_json())["treasury"]
        hybrid_world = path.endswith("edition5-capital-loop.toml")
        assert ("venice_network" in treasury) is hybrid_world
        assert ("venice_shadow_sink" in treasury) is hybrid_world
        seen += 1
    assert seen > 10
    assert TreasurySpec().venice_network is None and TreasurySpec().venice_shadow_sink is None


def test_the_capital_loop_world_puts_most_seats_on_venice_and_caps_two_conversions():
    world = capital_loop()
    menu = {m.id: m for m in world.models}
    venice = [a for a in world.assemblies if menu[a.model_id].provider == "venice"]
    assert 2 * len(venice) >= len(world.assemblies)
    assert world.treasury.venice_network == "base-mainnet"
    assert world.treasury.max_venice_per_window == 2 * FIVE
    assert int(world.treasury.venice_shadow_sink, 16) != 0
    assert (menu["venice:openai-gpt-56-luna"].input_usd_per_mtok,
            menu["venice:openai-gpt-56-luna"].output_usd_per_mtok) == ("0.25", "1.50")


def test_the_rehearsal_runner_admits_only_the_conversion_and_only_when_asked():
    from scripts import edition4_rehearsal as rehearsal

    world = capital_loop()
    plain = rehearsal.effective_manifest(world)
    assert plain.treasury.reserve_address is None and plain.treasury.venice_network is None
    kept = rehearsal.effective_manifest(world, capital_loop=True)
    assert kept.treasury.venice_network == "base-mainnet"
    assert kept.treasury.reserve_address == world.treasury.reserve_address
    assert kept.treasury.hyperevm_gas_budget_wei == kept.treasury.base_gas_budget_wei == 0
    with pytest.raises(rehearsal.RehearsalRefused, match="capital_loop_requires"):
        rehearsal.effective_manifest(load_manifest("worlds/edition5-testnet-rehearsal.toml"),
                                     capital_loop=True)

    class Inner:
        name = "inner"

        def plan(self, direction):
            return ("shadow_send", "venice_top_up")

        def preflight(self, direction, amount, gas_spent):
            return "checked"

    rail = rehearsal.CapitalLoopRail(Inner())
    assert rail.name == "inner" and rail.plan("to_venice") == ("shadow_send", "venice_top_up")
    assert rail.preflight("to_venice", FIVE, {}) == "checked"
    for direction in ("to_reserve", "to_venue", "spot_to_perps", "perps_to_spot"):
        with pytest.raises(RailError, match="denied by rehearsal"):
            rail.preflight(direction, FIVE, {})
    assert "treasury.to_venice" not in rehearsal._denied_rails(True)
    assert "treasury.to_venice" in rehearsal._denied_rails(False)


# ---- the live rail's code, against fakes


class Roles(Info):
    def __init__(self):
        super().__init__()
        self.roles = {}

    def post(self, path, body):
        if body["type"] == "userRole":
            return {"role": self.roles.get(body["user"].lower(), "user")}
        return super().post(path, body)


class Client:
    """The x402 client surface prepare_top_up and _venice_receipt read; no network."""

    def __init__(self, account):
        self.address, self._account = account.address, account
        self.credit = 1_000_000

    def usdc_balance(self):
        return 50_000_000

    def venice_balance(self):
        return self.credit

    def _request(self, method, path, body, **headers):
        return HTTPResponse(402, quote_fixture.__wrapped__())


def live(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    signer, reserve = Account.create(), Account.create()
    rail = HybridRail.__new__(HybridRail)
    rail.testnet = True
    rail.venue_address = signer.address
    rail.reserve_address = reserve.address
    rail.sink = SINK
    rail.spec = TreasurySpec(reserve_address=reserve.address, venice_network="base-mainnet",
                             venice_shadow_sink=SINK)
    rail.base, rail.venice_base = Chain(BASE_SEPOLIA), Chain(BASE)
    rail.exchange = SimpleNamespace(name="hyperliquid-testnet", _info=Roles())
    rail.posts = []
    rail._sdk = SimpleNamespace(wallet=signer, _post_action=lambda a, s, n: (
        rail.posts.append((deepcopy(a), s, n)) or {"status": "ok"}))
    rail._x402 = Client(reserve)
    rail.now_s = lambda: 1_000
    return rail


def test_the_live_plan_sends_the_shadow_first_and_preflights_both_pots(monkeypatch):
    rail = live(monkeypatch)
    assert rail.plan("to_venice") == ("shadow_send", "venice_top_up")
    assert rail.plan("to_reserve") == ("withdraw_burn", "mint_base")
    rail.preflight("to_venice", FIVE, {})
    rail.exchange._info.roles[SINK.lower()] = "missing"
    with pytest.raises(RailError, match="existing Hyperliquid testnet account"):
        rail.preflight("to_venice", FIVE, {})
    rail.exchange._info.roles[SINK.lower()] = "user"
    rail.venice_base.usdc = FIVE - 1
    with pytest.raises(RailError, match="mainnet reserve"):
        rail.preflight("to_venice", FIVE, {})
    rail.venice_base.usdc = FIVE
    rail.exchange._info.available = "4.99"
    with pytest.raises(RailError, match="venue pot"):
        rail.preflight("to_venice", FIVE, {})
    rail.exchange._info.available = "50"
    rail.exchange._info.roles[rail.venue_address.lower()] = "agent"
    with pytest.raises(RailError, match="main wallet"):
        rail.preflight("to_venice", FIVE, {})
    rail.exchange._info.roles[rail.venue_address.lower()] = "user"
    monkeypatch.setenv("VENICE_API_KEY", "set")
    with pytest.raises(RailError, match="API-key"):
        rail.preflight("to_venice", FIVE, {})


def test_the_shadow_send_is_a_testnet_usd_send_signed_once_at_its_journaled_nonce(monkeypatch):
    rail = live(monkeypatch)
    state = {"amount_micro": FIVE, "nonce": 1_700_000_000_000, "id": "treasury-0"}
    reference = rail.prepare("shadow_send", state, {})
    assert rail.posts == []  # preparing signs nothing
    assert reference["action"] == {"type": "usdSend", "destination": SINK, "amount": "5",
                                   "time": 1_700_000_000_000}
    rail.send("shadow_send", reference)
    rail.send("shadow_send", reference)
    assert rail.posts[0] == rail.posts[1]  # a resend is the same action at the same nonce
    action, signature, nonce = rail.posts[0]
    assert nonce == 1_700_000_000_000 and action["hyperliquidChain"] == "Testnet"
    signer = recover_user_from_user_signed_action(
        dict(action), signature, USD_SEND_SIGN_TYPES, "HyperliquidTransaction:UsdSend", False)
    assert signer == rail.venue_address
    tampered = deepcopy(reference)
    tampered["action"]["destination"] = rail.reserve_address
    with pytest.raises(RailError, match="modified"):
        rail.send("shadow_send", tampered)
    rail._sdk._post_action = lambda *a: {"status": "err"}
    with pytest.raises(RailError, match="venue rejected withdrawal"):
        rail.send("shadow_send", reference)

    def lost(*_):
        raise TimeoutError

    rail._sdk._post_action = lost
    with pytest.raises(Pending):
        rail.send("shadow_send", reference)


def shadow_row(rail, **delta):
    base = {"type": "internalTransfer", "usdc": "5.0", "user": rail.venue_address.lower(),
            "destination": SINK.lower(), "fee": "0.0"}
    return {"time": 1_700_000_000_500, "hash": "0x" + "ab" * 32, "delta": {**base, **delta}}


def test_the_shadow_leg_confirms_only_on_its_own_venue_ledger_row(monkeypatch):
    rail = live(monkeypatch)
    state = {"amount_micro": FIVE, "nonce": 1_700_000_000_000, "id": "treasury-0"}
    state["reference"] = rail.prepare("shadow_send", state, {})
    info = rail.exchange._info
    assert rail.poll("shadow_send", state) is None
    for wrong in ({"destination": rail.reserve_address}, {"usdc": "5.01"},
                  {"user": rail.reserve_address}):
        info.updates = [shadow_row(rail, **wrong)]
        assert rail.poll("shadow_send", state) is None
    info.updates = [shadow_row(rail)]
    confirmed = rail.poll("shadow_send", state)
    assert confirmed["confirmed"] and confirmed["principal_moved"]
    assert confirmed["route_data"]["shadow"]["sink"] == SINK
    # The newer "send" shape must name our nonce, and may land later than a class move.
    info.updates = [{"time": 1_700_000_090_000, "hash": "0x" + "cd" * 32, "delta": {
        "type": "send", "token": "USDC", "amount": "5", "sourceDex": "", "destinationDex": "",
        "user": rail.venue_address, "destination": SINK, "fee": "0", "nonce": 1_700_000_000_000}}]
    assert rail.poll("shadow_send", state)["confirmed"]
    info.updates[0]["delta"]["nonce"] = 1
    assert rail.poll("shadow_send", state) is None
    info.updates = [shadow_row(rail), {**shadow_row(rail), "hash": "0x" + "ef" * 32}]
    with pytest.raises(RailError, match="ambiguous"):
        rail.poll("shadow_send", state)
    info.updates = [shadow_row(rail, fee="1.0")]
    with pytest.raises(RailError, match="fee"):
        rail.poll("shadow_send", state)
    assert rail.expired("shadow_send", state, 1_700_000_000_000 * 1_000_000) is None
    assert rail.expired("shadow_send", state, (1_700_000_000_000 + 4 * 86_400_000) * 1_000_000)


def test_the_top_up_is_proven_on_base_mainnet_not_on_the_testnet_reserve_chain(monkeypatch):
    rail = live(monkeypatch)
    state = {"amount_micro": FIVE, "nonce": 1, "id": "treasury-0", "started_ns": 0,
             "route_data": {"shadow": {"sink": SINK}}}
    reference = rail.prepare("venice_top_up", state, {})
    assert reference["network"] == "eip155:8453" and reference["created_s"] == 1_000
    assert "signature" not in json.dumps(reference)
    state["reference"] = reference
    topics = [event_topic("AuthorizationUsed(address,bytes32)"),
              "0x" + word_address(rail.reserve_address).hex(),
              reference["authorization"]["nonce"]]
    log = {"address": BASE.usdc, "topics": topics, "transactionHash": "0x" + "12" * 32}
    receipt = {"status": "0x1", "blockHash": "0x" + "34" * 32,
               "logs": [{"address": BASE.usdc, "topics": topics}]}
    rail.base.log_rows, rail.base.proved = [log], receipt  # on Sepolia: must not count
    assert rail.poll("venice_top_up", state) is None
    rail.base.log_rows, rail.base.proved = [], None
    rail.venice_base.log_rows, rail.venice_base.proved = [log], receipt
    rail._x402.credit = 6_000_000
    confirmed = rail.poll("venice_top_up", state)
    assert confirmed["confirmed"] and confirmed["evidence"]["venice_credit_micro"] == FIVE
    assert confirmed["evidence"]["network"] == "eip155:8453"
    assert "venice_top_up" in rail.poll_only_steps


def test_a_replayed_top_up_whose_acknowledgment_died_is_never_resubmitted():
    """A kill between a real top-up's submission and its recorded result resumes as unknown."""
    ledger = Ledger(clock_ns=lambda: 0)
    sends = []

    class Rail:
        poll_only_steps = ("venice_top_up",)

        def send(self, step, reference):
            sends.append(step)

    rail = Rail()
    for step in ("venice_top_up", "shadow_send"):
        journal = RecoveryJournal(ledger, lambda: 0)
        journal.active = True
        args = (step, {"authorization": "a"})
        fingerprint = hashlib.sha256(canonical(encode((args, {})))).hexdigest()
        ledger.append({"kind": "io.call", "name": "treasury.rail.send",
                       "input_hash": fingerprint, "ts": 0})
        journal.tail = [ledger._recovery_items()[-1]]
        if step == "venice_top_up":
            with pytest.raises(Pending):
                journal.call("treasury.rail.send", rail.send, args, {})
            assert sends == []
        else:
            journal.call("treasury.rail.send", rail.send, args, {})
            assert sends == ["shadow_send"]  # an idempotent venue nonce may be resent


def test_the_hybrid_rail_refuses_a_mainnet_venue_and_a_sink_inside_the_pots(monkeypatch):
    from hyperliquid.utils.constants import MAINNET_API_URL

    reserve = Account.create()
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", reserve.key.hex())
    signer = Account.create()
    sdk = SimpleNamespace(wallet=signer, vault_address=None)

    def exchange(url):
        return SimpleNamespace(base_url=url, _exchange=sdk, _address=signer.address,
                               name="hyperliquid")

    spec = TreasurySpec(reserve_address=reserve.address, venice_network="base-mainnet",
                        venice_shadow_sink=SINK)
    with pytest.raises(RailError, match="testnet venue only"):
        HybridRail(exchange(MAINNET_API_URL), spec)
    from hyperliquid.utils.constants import TESTNET_API_URL

    with pytest.raises(RailError, match="outside every observed pot"):
        HybridRail(exchange(TESTNET_API_URL), replace(spec, venice_shadow_sink=signer.address))
    rail = HybridRail(exchange(TESTNET_API_URL), spec)
    assert rail.venice_base.chain.id == 8453 and rail.base.chain.id == 84532
    assert rail.venice_base.gas_budget_wei == 0  # mainnet is read, never written, by EVM
