"""The hosting pot: this droplet's invoice lines, booked month by month, never twice.

Every test runs against ``tests.digitalocean_fake``, which bills by its own rules (hourly
accrual per resource, a preview generated daily, a rollover lag, a monthly cap on the
final invoice, supplementary invoices, adjustments, and a billing history of any
type). Every month is checked against what the fake says it charged this droplet,
computed by the fake alone.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient
from factorylab.world.hosting import HostingAccount, HostingRefused, verify
from factorylab.world.treasury import FakeTreasury
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean, month_start, ns

#: DigitalOcean rounds a line to the cent; the launch month's pre-launch share is read
#: from a rounded line, so it may differ from the fake's exact accrual by under a cent.
CENT_MICRO = 10_000


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
    client = DigitalOceanClient(http=fake)
    treasury.hosting = HostingAccount(client, droplet_id=fake.droplet_id,
                                      bound=verify(client, fake.droplet_id, 10),
                                      launch_ns=ns(fake.now), budget_s=10)
    w = SimpleNamespace(ledger=ledger, wallet=wallet, treasury=treasury, fake=fake,
                        records=records, hosting=treasury.hosting,
                        droplet=str(fake.droplet_id), launch=fake.now)
    w.observe = treasury.observe_hosting
    return w


def items(w, kind):
    return [r for r in w.records if r["kind"] == kind]


def truth(w, month) -> int:
    """What DigitalOcean charged this droplet for a month after the launch, by the fake."""
    charged = micro(w.fake.billed(w.droplet, month))
    if month == w.launch.strftime("%Y-%m"):
        charged -= micro(w.fake.accrued_before(w.droplet, w.launch))
    return charged


def ledgered(w, month) -> int:
    return (sum(r["micro"] for r in items(w, "treasury.hosting_burn") if r["month"] == month)
            - sum(r["micro"] for r in items(w, "treasury.hosting_burn_reversed")
                  if r["month"] == month))


def launch_bound(w) -> int:
    entry = next((m for m in w.hosting.view()["burn_by_month"]
                  if m["month"] == w.launch.strftime("%Y-%m")), None)
    return entry["overshoot_bound_micro"] if entry else 0


def assert_months(w, months, extra=None):
    """Each invoiced month's booked burn is what the fake charged after the launch: exactly
    for every month but the launch month, whose pre-launch share is an estimate that may
    over-reach by at most its published bound (and falls short by under a cent)."""
    booked = w.hosting.burn_by_month()
    for month in months:
        expected = truth(w, month) + (extra or {}).get(month, 0)
        got = booked.get(month, 0)
        if month == w.launch.strftime("%Y-%m"):
            assert expected - CENT_MICRO <= got <= expected + launch_bound(w) + CENT_MICRO, (
                month, got, expected, launch_bound(w))
        else:
            assert got == expected, (month, got, expected)
        assert ledgered(w, month) == got


def assert_wallet_untouched(w):
    assert w.wallet.balance == 500_000_000 and w.wallet.check_conservation()
    assert not items(w, "wallet.settle")


def windows(w, count, hours=24):
    for _ in range(count):
        w.fake.advance(hours)
        w.observe()


# --- the launch month ------------------------------------------------------------------------

def test_the_launch_share_waits_for_a_line_that_reaches_past_the_launch():
    w = world()
    w.observe()        # the preview was generated at midnight, before the launch at noon
    assert w.hosting.pending_baseline and not items(w, "treasury.hosting_burn")
    assert len(items(w, "treasury.hosting_launch_share_pending")) == 1
    assert w.hosting.view()["launch_share"] == "pending"
    windows(w, 1)      # the next day's preview reaches past the launch
    share = items(w, "treasury.hosting_launch_share")[0]
    assert share["month"] == "2026-09" and share["source"] == "preview"
    assert abs(share["micro"] - micro(w.fake.accrued_before(w.droplet, w.launch))) <= CENT_MICRO
    assert w.hosting.burned_micro() > 0
    pots = w.wallet.pots()
    assert pots["hosting"] is None and pots["hosting_detail"]["balance"] == "unknown"
    assert_wallet_untouched(w)


def test_a_first_reading_without_the_droplets_line_books_no_prelaunch_accrual():
    """The review's 9.66 USD: an untagged first reading used to be a zero baseline."""
    w = world()
    w.fake.untagged = True
    windows(w, 2)
    assert w.hosting.pending_baseline and w.hosting.burned_micro() == 0
    w.fake.untagged = False
    windows(w, 1)
    booked = w.hosting.burn_by_month()["2026-09"]
    # Two and a half days of post-launch preview, never the 22.5 days before the launch.
    assert booked < micro("1.5")
    w.fake.advance(24 * 10)
    w.fake.post_invoice("2026-09")
    w.observe()
    assert_months(w, ["2026-09"])


