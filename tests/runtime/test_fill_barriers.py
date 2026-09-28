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
    assert row['retention_unknown'] is True
    assert row['reason'] == 'execution evidence unavailable or outside retained history'
    venue.lookup = lambda *a, **kw: status('o', size=1)
    c.poll(venue, now_ns=200)
    assert c.through_ns == 200
    row = next(r for r in reversed(c.ledger._recovery_items())
               if r['kind'] == 'consequence.fill_reconciliation')
    assert row['retention_unknown'] is False


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


@pytest.mark.parametrize('operation', ['venue.place_market', 'venue.place_limit', 'venue.close'])
def test_order_write_requires_live_account_anchor(operation):
    from factorylab.runtime.venue import VenueMixin

    c = FillCursor(Ledger(), start_ns=100, measured=True)
    rt = SimpleNamespace(consequence_fills=c,
                         _refuse_order=lambda h, reason: {'status': 'refused', 'reason': reason})
    result = VenueMixin._venue_write(rt, 'h', operation, {}, slot='output')
    assert result == {'status': 'refused', 'reason': 'fill account baseline unavailable'}


@pytest.mark.parametrize('observed,expected', [(200, 200), (None, 250), (0, 0)])
def test_baseline_uses_snapshot_or_actual_return(monkeypatch, observed, expected):
    import time
    monkeypatch.setattr(time, 'time_ns', lambda: 250)
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    c.initialize(account(observed=observed), now_ns=100)
    assert c.baseline_ns == expected


def test_preanchor_nonfactory_execution_is_absorbed_once_not_delivered():
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    c.initialize(account(observed=200, size=1), now_ns=100)
    f = Fill('external', 'BTC', True, D(1), D(10), D(0), 150, venue_id='old')
    venue = SimpleNamespace(fills=lambda start: [f], account=lambda: account(observed=300, size=1))
    assert c.poll(venue, now_ns=199) == []
    assert c.through_ns is None
    assert c.poll(venue, now_ns=200) == []
    assert c.through_ns == 200
    assert c.poll(venue, now_ns=210) == []
    absorbed = [r for r in c.ledger._recovery_items() if r['kind'] == 'consequence.fill_absorbed']
    assert len(absorbed) == 1
    assert absorbed[0]['baseline_ns'] == 200
    assert c.expected_positions == {'perp:BTC': '1'}


def test_execution_at_anchor_is_not_preanchor():
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    c.initialize(account(observed=200, size=1), now_ns=100)
    f = Fill('external', 'BTC', True, D(1), D(10), D(0), 200, venue_id='boundary')
    venue = SimpleNamespace(fills=lambda start: [f], account=lambda: account(size=1))
    assert [ts for ts, _ in c.poll(venue, now_ns=200)] == [200]
    assert c.expected_positions == {'perp:BTC': '1'}
    assert c.through_ns == 200
    assert c.poll(venue, now_ns=200) == []


@pytest.mark.gate
def test_5000_partials_one_resting_order_has_bounded_identity_memory():
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    c.initialize(account(observed=10), now_ns=0)
    c.submitted('resting', now_ns=11)
    c.acknowledged('resting', {'order_id': 'resting'})
    for i in range(1, 5001):
        now = i * 100
        f = Fill('resting', 'BTC', True, D(1), D(10), D(0), now - 10,
                 observed_at_ns=now, venue_id=str(i))
        starts = []
        venue = SimpleNamespace(
            fills=lambda start, f=f, starts=starts: starts.append(start) or (
                [f] if f.ts_ns >= start else []),
            account=lambda now=now, i=i: account(observed=now, size=i),
            lookup=lambda *a, now=now, i=i, **kw: status('resting', observed=now, size=i))
        assert len(c.poll(venue, now_ns=now, tick_ns=10)) == 1
        assert c.through_ns == now
        if i > 1:
            assert starts == [now - 120]
        assert c.orders['resting']['submitted_ns'] == now
        assert len(c.orders['resting']['identities']) <= 1
        assert len(c.seen) <= 1
        assert c.poll(venue, now_ns=now, tick_ns=10) == []
    assert c.orders['resting']['booked'] == '5000'
