"""The hosting pot: burn observed from DigitalOcean's billing, and a resize journaled first.

Every test runs against ``tests.digitalocean_fake`` at the HTTP boundary.
"""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pytest

from factorylab.kernel.budget import BudgetBook
from factorylab.kernel.ledger import Ledger, canonical
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.live import Reconciler
from factorylab.runtime.resume import RecoveryJournal, encode
from factorylab.world.digitalocean import TOKEN_ENV, DigitalOceanClient
from factorylab.world.hosting import UNCERTAIN_POLLS, HostingAccount
from factorylab.world.treasury import FakeTreasury
from tests.digitalocean_fake import TOKEN, FakeDigitalOcean


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, TOKEN)


def world(*, credit="200.00", cap="24", disk=False, **fake_args):
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
    fake = FakeDigitalOcean(credit=credit, **fake_args)
    from factorylab.kernel.money import usd_to_micro

    treasury.hosting = HostingAccount(DigitalOceanClient(http=fake), droplet_id=fake.droplet_id,
                                      max_monthly_micro=usd_to_micro(cap, rounding="exact"),
                                      allow_disk_resize=disk)
    return SimpleNamespace(ledger=ledger, wallet=wallet, treasury=treasury, fake=fake,
                           records=records, hosting=treasury.hosting)


def kinds(w, prefix):
    return [r["kind"] for r in w.records if r["kind"].startswith(prefix)]


# --- the pot -------------------------------------------------------------------------------

def test_the_first_reading_is_the_endowment_and_moves_nothing():
    w = world(credit="200.00")
    effect = w.treasury.observe_hosting()
    assert effect == {"effect": "endowment", "micro": 200_000_000}
    assert w.wallet.balance == 500_000_000
    endowment = next(r for r in w.records if r["kind"] == "treasury.hosting_endowment")
    assert endowment["micro"] == 200_000_000 and endowment["counterparty"] == "digitalocean"
    assert endowment["generated_at"] == "2026-09-23T12:00:00Z"
    pots = w.wallet.pots()
    assert pots["hosting"] == 200_000_000 and pots["hosting_detail"]["endowment_micro"]


def test_burn_is_observed_from_two_readings_and_debits_exactly_what_digitalocean_reports():
    w = world()
    w.treasury.observe_hosting()
    w.fake.bill("0.37")
    before = w.wallet.balance
    effect = w.treasury.observe_hosting()
    assert effect["effect"] == "burn" and effect["micro"] == 370_000
    assert w.wallet.balance == before - 370_000 and w.wallet.check_conservation()
    burn = next(r for r in w.records if r["kind"] == "treasury.hosting_burn")
    assert burn["counterparty"] == "digitalocean" and burn["micro"] == 370_000
    assert burn["generated_at"] == "2026-09-23T13:00:00Z"
    assert burn["previous_generated_at"] == "2026-09-23T12:00:00Z"
    assert burn["month_to_date_usage_micro"] == [0, 370_000]
    settle = next(r for r in w.records if r["kind"] == "wallet.settle")
    assert (settle["amount"], settle["reason"], settle["handle"]) == (
        -370_000, "hosting", "hosting:digitalocean")
    # The evidence precedes the debit, so the diary says why the wallet moved.
    assert w.records.index(burn) < w.records.index(settle)
    assert w.wallet.pots()["hosting"] == 199_630_000
    assert w.hosting.burned_micro == 370_000


def test_no_wallet_movement_without_a_billing_change():
    w = world()
    w.treasury.observe_hosting()
    balance = w.wallet.balance
    w.fake.tick()  # a newer reading, the same figures
    assert w.treasury.observe_hosting()["effect"] == "unchanged"
    w.fake.rollover()  # the invoice moves usage into the account balance: no money moved
    assert w.treasury.observe_hosting()["effect"] == "unchanged"
    w.fake.prepay("50")  # credit arriving from outside is ledgered, never booked
    credited = w.treasury.observe_hosting()
    assert (credited["effect"], credited["micro"]) == ("credited", 50_000_000)
    assert w.wallet.balance == balance
    assert kinds(w, "treasury.hosting_credited") == ["treasury.hosting_credited"]
    assert not any(r["kind"] == "wallet.settle" for r in w.records)


def test_a_stale_or_failed_reading_changes_nothing_and_leaves_the_pot_unknown():
    w = world()
    w.fake.bill("1.00")
    w.treasury.observe_hosting()
    w.fake.generated = w.fake.generated.replace(hour=1)  # older than the last reading
    w.fake.usage += 5
    assert w.treasury.observe_hosting()["effect"] == "stale"
    w.fake.down = True
    balance = w.wallet.balance
    assert w.treasury.observe_hosting() is None and w.treasury.observe_hosting() is None
    assert w.wallet.balance == balance
    assert kinds(w, "treasury.hosting_unread") == ["treasury.hosting_unread"]
    pots = w.wallet.pots()
    assert pots["hosting"] is None and not pots["complete"] and pots["total_micro"] is None