def test_a_first_reading_in_a_later_month_still_books_the_launch_months_burn():
    """The review's lost 8 days: the launch month is booked from its own invoice."""
    w = world()
    w.fake.down = True
    windows(w, 9)                            # nothing read until October
    w.fake.down = False
    windows(w, 1)
    assert w.hosting.burn_by_month().get("2026-09", 0) == 0 and w.hosting.pending_baseline
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    booked = w.hosting.burn_by_month()["2026-09"]
    share = items(w, "treasury.hosting_launch_share")[0]
    assert share["source"] == "invoice"
    invoice = micro(w.fake.billed(w.droplet, "2026-09"))
    start, end = month_start("2026-09"), month_start("2026-10")
    # Shared by the line's own span; DigitalOcean's monthly cap discounts the whole month,
    # so this is at least the fake's post-launch charge and at most its span share.
    span_share = invoice - invoice * int((w.launch - start).total_seconds()) // int(
        (end - start).total_seconds())
    assert truth(w, "2026-09") <= booked == span_share
    assert booked > 0


# --- month by month ------------------------------------------------------------------------------

def test_months_close_with_a_capped_invoice_that_differs_from_the_preview():
    w = world()
    w.observe()
    for month, following in (("2026-09", "2026-10"), ("2026-10", "2026-11"),
                             ("2026-11", "2026-12")):
        while w.fake.now < month_start(following):
            windows(w, 1)
        w.fake.advance(12)
        w.fake.post_invoice(month, tax="1.20")
        w.observe()
    assert_months(w, ["2026-09", "2026-10", "2026-11"])
    # The cap made an invoice lower than the preview had shown: the books followed it down.
    assert items(w, "treasury.hosting_burn_reversed")
    # Tax sits on the invoice, not on this droplet's line, so it is not in the burn.
    assert all(inv["amount"] > inv["lines"][w.droplet] for inv in w.fake.invoices)
    assert_wallet_untouched(w)


def test_reading_the_same_figures_again_books_nothing():
    w = world()
    windows(w, 2)
    before = len(w.records)
    for _ in range(3):
        w.observe()
    assert w.records[before:] == []


def test_the_pot_is_not_in_the_total_the_wallet_is_reconciled_with():
    w = world()
    before = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=w.wallet.pots())
    windows(w, 5)
    after = Reconciler.snapshot(w.wallet.balance, None, None, pots_view=w.wallet.pots())
    assert after["pots_micro"] == before["pots_micro"]
    assert after["discrepancy_micro"] == before["discrepancy_micro"]


def test_more_than_three_unreconciled_invoices_on_several_pages_are_each_read_once():
    w = world()
    windows(w, 2)
    w.fake.down = True
    w.fake.advance(24 * 130)                 # four month ends pass unread
    for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
        w.fake.post_invoice(month)
    w.fake.post_supplement("2026-10", {w.droplet: "0.40"})   # two invoices for October
    w.fake.omit_total = True                 # and no meta.total to stop paging
    w.fake.down = False
    windows(w, 4, hours=1)
    assert sorted(w.hosting.reconciled) == sorted(inv["uuid"] for inv in w.fake.invoices)
    assert len(items(w, "treasury.hosting_invoice")) == 5
    assert_months(w, ["2026-09", "2026-10", "2026-11", "2026-12"])
    windows(w, 2, hours=1)
    assert len(items(w, "treasury.hosting_invoice")) == 5   # each read once


def test_a_month_that_ends_while_the_preview_still_shows_it_books_once():
    w = world(rollover_lag_hours=30)
    windows(w, 8)                            # into October; the preview shows September
    assert w.hosting.burn_by_month().get("2026-10", 0) == 0
    windows(w, 2)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    assert_months(w, ["2026-09"])


def test_late_adjustments_up_and_down_are_followed():
    w = world()
    windows(w, 12)                           # into October, a month measured whole
    w.fake.adjust(w.droplet, "1.25")
    windows(w, 1)
    w.fake.adjust(w.droplet, "-0.75")
    windows(w, 1)
    assert [r["month"] for r in items(w, "treasury.hosting_burn_reversed")] == ["2026-10"]
    w.fake.advance(24 * 29)                  # October ends
    w.fake.adjust(w.droplet, "-0.30", month="2026-10")
    w.observe()
    w.fake.post_invoice("2026-09")
    w.fake.post_invoice("2026-10")
    windows(w, 2, hours=1)
    assert_months(w, ["2026-09", "2026-10"])


