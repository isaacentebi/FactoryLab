"""The eligibility tally equals the scan over three seeds (wave 17b), slow tier.

Contract: committee eligibility, kept as a running tally at settlement, equals on every
event the scan over every decision and account the world ever opened, released ones
included (regression caught: a settlement path the tally misses, which one seed's
short world does not reach). Each seed's world is run once, release live, with the
full scan beside it (``tally_against_scan``).
"""

import pytest

from tests.runtime.test_settled_release import _world, tally_against_scan

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_eligibility_tally_equals_the_scan_over_three_seeds(monkeypatch, seed):
    """500 world events on each of three seeds."""
    _summary, rows = tally_against_scan(monkeypatch, _world(500, seed=seed))
    for n, (tally, scan) in enumerate(rows, 1):
        assert tally == scan, (seed, n)
    assert any(tally for tally, _scan in rows)
