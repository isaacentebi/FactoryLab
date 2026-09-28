"""Chapter II §III.b independent account reconciliation, never latency as proof."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import AccountState, Fill, OrderResult, Position


class Venue:
    def __init__(self, fees=True):
        self.shown = []
        self.executed = []
        self.calls = []
        self.fees = fees

    def fills(self, start):
        self.calls.append(start)
        return [f for f in self.shown if f.ts_ns >= start]

    def account(self):
        size = sum((f.size if f.is_buy else -f.size for f in self.executed), D(0))
        fees = sum((f.fee for f in self.executed), D(0))
        cash = D(1000) + sum((f.realized - f.fee for f in self.executed), D(0))
        return AccountState(cash, cash, (Position('BTC', size, D(10)),) if size else (), D(0),
                            reconciliation_cash_usd=cash,
                            cumulative_fees_usd=fees if self.fees else None)


def fill(ts):
    return Fill(str(ts), 'BTC', True, D(1), D(10), D('.01'), ts,
                observed_at_ns=ts+10, venue_id=str(ts))


def cursor(venue):
    result = FillCursor(Ledger(), start_ns=0, measured=True)
    result.initialize(replace(venue.account(), observed_at_ns=0), now_ns=0)
    return result


def test_net_neutral_hidden_orders_recover_before_watermark():
    venue = Venue(fees=False)
    c = cursor(venue)
    for ts in (100, 150):
        c.submitted(str(ts), now_ns=ts)
        c.acknowledged(str(ts), {'order_id': str(ts), 'status': 'filled'})
    venue.lookup = lambda client, **kw: OrderResult(kw['order_id'], 'filled', D(1), None)
    venue.executed = [fill(100), replace(fill(150), is_buy=False, realized=D('.02'))]
    assert c.poll(venue, now_ns=200, tick_ns=10) == []
    assert c.through_ns is None
    venue.shown = venue.executed
    assert len(c.poll(venue, now_ns=210, tick_ns=10)) == 2
    assert venue.calls[-1] <= 100
    assert c.through_ns == 210


@pytest.mark.parametrize('terminal', ['cancelled', 'resting'])
def test_exact_partial_status_and_truncated_page_recovery(terminal):
    venue = Venue(fees=False)
    c = cursor(venue)
    c.submitted('order', now_ns=50)
    c.acknowledged('order', {'order_id': '100', 'status': 'resting'})
    first = fill(100)
    second = replace(fill(150), order_id='100')
    venue.executed = [first, second]
    venue.shown = [first]
    venue.lookup = lambda *a, **k: OrderResult('100', terminal, D(2), None)
    assert len(c.poll(venue, now_ns=200)) == 1
    assert c.through_ns is None
    venue.shown = venue.executed
    assert [ts for ts, _ in c.poll(venue, now_ns=210)] == [150]
    assert c.through_ns == 210
    assert bool(c.orders) == (terminal == 'resting')


def test_no_order_liquidation_recovers_via_position_residual():
    venue = Venue(fees=False)
    venue.executed = [fill(10)]
    c = cursor(venue)
    liquidation = replace(fill(150), order_id='', is_buy=False, liquidation=True)
    venue.executed.append(liquidation)
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    venue.shown = [liquidation]
    assert len(c.poll(venue, now_ns=210)) == 1
    assert c.through_ns == 210


def test_long_open_order_retains_identity_without_repeated_ledger_scan():
    venue = Venue()
    c = cursor(venue)
    c.submitted('open', now_ns=50)
    c.acknowledged('open', {'order_id': '100', 'status': 'resting'})
    venue.lookup = lambda *a, **kw: OrderResult('100', 'resting', D(1), None)
    venue.shown = venue.executed = [fill(100)]
    assert len(c.poll(venue, now_ns=200)) == 1
    def forbidden_scan():
        raise AssertionError('open execution identity was evicted')
    c.ledger._iter_items = forbidden_scan
    for now in (300, 400, 500):
        assert c.poll(venue, now_ns=now) == []
    assert len(c.seen) == 1


def test_submicro_cash_and_fee_audit_never_block_position_evidence():
    venue = Venue()
    venue.account = lambda: AccountState(D('999.00000001'), D(0), (), D(0),
        reconciliation_cash_usd=D('999.00000001'), cumulative_fees_usd=D('.00000001'))
    c = cursor(venue)
    c.poll(venue, now_ns=200)
    assert c.through_ns == 200
    assert c.expected_cash['perp'] is None


def test_production_shaped_open_position_and_funding_cash_are_audit_only():
    from types import SimpleNamespace

    from factorylab.world.exchange import HyperliquidExchange

    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    exchange._address = 'offline-fixture'
    exchange._guarded = lambda name, call: call()
    state = {'marginSummary': {'accountValue': '1000', 'totalRawUsd': '1000',
                              'totalMarginUsed': '0'}, 'assetPositions': []}
    exchange._info = SimpleNamespace(user_state=lambda address: state,
        query_order_by_oid=lambda address, oid: {'status': 'order', 'order': {
            'status': 'filled', 'order': {'oid': oid, 'origSz': '1', 'sz': '0'}}})
    c = cursor(exchange)
    c.submitted('factory', now_ns=50)
    c.acknowledged('factory', {'order_id': '100', 'status': 'filled'})
    state['assetPositions'] = [{'position': {'coin': 'BTC', 'szi': '1', 'entryPx': '100'}}]
    state['marginSummary'].update(accountValue='999', totalRawUsd='899', totalMarginUsed='10')
    exchange.fills = lambda start: [replace(fill(100), px=D(100), fee=D(1))]
    assert len(c.poll(exchange, now_ns=200)) == 1
    assert c.through_ns == 200
    state['marginSummary'].update(accountValue='998', totalRawUsd='898')
    assert c.poll(exchange, now_ns=210) == []
    assert c.through_ns == 210


def test_funding_cash_without_execution_does_not_block_positions():
    venue = Venue(fees=False)
    c = cursor(venue)
    venue.account = lambda: AccountState(D(999), D(999), (), D(0),
                                         reconciliation_cash_usd=D(999))
    c.poll(venue, now_ns=200)
    assert c.through_ns == 200


def test_late150_visible210_recovers_after_mismatch_without_advancing_checkpoint():
    venue = Venue()
    c = cursor(venue)
    venue.shown = venue.executed = [fill(100)]
    assert len(c.poll(venue, now_ns=110)) == 1
    venue.executed = [fill(100), fill(150)]
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    assert c.reconciliation_ns == 110
    venue.shown = [fill(100), replace(fill(150), observed_at_ns=210)]
    assert [ts for ts, _ in c.poll(venue, now_ns=210)] == [150]
    assert c.through_ns == 210


def test_unrecoverable_history_ledgers_exact_residual_and_incomplete_span():
    venue = Venue()
    c = cursor(venue)
    venue.executed = [fill(150)]
    c.poll(venue, now_ns=200)
    rows = [r for r in c.ledger._recovery_items() if r['kind'] == 'consequence.fill_unattributed']
    assert rows[-1]['position_delta'] == {'perp:BTC': '1'}
    assert rows[-1]['cash_delta_micro_usd'] == {'perp': -10000}
    assert rows[-1]['start_ns'] == 0
    assert c.through_ns is None


def test_missing_independent_fees_never_certifies_net_neutral_invisible_executions():
    venue = Venue(fees=False)
    c = cursor(venue)
    venue.executed = [fill(100), replace(fill(150), is_buy=False, realized=D('.02'))]
    for ts in (100, 150):
        c.submitted(str(ts), now_ns=ts)
        c.acknowledged(str(ts), {'order_id': str(ts), 'status': 'filled'})
    venue.lookup = lambda client, **kw: OrderResult(kw['order_id'], 'filled', D(1), None)
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    assert c.reconciliation_ns == 200


def test_missing_stable_identity_cannot_certify_live_read():
    venue = Venue()
    c = cursor(venue)
    venue.shown = venue.executed = [replace(fill(100), venue_id=None)]
    assert len(c.poll(venue, now_ns=110)) == 1
    assert c.through_ns is None


def test_known_fee_residual_prevents_net_neutral_checkpoint_advance():
    venue = Venue()
    c = cursor(venue)
    venue.executed = [fill(100), replace(fill(150), is_buy=False, realized=D('.02'))]
    for ts in (100, 150):
        c.submitted(str(ts), now_ns=ts)
        c.acknowledged(str(ts), {'order_id': str(ts), 'status': 'filled'})
    venue.lookup = lambda client, **kw: OrderResult(kw['order_id'], 'filled', D(1), None)
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    assert c.reconciliation_ns == 200  # Net agreement is not the discovery frontier.
    venue.shown = venue.executed
    assert len(c.poll(venue, now_ns=210)) == 2
    assert c.through_ns == 210


def test_stable_execution_ids_dedup_repeated_rows_not_distinct_same_payload():
    venue = Venue()
    c = cursor(venue)
    first = fill(100)
    second = replace(first, venue_id='other')
    venue.shown = [first, first, second]
    venue.executed = [first, second]
    assert len(c.poll(venue, now_ns=110)) == 2
    assert c.poll(venue, now_ns=120) == []


def test_geometric_recovery_reloads_durable_dedup_without_double_booking():
    venue = Venue()
    c = cursor(venue)
    venue.shown = venue.executed = [fill(100)]
    c.poll(venue, now_ns=110)
    c.poll(venue, now_ns=200)
    assert not c.seen
    venue.executed = [fill(100), fill(150)]
    venue.shown = venue.executed
    booked = []
    for now in range(210, 280, 10):
        booked += c.poll(venue, now_ns=now)
    assert [ts for ts, _ in booked] == [150]
    assert c.through_ns == 270


def test_unchanged_residual_is_annotated_once_then_late_fill_restores_accounting():
    venue = Venue()
    c = cursor(venue)
    venue.executed = [fill(150)]
    c.poll(venue, now_ns=200)
    c.poll(venue, now_ns=205)
    rows = [r for r in c.ledger._recovery_items() if r['kind'] == 'consequence.fill_unattributed']
    assert len(rows) == 1
    venue.shown = venue.executed
    assert len(c.poll(venue, now_ns=210)) == 1
    assert c.expected_cash == {'perp': 999990000, 'spot': 0}
    assert c.last_residual is None


def test_failed_baseline_retries_after_checkpoint_without_rebooking():
    from factorylab.runtime.resume import decode, encode

    venue = Venue()
    fresh = venue.account
    venue.account = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    venue.shown = venue.executed = [fill(100)]
    assert len(c.poll(venue, now_ns=110)) == 1
    assert c.expected_positions is None
    saved = decode(encode({k: v for k, v in vars(c).items() if k != 'ledger'}))
    restored = object.__new__(FillCursor)
    vars(restored).update(saved, ledger=c.ledger)
    venue.account = lambda: replace(fresh(), observed_at_ns=200)
    assert restored.poll(venue, now_ns=200) == []
    assert restored.expected_positions == {'perp:BTC': '1'}
    assert restored.through_ns == 200


def test_retry_baseline_absorbs_pre_anchor_nonfactory_execution():
    venue = Venue(fees=False)
    c = FillCursor(Ledger(), start_ns=0, measured=True)
    fresh = venue.account
    venue.account = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
    assert c.poll(venue, now_ns=50) == []
    assert c.baseline_ns is None
    assert c.through_ns is None
    venue.executed = [fill(100)]
    venue.account = lambda: replace(fresh(), observed_at_ns=200)
    assert c.poll(venue, now_ns=200) == []
    assert c.baseline_ns == 200
    assert c.through_ns == 200
    venue.shown = venue.executed
    venue.account = lambda: replace(fresh(), observed_at_ns=210)
    # The measured overlap encounters the delayed row; exhaustive preanchor discovery
    # is not promised after the account anchor has absorbed outside exposure.
    assert c.poll(venue, now_ns=210, tick_ns=100) == []
    assert c.expected_positions == {'perp:BTC': '1'}
    assert c.through_ns == 210
    venue.account = lambda: replace(fresh(), observed_at_ns=220)
    assert c.poll(venue, now_ns=220, tick_ns=100) == []
    absorbed = [r for r in c.ledger._recovery_items()
                if r['kind'] == 'consequence.fill_absorbed']
    assert len(absorbed) == 1
    assert absorbed[0]['baseline_ns'] == 200
    assert absorbed[0]['key'] == [100, 'venue', '100']


def test_backward_poll_replays_identity_append_before_propagation():
    from copy import deepcopy

    from factorylab.runtime.resume import RecoveryJournal

    venue = Venue()
    raw_account = venue.account
    venue.account = lambda: replace(raw_account(), observed_at_ns=1000)
    ledger = Ledger(clock_ns=lambda: 0)
    c = FillCursor(ledger, start_ns=0, measured=True)
    c.initialize(replace(venue.account(), observed_at_ns=0), now_ns=0)
    venue.shown = venue.executed = [fill(100)]
    c.poll(venue, now_ns=110)
    c.poll(venue, now_ns=200)
    venue.executed.append(fill(150))
    c.poll(venue, now_ns=210)
    c.recovery_span_ns = 100  # The next geometric backward read reaches the late execution.
    before = deepcopy({k: v for k, v in vars(c).items() if k != 'ledger'})
    prefix = len(ledger._recovery_items())
    venue.shown = venue.executed
    expected = c.poll(venue, now_ns=220)
    assert [ts for ts, _ in expected] == [150]
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.tail = iter(ledger._recovery_items()[prefix:])
    restored = object.__new__(FillCursor)
    vars(restored).update(before, ledger=journal)
    assert restored.poll(venue, now_ns=220) == expected
    assert journal.peek() is None


def test_recovery_identity_scan_cannot_see_future_ledger_rows():
    from factorylab.runtime.resume import RecoveryJournal

    ledger = Ledger()
    ledger.append({'kind': 'before'})
    ledger.append({'kind': 'consequence.fill_identity', 'key': [10, 'venue', 'x'], 'count': 1})
    journal = RecoveryJournal(ledger, lambda: 0)
    journal.tail = iter(ledger._recovery_items()[1:])
    assert [row['kind'] for row in journal._iter_items()] == ['before']


@pytest.mark.parametrize('count', [400, 5000])
def test_reconciled_identity_memory_is_bounded(count):
    import json

    from factorylab.runtime.resume import decode, encode

    venue = Venue()
    c = cursor(venue)
    sizes = []
    for i in range(1, count+1):
        f = fill(i*10)
        c.submitted(f.order_id, now_ns=f.ts_ns)
        c.acknowledged(f.order_id, {'order_id': f.order_id, 'status': 'filled'})
        venue.lookup = lambda client, **kw: OrderResult(kw['order_id'], 'filled', D(1), None)
        venue.shown = [f]
        # Constant-time account facts avoid retaining the entire synthetic venue history.
        venue.account = lambda i=i: AccountState(D(1000)-D('.01')*i, D(0),
            (Position('BTC', D(i), D(10)),), D(0),
            reconciliation_cash_usd=D(1000)-D('.01')*i, cumulative_fees_usd=D('.01')*i)
        assert len(c.poll(venue, now_ns=i*10+10)) == 1
        assert len(c.seen) <= 2
        assert c.poll(venue, now_ns=i*10+10) == []
        if i in (999, 2499, 4999):
            state = encode({k: v for k, v in vars(c).items() if k != 'ledger'})
            sizes.append(len(json.dumps(state)))
            restored = FillCursor(c.ledger, start_ns=0, measured=True)
            vars(restored).update(decode(state))
            c = restored
    assert not sizes or max(sizes) - min(sizes) <= 100
    assert c.through_ns == count*10+10