def test_a_month_below_zero_is_not_booked_below_zero():
    w = world()
    windows(w, 2)
    w.fake.advance(24 * 8)
    w.fake.post_invoice("2026-09")
    w.observe()
    w.fake.post_supplement("2026-09", {w.droplet: "-50.00"})   # a credit line larger than all
    w.observe()
    assert w.hosting.burn_by_month()["2026-09"] == 0
    negative = items(w, "treasury.hosting_negative_month")
    assert [n["month"] for n in negative] == ["2026-09"] and negative[0]["level_micro"] < 0
    assert ledgered(w, "2026-09") == 0


# --- other resources, other text -----------------------------------------------------------

def test_a_foreign_line_or_a_new_history_type_never_blocks_later_months():
    """The review's wedge: one line of another product sat in a finalized invoice and
    failed every read after it; a history type outside the list did the same."""
    w = world()
    windows(w, 2)
    w.fake.add("db-1", "Managed Databases: PostgreSQL", "0.02", "db")
    w.fake.add("app-1", "Uptime & Alerts", "0.001", "x")
    w.fake.pay("1.00", kind="Tax")
    w.fake.pay("2.00", kind="Promo Code")
    w.fake.advance(24 * 10)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    w.fake.advance(24 * 31)
    w.fake.post_invoice("2026-10")
    windows(w, 1)
    assert w.hosting.unread is None
    assert_months(w, ["2026-09", "2026-10"])
    kinds = {r["entry_type"] for r in items(w, "treasury.hosting_account_entry")}
    assert "unknown" in kinds and "Tax" not in kinds
    detail = w.wallet.pots()["hosting_detail"]
    assert "account" not in str(detail).replace("account-wide", "")   # nothing of the account


def test_the_droplets_backups_are_its_burn_and_a_snapshot_sharing_its_id_is_not():
    w = world()
    windows(w, 1)
    # A backup is billed against the droplet's id; a snapshot has its own id, which here
    # happens to be the same number; and another droplet is another droplet.
    w.fake.add("bk-1", "Droplet Backups", "0.00357", "weekly", billed_as=w.droplet)
    w.fake.add("snap-1", "Snapshots", "0.00083", "snapshot", billed_as=w.droplet)
    w.fake.add("888", "Droplets", "0.07143", "a-bigger-host")
    windows(w, 3)
    w.fake.remove("888")
    w.fake.advance(24 * 7)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    backups = micro(w.fake.billed("bk-1", "2026-09"))
    assert backups > 0 and micro(w.fake.billed("snap-1", "2026-09")) > 0
    assert_months(w, ["2026-09"], extra={"2026-09": backups -   # backups in, pre-launch out
                                         micro(w.fake.accrued_before("bk-1", w.launch))})
    reconciled = items(w, "treasury.hosting_invoice")[0]
    assert (reconciled["lines"], reconciled["other_lines"]) == (2, 2)     # snapshot out


def test_untagged_lines_are_flagged_never_labelled_invoiced_and_cleared_when_matched():
    w = world()
    windows(w, 2)
    w.fake.untagged = True
    w.fake.advance(24 * 8)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    assert w.hosting.unmatched == ["2026-09", "2026-10"]
    assert "2026-09" not in w.hosting.invoiced
    sept = next(m for m in w.hosting.view()["burn_by_month"] if m["month"] == "2026-09")
    assert sept["source"] == "preview"
    assert not items(w, "treasury.hosting_burn_reversed")      # nothing taken back
    w.fake.untagged = False
    w.fake.post_supplement("2026-09", {w.droplet: "0.10"})
    windows(w, 1)
    assert w.hosting.unmatched == []
    assert [r["month"] for r in items(w, "treasury.hosting_matched")] == ["2026-09", "2026-10"]


# --- the account this world is bound to ------------------------------------------------------

