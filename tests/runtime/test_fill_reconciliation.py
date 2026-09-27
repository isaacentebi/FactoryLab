"""Chapter II §III.b independent account reconciliation, never latency as proof."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.settlement.consequence import FillCursor
from factorylab.world.exchange import AccountState, Fill, Position


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
    result.initialize(venue.account(), now_ns=0)
    return result


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
    c.poll(venue, now_ns=200)
    assert c.through_ns is None
    assert c.reconciliation_ns == 0
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


@pytest.mark.parametrize('count', [400, 5000])
def test_reconciled_identity_memory_is_bounded(count):
    import json

    from factorylab.runtime.resume import decode, encode

    venue = Venue()
    c = cursor(venue)
    sizes = []
    for i in range(1, count+1):
        f = fill(i*10)
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
