"""The venue's vault terms, its wire, and the fake venue's vault book."""

from decimal import Decimal

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.world.exchange import FakeExchange
from factorylab.world.treasury import FakeTreasury
from factorylab.world.vaults import (
    CREATE_FEE_USD,
    FAKE_ACCOUNT,
    commission_on,
    details_from_wire,
    ledger_rows,
    match_intent,
    rebates,
)

OUTSIDE = "0x" + "0b5e".rjust(40, "0")


def _venue(cash="20000", lockup=0):
    return FakeExchange(start_cash_usd=Decimal(cash), vault_lockup_ns=lockup,
                        start_prices={"BTC": Decimal("100"), "ETH": Decimal("10")})


def test_commission_is_the_documented_share_of_the_withdrawn_profit():
    # The depositors page: 100 deposited, grown to 200, withdrawn in full pays 190.
    commission, basis = commission_on(Decimal(200), Decimal(100), Decimal(200))
    assert (commission, basis) == (Decimal(10), Decimal(100))
    assert Decimal(200) - commission == Decimal(190)
    # A mainnet vaultWithdraw row: commission == 0.1 * (requestedUsd - basis) exactly.
    commission, _ = commission_on(Decimal("500000"), Decimal("224030.402324"),
                                  Decimal("500000"))
    assert commission == Decimal("27596.959767")
    # A loss pays no commission.
    assert commission_on(Decimal(80), Decimal(100), Decimal(40))[0] == 0


def test_create_charges_the_deposit_and_the_fee_once_per_client_id():
    venue = _venue()
    assert venue.vault_create("v", "long enough", Decimal(100))["status"] == "rejected"
    assert "below minimum" in venue.vault_create("vault", "long enough", Decimal(99))["error"]
    poor = _venue(cash="10050")
    assert "insufficient" in poor.vault_create("vault", "long enough", Decimal(100))["error"]
    first = venue.vault_create("vault", "long enough", Decimal(1000), client_id="c1")
    assert first["status"] == "ok" and first["fee_usd"] == str(CREATE_FEE_USD)
    again = venue.vault_create("vault", "long enough", Decimal(1000), client_id="c1")
    assert again == first
    assert venue._cash == Decimal(20000) - 1000 - CREATE_FEE_USD
    assert venue._vault_equity() == Decimal(1000)
    assert [r["type"] for r in venue.vault_ledger(0)] == ["vaultCreate"]
    assert venue.vault_equities()["leading"][0]["vault"] == first["vault"]


def test_money_in_a_vault_leaves_perps_collateral_but_not_the_venue_pot():
    venue = _venue()
    ledger = Ledger(clock_ns=lambda: 0)
    treasury = FakeTreasury(ledger, Wallet(20_000_000_000, ledger, clock_ns=lambda: 0),
                            exchange=venue)
    before = treasury.pots()["total_micro"]
    vault = venue.vault_create("vault", "long enough", Decimal(1000))["vault"]
    free = venue.collateral_view("BTC")["eligible_equity_usd"]
    assert venue.vault_transfer(vault, True, Decimal(500))["status"] == "ok"
    assert venue.collateral_view("BTC")["eligible_equity_usd"] == free - 500
    treasury.forget_observations()
    pots = treasury.pots()
    assert pots["vaults"] == 1_500_000_000
    assert pots["perps"] + pots["vaults"] == pots["venue"]
    # The creation fee is the only money that left; the deposit only changed custodian.
    assert pots["total_micro"] == before - 10_000_000_000


def test_withdrawals_meet_the_lockup_the_equity_held_and_the_leader_minimum():
    venue = _venue(lockup=10)
    vault = venue.vault_create("vault", "long enough", Decimal(100))["vault"]
    assert "locked" in venue.vault_transfer(vault, False, Decimal(1))["error"]
    venue.advance(10)
    assert "exceeds" in venue.vault_transfer(vault, False, Decimal(101))["error"]
    # An outside deposit may not take the leader below 5% either.
    assert "below 5%" in venue.simulate_deposit(vault, OUTSIDE, Decimal(1901))["error"]
    assert venue.simulate_deposit(vault, OUTSIDE, Decimal(1900))["status"] == "ok"
    assert "below 5%" in venue.vault_transfer(vault, False, Decimal(1))["error"]
    assert venue.vault_details(vault)["leader_fraction"] == Decimal("0.05")


