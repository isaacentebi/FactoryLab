"""Regressions from the seven-tick rehearsal's terminal executions."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.runtime.live import LiveClock
from factorylab.world.exchange import OrderResult
from tests.audit.test_r3_b_authority import _producing_decision
from tests.conftest import make_runtime


def test_terminal_reconciliation_counts_last_fill_without_another_model_or_submission():
    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    h = _producing_decision(rt)
    result = rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert result["status"] == "filled"
    assert rt.stats.fills == 0
    invocations = rt.stats.invocations
    rt._finish_budget()
    summary = rt._summary()
    assert summary["execution"]["statuses"]["filled"] == 1
    assert summary["execution"]["polled_fills"] == 1
    assert rt.stats.invocations == invocations
    assert summary["wallet_conservation"] and summary["ledger_verify"]
    assert summary["terminated"] and summary["seal_key_released"]
    assert rt.consequence_fills.poll(rt.exchange.target) == []


def test_terminal_timeout_is_reconciled_once_without_resubmitting(monkeypatch):
    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    exchange = rt.exchange.target
    submissions = []
    lookups = []

    def submit(order):
        submissions.append(order)
        return OrderResult(None, "uncertain", Decimal(0), None, "submit timeout")

    def lookup(*args, **kwargs):
        lookups.append(args)
        return OrderResult(None, "uncertain", Decimal(0), None, "not observed")

    monkeypatch.setattr(exchange, "place", submit)
    monkeypatch.setattr(exchange, "lookup", lookup)
    h = _producing_decision(rt)
    rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    before = len(lookups)
    rt._finish_budget()
    assert len(submissions) == 1 and len(lookups) == before + 1
    assert rt._summary()["execution"]["statuses"]["uncertain"] == 1


@pytest.mark.parametrize("cut_kind", ["runtime.finish_budget", "consequence.fill_cursor",
                                      "fill.counted", "venue.terminal_reconciliation"])
def test_interruption_during_final_reconciliation_replays_once(tmp_path, cut_kind):
    from dataclasses import replace

    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import resume_runtime
    from factorylab.runtime.worlds import load_manifest
    from factorylab.world.exchange import FakeExchange
    from factorylab.world.scripted import ScriptedProvider

    manifest = load_manifest("scripted")
    manifest = replace(manifest, exchange=replace(manifest.exchange, kind="hyperliquid"), drip=None)
    path = str(tmp_path / "terminal.jsonl")
    exchange = FakeExchange()
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                 ledger_path=path, drip=False, router_gamma=.1, exchange=exchange,
                 provider=ScriptedProvider(), clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    rt.ledger.active = True
    rt._launch()
    h = _producing_decision(rt)
    rt._venue_write(h, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert rt._snapshot("before_terminal")
    append = rt.ledger.append

    class Interrupted(BaseException):
        pass

    def interrupt(item):
        result = append(item)
        if item["kind"] == cut_kind:
            raise Interrupted()
        return result

    rt.ledger.append = interrupt
    with pytest.raises(Interrupted):
        rt._finish_budget()
    rt._ledger_lock.close()
    restored = resume_runtime(manifest, path, exchange=exchange, provider=ScriptedProvider(),
                              now_ns=1)
    try:
        assert restored.termination.final
        assert restored.stats.fills == 1
        assert restored.wallet.check_conservation()
        entries = list(restored.ledger._recovery_items())
        assert sum(i["kind"] == "fill.counted" for i in entries) == 1
    finally:
        restored._ledger_lock.close()


def test_order_status_only_looks_up_original_identities(monkeypatch, capsys):
    import json

    from factorylab.runtime.cli import _cmd_order_status
    from factorylab.world import exchange

    calls = []

    def lookup(identity):
        calls.append(identity)
        return OrderResult(None, "uncertain", Decimal(0), None, "not observed")

    nonces = []

    def live_exchange(spec, *, launch_nonce=None):
        nonces.append(launch_nonce)
        return SimpleNamespace(lookup=lookup)

    monkeypatch.setattr(exchange, "live_exchange", live_exchange)
    _cmd_order_status(SimpleNamespace(world="testnet", client_id=["decision-172"],
                                      launch_nonce=None))
    report = json.loads(capsys.readouterr().out)
    assert calls == ["decision-172"] and report["read_only"]
    assert report["orders"]["decision-172"]["status"] == "uncertain"
    # A world that launched before launch-bound identities keeps its original derivation.
    assert nonces == [None] and report["launch_nonce"] is None


def test_existing_order_identity_does_not_fetch_or_submit_again():
    from factorylab.world.exchange import HyperliquidExchange, Order, OrderResult

    ex = HyperliquidExchange.__new__(HyperliquidExchange)
    ex._exchange = object()
    filled = OrderResult('123', 'filled', Decimal('.001'), Decimal('60000'))
    ex._client_results = {'original': filled}
    ex.mids = lambda: pytest.fail('a completed identity must not fetch a quote')
    assert ex.place(Order('BTC', True, Decimal('.001'), client_id='original')) == filled
    assert ex.close('BTC', client_id='original') == filled


def _order_rows(rt, kind: str) -> list[dict]:
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_an_uncertain_order_is_polled_on_a_bounded_schedule_then_left_unresolved(monkeypatch):
    """Rehearsal 5: one order (a ReadTimeout on submit) was re-ledgered "order not
    observed" on every later poll — 100 rows for one intent over 150 ticks. The venue is
    asked a bounded number of times, each answer is ledgered once, and then the runtime
    says so and stops asking."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    exchange = rt.exchange.target
    lookups = []
    monkeypatch.setattr(exchange, "place",
                        lambda order: OrderResult(None, "uncertain", Decimal(0), None,
                                                  "submit timeout"))
    monkeypatch.setattr(exchange, "lookup",
                        lambda *a, **kw: (lookups.append(a)
                                          or OrderResult(None, "uncertain", Decimal(0), None,
                                                         "order not observed")))
    h = _producing_decision(rt)
    rt._venue_write(h, "venue.place_market", {"coin": "BTC", "side": "buy", "size": "0.001"},
                    slot="output")
    for _ in range(50):
        rt._reconcile_orders()
    uncertain = _order_rows(rt, "order.uncertain")
    unresolved = _order_rows(rt, "order.unresolved")
    assert len(uncertain) == UNCERTAIN_ORDER_POLLS
    assert len(unresolved) == 1 and unresolved[0]["handle"] == h
    assert len(uncertain) + len(unresolved) == UNCERTAIN_ORDER_POLLS + 1
    # The schedule is the polling, not the bookkeeping: the venue was asked once per row.
    assert len(lookups) == UNCERTAIN_ORDER_POLLS - 1  # the submit's own answer is the first
    # And a repeat of the identical write never resubmits or re-asks past the bound.
    again = rt._venue_write(h, "venue.place_market",
                            {"coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert again["status"] == "uncertain"
    assert len(_order_rows(rt, "order.uncertain")) == UNCERTAIN_ORDER_POLLS
    assert len(_order_rows(rt, "order.unresolved")) == 1
