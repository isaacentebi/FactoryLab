"""Edition 3, R3-F: the acceptance for attention and continuity, completed.

The plan's acceptance for this workstream, verbatim (``docs/plans/edition3-r3.md``,
"R3-F Attention and continuity, completed", plus the three the task added):

* a refusal lands in the deciding seat's inbox with an id;
* an invocation failure leaves the fold offered and the next wake sees it;
* a second writer reads its own artifact;
* ``ack_through`` an old id leaves newer items unread;
* a declined commission is not malformed;
* a fill reaches the ordering seat's inbox;
* garbage collection never removes an owned or public blob.

Nothing here reaches a network, a credential or a venue beyond the fake.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import PRIVATE_REFUSAL, ArtifactStore
from factorylab.runtime.continuity import INLINE_OUTCOMES, OUTCOME_UNKNOWN, OutcomeInbox
from factorylab.runtime.propensity import DECLINED, MALFORMED, action_class, action_label
from factorylab.runtime.subscriptions import Subscription, SubscriptionBook
from factorylab.runtime.venue import VenueMixin


class Evidence:
    """A ledger double; the same shape the third-reading regressions use."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row):
        self.rows.append(dict(row))
        return len(self.rows)

    def kinds(self, kind: str) -> list[dict]:
        return [row for row in self.rows if row.get("kind") == kind]


@pytest.fixture
def archive():
    ledger = Evidence()
    clock = SimpleNamespace(ns=0)
    return ledger, clock, ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)


@pytest.fixture
def inbox(archive):
    ledger, clock, store = archive
    return OutcomeInbox(store, ledger, lambda: clock.ns)


# ---- a refusal lands in the deciding seat's inbox with an id ----------------------------

def test_a_refusal_lands_in_the_deciding_seats_inbox_with_an_id(inbox):
    """The refusal path (kept from the third-reading patch) now produces an addressed
    item with an exact ``outcome_id``, which is what ``outcome.get`` takes."""
    ledger = Evidence()
    rt = SimpleNamespace(ledger=ledger, registration_feedback=[],
                         clock=SimpleNamespace(now_ns=7), outcomes=inbox,
                         handle_to_assembly={"d1": "alice"})
    inbox.record_said("alice", "d1", {"rationale": "probe the collateral rule"})

    result = VenueMixin._refuse_order(rt, "d1", "insufficient collateral")

    assert result["status"] == "rejected"
    unread = inbox.unread("alice")
    assert unread["count"] == 1 and unread["more"] == 0
    item = unread["items"][0]
    assert item["outcome_id"] == "outcome:1"
    assert item["outcome"]["kind"] == "order_refused"
    assert item["outcome"]["reason"] == "insufficient collateral"
    # The seat can fetch it again by that id, and it carries what the seat said then.
    fetched = inbox.get("alice", "outcome:1")
    assert fetched["said"]["rationale"] == "probe the collateral rule"
    # A refusal is not something the seat has to guess about: it did not vanish, and
    # one seat's refusal is not another's.
    assert inbox.get("bob", "outcome:1") == {"error": OUTCOME_UNKNOWN}
    assert ledger.kinds("order.refused")


# ---- an invocation failure leaves the fold offered --------------------------------------

def _print(book, seat, coin, mid, *, tick):
    book.observe([seat], "MarketMid", {"coin": coin, "mid": mid}, tick * 1_000_000_000,
                 now=tick)


def test_an_invocation_failure_leaves_the_fold_offered_and_the_next_wake_sees_it():
    book = SubscriptionBook()
    _print(book, "alice", "BTC", "100", tick=1)
    _print(book, "alice", "BTC", "120", tick=2)
    assert book.fold_state("alice") == "offered"

    delivered = book.take("alice", now=2)
    assert book.fold_state("alice") == "delivered"
    assert delivered["coins"]["BTC"] == {"first": "100", "last": "120", "high": "120",
                                         "low": "100", "prints": 2,
                                         "first_t_s": 1, "last_t_s": 2}

    # The request failed: nothing was shown, so nothing was read.
    assert book.return_to_offered("alice") is True
    assert book.fold_state("alice") == "offered"
    _print(book, "alice", "BTC", "90", tick=3)

    next_wake = book.take("alice", now=3)
    assert next_wake["from_tick"] == 1
    assert next_wake["coins"]["BTC"] == {"first": "100", "last": "90", "high": "120",
                                         "low": "90", "prints": 3,
                                         "first_t_s": 1, "last_t_s": 3}
    # And an ok answer ends it: the world that was delivered is now read.
    assert book.acknowledge("alice") is True
    assert book.fold_state("alice") == "acknowledged"
    assert book.take("alice", now=4)["prints"] == 0