@pytest.mark.parametrize(("change", "reason"), [
    (lambda f: setattr(f, "user_uuid", "00000000-0000-4000-8000-000000000bad"),
     HostingRefused.ACCOUNT_MISMATCH),
    (lambda f: f.remove(str(f.droplet_id)), HostingRefused.DROPLET_NOT_HELD),
    (lambda f: setattr(f, "droplet_id", 42), HostingRefused.DROPLET_MISMATCH),
])
def test_a_reading_that_is_not_the_bound_droplets_is_refused_and_books_nothing(change, reason):
    w = world()
    windows(w, 2)
    booked = w.hosting.burn_by_month()
    change(w.fake)
    windows(w, 2)
    assert [r["reason"] for r in items(w, "treasury.hosting_refused")] == [reason]
    assert w.hosting.burn_by_month() == booked


def test_back_on_the_bound_account_the_books_catch_up_once():
    w = world()
    windows(w, 2)
    w.fake.user_uuid, real = "00000000-0000-4000-8000-000000000bad", w.fake.user_uuid
    windows(w, 3)
    w.fake.user_uuid = real
    windows(w, 1, hours=0)
    windows(w, 1, hours=0)
    w.fake.advance(24 * 5)
    w.fake.post_invoice("2026-09")
    w.observe()
    assert_months(w, ["2026-09"])


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


# --- deadlines, resume, secrets ----------------------------------------------------------------

def test_a_read_that_passes_its_deadline_books_nothing():
    import time

    w = world()
    windows(w, 2)
    booked = w.hosting.burn_by_month()
    w.hosting.budget_s = 0.05
    w.fake.stall = lambda url, timeout: time.sleep(0.03)
    windows(w, 1)
    assert items(w, "treasury.hosting_unread")[0]["reason"] == (
        "billing read failed or passed its deadline")
    assert w.hosting.burn_by_month() == booked


def test_the_books_survive_a_checkpoint_and_book_on_from_it():
    w = world()
    windows(w, 3)
    saved = w.treasury.snapshot()
    twin = world(w.fake)
    twin.treasury.restore(deepcopy(saved))
    assert twin.hosting.state() == w.hosting.state()
    twin.observe()
    assert not items(twin, "treasury.hosting_burn")     # the same figures: nothing new
    w.fake.advance(24 * 8)
    w.fake.post_invoice("2026-09")
    twin.observe()
    # Measured from the launch the checkpoint carries, not from when the twin started.
    assert twin.hosting.launch_ns == w.hosting.launch_ns
    got, expected = twin.hosting.burn_by_month()["2026-09"], truth(w, "2026-09")
    assert expected - CENT_MICRO <= got <= expected + launch_bound(twin) + CENT_MICRO


def test_the_token_never_reaches_the_ledger_even_when_an_error_echoes_it(monkeypatch):
    w = world()
    windows(w, 1)
    monkeypatch.setenv(TOKEN_ENV, "dop_v1_" + "b" * 64)   # a wrong token from here on
    w.fake.echo_auth_on_error = True
    windows(w, 1)
    assert items(w, "treasury.hosting_unread")
    assert "b" * 64 not in str(w.records) and TOKEN not in str(w.records)
    assert "b" * 64 not in str(w.treasury.snapshot())


@pytest.mark.parametrize("late", [False, True])
def test_the_launch_months_burn_is_labelled_an_estimate_with_its_bound(late):
    """The pre-launch share is an allocation, not a measurement: every item and the pot
    say so, with the most the booked burn can exceed the true post-launch charge by."""
    w = world()
    if late:
        w.fake.down = True                   # first read after the month, from its invoice
    windows(w, 9)
    w.fake.down = False
    w.fake.post_invoice("2026-09")
    windows(w, 2)
    share = items(w, "treasury.hosting_launch_share")[0]
    assert share["estimated"] is True and share["source"] == ("invoice" if late else "preview")
    assert share["overshoot_bound_micro"] >= 0
    assert "at most overshoot_bound_micro" in share["bound"]
    bound = items(w, "treasury.hosting_launch_share")[-1]["overshoot_bound_micro"]
    launch = [r for r in items(w, "treasury.hosting_burn") + items(
        w, "treasury.hosting_burn_reversed") if r["month"] == "2026-09"]
    assert launch and all(r["estimated"] is True and r["overshoot_bound_micro"] >= 0
                          for r in launch)
    later = [r for r in items(w, "treasury.hosting_burn") if r["month"] != "2026-09"]
    assert later and all(r["estimated"] is False for r in later)
    view = {m["month"]: m for m in w.hosting.view()["burn_by_month"]}
    assert view["2026-09"]["estimated"] is True
    assert view["2026-09"]["overshoot_bound_micro"] == bound
    assert view["2026-10"]["estimated"] is False
    # The bound holds against what the fake charged after the launch.
    overshoot = w.hosting.burn_by_month()["2026-09"] - truth(w, "2026-09")
    assert overshoot <= bound + CENT_MICRO
    if late:
        assert overshoot > CENT_MICRO        # the capped invoice made it a real estimate


