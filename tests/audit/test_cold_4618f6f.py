"""Offline characterizations of upload 4618f6f; passing tests document defects, not safety.

Run without the repository's integration conftest (its optional SDK imports are absent here):
  PYTHONDONTWRITEBYTECODE=1 python -m pytest -o addopts='' --noconftest \
      tests/audit/test_cold_4618f6f.py -q

No provider, network, venue, disk diary, or key is used. Production functions whose
module imports unavailable SDKs are compiled unchanged from their AST, with explicit
fake dependencies. Those tests are unit characterizations, not integration/replay tests.
"""
from __future__ import annotations

import ast
import copy
import json
import socket
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factorylab.kernel.artifacts import PRIVATE_REFUSAL, ArtifactStore
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime import witness
from factorylab.runtime.continuity import MAX_SAID, OutcomeInbox, WorkingState
from factorylab.runtime.subscriptions import SubscriptionBook, evaluate_trigger
from factorylab.runtime.venue import VenueMixin, wind_down
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import OrderResult

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def prohibit_network_and_key_reads(monkeypatch):
    """Defense in depth: no test may connect or open a *.key through Python I/O."""
    import builtins
    import io
    import os
    old_open, old_io_open, old_os_open = builtins.open, io.open, os.open

    def check(path):
        if isinstance(path, (str, bytes, os.PathLike)):
            name = os.fsdecode(path)
            if name.endswith('.key'):
                raise AssertionError('key access is forbidden in this audit')

    def guarded_open(path, *a, **kw):
        check(path)
        return old_open(path, *a, **kw)

    def guarded_io_open(path, *a, **kw):
        check(path)
        return old_io_open(path, *a, **kw)

    def guarded_os_open(path, *a, **kw):
        check(path)
        return old_os_open(path, *a, **kw)

    def deny(*a, **kw):
        raise AssertionError('network access is forbidden in this audit')

    monkeypatch.setattr(builtins, 'open', guarded_open)
    monkeypatch.setattr(io, 'open', guarded_io_open)
    monkeypatch.setattr(os, 'open', guarded_os_open)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, '_killed_here', set())


class Evidence:
    def __init__(self, fail_kind=None):
        self.rows = []
        self.fail_kind = fail_kind

    def append(self, row):
        if row.get('kind') == self.fail_kind:
            raise OSError('injected evidence-store failure')
        self.rows.append(copy.deepcopy(row))
        return len(self.rows)


def archive():
    ledger = Evidence()
    clock = SimpleNamespace(ns=0)
    store = ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)
    return ledger, clock, store


def source_objects(relative, *names, injected=None):
    """Compile selected original definitions verbatim; no production file is edited."""
    source = (ROOT / relative).read_text()
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                and n.name in names]
    assert len(selected) == len(names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *selected], type_ignores=[]))
    namespace = {'json': json, 'Any': Any, '__name__': 'audit_isolated', **(injected or {})}
    exec(compile(module, str(ROOT / relative), 'exec'), namespace)
    return tuple(namespace[n] for n in names)


def test_characterize_same_bytes_second_writer_cannot_read_own_artifact():
    _, _, store = archive()
    sha = store.put(b'own independently written content', owner='alice', kind='working.state')
    assert store.put(b'own independently written content', owner='bob', kind='working.state') == sha
    assert store.read(sha, reader='bob')['error'] == PRIVATE_REFUSAL
    assert store.list(owner='bob') == []


def test_characterize_republishing_existing_hash_does_not_publish():
    _, _, store = archive()
    sha = store.put(b'publish me', owner='alice', kind='working.state')
    store.put(b'publish me', owner='alice', kind='note', public=True)
    assert store.read(sha, reader='bob')['error'] == PRIVATE_REFUSAL


def test_characterize_orphan_blob_is_readable_by_stranger():
    _, _, store = archive()
    sha = store.put(b'private data from an unindexed tail', owner='alice', kind='working.state')
    # An earlier checkpoint restores an index that predates the durable blob.
    store.index.clear()
    assert store.read(sha, reader='bob')['text'] == 'private data from an unindexed tail'