def test_the_three_fold_states_survive_a_checkpoint():
    book = SubscriptionBook()
    _print(book, "alice", "BTC", "100", tick=1)
    book.take("alice", now=1)
    _print(book, "alice", "BTC", "150", tick=2)

    restored = SubscriptionBook()
    restored.restore(book.state())
    assert restored.fold_state("alice") == "delivered"
    restored.return_to_offered("alice")
    assert restored.take("alice", now=2)["coins"]["BTC"]["first"] == "100"


def test_a_coin_filter_applies_to_the_delivered_fold_not_only_to_admission():
    book = SubscriptionBook()
    book.set_subscription("alice", Subscription(coins=frozenset({"BTC"})))
    _print(book, "alice", "BTC", "100", tick=1)
    _print(book, "alice", "ETH", "3000", tick=1)
    book.observe(["alice"], "Funding", {"coin": "ETH", "rate": "0.01"}, 1, now=1)

    delivered = book.take("alice", now=1)
    assert list(delivered["coins"]) == ["BTC"] and delivered["funding"] == {}
    assert delivered["prints"] == 1


# ---- a second writer reads its own artifact ---------------------------------------------

def test_a_second_writer_of_identical_bytes_reads_its_own_artifact(archive):
    _, _, store = archive
    data = b"the same conclusion, reached twice"
    sha = store.put(data, owner="alice", kind="working.state")
    assert store.put(data, owner="bob", kind="note") == sha

    assert store.read(sha, reader="bob")["text"] == data.decode()
    assert store.read(sha, reader="alice")["text"] == data.decode()
    assert store.read(sha, reader="carol") == {"sha": sha, "error": PRIVATE_REFUSAL}
    # Each reference is its own: bob's kind is bob's, and the owner of record stands.
    assert store.read(sha, reader="bob")["kind"] == "note"
    assert store.read(sha, reader="alice")["kind"] == "working.state"
    assert store.owner_for(sha) == "alice"
    # And the listing shows owner and public per reference.
    rows = {owner: public for _sha, owner, public, _b, _t in store.entries()}
    assert rows == {"alice": False, "bob": False}
    assert [row["sha"] for row in store.list(owner="bob")] == [sha]


def test_publishing_an_existing_sha_publishes_the_blob(archive):
    _, _, store = archive
    sha = store.put(b"a shared finding", owner="alice", kind="working.state")
    assert store.read(sha, reader="stranger")["error"] == PRIVATE_REFUSAL
    store.put(b"a shared finding", owner="bob", kind="note", public=True)
    assert store.read(sha, reader="stranger")["text"] == "a shared finding"
    assert store.index[sha]["public"] is True


# ---- garbage collection never removes an owned or public blob ---------------------------

def test_collection_removes_only_unreferenced_unpublished_blobs(archive):
    ledger, _, store = archive
    owned = store.put(b"a seat's working state", owner="alice", kind="working.state")
    published = store.put(b"a note the population shares", owner="bob", kind="note",
                          public=True)
    # An orphan: durable bytes whose reference never landed (a crash between the two).
    orphan_bytes = b"bytes that outlived their failed record"
    orphan = hashlib.sha256(orphan_bytes).hexdigest()
    store._write(orphan, orphan_bytes)
    # A published record every reference released is still public, and still kept.
    store.index[published]["refs"] = {}
    store.index[published]["readers"] = []

    assert store.collect() == [orphan]

    assert store.get(owned) == b"a seat's working state"
    assert store.get(published) == b"a note the population shares"
    assert orphan not in store._memory and orphan not in store.index
    assert [row["sha"] for row in ledger.kinds("artifact.collected")] == [orphan]
    # Collection is idempotent and takes nothing on a second pass.
    assert store.collect() == []


# ---- ack_through an old id leaves newer items unread ------------------------------------