# --- Codex on e78b8ae --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["hosting.billing", "hosting.droplet", "hosting.catalogue"])
def test_an_interrupted_hosting_read_replays_as_unavailable_and_is_never_sent(name):
    """A crash between a hosting read's io.call and its io.result: the resume completes
    the call as unavailable and asks DigitalOcean nothing."""
    import hashlib

    from factorylab.kernel.ledger import canonical
    from factorylab.runtime.resume import RecoveryJournal, encode

    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = journal.recovering = True
    args = (599960972,)
    fingerprint = hashlib.sha256(canonical(encode((args, {})))).hexdigest()
    journal.tail = [{"kind": "io.call", "name": name, "input_hash": fingerprint,
                     "seq": 0, "ts": 0}]
    fake = FakeDigitalOcean()
    live = DigitalOceanClient(http=fake)
    with pytest.raises(OSError):
        journal.call(name, lambda *a: live.identity(a[0], 10), args, {})
    assert fake.calls == []                          # zero DigitalOcean calls
    assert recorded == [{"kind": "io.result", "call": 0, "error": "OSError"}]


def test_a_uuid_only_attached_line_on_a_supplement_read_first_is_booked_not_dropped():
    """The first read finds a closed supplemental invoice, ordered before the month's main
    invoice, whose only line names the droplet by uuid alone. It is booked once the uuid is
    known, never reconciled as foreign."""
    import uuid as uuidlib

    w = world()
    w.fake.down = True
    windows(w, 9)                            # nothing read until October
    w.fake.post_invoice("2026-09")
    droplet_uuid = str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, w.droplet))
    supplement = w.fake.post_supplement("2026-09", {"attached-1": "2.50"},
                                        uuid="00000000-0000-4000-8000-00000000000a")
    w.fake.resources["attached-1"] = {**w.fake.resources[w.droplet],
                                      "product": "Reserved Capacity", "billed_as": ""}
    w.fake.uuid_of["attached-1"] = droplet_uuid          # billed against the droplet's uuid
    w.fake.down = False
    windows(w, 3, hours=1)
    assert supplement["uuid"] in w.hosting.reconciled
    reconciled = {r["uuid"]: r for r in items(w, "treasury.hosting_invoice")}
    assert reconciled[supplement["uuid"]]["lines"] == 1          # matched, not foreign
    assert w.hosting.lines["2026-09"] and sum(
        v for k, v in w.hosting.lines["2026-09"].items()
        if k.startswith(supplement["uuid"])) == 2_500_000


def test_the_launch_share_follows_a_backup_line_that_arrives_after_the_first_read():
    """The pre-launch share and its bound are recomputed from every current launch-month
    line on each read: a backup billed from before the launch, appearing later, is not
    booked as post-launch burn. At every read: booked <= bound + true post-launch."""
    w = world()
    ours = [w.droplet]

    def check():
        booked = w.hosting.burn_by_month().get("2026-09", 0)
        entry = next((m for m in w.hosting.view()["burn_by_month"]
                      if m["month"] == "2026-09"), None)
        bound = entry["overshoot_bound_micro"] if entry else 0
        post = sum(micro(w.fake.visible(r, "2026-09")) for r in ours) - sum(
            micro(w.fake.accrued_before(r, w.launch)) for r in ours)
        assert booked <= bound + max(0, post) + CENT_MICRO, (booked, bound, post)
        if entry:
            assert entry["estimated"] is True

    windows(w, 2)
    check()
    # A weekly backup, billed against the droplet since the start of the month, whose
    # line only now appears.
    w.fake.add("bk-1", "Droplet Backups", "0.00357", "weekly", billed_as=w.droplet,
               since=month_start("2026-09"))
    ours.append("bk-1")
    for _ in range(4):
        windows(w, 1)
        check()
    assert w.hosting.view()["burn_by_month"][0]["estimate_final"] is False
    w.fake.advance(24 * 3)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    check()
    assert w.hosting.view()["burn_by_month"][0]["estimate_final"] is True
    shares = items(w, "treasury.hosting_launch_share")
    assert len(shares) > 1                            # recomputed as lines changed


# --- Codex on 2f61a39 --------------------------------------------------------------------------