def test_characterize_missing_state_silently_becomes_no_state():
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    head = state.put('alice', {'keep': 'a hypothesis'}, handle='d1')
    store._memory.pop(head['sha'])
    assert state.head('alice') is not None
    assert state.render('alice') is None


def test_characterize_failed_state_evidence_advances_head():
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    old = state.put('alice', {'version': 1}, handle='d1')['sha']
    ledger.fail_kind = 'state.put'
    with pytest.raises(OSError):
        state.put('alice', {'version': 2}, handle='d2')
    assert state.head('alice')['sha'] != old
    assert state.render('alice')['state'] == {'version': 2}


def test_characterize_rewrite_reprices_previous_rent_interval():
    ledger, clock, store = archive()
    state = WorkingState(store, ledger, lambda: clock.ns)
    initial = state.put('alice', {'data': 'x' * 1000}, handle='d1')
    old_bytes = initial['bytes']
    clock.ns = 86_400_000_000_000
    replacement = state.put('alice', {}, handle='d2')
    assert replacement['rent_ns'] == 0
    assert replacement['bytes'] == 2
    assert replacement['bytes'] * (clock.ns - replacement['rent_ns']) < old_bytes * clock.ns
    assert replacement['rent_due'] == 0


def test_characterize_inbox_same_decision_older_fact_not_retrievable_by_handle():
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.append('alice', handle='d1', outcome={'first': True}, evidence='first-fact')
    for i in range(9):
        inbox.append('alice', handle='d1', outcome={'later': i}, evidence=f'later-{i}')
    assert inbox.unread('alice')['count'] == 10
    assert all('first' not in row['outcome'] for row in inbox.unread('alice')['items'])
    assert inbox.get('alice', 'd1')['outcome'] == {'later': 8}


def test_characterize_ack_latest_handle_acknowledges_unseen_older_facts():
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    for i in range(10):
        inbox.append('alice', handle=f'd{i}', outcome={'n': i})
    shown = inbox.unread('alice')
    assert len(shown['items']) == 8
    assert all(row['handle'] != 'd0' for row in shown['items'])
    inbox.ack_through('alice', 'd9')
    assert inbox.unread('alice')['count'] == 0
    assert inbox.get('alice', 'd0')['read'] is True


def test_characterize_original_rationale_evicted_before_delayed_outcome():
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    inbox.record_said('alice', 'original', {'rationale': 'a long-horizon hypothesis'})
    for i in range(MAX_SAID):
        inbox.record_said('bob', f'other-{i}', {'rationale': 'unrelated'})
    inbox.append('alice', handle='original', outcome={'settled': True})
    assert inbox.get('alice', 'original')['said']['rationale'] is None


def test_characterize_duplicate_settlement_fact_is_appended_twice():
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    for _ in range(2):
        inbox.append('alice', handle='d1', outcome={'paid': 1}, evidence='same-receipt')
    assert inbox.unread('alice')['count'] == 2


def test_characterize_failed_inbox_evidence_still_publishes_item():
    ledger, clock, store = archive()
    inbox = OutcomeInbox(store, ledger, lambda: clock.ns)
    ledger.fail_kind = 'outcome.addressed'
    with pytest.raises(OSError):
        inbox.append('alice', handle='d1', outcome={'paid': 1})
    assert inbox.unread('alice')['count'] == 1


def test_characterize_missing_watcher_observation_erases_last_valid_value():
    trigger = {'kind': 'price_cross', 'coin': 'BTC', 'level': '100'}
    _, before = evaluate_trigger(trigger, {'mids': {'BTC': '90'}}, None)
    _, missing = evaluate_trigger(trigger, {'mids': {}}, before)
    fact, _ = evaluate_trigger(trigger, {'mids': {'BTC': '110'}}, missing)
    assert fact is None


def test_characterize_judge_work_ignores_its_defer():
    book = SubscriptionBook()
    book.defer('judge', 100, now=1)
    assert book.absent('judge', 'WorldUpdate', now=2, coins=frozenset()).startswith('asleep:')
    assert book.absent('judge', 'ProducerReturn', now=2, coins=frozenset()) == ''


