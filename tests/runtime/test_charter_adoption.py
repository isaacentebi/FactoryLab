"""Whole-charter adoption cannot turn abstention or duplicated seats into approval."""
from types import SimpleNamespace

import pytest

from factorylab.charter.committee import Seat
from scripts.charter_session import adoption_vote, approved


@pytest.mark.parametrize('text,stop', [
    ('{"adopt": "yes", "reason": "ok"}', 'stop'),
    ('{"adopt": true, "reason": "ok"}', 'length'),
    ('{"adopt": true, "reason": ""}', 'stop'),
    ('{"selected": ["one"], "reason": "ok"}', 'stop'),
])
def test_invalid_ballot_cannot_approve(text, stop):
    with pytest.raises(ValueError):
        adoption_vote(SimpleNamespace(text=text, stop_reason=stop))


def test_rejection_stays_rejection():
    assert adoption_vote(SimpleNamespace(text='{"adopt": false, "reason": "disagree"}',
                                         stop_reason='stop')) == (False, 'disagree')


def test_majority_counts_all_drawn_seats_and_rejects_duplicates():
    seats = [Seat(str(i), str(i), 'producer') for i in range(5)]
    calls = [dict(seat=dict(assembly_id=str(i)), valid=i < 2, adopt=i < 2) for i in range(5)]
    assert not approved(calls, seats)
    calls[2].update(valid=True, adopt=True)
    assert approved(calls, seats)
    calls[4] = calls[0]
    with pytest.raises(ValueError, match='exactly once'):
        approved(calls, seats)
