"""The bounded memory contract: an inbox that indexes, and a head that is not pasted.

Two things a seat's request used to carry whole now arrive as references. Eight
outcome bodies became eight index entries, and a large working state became its
own sha and size. The point of these tests is that bounding the *display* lost
no *fact*: every body is still exact behind ``outcome.get``, every head is still
exact behind ``artifact.get``, a later item stays reachable without
acknowledging an earlier one, and no seat can read another's queue.

Nothing here reaches a network, a credential, a model or a venue.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import ArtifactStore
from factorylab.runtime.continuity import (
    INLINE_OUTCOMES,
    INLINE_STATE_BYTES,
    MAX_INDEX_FIELD,
    MAX_LIST_LIMIT,
    OUTCOME_UNKNOWN,
    OutcomeInbox,
    WorkingState,
)


class Evidence:
    """A ledger double: the shape the other continuity tests use."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row):
        self.rows.append(dict(row))
        return len(self.rows)


@pytest.fixture
def archive():
    ledger = Evidence()
    clock = SimpleNamespace(ns=0)
    store = ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)
    return ledger, clock, store


@pytest.fixture
def inbox(archive):
    ledger, clock, store = archive
    return OutcomeInbox(store, ledger, lambda: clock.ns)


def fill(inbox, seat="alice", count=12, said=True):
    """``count`` settled consequences addressed to one seat, each with a long body."""
    records = []
    for i in range(count):
        handle = f"decision-{i}"
        if said:
            inbox.record_said(seat, handle, {"rationale": "R" * 900, "payoff": 0.5,
                                             "forecasts": []})
        records.append(inbox.append(
            seat, handle=handle,
            outcome={"kind": "verdict", "status": "settled", "score": i / 100,
                     "reason": "W" * 900},
            delta_micro=-i, evidence=f"diary:{i}", observed_at_ns=1_000 + i))
    return records


# ---- the inline window is an index, and the index is exact ------------------------------


def test_unread_window_indexes_and_carries_no_body_text(inbox):
    fill(inbox)
    shown = inbox.unread("alice")
    assert shown["count"] == 12
    assert len(shown["items"]) == INLINE_OUTCOMES and shown["more"] == 4
    assert [item["outcome_id"] for item in shown["items"]] == [
        "outcome:1", "outcome:2", "outcome:3", "outcome:4",
        "outcome:9", "outcome:10", "outcome:11", "outcome:12",
    ]
    assert shown["next_after"] == 4
    assert shown["paging"]["args"] == {"after": 4, "limit": INLINE_OUTCOMES}
    assert all("preview" not in item for item in shown["items"][:4])
    assert all(item["preview"] is True for item in shown["items"][4:])
    assert "outcome.get" in shown["preview_semantics"]
    blob = json.dumps(shown)
    assert "R" * 900 not in blob and "W" * 900 not in blob
    assert "said" not in shown["items"][0] and "outcome" not in shown["items"][0]
    # Oldest first, addressable, and every retained field exact.
    first = shown["items"][0]
    assert first["outcome_id"] == "outcome:1"
    assert first["handle"] == "decision-0"
    assert first["observed_at_ns"] == 1_000
    assert first["kind"] == "verdict" and first["status"] == "settled"
    assert first["score"] == 0.0 and first["delta_micro"] == 0
    assert first["evidence"] == "diary:0"
    assert first["read"] is False
    assert first["read_with"] == {"tool": "outcome.get",
                                  "args": {"outcome_id": "outcome:1"}}
    third = shown["items"][2]
    assert third["score"] == 0.02 and third["delta_micro"] == -2
    # The sha and size name bytes that are really there and really that long.
    assert inbox.artifacts.get(first["sha"]) is not None
    assert first["bytes"] == len(inbox.artifacts.get(first["sha"]))
    assert first["bytes"] > len(json.dumps(first))


def test_unread_at_the_inline_bound_keeps_the_whole_oldest_window(inbox):
    fill(inbox, count=INLINE_OUTCOMES)
    shown = inbox.unread("alice")
    assert [item["outcome_id"] for item in shown["items"]] == [
        f"outcome:{seq}" for seq in range(1, INLINE_OUTCOMES + 1)
    ]
    assert shown["more"] == 0 and shown["next_after"] is None
    assert shown["paging"]["args"] == {
        "after": INLINE_OUTCOMES, "limit": INLINE_OUTCOMES,
    }
    assert inbox.delivered_through["alice"] == INLINE_OUTCOMES
    assert "alice" not in inbox.delivered_sparse
    assert "preview_semantics" not in shown


