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
    CREDIT_SHORT,
    FAKE_MAINNET_RESERVE_MICRO,
    HYBRID_STRANDED,
    RESERVE_FLOOR,
    TOP_UP_WAIT_EXCEEDED,
    TRANSFER_BLOCKED,
    VENICE_TOTAL_EXHAUSTED,
    FakeTreasury,
)
from factorylab.world.treasury_rails import HybridRail
from factorylab.world.x402 import HTTPResponse, X402Error
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
    # The wait is counted in world ticks the runtime states (time audit T13).
    treasury.forward_wait_ticks = 2
    cash = exchange._cash
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick_index = 2
    treasury.tick(2)
    pots = treasury.pots()
    assert pots["pending"] and pots["pending_reason"] == "Venice top-up quote unavailable"
    treasury.tick_index = 3
    treasury.tick(3)
    assert treasury.state["status"] == "submitted"  # one tick short of the bound
    treasury.tick_index = 4
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
    return load_manifest("worlds/edition6-capital-loop.toml")


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


@pytest.mark.parametrize(("field", "value", "message"), [
    ("max_venice_total_micro", None, "requires treasury.max_venice_total_usd"),
    ("max_venice_total_micro", 0, "max_venice_total_usd must be positive"),
    ("venice_reserve_floor_micro", None, "requires treasury.venice_reserve_floor_usd"),
    ("venice_pay_to", None, "requires treasury.venice_pay_to"),
    ("venice_pay_to", "0x" + "0" * 40, "venice_pay_to must be a nonzero EVM address"),
])
def test_real_money_mode_needs_an_absolute_bound_a_floor_and_a_pinned_payee(
        field, value, message):
    world = capital_loop()
    with pytest.raises(ValueError, match=message):
        replace(world, treasury=replace(world.treasury, **{field: value})).validate()
    plain = load_manifest("worlds/edition6-testnet-rehearsal.toml")
    if value is not None:
        with pytest.raises(ValueError, match="requires treasury.venice_network"):
            replace(plain, treasury=replace(plain.treasury, **{field: value})).validate()


def test_only_the_capital_loop_world_names_the_hybrid_keys():
    import glob

    from factorylab.runtime.worlds import HYBRID_VENICE_KEYS

    seen = 0
    for path in sorted(glob.glob("worlds/*.toml")):
        try:
            world = load_manifest(path)
        except ValueError as exc:
            # R8: a world whose roster no longer meets the evaluator population the
            # kernel requires is refused, and only the pre-Wave-5a editions are.
            assert "evaluator population" in str(exc)
            assert path.split("/")[-1].startswith(("edition3-", "edition5-"))
            continue
        treasury = json.loads(world.canonical_json())["treasury"]
        hybrid_world = path.endswith("edition6-capital-loop.toml")
        for key in HYBRID_VENICE_KEYS:
            # R8: every key is hashed; only the capital-loop world gives these a value.
            assert (treasury[key] is not None) is hybrid_world
        seen += 1
    assert seen >= 7
    assert all(getattr(TreasurySpec(), key) is None for key in HYBRID_VENICE_KEYS)


def test_the_capital_loop_world_starts_on_openrouter_and_caps_two_conversions():
    # The first live rehearsal (23 September 2026) seeded seven seats on Venice with $0.098
    # of credit and none could think. Seats start where the operator funded them; Venice
    # routes of the seated families stay on the menu for credit the factory buys.
    world = capital_loop()
    menu = {m.id: m for m in world.models}
    assert all(menu[a.model_id].provider == "openrouter" for a in world.assemblies)
    assert {"venice:openai-gpt-6-luna", "venice:deepseek-v4-1-flash",
            "venice:qwen-3-8-flash"} <= set(menu)
    assert world.treasury.venice_network == "base-mainnet"
    assert world.treasury.max_venice_per_window == 2 * FIVE
    assert world.treasury.max_venice_total_micro == 2 * FIVE
    assert world.treasury.venice_pay_to == PAYEE
    assert int(world.treasury.venice_shadow_sink, 16) != 0
    assert (menu["venice:openai-gpt-6-luna"].input_usd_per_mtok,
            menu["venice:openai-gpt-6-luna"].output_usd_per_mtok) == ("0.125", "0.625")


