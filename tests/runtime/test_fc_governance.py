from types import SimpleNamespace

from factorylab.charter.amendment import PredictedEffect
from factorylab.cortex.registration import RouterProposal, parse_proposals
from factorylab.cortex.request import Return
from tests.runtime.test_fa_defects import make_runtime


def test_router_string_booleans_are_case_insensitive_and_do_not_use_truthiness():
    for value, expected in ((True, True), (False, False), ('true', True), ('TRUE', True),
                            ('TrUe', True), ('false', False), ('FALSE', False), ('FaLsE', False)):
        accepted, rejected = parse_proposals(
            {'register': [{'kind': 'router', 'learner': 'exp3', 'event_kind': 'Tick',
                           'add': value}]}, event_kinds=frozenset({'Tick'}),
            known_models=frozenset(), known_assemblies=frozenset(),
        )
        assert not rejected and accepted[0].add is expected


def test_proposal_examples_preserve_required_scalar_and_enum_types():
    from factorylab.runtime.loop import Runtime

    shapes = Runtime.PROPOSAL_SHAPES
    assert type(shapes['router']['add']) is bool
    assert shapes['router']['learner'] in ('exp3', 'blum_mansour')
    assert shapes['router']['event_kind'] == 'Tick'
    assert shapes['assembly']['role'] == 'producer'
    assert shapes['assembly']['effort'] in ('low', 'medium', 'high')
    assert type(shapes['assembly']['max_tokens']) is int
    assert type(shapes['tool']['timeout_s']) is int
    assert type(shapes['amendment']['add'][0]['lambda']) in (int, float)


def test_amendments_share_the_return_cap_and_public_refusal(monkeypatch):
    rt = make_runtime()
    calls = []
    monkeypatch.setattr(rt, '_propose_amendment', lambda h, i: calls.append(i['id']))
    monkeypatch.setattr(rt, '_register', lambda h, p: calls.append(p))
    proposals = [{'kind': 'amendment', 'id': 'first'},
                 {'kind': 'router', 'learner': 'exp3', 'event_kind': 'Tick'},
                 {'kind': 'amendment', 'id': 'second'},
                 {'kind': 'amendment', 'id': 'excess'}]
    rt._apply_registrations('parent', Return('parent', {'register': proposals}, 0, 'ok'))
    assert calls == ['first', RouterProposal('Tick', 'exp3', .1), 'second']
    assert rt.registration_feedback[-1]['index'] == 3
    assert 'cap' in rt.registration_feedback[-1]['reason']


def test_votes_have_one_queue_decision_per_seat_per_amendment(monkeypatch):
    rt = make_runtime()
    monkeypatch.setattr(rt.charter_book, 'vote', lambda *_: None)
    monkeypatch.setattr(rt.charter_book, 'abstain', lambda *_: None)
    monkeypatch.setattr(rt.charter_book, 'tally', lambda *_: 'failed')
    amendment = SimpleNamespace(id='test', proposed_prices=(), add=(), replace=(), remove=(),
                                predicted_effect=PredictedEffect("cost_per_return", "decrease", 1),
                    tick_interval=None)
    committee = SimpleNamespace(seats=[('seat1', 'seed-decider'), ('seat2', 'seed-decider')])
    rt._hold_vote(amendment, committee)
    n = rt.stats.invocations
    rt._hold_vote(amendment, committee)
    assert rt.stats.invocations == n == 2
    items = rt.ledger._recovery_items()
    decisions = [i for i in items if i['kind'] == 'decision.open']
    calls = [i for i in items if i['kind'] == 'invocation']
    assert len(decisions) == len(calls) == 2
    assert {i['handle'] for i in decisions} == {i['handle'] for i in calls}
    for call in calls:
        assert rt.queue.history(call['handle'])
    assert rt.window.invocations == 2


def test_a_router_add_at_the_cap_is_refused_before_the_receipt_is_spent():
    """Codex finding: an `add` proposal registered its router contract, consuming the
    novelty receipt, and only then did `_build_router` refuse it at the cap: an orphan
    contract in an append-only registry and no refund. Everything that can refuse the
    router is checked first."""
    rt = make_runtime()
    rt._manage_reserve_window()
    cap = rt.m.tools.max_routers_per_kind
    while len(rt.routers['Tick']) < cap:
        rt._build_router('Tick', 'exp3', .1, replace=False)
    registry, remaining = rt.registry.state(), rt.reserve.remaining()
    rt._apply_registrations('author', Return('author', {'register': [
        {'kind': 'router', 'learner': 'exp3', 'event_kind': 'Tick', 'add': True}]}, 0, 'ok'))
    assert rt.stats.registrations_rejected == 1 and rt.stats.registrations_accepted == 0
    assert 'router cap reached' in rt.registration_feedback[-1]['reason']
    assert len(rt.routers['Tick']) == cap
    assert rt.registry.state() == registry and rt.reserve.remaining() == remaining
    assert not [i for i in rt.ledger._recovery_items() if i['kind'] == 'novelty.release']
    assert rt.wallet.check_conservation()
    # Replacing a router is still allowed at the cap, and that one does register.
    rt._register('author', RouterProposal('Tick', 'exp3', .1))
    assert len(rt.routers['Tick']) == 1 and rt.reserve.remaining() < remaining