def test_an_index_carries_no_sender_field(inbox):
    # R11: there are no messages between seats, so an index names no sender even
    # when an outcome body carries a ``from`` key.
    inbox.append("bob", handle="h1", evidence="slot:0",
                 outcome={"kind": "finding", "from": "alice", "subject": "the lot table"})
    entry = inbox.unread("bob")["items"][0]
    assert entry["subject"] == "the lot table"
    assert "from" not in entry


# ---- a typed field cannot become a body under another name -----------------------------


def test_an_oversized_or_nested_typed_field_is_named_and_never_inlined(inbox):
    subject = "S" * 10_000
    status = {"code": "contested", "detail": ["D" * 4_000], "open": True}
    inbox.append("bob", handle="h1", evidence="diary:1", delta_micro=7,
                 outcome={"kind": "verdict", "subject": subject, "status": status,
                          "phase": "final", "score": 0.25})
    entry = inbox.unread("bob")["items"][0]
    # The short scalars still ride, with their types intact.
    assert entry["kind"] == "verdict" and entry["phase"] == "final"
    assert entry["score"] == 0.25 and isinstance(entry["score"], float)
    assert entry["delta_micro"] == 7 and isinstance(entry["delta_micro"], int)
    # The two bodies-in-disguise do not, and say so with shape and exact size.
    assert "subject" not in entry and "status" not in entry
    assert entry["subject_not_loaded"] == {"shape": "str", "bytes": 10_000}
    assert entry["status_not_loaded"]["shape"] == "dict"
    assert entry["status_not_loaded"]["items"] == 3
    assert entry["status_not_loaded"]["bytes"] > 4_000
    # Neither payload reached the request, at any size, in any field.
    blob = json.dumps(entry)
    assert "S" * 300 not in blob and "D" * 300 not in blob
    assert len(blob) < 1_000
    # Both are exact behind the route the entry itself names.
    assert entry["read_with"] == {"tool": "outcome.get",
                                  "args": {"outcome_id": entry["outcome_id"]}}
    whole = inbox.get("bob", entry["outcome_id"])["outcome"]
    assert whole["subject"] == subject and whole["status"] == status


def test_latest_rejection_is_visible_under_the_same_privacy_bound(inbox):
    private = "PRIVATE" * 2_000
    fill(inbox, seat="bob", count=8, said=False)
    inbox.append("bob", handle="h1", outcome={
        "kind": "rejected",
        "rejection_reason": "missing depth",
        "rejected_section": "tool_calls[0]",
    })
    inbox.append("bob", handle="h2", outcome={
        "kind": "rejected",
        "rejection_reason": private,
        "rejected_section": {"name": private},
    })

    shown = inbox.unread("bob")
    by_id = {item["outcome_id"]: item for item in shown["items"]}
    assert list(by_id) == [
        "outcome:1", "outcome:2", "outcome:3", "outcome:4",
        "outcome:7", "outcome:8", "outcome:9", "outcome:10",
    ]
    exact, bounded = by_id["outcome:9"], by_id["outcome:10"]
    assert exact["preview"] is True and bounded["preview"] is True
    assert exact["rejection_reason"] == "missing depth"
    assert exact["rejected_section"] == "tool_calls[0]"
    assert "rejection_reason" not in bounded and "rejected_section" not in bounded
    assert bounded["rejection_reason_not_loaded"] == {
        "shape": "str", "bytes": len(private.encode("utf-8")),
    }
    assert bounded["rejected_section_not_loaded"]["shape"] == "dict"
    assert private not in json.dumps(bounded)