def test_the_pot_is_in_the_books_the_reconciliation_compares():
    from factorylab.world.treasury import Treasury

    ledger = Ledger(clock_ns=lambda: 0)
    # Venue and reserve hold still; the launch balance is backed by every pot, hosting too.
    rail = SimpleNamespace(name="still", balances=lambda: {"venue": 300_000_000,
                                                           "reserve": 100_000_000})
    wallet = Wallet(500_000_000, ledger, clock_ns=lambda: 0)
    treasury = Treasury(ledger, wallet, rail)
    fake = FakeDigitalOcean(credit="100.00")
    treasury.hosting = HostingAccount(DigitalOceanClient(http=fake), droplet_id=fake.droplet_id,
                                      max_monthly_micro=0)
    treasury.observe_hosting()
    pots = treasury.refresh_pots()
    assert pots["complete"] and pots["total_micro"] == 500_000_000
    snapshot = Reconciler.snapshot(wallet.balance, None, None, pots_view=pots)
    assert snapshot["pots_micro"] == 500_000_000 and snapshot["discrepancy_micro"] == 0
    # A burn moves the wallet and the pot by the same amount: the books still agree.
    fake.bill("2.50")
    treasury.observe_hosting()
    after = Reconciler.snapshot(wallet.balance, None, None, pots_view=treasury.refresh_pots())
    assert after["pots_micro"] == 497_500_000 and after["discrepancy_micro"] == 0
    # Unread, the pot leaves the total unknown rather than short.
    fake.down = True
    treasury.observe_hosting()
    assert Reconciler.snapshot(wallet.balance, None, None,
                               pots_view=treasury.refresh_pots())["pots_micro"] is None


def test_no_seat_pays_the_burn_the_pool_absorbs_it():
    w = world()
    book = BudgetBook(w.wallet, w.ledger, clock_ns=lambda: 0)
    book.grant("seat-a", 100_000_000, "test")
    w.treasury.observe_hosting()
    pool = book.unallocated()
    w.fake.bill("3.00")
    w.treasury.observe_hosting()
    assert book.entitlement("seat-a") == 100_000_000
    assert book.unallocated() == pool - 3_000_000 and book.check_invariant()


def test_the_pot_survives_a_checkpoint():
    w = world()
    w.treasury.observe_hosting()
    w.fake.bill("0.10")
    w.treasury.observe_hosting()
    saved = w.treasury.snapshot()
    twin = world()
    twin.treasury.restore(deepcopy(saved))
    assert twin.hosting.state() == w.hosting.state()
    twin.fake.usage = w.fake.usage
    twin.fake.generated = w.fake.generated
    twin.fake.bill("0.05")
    assert twin.treasury.observe_hosting()["micro"] == 50_000  # measured from the saved read


# --- the resize surface --------------------------------------------------------------------

def resize(w, size, *, disk=False, client_id="h:tool:0"):
    return w.hosting.resize(w.ledger, client_id=client_id, handle="h", size=size, disk=disk)


@pytest.mark.parametrize(("size", "disk", "reason"), [
    ("s-4vcpu-8gb", False, "exceeds [hosting] max_monthly_usd"),
    ("s-3vcpu-7gb", False, "not on DigitalOcean's published size list"),
    ("s-2vcpu-4gb", True, "disk resize is not allowed"),
    ("g-2vcpu-8gb", False, "not available in the droplet's region"),
    ("s-1vcpu-2gb", False, "already that size"),
])
def test_a_resize_is_refused_before_any_intent_and_nothing_is_sent(size, disk, reason):
    w = world(cap="100" if size == "g-2vcpu-8gb" else "24")
    result = resize(w, size, disk=disk)
    assert result["status"] == "refused" and reason in result["error"]
    assert w.fake.posts == [] and w.hosting.intents == {}
    assert kinds(w, "hosting.") == ["hosting.refused"]


def test_disk_growth_is_sent_only_when_the_manifest_allows_it():
    w = world(disk=True)
    assert resize(w, "s-2vcpu-4gb", disk=True)["status"] == "in-progress"
    assert w.fake.posts == [{"type": "resize", "size": "s-2vcpu-4gb", "disk": True}]
    plain = world()
    resize(plain, "s-2vcpu-4gb")
    assert plain.fake.posts == [{"type": "resize", "size": "s-2vcpu-4gb", "disk": False}]