def test_the_capital_loop_world_seats_the_testnet_worlds_models_on_other_routes():
    # Codex review of #135: the two rehearsals must differ only in the capital loop, so
    # every seat holds the same model in both worlds, whichever provider serves it.
    def served(world):
        menu = {m.id: m for m in world.models}
        return {a.id: menu[a.model_id].id.removeprefix("venice:") for a in world.assemblies}

    same = {"deepseek-v4-1-flash": "deepseek/deepseek-v4.1-flash",
            "openai-gpt-6-luna": "openai/gpt-6-luna",
            "qwen-3-8-flash": "qwen/qwen3.8-flash"}
    loop = {seat: same.get(m, m) for seat, m in served(capital_loop()).items()}
    assert loop == served(load_manifest("worlds/edition6-testnet-rehearsal.toml"))


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
        rehearsal.effective_manifest(load_manifest("worlds/edition6-testnet-rehearsal.toml"),
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
        self.quote = quote_fixture.__wrapped__()  # the recorded unpaid 402, no network
        self.requests = []

    def usdc_balance(self):
        return 50_000_000

    def venice_balance(self):
        return self.credit

    def _request(self, method, path, body, **headers):
        self.requests.append((method, path, sorted(headers)))
        return HTTPResponse(402, deepcopy(self.quote))


# Venice's payee in the unpaid 402 quote recorded live on 11 September 2026
# (docs/research/venice.md, "x402 flow") and pinned by the capital-loop world.
PAYEE = "0x2670b922ef37c7df47158725c0cc407b5382293f"


class Final(Chain):
    """A Base whose finalized head and scan coverage the test sets."""

    def __init__(self, config):
        super().__init__(config)
        self.final = {"number": hex(500), "timestamp": hex(10_000)}
        self.authorization_used = False  # USDC's authorizationState(reserve, nonce)
        self.state_reads = []

    def call(self, method, args):
        if method == "eth_getBlockByNumber" and args[0] == "finalized":
            return self.final
        if method == "eth_call":
            self.state_reads.append(args)
            return "0x" + ("1" if self.authorization_used else "0").rjust(64, "0")
        return super().call(method, args)


def live(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    signer, reserve = Account.create(), Account.create()
    rail = HybridRail.__new__(HybridRail)
    rail.testnet = True
    rail.venue_address = signer.address
    rail.reserve_address = reserve.address
    rail.sink = SINK
    rail.pay_to = PAYEE
    rail.reserve_floor_micro = 0
    rail.metered_usage_since = lambda since_ns: 0
    rail.spec = TreasurySpec(reserve_address=reserve.address, venice_network="base-mainnet",
                             venice_shadow_sink=SINK, venice_pay_to=PAYEE,
                             max_venice_total_micro=2 * FIVE, venice_reserve_floor_micro=0)
    rail.base, rail.venice_base = Chain(BASE_SEPOLIA), Final(BASE)
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

    def exchange(url, account=signer):
        return SimpleNamespace(base_url=url, _address=account.address, name="hyperliquid",
                               _exchange=SimpleNamespace(wallet=account, vault_address=None))

    spec = TreasurySpec(reserve_address=reserve.address, venice_network="base-mainnet",
                        venice_shadow_sink=SINK, venice_pay_to=PAYEE,
                        max_venice_total_micro=2 * FIVE, venice_reserve_floor_micro=0)
    with pytest.raises(RailError, match="testnet venue only"):
        HybridRail(exchange(MAINNET_API_URL), spec)
    from hyperliquid.utils.constants import TESTNET_API_URL

    with pytest.raises(RailError, match="outside every observed pot"):
        HybridRail(exchange(TESTNET_API_URL), replace(spec, venice_shadow_sink=signer.address))
    # Finding 5: one key for the venue and the reserve is refused.
    with pytest.raises(RailError, match="venue and the reserve must be different"):
        HybridRail(exchange(TESTNET_API_URL, reserve), spec)
    with pytest.raises(RailError, match="payee, total and floor"):
        HybridRail(exchange(TESTNET_API_URL), replace(spec, venice_pay_to=None))
    rail = HybridRail(exchange(TESTNET_API_URL), spec)
    assert rail.venice_base.chain.id == 8453 and rail.base.chain.id == 84532
    assert rail.venice_base.gas_budget_wei == 0  # mainnet is read, never written, by EVM
    assert rail.pay_to.lower() == PAYEE


# ---- cold review of feat/venice-hybrid: one regression per finding


def top_up_state(rail):
    state = {"amount_micro": FIVE, "nonce": 1, "id": "treasury-0", "started_ns": 0,
             "route_data": {"shadow": {"sink": SINK}}}
    state["reference"] = rail.prepare("venice_top_up", state, {})
    return state


def debit(rail, reference):
    """A finalized AuthorizationUsed debit of ``reference`` on Base mainnet."""
    topics = [event_topic("AuthorizationUsed(address,bytes32)"),
              "0x" + word_address(rail.reserve_address).hex(),
              reference["authorization"]["nonce"]]
    rail.venice_base.log_rows = [{"address": BASE.usdc, "topics": topics,
                                  "transactionHash": "0x" + "12" * 32}]
    rail.venice_base.proved = {"status": "0x1", "blockHash": "0x" + "34" * 32,
                               "logs": [{"address": BASE.usdc, "topics": topics}]}


def ordinary_rail(monkeypatch):
    """The ordinary mainnet LiveRail on the same fakes: its own base is Base mainnet."""
    from factorylab.world.treasury_rails import LiveRail

    hybrid_rail = live(monkeypatch)
    rail = LiveRail.__new__(LiveRail)
    rail.reserve_address = hybrid_rail.reserve_address
    rail.base = hybrid_rail.venice_base
    rail.venice_base = rail.base  # so ``debit`` below writes to the chain it reads
    return rail, top_up_state(hybrid_rail)


@pytest.mark.parametrize("kind", ["ordinary", "hybrid"])
def test_1_5_the_reviewers_probe_expiry_is_on_finalized_base_for_both_rails(
        monkeypatch, kind):
    from factorylab.world.treasury_rails import LiveRail

    if kind == "ordinary":
        rail, state = ordinary_rail(monkeypatch)
        assert not hasattr(LiveRail, "VENICE_EXPIRY_GRACE_S")
        assert LiveRail.poll_only_steps == ("venice_top_up",)
    else:
        rail = live(monkeypatch)
        state = top_up_state(rail)
    chain = rail._venice_base()
    valid_before = int(state["reference"]["authorization"]["validBefore"])
    probe = (valid_before + 3_601) * S  # the review's runtime clock: past the old grace
    chain.final = {"number": hex(500), "timestamp": hex(valid_before)}  # Base lags
    chain.scanned_to = 500
    assert rail.expired("venice_top_up", state, probe) is None
    chain.final["timestamp"] = hex(valid_before + 1)
    chain.scanned_to = 499
    assert rail.expired("venice_top_up", state, probe) is None  # scan short of the head
    chain.scanned_to = 500
    chain.authorization_used = True  # a lagging RPC returned [] logs; the contract knows
    assert rail.expired("venice_top_up", state, probe) is None
    assert chain.state_reads[-1][1] == hex(500)  # read at the finalized block itself
    chain.authorization_used = False
    debit(rail, state["reference"])
    assert rail.expired("venice_top_up", state, probe) is None  # it did settle
    chain.log_rows = []
    assert rail.expired("venice_top_up", state, 0) == (
        "Venice authorization expired unused on finalized Base")

    def unreachable(*args, **kwargs):
        raise TimeoutError

    chain.scan = unreachable
    assert rail.expired("venice_top_up", state, probe) is None


def test_1_a_superseded_authorization_is_still_polled_and_its_late_debit_booked(monkeypatch):
    rail = live(monkeypatch)
    old = top_up_state(rail)["reference"]
    state = top_up_state(rail)
    state["route_data"]["superseded_references"] = [old]
    debit(rail, old)
    rail._x402.credit = 6_000_000
    confirmed = rail.poll("venice_top_up", state)
    assert confirmed["confirmed"] and confirmed["evidence"]["nonce"] == old["authorization"][
        "nonce"]


def test_1_a_late_debit_found_at_recovery_is_booked_and_nothing_new_is_authorized():
    treasury, _, _, ledger = hybrid(max_venice_total_micro=2 * FIVE)
    rail, books = treasury.rail, treasury.rail.hybrid_books
    rail.script.update(top_up="unknown_lost", expire=True)
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    assert treasury.state["status"] == "stranded"
    rail.land("treasury-0:authorization-0")  # settled after all
    treasury.tick(3)  # recovery polls the superseded authorization before anything else
    assert treasury.tick(4)[0]["status"] == "confirmed"
    assert books["authorizations"] == 1 and treasury.venice_authorized_micro == FIVE
    assert books["mainnet_reserve"] == FAKE_MAINNET_RESERVE_MICRO - FIVE
    assert kinds(ledger, "treasury.recovered")[-1]["late_debit"] is True
    assert len(kinds(ledger, "treasury.financing")) == 1


def test_2_the_reviewers_probe_six_strands_then_top_ups_never_passes_the_absolute_cap():
    treasury, _, _, ledger = hybrid(max_venice_per_window=100 * FIVE,
                                    max_venice_total_micro=2 * FIVE)
    rail, books = treasury.rail, treasury.rail.hybrid_books
    rail.script["top_up_unavailable"] = True
    treasury.forward_wait_ticks = 2
    now, window, results = 1, 1, []
    for n in range(6):  # try to park six paid shadows, each past forward_wait_ticks
        results.append(treasury.transfer("to_venice", "5", handle=f"seat-{n}", now_ns=now))
        for _ in range(3):
            now += 1
            treasury.tick_index = now
            treasury.tick(now)
            window += 1
            treasury.open_window(window)
    assert results[0]["status"] == "submitted"
    assert {r["error"] for r in results[1:]} <= {HYBRID_STRANDED, TRANSFER_BLOCKED}
    assert len(treasury.stranded) == 1 and books["shadow_sent"] == FIVE
    rail.script["top_up_unavailable"] = False
    for _ in range(30):  # every free slot is taken, every window renewed
        now += 1
        treasury.tick_index = now
        treasury.tick(now)
        treasury.transfer("to_venice", "5", handle=f"late-{now}", now_ns=now)
        window += 1
        treasury.open_window(window)
    spent = FAKE_MAINNET_RESERVE_MICRO - books["mainnet_reserve"]
    assert spent == treasury.venice_authorized_micro == 2 * FIVE  # the cap, never past it
    assert sum(i["amount_micro"] for i in kinds(ledger, "treasury.venice_authorized")) == spent
    assert all(i["authorized_micro"] <= i["cap_micro"]
               for i in kinds(ledger, "treasury.venice_authorized"))
    assert VENICE_TOTAL_EXHAUSTED in {i["reason"] for i in kinds(ledger, "treasury.refused")}


def test_2_re_authorizations_count_and_a_strand_past_the_cap_signs_nothing_across_a_kill():
    treasury, wallet, exchange, ledger = hybrid(max_venice_per_window=100 * FIVE,
                                                max_venice_total_micro=2 * FIVE)
    rail, books = treasury.rail, treasury.rail.hybrid_books
    rail.script.update(top_up="unknown_lost", expire=True)
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    for now in range(2, 10):
        treasury.tick(now)
    # One authorization, one re-authorization after it provably expired, then the cap.
    assert books["authorizations"] == 2 and treasury.venice_authorized_micro == 2 * FIVE
    assert treasury.state["status"] == "stranded" and treasury.stranded
    assert treasury.transfer("to_venice", "5", handle="h2", now_ns=10)["error"] == (
        HYBRID_STRANDED)
    saved = json.loads(json.dumps(treasury.snapshot()))
    assert saved["venice_authorized_micro"] == 2 * FIVE
    restored = FakeTreasury(ledger, wallet, exchange=exchange, venice_shadow_sink=SINK,
                            max_venice_total_micro=2 * FIVE, max_venice_per_window=100 * FIVE)
    restored.restore(saved)
    restored.rail.script["top_up"] = "settled"
    for now in range(11, 20):
        restored.tick(now)
    assert restored.venice_authorized_micro == 2 * FIVE
    assert restored.rail.hybrid_books["authorizations"] == 2  # nothing past the cap
    assert restored.stranded


def test_2_a_recovery_is_charged_to_the_window_it_authorizes_in():
    treasury, _, _, _ = hybrid(max_venice_per_window=FIVE)
    rail, books = treasury.rail, treasury.rail.hybrid_books
    rail.script.update(top_up="unknown_lost", expire=True)
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    rail.script.update(top_up="settled", expire=False)
    treasury.tick(3)
    assert books["authorizations"] == 1 and treasury.stranded  # window 1 is spent
    treasury.open_window(2)
    treasury.tick(4)
    assert treasury.tick(5)[0]["status"] == "confirmed"
    assert books["authorizations"] == 2 and treasury.venice_spent == FIVE


def test_2_the_on_chain_reserve_floor_bounds_every_run(monkeypatch):
    treasury, _, _, _ = hybrid(venice_reserve_floor_micro=FAKE_MAINNET_RESERVE_MICRO - FIVE)
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    treasury.tick(2)
    assert treasury.tick(3)[0]["status"] == "confirmed"
    assert treasury.transfer("to_venice", "5", handle="h2", now_ns=4)["error"] == RESERVE_FLOOR
    rail = live(monkeypatch)
    rail.venice_base.usdc = 50_000_000
    rail.reserve_floor_micro = 45_000_000
    rail.preflight("to_venice", FIVE, {})
    rail.reserve_floor_micro = 45_000_001
    with pytest.raises(RailError, match="venice_reserve_floor_usd"):
        rail.preflight("to_venice", FIVE, {})
    with pytest.raises(RailError, match="venice_reserve_floor_usd"):
        top_up_state(rail)


def test_3_a_quote_or_reference_naming_another_payee_is_refused_before_signing(monkeypatch):
    rail = live(monkeypatch)
    state = top_up_state(rail)
    assert state["reference"]["accepted"]["payTo"] == PAYEE
    tampered = deepcopy(state["reference"])
    tampered["accepted"]["payTo"] = tampered["authorization"]["to"] = rail.reserve_address
    before = list(rail._x402.requests)
    with pytest.raises(X402Error, match="payee differs"):
        rail.send("venice_top_up", tampered)
    assert rail._x402.requests == before  # nothing was submitted, let alone signed
    rail._x402.quote["accepts"][0]["payTo"] = rail.reserve_address
    with pytest.raises(RailError, match="payee differs"):
        top_up_state(rail)


def test_2_financing_books_on_the_debit_despite_real_spend_and_rounding(monkeypatch):
    from factorylab.world.treasury_rails import CREDIT_TOLERANCE_MICRO

    rail = live(monkeypatch)
    state = top_up_state(rail)  # credit before: $1.000000
    debit(rail, state["reference"])
    # Seats spent through the finality wait: the diary metered $1.37, Venice charged
    # $0.12 more than the table estimate, and the balance rounded down a micro.
    rail.metered_usage_since = lambda since_ns: 1_370_000
    rail._x402.credit = 1_000_000 + FIVE - 1_370_000 - 120_000 - 1
    confirmed = rail.poll("venice_top_up", state)
    assert confirmed["confirmed"]
    assert confirmed["evidence"]["credit_shortfall_micro"] == 120_001  # recorded, not held
    rail.metered_usage_since = None  # no diary: the canonical debit alone decides
    assert rail.poll("venice_top_up", state)["evidence"]["credit_shortfall_micro"] is None
    rail.metered_usage_since = lambda since_ns: 1_370_000
    rail._x402.credit = 1_000_000 + FIVE - 1_370_000 - CREDIT_TOLERANCE_MICRO
    assert rail.poll("venice_top_up", state)["confirmed"]  # exactly at the tolerance
    rail._x402.credit -= 1
    with pytest.raises(Pending, match="financing held unresolved"):  # the credit is missing
        rail.poll("venice_top_up", state)
    rail._x402.credit = 1_000_000  # nothing arrived at all
    with pytest.raises(Pending, match="financing held unresolved"):
        rail.poll("venice_top_up", state)


def test_3_a_short_credit_holds_the_principal_and_is_never_re_authorized():
    treasury, wallet, _, ledger = hybrid()
    rail, books = treasury.rail, treasury.rail.hybrid_books
    rail.script.update(short_credit=True, expire=True)
    treasury.transfer("to_venice", "5", handle="h", now_ns=1)
    for now in range(2, 6):
        treasury.tick(now)
    assert treasury.state["status"] == "submitted"
    assert treasury.pots()["pending_reason"] == CREDIT_SHORT
    assert books["authorizations"] == 1 and not kinds(ledger, "treasury.financing")
    assert wallet.available == wallet.balance - FIVE
    rail.script["short_credit"] = False
    assert treasury.tick(6)[0]["status"] == "confirmed"
    assert len(kinds(ledger, "treasury.financing")) == 1


def test_4_a_live_hybrid_world_will_not_start_or_resume_without_the_opt_in():
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import runtime_state
    from factorylab.world.scripted import ScriptedProvider

    with pytest.raises(RailError, match="--capital-loop"):
        Runtime(capital_loop(), events=1, seed=1, initial_balance_micro=None,
                ledger_path=None, router_gamma=0.1, provider=ScriptedProvider(),
                exchange=FakeExchange())
    # The opt-in is never checkpointed, so a resume has to be asked for it again.
    rt = Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    assert "capital_loop" not in runtime_state(rt)["config"]


def test_6_a_shadow_send_executing_late_is_matched_inside_its_nonce_window(monkeypatch):
    rail = live(monkeypatch)
    state = {"amount_micro": FIVE, "nonce": 1_700_000_000_000, "id": "treasury-0"}
    state["reference"] = rail.prepare("shadow_send", state, {})
    late = {**shadow_row(rail), "time": 1_700_000_000_000 + 10 * 60_000}  # ten minutes
    rail.exchange._info.updates = [late]
    assert rail.poll("shadow_send", state)["confirmed"]
    rail.exchange._info.updates = [{**late, "time": 1_700_000_000_000 + 4 * 86_400_000}]
    assert rail.poll("shadow_send", state) is None  # past the window it never executed