def test_characterize_coin_subscription_does_not_filter_delivered_fold():
    from factorylab.runtime.subscriptions import Subscription
    book = SubscriptionBook()
    book.set_subscription('alice', Subscription(coins=frozenset({'BTC'})))
    for coin in ('BTC', 'ETH'):
        book.observe(['alice'], 'MarketMid', {'coin': coin, 'mid': '100'}, 1, now=1)
    delivered = book.take('alice', now=1)
    assert 'ETH' in json.dumps(delivered)


def test_characterize_forwarded_private_outputs_remain_unredacted():
    (outputs,) = source_objects('factorylab/runtime/wake.py', '_outputs')
    private = {'action': 'hold', 'working_state': {'private_hypothesis': 'sensitive'},
               'propensity': {'hold': .9, 'order': .1}}
    assert outputs(json.dumps(private)) == private


class FakeVenue:
    def __init__(self, close_status='filled'):
        self.close_status = close_status
        self.close_calls = 0
        self.positions = [SimpleNamespace(coin='BTC', size=Decimal('1'))]

    def open_orders(self):
        return []

    def account(self):
        return SimpleNamespace(positions=self.positions, spot_balances=[],
                               equity_usd=Decimal('100'), margin_used_usd=Decimal('0'))

    def mids(self):
        return {'BTC': Decimal('100')}

    def close(self, *args, **kwargs):
        self.close_calls += 1
        return OrderResult('order-1', self.close_status, Decimal('0'), None)


def test_characterize_wind_down_counts_resting_as_closed_without_flatness():
    venue = FakeVenue('resting')
    report = wind_down(venue, Evidence())
    assert report['closed'] == 1 and report['failed'] == 0
    assert venue.positions[0].size != 0


def test_characterize_wind_down_ledger_failure_escapes_and_prevents_kill():
    term = SimpleNamespace(final=False)
    def terminate(reason):
        term.final = True
    term.kill = terminate
    rt = SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=True, dust_micro=1)),
                         exchange=FakeVenue(), ledger=Evidence('kill.wind_down'), termination=term)
    with pytest.raises(OSError):
        VenueMixin.kill(rt, 'explicit_kill')
    assert term.final is False


def test_characterize_repeat_runtime_kill_repeats_external_close():
    venue = FakeVenue()
    term = SimpleNamespace(final=True, kill=lambda reason: None)
    rt = SimpleNamespace(m=SimpleNamespace(kill=SimpleNamespace(wind_down=True, dust_micro=1)),
                         exchange=venue, ledger=Evidence(), termination=term)
    VenueMixin.kill(rt, 'explicit_kill')
    assert venue.close_calls == 1


def test_characterize_unsetting_remote_witness_removes_its_veto(monkeypatch):
    monkeypatch.setenv(witness.URL_ENV, 'https://witness.invalid/')
    monkeypatch.setattr(witness, '_post', lambda *a, **kw: {'killed': True})
    args = dict(world='audit', launch_nonce='nonce', diary='diary', ledger_path=None)
    assert witness.killed(**args) == 'remote'
    monkeypatch.delenv(witness.URL_ENV)
    assert witness.killed(**args) is None


def test_characterize_renaming_diary_changes_local_witness_location(tmp_path):
    first = tmp_path / 'runs' / 'first.jsonl'
    renamed = tmp_path / 'runs' / 'renamed.jsonl'
    line = {'event': 'kill', 'launch_nonce': 'nonce', 'diary': 'diary'}
    assert witness._append(witness.witness_path(first), line)
    args = dict(world='audit', launch_nonce='nonce', diary='diary', remote=False)
    assert witness.killed(ledger_path=first, **args) == 'local'
    assert witness.killed(ledger_path=renamed, **args) is None


