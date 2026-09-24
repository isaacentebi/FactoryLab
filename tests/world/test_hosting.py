"""The hosting pot: this droplet's invoice lines, booked month by month, never twice.

Every test runs against ``tests.digitalocean_fake``, which bills by DigitalOcean's rules
(hourly accrual per resource, a preview for the month in progress, invoices that post
when the test says, adjustments, credits and refunds in the billing history). The
books are checked, month by month, against what the fake shows for this droplet.
"""

from copy import deepcopy
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
    return int(usd * 1_000_000)


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
    client = DigitalOceanClient(http=fake)
    treasury.hosting = HostingAccount(client, droplet_id=fake.droplet_id,
                                      bound=verify(client, fake.droplet_id, 10), budget_s=10)
    w = SimpleNamespace(ledger=ledger, wallet=wallet, treasury=treasury, fake=fake,
                        records=records, hosting=treasury.hosting,
                        droplet=str(fake.droplet_id))
    w.observe = treasury.observe_hosting
    return w


def items(w, kind):
    return [r for r in w.records if r["kind"] == kind]


def assert_every_month_is_digitaloceans(w):
    """Each booked month is DigitalOcean's figure for this droplet, less the launch baseline;
    and the ledger's burn items sum, month by month, to the same."""
    booked = w.hosting.burn_by_month()
    assert booked, "nothing booked"
    for month, amount in booked.items():
        truth = micro(w.fake.billed(w.droplet, month)) - w.hosting.baseline.get(month, 0)
        assert amount == truth, (month, amount, truth)
        ledgered = (sum(r["micro"] for r in items(w, "treasury.hosting_burn")
                        if r["month"] == month)
                    - sum(r["micro"] for r in items(w, "treasury.hosting_burn_reversed")
                          if r["month"] == month))
        assert ledgered == amount, (month, ledgered, amount)


def assert_wallet_untouched(w):
    assert w.wallet.balance == 500_000_000 and w.wallet.check_conservation()
    assert not items(w, "wallet.settle")


# --- the pot -------------------------------------------------------------------------------

def test_the_first_reading_is_a_baseline_and_nothing_moves():
    w = world()
    result = w.observe()
    baseline = micro(w.fake.billed(w.droplet, "2026-09"))
    assert result["baseline"] == {"month": "2026-09", "micro": baseline} and baseline > 0
    assert items(w, "treasury.hosting_baseline")[0]["micro"] == baseline
    assert not items(w, "treasury.hosting_burn") and w.hosting.burned_micro() == 0
    pots = w.wallet.pots()
    assert pots["hosting"] is None and pots["hosting_detail"]["balance"] == "unknown"
    assert_wallet_untouched(w)


def test_a_reading_books_the_droplets_line_increase_and_never_the_wallet():
    w = world()
    w.observe()
    w.fake.advance(48)
    w.observe()
    burn = items(w, "treasury.hosting_burn")
    assert len(burn) == 1 and burn[0]["counterparty"] == "digitalocean"
    assert burn[0]["month"] == "2026-09" and burn[0]["source"] == "preview"
    # 48 hours at the droplet's hourly price, as DigitalOcean rounds its line to cents.
    assert abs(burn[0]["micro"] - micro(48 * w.fake.resources[w.droplet]["hourly"])) <= 10_000
    assert_every_month_is_digitaloceans(w)
    assert_wallet_untouched(w)


def test_reading_the_same_figures_again_books_nothing():
    w = world()
    w.observe()
    w.fake.advance(10)
    w.observe()
    before = len(w.records)
    for _ in range(3):
        w.observe()
    assert len(items(w, "treasury.hosting_burn")) == 1
    assert [r["kind"] for r in w.records[before:]] == []
    # The same reading handed over twice books once.
    reading = DigitalOceanClient(http=w.fake).billing(
        w.fake.droplet_id, since=w.hosting.since, done=w.hosting.finalized, budget_s=10)
    assert w.hosting.observe(reading)["changes"] == []


def test_the_pot_is_not_in_the_total_the_wallet_is_reconciled_with():
    w = world()
    before = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=w.wallet.pots())
    w.observe()
    w.fake.advance(100)
    w.observe()
    after = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=w.wallet.pots())
    assert after["pots_micro"] == before["pots_micro"]
    assert after["discrepancy_micro"] == before["discrepancy_micro"]


# --- month by month, against DigitalOcean ------------------------------------------------------