def test_the_intent_is_journaled_before_the_call_and_the_resize_followed_by_reading():
    w = world()
    seen = []
    w.fake.observer = lambda method, url: seen.append(
        (method, [r["kind"] for r in w.records if r["kind"].startswith("hosting.")]))
    result = resize(w, "s-2vcpu-4gb")
    post = next(kinds_then for method, kinds_then in seen if method == "POST")
    assert post == ["hosting.intent"]  # durable before DigitalOcean was asked
    assert result["status"] == "in-progress" and result["price_monthly_micro"] == 24_000_000
    intent = next(r for r in w.records if r["kind"] == "hosting.intent")
    assert (intent["from_size"], intent["size"], intent["disk"]) == (
        "s-1vcpu-2gb", "s-2vcpu-4gb", False)
    assert resize(w, "s-3vcpu-7gb", client_id="other:tool:0")["error"] == (
        "a resize of this droplet is still open")
    w.hosting.tick(w.ledger)
    assert w.hosting.intents["h:tool:0"]["status"] == "in-progress"
    w.fake.finish(result["action_id"])
    w.hosting.tick(w.ledger)
    assert w.hosting.intents["h:tool:0"]["status"] == "completed"
    assert kinds(w, "hosting.") == ["hosting.intent", "hosting.acknowledged", "hosting.refused",
                                    "hosting.resized"]
    assert len(w.fake.posts) == 1
    # The price consequence is billing, never a debit computed from the list.
    assert not any(r["kind"] == "wallet.settle" for r in w.records)


def test_a_lost_answer_is_recovered_from_the_droplet_and_never_resent():
    w = world()
    w.fake.lose_post_answer = True
    result = resize(w, "s-2vcpu-4gb")
    assert result["status"] == "uncertain" and len(w.fake.posts) == 1
    assert resize(w, "s-2vcpu-4gb")["status"] == "uncertain"  # the same identity: no resend
    assert len(w.fake.posts) == 1
    w.hosting.tick(w.ledger)  # the droplet is locked while it resizes: no poll spent
    assert w.hosting.intents["h:tool:0"]["polls"] == 0
    w.fake.finish(next(iter(w.fake.actions)))
    w.hosting.tick(w.ledger)
    assert w.hosting.intents["h:tool:0"]["status"] == "completed"
    assert len(w.fake.posts) == 1
    assert kinds(w, "hosting.") == ["hosting.intent", "hosting.uncertain", "hosting.resized"]


def test_an_unanswered_resize_that_never_happened_is_released_not_resent():
    w = world()

    def refuse_posts(method, url):
        if method == "POST":  # the POST never reached DigitalOcean
            raise ConnectionRefusedError("connection refused")

    w.fake.observer = refuse_posts
    assert resize(w, "s-2vcpu-4gb")["status"] == "uncertain"
    w.fake.observer = None
    for _ in range(UNCERTAIN_POLLS):
        w.hosting.tick(w.ledger)
    assert w.hosting.intents["h:tool:0"]["status"] == "unresolved"
    assert w.fake.posts == []
    assert kinds(w, "hosting.")[-1] == "hosting.unresolved"


def test_a_crash_between_the_call_and_its_answer_replays_as_uncertain_never_resent():
    """The journal holds ``io.call hosting.resize`` and no result: DigitalOcean powered the
    droplet down, and the process with it. The replay completes the call as uncertain
    without calling DigitalOcean, and the intent owner resolves it by reading."""
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = journal.recovering = True
    args = (599960972, "s-2vcpu-4gb", False)
    fingerprint = hashlib.sha256(canonical(encode((args, {})))).hexdigest()
    journal.tail = [{"kind": "io.call", "name": "hosting.resize", "input_hash": fingerprint,
                     "seq": 0, "ts": 0}]
    result = journal.call("hosting.resize", lambda *a: pytest.fail("resize sent twice"),
                          args, {})
    assert result == {"status": "uncertain"}
    assert recorded == [{"kind": "io.result", "call": 0,
                         "result": encode({"status": "uncertain"})}]
    # The reads the owner resolves it with are read-only, so they replay or run freely.
    from factorylab.runtime.resume import _read_only

    assert all(_read_only(f"hosting.{name}") for name in ("balance", "droplet", "sizes",
                                                           "action"))
    assert not _read_only("hosting.resize")


def test_the_token_never_reaches_the_ledger():
    w = world()
    w.treasury.observe_hosting()
    w.fake.bill("1.00")
    w.treasury.observe_hosting()
    w.fake.lose_post_answer = True
    resize(w, "s-2vcpu-4gb")
    w.fake.down = True
    w.treasury.observe_hosting()
    resize(w, "s-2vcpu-2gb", client_id="x:tool:0")
    # Every item the ledger was given, errors and refusals included.
    assert any(r["kind"] == "treasury.hosting_unread" for r in w.records)
    assert TOKEN not in str(w.records)
    assert TOKEN not in str(w.treasury.snapshot())
