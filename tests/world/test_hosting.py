"""The hosting pot: its own pot, booked only by what DigitalOcean took, bound to one account.

Every test runs against ``tests.digitalocean_fake`` at the HTTP boundary. The fake
keeps DigitalOcean's books by DigitalOcean's rules (``taken``, ``prepaid``), and the
billing tests assert the pot's books against those, never against themselves.
"""

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient
from factorylab.world.hosting import HostingAccount, HostingRefused, verify
from factorylab.world.treasury import FakeTreasury
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def micro(usd) -> int:
    return int(Decimal(usd) * 1_000_000)


def world(fake=None, **fake_args):
    ledger = Ledger(clock_ns=lambda: 0)
    records = []
    append = ledger.append

    def record(item):
        records.append(deepcopy(item))
        return append(item)

    ledger.append = record
    wallet = Wallet(500_000_000, ledger, clock_ns=lambda: 0)
    treasury = FakeTreasury(ledger, wallet)
    wallet.bind_pots(treasury.pots)
    fake = fake or FakeDigitalOcean(**fake_args)
    client = DigitalOceanClient(http=fake, attempts=1, timeout_s=5)
    treasury.hosting = HostingAccount(client, droplet_id=fake.droplet_id,
                                      identity=verify(client, fake.droplet_id))
    w = SimpleNamespace(ledger=ledger, wallet=wallet, treasury=treasury, fake=fake,
                        records=records, hosting=treasury.hosting)
    w.observe = treasury.observe_hosting
    return w


def items(w, kind):
    return [r for r in w.records if r["kind"] == kind]


def invoice_credits(w):
    return sum(r["micro"] for r in items(w, "treasury.hosting_credited")
               if r["cause"] == "invoice")


def outside_credits(w):
    return sum(r["micro"] for r in items(w, "treasury.hosting_credited")
               if r["cause"] == "outside")


def assert_books_are_digitaloceans(w, taken_at_start, prepaid_at_start=Decimal(0)):
    """What the books took is what DigitalOcean took, and the pot's books are its balance."""
    booked = sum(r["micro"] for r in items(w, "treasury.hosting_burn"))
    assert booked == w.hosting.burned_micro
    assert booked - invoice_credits(w) == micro(w.fake.taken - taken_at_start)
    assert outside_credits(w) == micro(w.fake.prepaid - prepaid_at_start)
    view = w.hosting.view()
    assert view["books_micro"] == view["credit_micro"] and view["discrepancy_micro"] == 0


def assert_wallet_untouched(w):
    assert w.wallet.balance == 500_000_000 and w.wallet.check_conservation()
    assert not items(w, "wallet.settle")


# --- the pot -------------------------------------------------------------------------------

def test_the_first_reading_is_the_endowment_and_nothing_moves():
    w = world(credit="200.00")
    assert w.observe() == {"effect": "endowment", "micro": 200_000_000}
    endowment = items(w, "treasury.hosting_endowment")[0]
    assert (endowment["micro"], endowment["counterparty"]) == (200_000_000, "digitalocean")
    assert endowment["generated_at"] == "2026-09-23T12:00:00Z"
    assert w.wallet.pots()["hosting"] == 200_000_000
    assert_wallet_untouched(w)


def test_a_burn_lowers_the_hosting_pot_and_never_the_wallet():
    w = world()
    w.observe()
    w.fake.bill("0.37")
    effect = w.observe()
    assert effect["burn_micro"] == 370_000
    burn = items(w, "treasury.hosting_burn")[0]
    assert burn["counterparty"] == "digitalocean" and burn["micro"] == 370_000
    assert burn["generated_at"] == "2026-09-23T13:00:00Z"
    assert burn["previous_generated_at"] == "2026-09-23T12:00:00Z"
    assert burn["month_to_date_usage_micro"] == [0, 370_000]
    assert w.wallet.pots()["hosting"] == 199_630_000
    assert_wallet_untouched(w)