def test_characterize_venue_loss_can_kill_untouched_compute_credit():
    ledger = Ledger()
    wallet = Wallet(10_000_000, ledger)
    untouched_provider_credit = 10_000_000
    rt = SimpleNamespace(
        wallet=wallet, consequences=SimpleNamespace(table=None, observe=lambda *a: None),
        n=0, stats=SimpleNamespace(fills=0),
        window=SimpleNamespace(fills=0, notional_micro=0, realized_pnl_micro=0, index=0),
        realized_to_date=0, fees_to_date=0, funding_to_date=0, ledger=ledger,
        internal=[], _kernel_event=lambda e: e,
    )
    fill = WorldEvent(WorldEventKind.FILL, 1, 'fake', {
        'order_id': '1', 'coin': 'BTC', 'market': 'perp', 'realized_usd': '-20',
        'fee_usd': '0.1', 'size': '1', 'px': '100', 'is_buy': False,
    })
    VenueMixin._settle_exchange_effects(rt, [fill], observe_positions=False)
    assert wallet.balance == -10_100_000 and wallet.dead
    assert untouched_provider_credit == 10_000_000
    assert wallet.check_conservation()  # arithmetic conservation is not economic truth


def test_control_payoff_net_excludes_already_paid_compute():
    from factorylab.settlement.lots import LotTable
    table = LotTable().start('d1', 0).finish('d1', 1000).resolve(1, 10, {})
    payoff = table.account('d1').payoff
    assert payoff.net_micro == 0 and payoff.cost_micro == 1000


def test_control_wallet_reservation_is_single_use():
    from factorylab.kernel.wallet import Infeasible
    wallet = Wallet(10_000_000, Ledger())
    hold = wallet.reserve(1000, 'd1', 'model:test')
    wallet.commit(hold, 750)
    with pytest.raises(Infeasible):
        wallet.commit(hold, 750)
    assert wallet.balance == 9_999_250 and wallet.check_conservation()


def test_characterize_late_credit_resurrects_retired_budget_seat():
    from factorylab.kernel.budget import BudgetBook
    ledger = Ledger()
    wallet = Wallet(100_000_000, ledger)
    budget = BudgetBook(wallet, ledger, clock_ns=lambda: 0)
    budget.genesis(['alice', 'bob'])
    budget.retire('alice', 'approved-retirement')
    assert 'alice' not in budget.seats()
    budget.credit('alice', 1, 'late trading consequence')
    assert 'alice' in budget.seats()
    assert 'alice' in budget.heads()


def test_characterize_fidelity_objection_is_punished_when_disputed_proxy_is_good():
    from factorylab.settlement.fidelity import FidelityObjection
    from factorylab.settlement.scoring import PrevalenceBaseline
    from factorylab.settlement.settle import Settler
    from factorylab.settlement.standing import ConsequenceStanding
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(None, None, standing, PrevalenceBaseline(), None)
    # This is exactly the objection's contract: proxy looks good, norm may not be served.
    objection = FidelityObjection('fidelity', 'successful_proxy', 'independent counterexample', 0)
    settler._Settler__objections['judge-decision'] = objection
    result = settler.settle_verdict(evaluator_id='alice', about_handle='decision-1',
                                   q=1.0, share=0.0, judge_handle='judge-decision')
    assert result.brier == 1.0
    assert result.objection_brier == 0.0
    assert result.objection_baseline_brier == 0.75


def test_characterize_minimal_world_bypasses_seat_privacy_partition():
    from factorylab.cortex.request import Request
    req = Request('decision-1', 'work', {'you': 'alice', 'world': {'seats': [
        {'seat_id': 'alice', 'your_resources': {'mine': 1}},
        {'seat_id': 'bob', 'your_resources': {'secret': 'BOB_PRIVATE_ENTITLEMENT'}}]}},
        {}, {}, 10, 1, None, 'done', 'verdict', 'alice')
    assert 'BOB_PRIVATE_ENTITLEMENT' not in req.seat_text()
    assert 'BOB_PRIVATE_ENTITLEMENT' in req.prompt_text()


def test_characterize_fill_is_not_addressed_to_its_owner():
    from factorylab.kernel.events import Event, EventKind
    (RoutingMixin,) = source_objects('factorylab/runtime/routing.py', 'RoutingMixin',
                                    injected={'EventKind': EventKind})
    event = Event('fill-1', EventKind.FILL, 0,
                  {'owner': 'alice', 'handle': 'decision-1', 'order_id': 'order-1'}, 'venue')
    assert RoutingMixin._addressed_seat(SimpleNamespace(), event) is None


