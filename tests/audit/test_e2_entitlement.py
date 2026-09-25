"""Edition 2, C10: per-seat entitlement is a classification of one wallet.

Over a scripted run, the entitlements plus the unallocated pool equal the unlocked
wallet less its holds at the end, every commit splits exactly into own and commons
money, and every hold is settled or released exactly once.
"""

from collections import Counter

import pytest

from factorylab.kernel.budget import unlocked_balance
from factorylab.runtime.worlds import load_manifest

EVENTS = 100


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


@pytest.fixture(scope="module")
def world(scripted_runtime_run):
    manifest = load_manifest("scripted")
    record = scripted_runtime_run(manifest, EVENTS, 1)
    return record.runtime(manifest), record.entries


def budget(entries, op):
    return [e for e in entries if e["kind"] == "budget" and e["op"] == op]


def test_the_invariant_holds_at_every_ledgered_movement_and_at_the_end(world):
    rt, entries = world
    assert invariant(rt)
    commits = budget(entries, "commit")
    assert len(commits) > 100  # the run moved money many times
    for item in commits:
        assert item["amount"] == item["own"] + item["commons"]
    # each hold is settled or released exactly once, and nothing is held at the end
    holds = Counter(item["reservation_id"] for item in budget(entries, "hold"))
    settled = Counter(item["reservation_id"]
                      for op in ("commit", "release_hold") for item in budget(entries, op))
    assert holds == settled and rt.budget.holds() == 0