def test_an_outside_depositor_pays_the_leader_and_a_self_withdrawal_repays_itself():
    venue = _venue()
    vault = venue.vault_create("vault", "long enough", Decimal(1000))["vault"]
    cash = venue._cash
    venue.simulate_deposit(vault, OUTSIDE, Decimal(5000))
    venue.mark_vaults(Decimal(1000))  # +10%
    paid = venue.simulate_withdraw(vault, OUTSIDE, Decimal(5500))
    assert paid["commission"] == "50.000000" and paid["net"] == "5450.000000"
    assert venue._cash == cash + 50
    rows = venue.vault_ledger(0)
    assert rows[-1]["type"] == "vaultLeaderCommission" and rows[-1]["usd"] == 50
    assert rebates(rows, FAKE_ACCOUNT) == set()
    # The leader's own withdrawal is charged and repaid in one transaction.
    own = venue.vault_transfer(vault, False, Decimal(1100))
    assert own["commission"] == own["commission_rebate"] == "10.000000"
    rows = venue.vault_ledger(0)
    assert (rows[-1]["hash"], rows[-1]["usd"]) in rebates(rows, FAKE_ACCOUNT)


def test_scripted_depositor_enters_and_leaves_only_when_asked():
    venue = _venue()
    venue.vault_return_bps, venue.vault_depositor_usd = Decimal(100), Decimal(5000)
    venue.vault_depositor_steps = 2
    vault = venue.vault_create("vault", "long enough", Decimal(1000))["vault"]
    for step in range(1, 5):
        venue.advance(step)
    kinds = [r["type"] for r in venue.vault_ledger(0)]
    assert kinds == ["vaultCreate", "vaultLeaderCommission"]
    assert venue.vault_details(vault)["depositors"] == 0
    quiet = _venue()
    quiet.vault_create("vault", "long enough", Decimal(1000))
    quiet.advance(1)
    assert [r["type"] for r in quiet.vault_ledger(0)] == ["vaultCreate"]


def test_a_lost_write_is_confirmed_by_exactly_one_ledger_row():
    page = [
        {"time": 1, "hash": "0xa", "delta": {"type": "vaultDeposit", "vault": "0xV",
                                             "usdc": "10.0"}},
        {"time": 2, "hash": "0xb", "delta": {"type": "vaultWithdraw", "vault": "0xv",
                                             "user": "0xME", "requestedUsd": "5.0",
                                             "commission": "0.1", "closingCost": "0.0",
                                             "basis": "4.0", "netWithdrawnUsd": "4.9"}},
        {"time": 2, "hash": "0xb", "delta": {"type": "vaultLeaderCommission",
                                             "user": "0xme", "usdc": "0.1"}},
        {"time": 3, "hash": "0xc", "delta": {"type": "send", "usdc": "1"}},
    ]
    rows = ledger_rows(page)
    assert [r["type"] for r in rows] == ["vaultDeposit", "vaultWithdraw",
                                         "vaultLeaderCommission"]
    deposit = match_intent(rows, "venue.vault_deposit", {"vault": "0xv", "usd": "10"}, "0xme")
    assert deposit["status"] == "ok" and deposit["hash"] == "0xa"
    withdraw = match_intent(rows, "venue.vault_withdraw", {"vault": "0xv", "usd": 5}, "0xme")
    assert withdraw["net"] == "4.9" and withdraw["commission_rebate"] == "0.1"
    assert match_intent(rows, "venue.vault_deposit", {"vault": "0xv", "usd": "11"},
                        "0xme")["status"] == "uncertain"
    twice = ledger_rows([*page, {**page[0], "hash": "0xd"}])
    assert "two matching" in match_intent(twice, "venue.vault_deposit",
                                          {"vault": "0xv", "usd": "10"}, "0xme")["error"]


def test_vault_details_read_the_venue_shape():
    # Trimmed from a public testnet vaultDetails answer (2026-09-22), user = leader.
    raw = {"name": "tesahifgibz", "vaultAddress": "0x921F", "leader": "0x0E63",
           "description": "adbauyfzgnfiz", "portfolio": [["day", {"accountValueHistory": [
               [1, "205.0"]]}]], "apr": 0.0,
           "followerState": {"user": "0x0e63", "vaultEquity": "200.0",
                             "lockupUntil": 1789727223855},
           "leaderFraction": 0.975609756097561, "leaderCommission": 0.1,
           "followers": [{"user": "Leader", "vaultEquity": "200.0"},
                         {"user": "0xc765", "vaultEquity": "5.0"}],
           "maxDistributable": 102.5, "maxWithdrawable": 100.0, "isClosed": False,
           "allowDeposits": True}
    detail = details_from_wire(raw, "0x0E63", 7)
    assert detail["is_leader"] and detail["depositors"] == 1
    assert detail["equity_usd"] == Decimal("205.0")
    assert detail["own_equity_usd"] == Decimal("200.0")
    assert detail["own_lockup_until_ns"] == 1789727223855 * 1_000_000
    assert detail["leader_commission"] == Decimal("0.1")
    assert details_from_wire(None, "0x0e63", 7) == {"error": "vault not found"}