def test_ack_through_an_old_id_leaves_newer_items_unread(inbox):
    for i in range(5):
        inbox.append("alice", handle=f"d{i}", outcome={"n": i})
    shown = inbox.unread("alice")
    assert [row["outcome_id"] for row in shown["items"]] == [f"outcome:{n}"
                                                             for n in range(1, 6)]

    assert inbox.ack_through("alice", "outcome:2") == 2
    remaining = inbox.unread("alice")
    assert remaining["count"] == 3
    assert [row["outcome_id"] for row in remaining["items"]] == ["outcome:3", "outcome:4",
                                                                 "outcome:5"]
    assert inbox.get("alice", "outcome:5")["read"] is False
    # And the cursor never goes backwards.
    assert inbox.ack_through("alice", "outcome:1") == 2


def test_ack_through_cannot_acknowledge_an_item_the_seat_was_never_shown(inbox):
    for i in range(INLINE_OUTCOMES + 4):
        inbox.append("alice", handle="d1", outcome={"n": i}, evidence=f"fact-{i}")
    shown = inbox.unread("alice")
    assert len(shown["items"]) == INLINE_OUTCOMES
    assert shown["more"] == 4 and shown["count"] == INLINE_OUTCOMES + 4
    # The seat learned the later ids from ``related_outcomes`` without being shown them.
    last = f"outcome:{INLINE_OUTCOMES + 4}"
    assert last in inbox.get("alice", shown["items"][0]["outcome_id"])["related_outcomes"]

    # Only the eight it was shown are acknowledged; the four it never saw stay unread.
    assert inbox.ack_through("alice", last) == INLINE_OUTCOMES
    assert inbox.unread("alice")["count"] == 4


def test_a_handle_is_a_fallback_that_answers_with_the_oldest_unread_item(inbox):
    inbox.append("alice", handle="d1", outcome={"first": True}, evidence="a")
    inbox.append("alice", handle="d1", outcome={"second": True}, evidence="b")

    first = inbox.get("alice", "d1")
    assert first["outcome"] == {"first": True}
    assert "outcome_id" in first["note"]
    inbox.unread("alice")
    inbox.ack_through("alice", "outcome:1")
    assert inbox.get("alice", "d1")["outcome"] == {"second": True}


# ---- a declined commission is not malformed ---------------------------------------------

def test_a_declined_commission_is_not_malformed():
    """A judge or meta commission is paid work a seat may decline by answering ``cannot``.
    The label is its own arm, so a learner can learn that declining was right."""
    declined = {"status": "cannot", "reason": "no evidence window covers this return"}
    assert action_label("evaluator", declined, "refused") == DECLINED
    assert action_class(DECLINED, declined) == DECLINED
    # A genuinely unparseable return is still malformed, in either role.
    assert action_label("evaluator", {"verdict": "yes"}, "ok") == MALFORMED
    assert action_label("producer", {}, "failed") == MALFORMED
    # And ``cannot`` without a reason is not a decline; the runtime never marks it refused.
    assert action_label("evaluator", {"status": "cannot"}, "ok") == MALFORMED


# ---- a fill reaches the ordering seat's inbox -------------------------------------------

def test_a_fill_reaches_the_ordering_seat_not_whoever_was_awake(inbox):
    from factorylab.runtime.feedback import FeedbackMixin

    ledger = Evidence()
    rt = SimpleNamespace(
        ledger=ledger, clock=SimpleNamespace(now_ns=11), outcomes=inbox,
        consequences=SimpleNamespace(table=SimpleNamespace(
            orders=[SimpleNamespace(order_id="o-1", handle="d1")])),
        handle_to_assembly={"d1": "alice", "d2": "bob"})
    rt._undeliverable = lambda *a: FeedbackMixin._undeliverable(rt, *a)
    payload = {"order_id": "o-1", "coin": "BTC", "market": "perp", "is_buy": False,
               "size": "0.01", "px": "60000", "fee_usd": "0.02", "realized_usd": "1.50"}

    FeedbackMixin._address_fill_to_inbox(rt, payload)

    item = inbox.unread("alice")["items"][0]
    assert item["outcome"]["kind"] == "fill" and item["outcome"]["order_id"] == "o-1"
    assert item["outcome"]["realized_usd"] == "1.50"
    assert item["handle"] == "d1"
    assert inbox.unread("bob")["count"] == 0
    # The same fill observed twice is one consequence, not two.
    FeedbackMixin._address_fill_to_inbox(rt, payload)
    assert inbox.unread("alice")["count"] == 1
    # A fill no decision owns reaches nobody, and says so rather than vanishing.
    FeedbackMixin._address_fill_to_inbox(rt, {**payload, "order_id": "o-9"})
    assert ledger.kinds("outcome.undeliverable")[0]["consequence"] == "fill"
