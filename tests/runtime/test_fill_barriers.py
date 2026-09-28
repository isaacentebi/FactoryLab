"""Chapter II §III.b finality requires fresh, independent venue observations."""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import AccountState, Fill


def account(*, observed=200, size=0):
    from factorylab.world.exchange import Position
    return AccountState(D(1000), D(1000),
                        (Position('BTC', D(size), D(10)),) if size else (), D(0),
                        observed_at_ns=observed)


def setup_cursor():
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    c.initialize(account(observed=0), now_ns=0)
    return c


def status(oid, *, observed=200, size=0, state='resting'):
    return SimpleNamespace(order_id=oid, status=state, filled_size=D(size),
                           observed_at_ns=observed)


@pytest.mark.parametrize('stale', ['first', 'second', 'positions'])
def test_all_barrier_observations_must_cover_target(stale):
    c = setup_cursor()
    for oid in ('first', 'second'):
        c.submitted(oid, now_ns=50)
        c.acknowledged(oid, {'order_id': oid})
    venue = SimpleNamespace(fills=lambda start: [],
        account=lambda: account(observed=199 if stale == 'positions' else 200),
        lookup=lambda client, **kw: status(client, observed=199 if client == stale else 200))
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    assert stale == 'positions' or stale in c.orders


@pytest.mark.parametrize('missing', ['order', 'positions'])
def test_missing_observation_uses_actual_return_not_target(monkeypatch, missing):
    import time
    monkeypatch.setattr(time, 'time_ns', lambda: 199)
    c = setup_cursor()
    c.submitted('o', now_ns=50)
    c.acknowledged('o', {'order_id': 'o'})
    venue = SimpleNamespace(fills=lambda start: [],
        account=lambda: account(observed=None if missing == 'positions' else 200),
        lookup=lambda *a, **kw: status('o', observed=None if missing == 'order' else 200))
    c.poll(venue, now_ns=200)
    assert c.through_ns is None


def test_incomplete_history_is_audited_without_globally_blocking_barrier():
    c = setup_cursor()
    c.submitted('o', now_ns=50)
    c.acknowledged('o', {'order_id': 'o'})
    f = Fill('o', 'BTC', True, D(1), D(10), D(0), 100,
             observed_at_ns=200, venue_id='f', history_complete=False)
    venue = SimpleNamespace(fills=lambda start: [f], account=lambda: account(size=1),
                            lookup=lambda *a, **kw: status('o', size=2))
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    rows = c.ledger._recovery_items()
    row = next(r for r in reversed(rows) if r['kind'] == 'consequence.fill_reconciliation')
    assert row['history_complete'] is False
    assert row['reason'] == 'execution evidence unavailable or outside retained history'
    venue.lookup = lambda *a, **kw: status('o', size=1)
    c.poll(venue, now_ns=200)
    assert c.through_ns == 200


def test_missing_observation_return_clock_is_replayed(monkeypatch):
    import time

    from factorylab.runtime.resume import RecoveryJournal

    ledger = Ledger(clock_ns=lambda: 0)
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.active = True
    c = FillCursor(journal, start_ns=0, measured=True)
    c.initialize(account(observed=0), now_ns=0)
    c.submitted('o', now_ns=50)
    c.acknowledged('o', {'order_id': 'o'})
    venue = SimpleNamespace(fills=lambda start: [], account=lambda: account(observed=None),
                            lookup=lambda *a, **kw: status('o', observed=None))
    monkeypatch.setattr(time, 'time_ns', lambda: 201)
    prefix = len(ledger._recovery_items())
    c.poll(venue, now_ns=200)
    assert c.through_ns == 200
    replay = RecoveryJournal(ledger, lambda: 0)
    restored = FillCursor(Ledger(), start_ns=0, measured=True)
    restored.initialize(account(observed=0), now_ns=0)
    restored.submitted('o', now_ns=50)
    restored.acknowledged('o', {'order_id': 'o'})
    restored.ledger = replay
    replay.active = True
    replay.tail = iter(ledger._recovery_items()[prefix:])
    monkeypatch.setattr(time, 'time_ns', lambda: 199)
    restored.poll(venue, now_ns=200)
    assert restored.through_ns == 200
    assert replay.peek() is None