def test_the_pot_is_not_in_the_total_the_wallet_is_reconciled_with():
    w = world(credit="100.00")
    before = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=w.wallet.pots())
    w.observe()
    w.fake.bill("2.50")
    w.observe()
    pots = w.wallet.pots()
    assert pots["hosting"] == 97_500_000
    assert pots["total_micro"] == before["pots_micro"]  # DigitalOcean credit backs nothing
    after = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=pots)
    assert after["discrepancy_micro"] == before["discrepancy_micro"]
    # The pot is reconciled against its own counterparty instead.
    assert pots["hosting_detail"]["discrepancy_micro"] == 0


def test_a_stale_or_failed_reading_books_nothing_and_leaves_the_pot_unknown():
    w = world()
    w.fake.bill("1.00")
    w.observe()
    w.fake.generated = w.fake.generated.replace(hour=1)  # older than the last reading
    w.fake.usage += 5
    assert w.observe()["effect"] == "stale"
    w.fake.down = True
    assert w.observe() is None and w.observe() is None
    assert [r["reason"] for r in items(w, "treasury.hosting_unread")] == ["billing read failed"]
    assert w.wallet.pots()["hosting"] is None
    assert w.hosting.burned_micro == 0
    assert_wallet_untouched(w)


# --- billing, against what DigitalOcean took --------------------------------------------------

def test_a_rollover_whose_usage_resets_before_the_invoice_lands():
    w = world(usage="3.00")
    w.observe()
    start = w.fake.taken
    w.fake.bill("7.00")
    w.observe()
    w.fake.bill("0.40")      # usage after the last reading of the month
    w.fake.reset_usage()     # the month closes; the invoice has not landed
    w.fake.bill("0.25")
    w.observe()
    assert w.hosting.burned_micro == micro("7.25")  # the dip in the balance booked nothing
    w.fake.land_invoice(tax="0.50")
    w.observe()
    w.fake.bill("1.00")
    w.observe()
    assert_books_are_digitaloceans(w, start)
    assert_wallet_untouched(w)


def test_a_rollover_whose_invoice_lands_before_the_usage_resets():
    w = world()
    w.observe()
    start = w.fake.taken
    w.fake.bill("10.00")
    w.observe()
    w.fake.invoice_before_reset()   # the balance double counts until the reset
    w.observe()
    assert w.hosting.burned_micro == micro("10")
    w.fake.reset_after_invoice()
    w.fake.bill("0.30")
    w.observe()
    assert_books_are_digitaloceans(w, start)


def test_a_rollover_read_only_across_it_still_books_once():
    w = world()
    w.observe()
    start = w.fake.taken
    w.fake.bill("4.00")
    w.observe()
    w.fake.bill("1.00")
    w.fake.rollover(tax="0.20")
    w.fake.bill("0.50")
    w.observe()   # one reading spans the end of the month, the reset and the invoice
    assert_books_are_digitaloceans(w, start)


def test_a_downward_revision_later_reaccrued_books_only_what_is_above_the_mark():
    w = world()
    w.observe()
    start = w.fake.taken
    w.fake.bill("5.00")
    w.observe()
    w.fake.revise("2.00")
    w.observe()
    w.fake.bill("2.00")         # back to the mark: nothing new was taken
    w.observe()
    assert w.hosting.burned_micro == micro("5")
    w.fake.bill("1.50")
    w.observe()
    assert w.hosting.burned_micro == micro("6.5")
    assert_books_are_digitaloceans(w, start)


def test_a_revision_never_reaccrued_is_returned_when_the_invoice_lands():
    w = world()
    w.observe()
    start = w.fake.taken
    w.fake.bill("5.00")
    w.observe()
    w.fake.revise("2.00")
    w.observe()
    w.fake.rollover()
    w.observe()
    assert_books_are_digitaloceans(w, start)


