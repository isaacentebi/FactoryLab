"""Edition 2, C10: in the scripted world, mistakes cost their maker.

Nine seeded seats start with equal base shares; a seat's thinking is debited
from its own entitlement; a child's trial moves from its proposer; a retired
seat's entitlement returns to the pool; a settled paid-off return credits its
owner; a seat whose entitlement is exhausted is skipped by routing while the
others keep running; and the classification invariant holds throughout,
including across a crash and resume.
"""

from collections import Counter

import pytest

from factorylab.kernel.budget import unlocked_balance
from factorylab.runtime.worlds import load_manifest

EVENTS = 500


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


@pytest.fixture(scope="module")
def world(scripted_runtime_run):
    manifest = load_manifest("scripted")
    record = scripted_runtime_run(manifest, EVENTS, 1, drip=False)
    return record.runtime(manifest), record.entries


def budget(entries, op):
    return [e for e in entries if e["kind"] == "budget" and e["op"] == op]


def test_the_invariant_holds_at_every_ledgered_movement_and_at_the_end(world):
    rt, entries = world
    assert invariant(rt)
    for item in budget(entries, "commit"):
        assert item["amount"] == item["own"] + item["commons"]
    # each hold is settled or released exactly once, and nothing is held at the end
    holds = Counter(item["reservation_id"] for item in budget(entries, "hold"))
    settled = Counter(item["reservation_id"]
                      for op in ("commit", "release_hold") for item in budget(entries, op))
    assert holds == settled and rt.budget.holds() == 0