def test_the_field_bound_is_utf8_bytes_and_a_field_at_it_still_rides(inbox):
    at_bound = "s" * MAX_INDEX_FIELD
    over_by_one_character = "é" * ((MAX_INDEX_FIELD // 2) + 1)
    inbox.append("bob", handle="h1", outcome={"subject": at_bound})
    inbox.append("bob", handle="h2", outcome={"subject": over_by_one_character})
    first, second = inbox.unread("bob")["items"]
    assert first["subject"] == at_bound
    assert "subject" not in second
    assert second["subject_not_loaded"] == {"shape": "str",
                                            "bytes": MAX_INDEX_FIELD + 2}
    assert inbox.get("bob", "outcome:2")["outcome"]["subject"] == over_by_one_character


def test_a_nested_evidence_pointer_is_named_not_carried(inbox):
    inbox.append("bob", handle="h1", outcome={"kind": "fill"},
                 evidence={"diary": ["e" * 2_000]})
    entry = inbox.unread("bob")["items"][0]
    assert "evidence" not in entry
    assert entry["evidence_not_loaded"]["shape"] == "dict"
    assert entry["evidence_not_loaded"]["bytes"] > 2_000
    assert "e" * 300 not in json.dumps(entry)
    assert inbox.get("bob", "outcome:1")["evidence"] == {"diary": ["e" * 2_000]}


def test_a_whole_page_of_hostile_bodies_stays_small(inbox):
    for i in range(MAX_LIST_LIMIT):
        inbox.append("bob", handle=f"h{i}", evidence=["E" * 5_000],
                     outcome={"kind": "verdict", "status": {"why": "W" * 5_000},
                              "subject": "S" * 5_000, "score": i})
    page = inbox.list("bob", limit=MAX_LIST_LIMIT)
    blob = json.dumps(page)
    assert page["count"] == MAX_LIST_LIMIT
    assert "S" * 300 not in blob and "W" * 300 not in blob and "E" * 300 not in blob
    # Every entry is an address of fixed shape whatever the body weighs: the whole
    # page costs under 600 bytes an item against 15 KiB of body each, and under a
    # twentieth of what the bodies it names would have cost inline.
    assert all(item["bytes"] > 15_000 for item in page["items"])
    assert len(blob) < 600 * MAX_LIST_LIMIT
    assert len(blob) * 20 < sum(item["bytes"] for item in page["items"])


def test_get_still_returns_the_body_exactly_as_it_was_stored(inbox):
    fill(inbox, count=3)
    view = inbox.get("alice", "outcome:2")
    assert view["handle"] == "decision-1"
    assert view["outcome"] == {"kind": "verdict", "status": "settled",
                               "score": 0.01, "reason": "W" * 900}
    assert view["said"]["rationale"] == "R" * 900
    assert view["delta_micro"] == -1 and view["evidence"] == "diary:1"
    assert view["observed_at_ns"] == 1_001


# ---- paging past the window without acknowledging what is behind it ---------------------


def test_later_ids_are_discoverable_without_acknowledging_earlier_ones(inbox):
    fill(inbox, count=20)
    shown = inbox.unread("alice")
    assert shown["next_after"] == 4
    page = inbox.list("alice", after=shown["next_after"], limit=8)
    assert [i["outcome_id"] for i in page["items"]] == [
        f"outcome:{n}" for n in range(5, 13)]
    assert page["count"] == 8 and page["more"] == 8 and page["next_after"] == 12
    assert page["unread"] == 20
    last = inbox.list("alice", after=page["next_after"], limit=8)
    assert [i["outcome_id"] for i in last["items"]] == [
        f"outcome:{n}" for n in range(13, 21)]
    assert last["more"] == 0 and last["next_after"] is None
    # Listing delivered nothing, so acknowledging a listed id cannot reach past
    # the oldest four the seat was actually shown contiguously: items 5 to 20
    # stay unread even though the newest four were visible.
    assert inbox.ack_through("alice", "outcome:20") == INLINE_OUTCOMES // 2
    assert inbox.unread("alice")["count"] == 16
    assert inbox.get("alice", "outcome:20")["handle"] == "decision-19"


def test_sparse_fetch_cannot_acknowledge_the_gap_and_survives_restore(inbox):
    fill(inbox, count=20)
    inbox.unread("alice")
    assert inbox.list("alice", after=19, limit=1)["items"][0]["outcome_id"] == "outcome:20"
    assert inbox.get("alice", "outcome:20")["handle"] == "decision-19"
    assert inbox.delivered_through["alice"] == 4
    assert inbox.delivered_sparse["alice"] == {20}

    saved = inbox.delivery_state()
    inbox.get("alice", "outcome:20")  # replaying the same delivery is idempotent
    assert inbox.delivery_state() == saved
    inbox.delivered_through.clear()
    inbox.delivered_sparse.clear()
    inbox.restore_delivery(saved)
    assert inbox.ack_through("alice", "outcome:20") == 4
    assert inbox.unread("alice")["count"] == 16

    # Each window advances only its four-item oldest prefix. Previewed latest
    # items are not deliveries; the explicitly fetched twentieth stays sparse
    # until every middle item has actually been shown.
    assert inbox.ack_through("alice", "outcome:20") == 8
    assert inbox.unread("alice")["count"] == 12
    assert inbox.ack_through("alice", "outcome:20") == 12
    assert inbox.unread("alice")["count"] == 8
    assert inbox.ack_through("alice", "outcome:20") == 20
    assert inbox.unread("alice")["count"] == 0


def test_repeated_latest_previews_do_not_grow_sparse_delivery_state(inbox):
    fill(inbox, count=20)
    shown = inbox.unread("alice")
    assert inbox.delivered_through["alice"] == 4
    assert "alice" not in inbox.delivered_sparse

    for seq in range(21, 61):
        inbox.append("alice", handle=f"decision-{seq}",
                     outcome={"kind": "rejected", "rejection_reason": "latest"})
        shown = inbox.unread("alice")

    latest = shown["items"][-1]
    assert latest["outcome_id"] == "outcome:60" and latest["preview"] is True
    assert inbox.delivered_through["alice"] == 4
    assert "alice" not in inbox.delivered_sparse

    inbox.get("alice", latest["outcome_id"])
    assert inbox.delivered_sparse["alice"] == {60}
    saved = inbox.delivery_state()
    inbox.delivered_through.clear()
    inbox.delivered_sparse.clear()
    inbox.restore_delivery(saved)
    assert inbox.delivered_through["alice"] == 4
    assert inbox.delivered_sparse["alice"] == {60}
    assert inbox.ack_through("alice", latest["outcome_id"]) == 4


def test_delivery_prefix_uses_the_seats_order_not_adjacent_global_ids(inbox):
    inbox.append("alice", handle="a1", outcome={"fact": 1})
    inbox.append("carol", handle="c1", outcome={"fact": 2})
    inbox.append("alice", handle="a2", outcome={"fact": 3})

    inbox.get("alice", "outcome:1")
    inbox.get("alice", "outcome:3")
    assert inbox.delivered_through["alice"] == 3
    assert inbox.ack_through("alice", "outcome:3") == 3


def test_listing_acknowledges_nothing_and_loses_nothing(inbox):
    fill(inbox, count=20)
    before = inbox.cursors.get("alice", 0), dict(inbox.delivered_through)
    inbox.list("alice", after=0, limit=20)
    assert (inbox.cursors.get("alice", 0), dict(inbox.delivered_through)) == before
    assert inbox.unread("alice")["count"] == 20


def test_paging_bounds_are_clamped_and_never_dump(inbox):
    fill(inbox, count=50)
    assert inbox.list("alice", limit=1_000)["count"] == MAX_LIST_LIMIT
    assert inbox.list("alice", limit=0)["count"] == 1
    assert inbox.list("alice", after=-5)["items"][0]["outcome_id"] == "outcome:1"
    assert inbox.list("alice", after="nonsense", limit="nonsense")["count"] == (
        INLINE_OUTCOMES)
    assert "W" * 900 not in json.dumps(inbox.list("alice", limit=MAX_LIST_LIMIT))


def test_a_page_skips_what_was_acknowledged_and_never_shows_another_seat(inbox):
    fill(inbox, count=12)
    fill(inbox, seat="carol", count=2, said=False)
    inbox.unread("alice")
    inbox.ack_through("alice", "outcome:4")
    page = inbox.list("alice", after=0, limit=32)
    assert [i["outcome_id"] for i in page["items"]] == [
        f"outcome:{n}" for n in range(5, 13)]
    # Carol's items are seqs 13 and 14 and belong to her alone.
    assert inbox.list("bob")["items"] == [] and inbox.list("bob")["unread"] == 0
    assert [i["outcome_id"] for i in inbox.list("carol")["items"]] == [
        "outcome:13", "outcome:14"]
    assert inbox.get("alice", "outcome:13") == {"error": OUTCOME_UNKNOWN}
    assert inbox.get("bob", "outcome:1") == {"error": OUTCOME_UNKNOWN}


# ---- the working state display bound ----------------------------------------------------


@pytest.fixture
def state(archive):
    ledger, clock, store = archive
    return WorkingState(store, ledger, lambda: clock.ns)


def test_small_state_is_still_rendered_inline_verbatim(state):
    written = {"plan": "buy the dip", "n": 3}
    state.put("alice", written, handle="h1")
    view = state.render("alice")
    assert view["loaded"] is True and view["state"] == written
    assert view["bytes"] < INLINE_STATE_BYTES


def test_large_state_is_named_not_pasted_and_is_read_back_exactly(state):
    written = {"notes": ["n" * 200 for _ in range(40)], "n": 3}
    record = state.put("alice", written, handle="h1")
    view = state.render("alice")
    assert view["bytes"] > INLINE_STATE_BYTES
    assert view["loaded"] is False and "state" not in view
    assert view["sha"] == record["sha"]
    assert view["read_with"] == {"tool": "artifact.get", "args": {"sha": record["sha"]}}
    assert str(INLINE_STATE_BYTES) in view["state_not_loaded"]
    assert "n" * 200 not in json.dumps(view)
    # Nothing was summarised and nothing was dropped: the bytes are the bytes.
    assert json.loads(state.artifacts.get(view["sha"]).decode("utf-8")) == written
    # The head, the soft allowance and the rent accrual are untouched by display.
    assert state.head("alice")["bytes"] == view["bytes"]
    assert [r["kind"] for r in state.ledger.rows] == ["artifact.put", "state.put"]
    assert state.ledger.rows[-1]["over_soft"] is False


def test_a_head_whose_bytes_are_gone_is_unavailable_at_any_size(state):
    for payload in ({"small": 1}, {"big": ["x" * 200 for _ in range(40)]}):
        state.put("alice", payload, handle="h1")
        sha = state.head("alice")["sha"]
        state.artifacts._memory.pop(sha, None)
        state.artifacts.index.pop(sha, None)
        with pytest.raises(RuntimeError, match="present but unavailable"):
            state.render("alice")


# ---- retention: acknowledged, or past the published horizon (wave 17b) -------------------


def test_an_item_is_kept_until_acknowledged_or_past_its_retention_horizon(archive):
    """Essay II.IV.c: a verdict is "consumed ... and then discarded". An unacknowledged
    item addressed at or after the horizon stays; an acknowledged one, or one older than
    the horizon, leaves the inbox and its body is released from the archive."""
    ledger, clock, store = archive
    ticks = SimpleNamespace(now=0)
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.tick = lambda: ticks.now
    records = []
    for tick in range(6):
        ticks.now = tick
        records.append(inbox.append("alice", handle=f"decision-{tick}",
                                    outcome={"kind": "verdict", "score": tick / 10},
                                    evidence=f"diary:{tick}", observed_at_ns=1_000 + tick))
    inbox.unread("alice")
    inbox.ack_through("alice", "outcome:2")  # delivered, then acknowledged
    assert inbox.release_items(before_tick=4) == 4  # 1, 2 acknowledged; 3, 4 expired
    assert [r["seq"] for r in inbox.items["alice"]] == [5, 6]
    released = [row for row in ledger.rows if row["kind"] == "artifact.released"]
    assert {r["sha"] for r in released} == {r["sha"] for r in records[:4]}
    assert all(r["cause"] == "retention" and r["artifact_kind"] == "outcome.item"
               for r in released)
    # An id no longer held answers as one never addressed does, and says why it may be.
    assert inbox.get("alice", "outcome:3") == {"error": OUTCOME_UNKNOWN}
    assert "retention horizon" in OUTCOME_UNKNOWN
    assert inbox.unread("alice")["count"] == 2
    assert inbox.release_items(before_tick=4) == 0  # nothing more is due


def test_a_body_another_held_item_carries_is_not_released(archive):
    ledger, clock, store = archive
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.tick = lambda: 0
    first = inbox.append("alice", handle="h", outcome={"kind": "fill"}, observed_at_ns=1)
    inbox.tick = lambda: 9
    second = inbox.append("alice", handle="h", outcome={"kind": "fill"}, observed_at_ns=1)
    assert first["sha"] == second["sha"] and first["seq"] != second["seq"]
    assert inbox.release_items(before_tick=5) == 1
    assert not [r for r in ledger.rows if r["kind"] == "artifact.released"]
    assert inbox.get("alice", f"outcome:{second['seq']}")["outcome"] == {"kind": "fill"}