def _recorded_world(fake=None):
    """A world whose hosting reads and ledger items go through a recovery journal, so a
    reading it booked can be replayed in a second world from the record alone."""
    from factorylab.runtime.resume import JournalProxy, RecoveryJournal

    w = world(fake)
    recorded = []

    def append(item):
        recorded.append({**item, "seq": len(recorded), "ts": item.get("ts", 0)})
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = True
    w.treasury.ledger = journal
    w.hosting.client = JournalProxy(DigitalOceanClient(http=w.fake), journal, "hosting")
    return w, journal, recorded


def _replay(w, recorded):
    """A second world restored to where ``w`` started, replaying ``recorded``."""
    from factorylab.runtime.resume import JournalProxy, RecoveryJournal

    twin = world(w.fake)
    journal = RecoveryJournal(SimpleNamespace(append=lambda item: len(recorded)), lambda: 0)
    journal.active = journal.recovering = True
    journal.tail = list(recorded)
    twin.treasury.ledger = journal
    twin.hosting.launch_ns = w.hosting.launch_ns
    twin.hosting.client = JournalProxy(DigitalOceanClient(http=lambda *a: pytest.fail(
        "a replay asked DigitalOcean")), journal, "hosting")
    return twin


@pytest.mark.parametrize("shape", ["zero", "inverted"])
def test_a_line_with_no_span_or_an_inverted_one_never_crashes_a_read_or_its_replay(shape):
    w, _, recorded = _recorded_world()
    start = w.launch - timedelta(hours=5)
    w.fake.span_of[w.droplet] = ((w.launch + timedelta(hours=2),) * 2 if shape == "zero"
                                 else (w.launch + timedelta(hours=3), start))
    w.fake.advance(24)
    w.treasury.observe_hosting()            # must not raise
    if shape == "zero":
        # No time to split: booked whole, in the month it is dated, after the launch.
        assert w.hosting.unread is None
        assert w.hosting.burn_by_month()["2026-09"] == micro(w.fake.visible(w.droplet,
                                                                           "2026-09"))
    else:
        assert w.hosting.unread == "billing read failed or passed its deadline"
        assert w.hosting.burn_by_month() == {}
    twin = _replay(w, recorded)
    twin.treasury.observe_hosting()         # the same recorded answer resumes cleanly
    assert twin.hosting.state() == w.hosting.state()


def test_the_bound_is_priced_at_the_launch_months_rate_not_after_a_price_change():
    """A resize after the launch month's first read lowers the droplet's hourly price; a
    bound priced at the new rate collapsed to zero while the booked burn still over-reached."""
    w = world()
    windows(w, 2)
    launch_price = items(w, "treasury.hosting_launch_price")
    assert launch_price and launch_price[0]["price_hourly_micro"] == 17_860
    w.fake.size = "s-1vcpu-1gb"             # the droplet now lists at half the rate
    w.fake.advance(24 * 9)
    w.fake.post_invoice("2026-09")
    windows(w, 1)
    bound = launch_bound(w)
    overshoot = w.hosting.burn_by_month()["2026-09"] - truth(w, "2026-09")
    assert overshoot > CENT_MICRO and bound >= overshoot
    assert_months(w, ["2026-09"])


def test_without_a_launch_price_the_bound_is_the_whole_post_launch_allocation():
    w = world()
    w.fake.down = True
    windows(w, 9)
    w.fake.down = False
    w.fake.post_invoice("2026-09")
    windows(w, 2)
    assert not items(w, "treasury.hosting_launch_price")
    assert launch_bound(w) == w.hosting.burn_by_month()["2026-09"] > 0
    assert_months(w, ["2026-09"])


def test_a_credit_spanning_the_launch_widens_the_bound_and_never_narrows_it():
    def run(with_credit):
        w = world()
        if with_credit:
            w.fake.add("cr-1", "Droplet Backups", "-0.00200", "credit",
                       billed_as=w.droplet, since=month_start("2026-09"))
        windows(w, 3)
        return w

    plain, credited = run(False), run(True)
    assert launch_bound(credited) >= launch_bound(plain) + 10_000 > 0
    post = sum(micro(credited.fake.visible(r, "2026-09")) for r in (credited.droplet, "cr-1"))
    post -= sum(micro(credited.fake.accrued_before(r, credited.launch))
                for r in (credited.droplet, "cr-1"))
    booked = credited.hosting.burn_by_month()["2026-09"]
    assert booked <= launch_bound(credited) + post + CENT_MICRO