def test_months_close_and_their_invoices_replace_the_preview():
    w = world()
    w.observe()
    for _ in range(3):                       # three month ends, each invoice on time
        w.fake.advance(24 * 12)
        w.observe()
        w.fake.advance(24 * 20)
        if w.fake.closed:
            w.fake.post_invoice(tax="1.20")
        w.observe()
    assert w.hosting.finalized == ["2026-09", "2026-10", "2026-11"]
    assert_every_month_is_digitaloceans(w)
    # The invoice's tax is on the account, not on this droplet's line.
    assert all(inv["amount"] > inv["lines"][w.droplet] for inv in w.fake.invoices)


def test_reads_missed_across_month_ends_are_booked_once_the_invoices_post():
    w = world()
    w.observe()
    w.fake.advance(24 * 3)
    w.observe()                              # September, three days on
    w.fake.advance(24 * 60)                  # no reading for two month ends
    w.observe()                              # November's preview; nothing has posted
    assert w.hosting.finalized == []
    w.fake.post_invoice()
    w.fake.post_invoice()
    w.observe()
    w.observe()
    assert w.hosting.finalized == ["2026-09", "2026-10"]
    assert_every_month_is_digitaloceans(w)
    october = [r for r in items(w, "treasury.hosting_burn") if r["month"] == "2026-10"]
    assert [r["source"] for r in october] == ["invoice"]   # never seen in preview


def test_late_adjustments_up_and_down_are_followed_to_digitaloceans_figure():
    w = world()
    w.observe()
    w.fake.advance(24)
    w.fake.adjust(w.droplet, "1.25")
    w.observe()
    w.fake.adjust(w.droplet, "-0.75")
    w.observe()
    assert items(w, "treasury.hosting_burn_reversed")[0]["micro"] == 750_000
    w.fake.advance(24 * 10)                  # September closes
    w.fake.adjust(w.droplet, "-2.00", month="2026-09")   # adjusted before it posts
    w.observe()
    w.fake.post_invoice()
    w.observe()
    assert_every_month_is_digitaloceans(w)


def test_a_promo_covered_month_books_the_droplets_line_and_the_credit_as_an_account_fact():
    w = world()
    w.observe()
    w.fake.advance(24 * 10)
    w.fake.post_invoice(promo="50.00")      # the whole month covered by a promotion
    w.fake.pay("20.00")
    w.fake.pay("3.00", kind="Refund")
    w.observe()
    assert_every_month_is_digitaloceans(w)
    entries = {r["entry_type"]: r for r in items(w, "treasury.hosting_account_entry")}
    assert set(entries) == {"Invoice", "Credit", "Payment", "Refund"}
    assert all(r["attributed"] is False for r in entries.values())
    assert entries["Credit"]["micro"] == -50_000_000
    detail = w.wallet.pots()["hosting_detail"]
    assert detail["account_entries_micro"]["Credit"] == -50_000_000
    assert detail["balance_micro"] is None     # no credit is taken to be this droplet's
    assert_wallet_untouched(w)


def test_history_already_on_the_account_at_launch_is_not_this_worlds():
    fake = FakeDigitalOcean()
    fake.pay("100.00")
    w = world(fake)
    w.observe()
    assert w.wallet.pots()["hosting_detail"]["account_entries_micro"] == {}
    fake.pay("7.00")
    w.observe()
    assert w.wallet.pots()["hosting_detail"]["account_entries_micro"] == {"Payment": -7_000_000}


def test_other_resources_added_and_removed_are_never_booked():
    w = world()
    w.observe()
    w.fake.add("888", "Droplets", "0.07143", "a-bigger-host")
    w.fake.add("vol-1", "Volumes", "0.01400", "data")
    w.fake.advance(24 * 3)
    w.observe()
    w.fake.remove("888")
    w.fake.advance(24 * 10)
    w.fake.post_invoice()
    w.observe()
    w.fake.advance(24)
    w.observe()
    assert_every_month_is_digitaloceans(w)
    assert all(r["line"] for r in items(w, "treasury.hosting_burn"))
    total = sum(micro(w.fake.billed(rid, m)) for rid in ("888", "vol-1")
                for m in ("2026-09", "2026-10"))
    assert total > 0 and w.hosting.burned_micro() < total + 1   # none of theirs in it


def test_lines_that_name_no_resource_are_flagged_and_never_booked():
    w = world()
    w.observe()
    w.fake.untagged = True
    w.fake.advance(24)
    w.observe()
    w.observe()
    assert [r["month"] for r in items(w, "treasury.hosting_unmatched")] == ["2026-09"]
    # The droplet's line could not be told apart: nothing is booked for it, and nothing
    # already booked is taken back.
    assert not items(w, "treasury.hosting_burn")
    assert not items(w, "treasury.hosting_burn_reversed")
    w.fake.untagged = False
    w.observe()
    assert_every_month_is_digitaloceans(w)