def test_characterize_non_payoff_forecast_does_not_reach_seat_inbox():
    (FeedbackMixin,) = source_objects('factorylab/runtime/feedback.py', 'FeedbackMixin')
    rt = SimpleNamespace()
    # All attributes deliberately absent: the method returns before looking up the owner.
    assert FeedbackMixin._deliver_consequence_to_inbox(
        rt, SimpleNamespace(predicate_id='wallet_up')) is None


def test_characterize_cascade_counts_arrivals_not_elapsed_time():
    from factorylab.kernel.events import Event, EventKind
    from factorylab.runtime.cascade import CascadeGate
    gate = CascadeGate(3)
    for i in range(3):
        event = Event(f'verdict-{i}', EventKind.VERDICT, 100,
                      {'verdict': 1.0, 'evaluator_handle': f'judge-{i}'}, 'judge')
        gate, released = gate.add(event)
    assert released is not None
    assert released.ts_ns == 100
    assert released.payload['window']['count'] == 3


def method_from_source(relative, class_name, method_name, injected=None, source=None):
    tree = ast.parse(source if source is not None else (ROOT / relative).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, fn], type_ignores=[]))
    namespace = {'__name__': 'audit_isolated', **(injected or {})}
    exec(compile(module, str(ROOT / relative), 'exec'), namespace)
    return namespace[method_name]


def test_characterize_empty_router_draw_enters_producer_evaluation_pipeline():
    called = []
    step = method_from_source('factorylab/runtime/loop.py', 'Runtime', '_assembly_step',
                              {'NOOP': 'noop'})
    rt = SimpleNamespace(_start_return=lambda h: None, _event_subject=lambda ev: None,
                         _producer_step=lambda *args: called.append(args))
    step(rt, object(), 'd1', SimpleNamespace(chosen='noop'), 10)
    assert len(called) == 1


def test_characterize_open_commitments_filter_uses_router_actor_not_executing_seat():
    # A minimal queue double implements the exact actor filter in queue.outstanding.
    d = SimpleNamespace(handle='d1', actor='router:WorldUpdate', channel='verdict',
                        deadline_ns=100, opened_ns=0, cost_ceiling=10)
    queue = SimpleNamespace(outstanding=lambda actor=None:
                            [d] if actor is None or actor == d.actor else [])
    fn = method_from_source('factorylab/cortex/schematics.py', 'SchematicsMixin',
                            '_open_commitments', {'DIRECTORY_PAGE': 50, '_usd': str, '_utc': str})
    rt = SimpleNamespace(queue=queue, book=SimpleNamespace(pending=lambda: []), n=1,
                         handle_to_assembly={'d1': 'alice'})
    assert fn(rt, 'alice')['open_decision_count'] == 0
    assert queue.outstanding()[0].handle == 'd1'


def test_characterize_same_income_receipt_books_twice():
    earn = method_from_source('factorylab/world/treasury.py', 'Treasury', 'earn')
    t = SimpleNamespace(ledger=Evidence(), income={'earned_micro': 0})
    earn(t, 'service', 1000, 'same-transaction', payer='buyer')
    earn(t, 'service', 1000, 'same-transaction', payer='buyer')
    assert t.income['earned_micro'] == 2000


def test_characterize_refusal_is_broadcast_not_addressed_to_deciding_seat():
    ledger = Evidence()
    class Inbox:
        def __init__(self): self.calls = []
        def append(self, *a, **kw): self.calls.append((a, kw))
        def seat_of(self, handle): return 'alice'
    inbox = Inbox()
    rt = SimpleNamespace(ledger=ledger, registration_feedback=[], clock=SimpleNamespace(now_ns=1),
                         outcomes=inbox, handle_to_assembly={'d1': 'alice'})
    result = VenueMixin._refuse_order(rt, 'd1', 'insufficient collateral')
    assert result['status'] == 'rejected'
    assert rt.registration_feedback and not inbox.calls