def test_credit_applied_at_invoice_is_credit_and_a_prepayment_is_credit_from_outside():
    w = world()
    w.observe()
    start, prepaid = w.fake.taken, w.fake.prepaid
    w.fake.bill("12.00")
    w.observe()
    w.fake.prepay("50.00")
    w.observe()
    w.fake.bill("3.00")
    w.observe()
    w.fake.reset_usage()
    w.observe()
    w.fake.land_invoice(promo="4.00")
    w.observe()
    assert invoice_credits(w) == micro("4") and outside_credits(w) == micro("50")
    # The prepayment reduced no burn: every dollar of usage after it was booked.
    assert w.hosting.burned_micro == micro("15")
    assert_books_are_digitaloceans(w, start, prepaid)
    assert_wallet_untouched(w)


# --- the account this world is bound to -------------------------------------------------------

def test_a_reading_from_another_account_is_refused_loudly_and_never_booked():
    w = world()
    w.observe()
    w.fake.bill("1.00")
    w.fake.user_uuid = "00000000-0000-4000-8000-000000000bad"   # another account's token
    w.fake.bill("40.00")
    assert w.observe() is None and w.observe() is None
    refused = items(w, "treasury.hosting_refused")
    assert [r["reason"] for r in refused] == [HostingRefused.ACCOUNT_MISMATCH]
    assert w.hosting.burned_micro == 0 and w.wallet.pots()["hosting"] is None
    w.fake.user_uuid = w.hosting.bound["billing_uuid"]
    w.observe()
    assert w.hosting.burned_micro == micro("41")   # measured from the bound account's reading
    w.fake.droplets = []
    assert w.observe() is None
    assert items(w, "treasury.hosting_refused")[-1]["reason"] == HostingRefused.DROPLET_NOT_HELD


def test_a_checkpoint_bound_to_another_account_is_refused_on_restore():
    w = world()
    w.observe()
    saved = w.treasury.snapshot()
    other = world(FakeDigitalOcean(user_uuid="00000000-0000-4000-8000-0000000000aa"))
    with pytest.raises(HostingRefused) as refused:
        other.treasury.restore(deepcopy(saved))
    assert refused.value.reason == HostingRefused.ACCOUNT_MISMATCH
    twin = world()
    twin.treasury.restore(deepcopy(saved))
    assert twin.hosting.state() == w.hosting.state()


@pytest.mark.parametrize(("change", "reason"), [
    (lambda f: setattr(f, "on_droplet", False), HostingRefused.NOT_ON_DROPLET),
    (lambda f: setattr(f, "droplet_id", 42), HostingRefused.DROPLET_MISMATCH),
    (lambda f: f.droplets.append(777), HostingRefused.NOT_DEDICATED),
    (lambda f: setattr(f, "volumes", 1), HostingRefused.NOT_DEDICATED),
    (lambda f: setattr(f, "snapshots", 1), HostingRefused.NOT_DEDICATED),
    (lambda f: setattr(f, "droplets", [123]), HostingRefused.DROPLET_NOT_HELD),
    (lambda f: setattr(f, "down", True), HostingRefused.NOT_ON_DROPLET),
])
def test_verification_refuses_a_host_that_is_not_a_dedicated_droplet(change, reason):
    fake = FakeDigitalOcean()
    change(fake)
    with pytest.raises(HostingRefused) as refused:
        verify(DigitalOceanClient(http=fake), 599960972)
    assert refused.value.reason == reason


def test_the_token_never_reaches_the_ledger_even_when_an_error_echoes_it(monkeypatch):
    w = world()
    w.observe()
    monkeypatch.setenv(TOKEN_ENV, "dop_v1_" + "b" * 64)   # a wrong token from here on
    w.fake.echo_auth_on_error = True
    assert w.observe() is None
    assert items(w, "treasury.hosting_unread")
    assert "b" * 64 not in str(w.records) and TOKEN not in str(w.records)
    assert "b" * 64 not in str(w.treasury.snapshot())