# --- the account this world is bound to --------------------------------------------------------

@pytest.mark.parametrize(("change", "reason"), [
    (lambda f: setattr(f, "user_uuid", "00000000-0000-4000-8000-000000000bad"),
     HostingRefused.ACCOUNT_MISMATCH),
    (lambda f: f.remove(str(f.droplet_id)), HostingRefused.DROPLET_NOT_HELD),
    (lambda f: setattr(f, "droplet_id", 42), HostingRefused.DROPLET_MISMATCH),
])
def test_a_reading_that_is_not_the_bound_droplets_is_refused_and_books_nothing(change, reason):
    w = world()
    w.observe()
    w.fake.advance(24)
    change(w.fake)
    w.fake.advance(24)
    assert w.observe() is None and w.observe() is None
    assert [r["reason"] for r in items(w, "treasury.hosting_refused")] == [reason]
    assert not items(w, "treasury.hosting_burn")


def test_back_on_the_bound_account_the_books_catch_up_to_its_figure_once():
    w = world()
    w.observe()
    w.fake.user_uuid, real = "00000000-0000-4000-8000-000000000bad", w.fake.user_uuid
    w.fake.advance(48)
    assert w.observe() is None
    w.fake.user_uuid = real
    w.observe()
    w.observe()
    assert len(items(w, "treasury.hosting_burn")) == 1
    assert_every_month_is_digitaloceans(w)


@pytest.mark.parametrize(("change", "reason"), [
    (lambda f: setattr(f, "on_droplet", False), HostingRefused.NOT_ON_DROPLET),
    (lambda f: setattr(f, "droplet_id", 42), HostingRefused.DROPLET_MISMATCH),
])
def test_a_launch_off_the_droplet_is_refused(change, reason):
    fake = FakeDigitalOcean()
    change(fake)
    with pytest.raises(HostingRefused) as refused:
        verify(DigitalOceanClient(http=fake), 599960972, 10)
    assert refused.value.reason == reason


def test_a_launch_on_an_account_with_other_resources_is_accepted():
    fake = FakeDigitalOcean()
    fake.add("888", "Droplets", "0.07143", "another")
    fake.add("vol-1", "Volumes", "0.01400", "data")
    assert verify(DigitalOceanClient(http=fake), fake.droplet_id, 10)["droplet_id"] == \
        fake.droplet_id


# --- deadlines, resume, secrets ----------------------------------------------------------------

def test_a_read_that_passes_its_deadline_books_nothing():
    import time

    w = world()
    w.observe()
    w.fake.advance(24)
    w.hosting.budget_s = 0.05
    w.fake.stall = lambda url, timeout: time.sleep(0.03)
    assert w.observe() is None
    assert items(w, "treasury.hosting_unread")[0]["reason"] == (
        "billing read failed or passed its deadline")
    assert not items(w, "treasury.hosting_burn")
    w.fake.stall, w.hosting.budget_s = None, 10
    w.observe()
    assert_every_month_is_digitaloceans(w)


def test_the_books_survive_a_checkpoint_and_book_on_from_it():
    w = world()
    w.observe()
    w.fake.advance(24 * 10)
    w.observe()
    saved = w.treasury.snapshot()
    twin = world(w.fake)
    twin.treasury.restore(deepcopy(saved))
    assert twin.hosting.state() == w.hosting.state()
    twin.observe()                            # the same figures: nothing new
    assert not items(twin, "treasury.hosting_burn")
    w.fake.post_invoice()
    twin.observe()
    booked = twin.hosting.burn_by_month()["2026-09"]
    assert booked == micro(w.fake.billed(twin.droplet, "2026-09")) - twin.hosting.baseline[
        "2026-09"]


def test_the_token_never_reaches_the_ledger_even_when_an_error_echoes_it(monkeypatch):
    w = world()
    w.observe()
    monkeypatch.setenv(TOKEN_ENV, "dop_v1_" + "b" * 64)   # a wrong token from here on
    w.fake.echo_auth_on_error = True
    assert w.observe() is None
    assert items(w, "treasury.hosting_unread")
    assert "b" * 64 not in str(w.records) and TOKEN not in str(w.records)
    assert "b" * 64 not in str(w.treasury.snapshot())
