"""Regressions from the seven-tick rehearsal's terminal executions."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.cortex.request import Return
from factorylab.runtime.live import LiveClock
from factorylab.runtime.propensity import action_label, declared_record
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


def test_label_is_not_silently_executed_or_sized_and_invalid_side_cannot_sell():
    rt = make_runtime()
    for out in ({"action": "buy:ETH:xs"},
                {"action": "order", "coin": "ETH", "side": "typo", "size": "0.01"}):
        h = _producing_decision(rt)
        rt._execute_outputs(Return(h, out, 0, "ok"))
    assert action_label("producer", {"action": "buy:ETH:xs"}, "ok") == "malformed"
    assert not rt.order_intents
    assert len(rt.registration_feedback) == 2
    assert "explicit coin" in rt.registration_feedback[0]["reason"]
    assert "side must" in rt.registration_feedback[1]["reason"]


@pytest.mark.parametrize("role,outputs,declared,expected,mass", [
    ("producer", {"action": "order", "side": "buy", "coin": "ETH", "size": "0.005"},
     {"buy:eth:xs": 0.7, "hold": 0.3}, "buy:ETH:xs", 0.7),
    ("evaluator", {"verdict": 0.25}, {"verdict:0.25": 0.6, "verdict:0.2": 0.4},
     "verdict:0.2", 1.0),
    ("meta", {"conformity": 0.75}, {"conformity:0.75": 1.0}, "conformity:0.8", 1.0),
])
def test_published_action_and_declared_distribution_share_canonical_bins(
        role, outputs, declared, expected, mass):
    label = action_label(role, outputs, "ok")
    record, reason = declared_record(label, declared, learner_id="test", state_hash="test")
    assert label == expected and reason is None
    assert record.probs[record.action_ids.index(expected)] == mass


def test_runtime_does_not_poll_fills_twice_per_tick():
    from factorylab.runtime.live import LiveVenue

    def forbidden(*args):
        raise AssertionError("runtime owns the inclusive fill cursor")

    exchange = SimpleNamespace(name="fake", mids=lambda: {}, funding=lambda: [],
                               fills=forbidden, funding_payments=lambda *args: [])
    venue = LiveVenue(exchange, last_fill_ns=0, last_funding_ns=0)
    assert venue.on_tick(1, include_fills=False) == []


def test_terminal_failed_fill_read_is_visible_and_still_seals(monkeypatch):
    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))

    def unavailable(*args):
        raise RuntimeError("sensitive provider response")

    monkeypatch.setattr(rt.exchange.target, "fills", unavailable)
    rt._finish_budget()
    summary = rt._summary()
    assert summary["execution"]["terminal_reconciliation"]["fill_read_error"] == "RuntimeError"
    assert summary["seal_key_released"]


def test_trading_sdk_receives_the_same_bounded_timeout_as_reads(monkeypatch):
    import eth_account
    import hyperliquid.exchange
    import hyperliquid.info

    from factorylab.world.exchange import HyperliquidExchange

    calls = []

    def info(*args, **kwargs):
        calls.append(("read", kwargs["timeout"]))
        return SimpleNamespace(meta=lambda: {"universe": []},
                               spot_meta=lambda: {"tokens": [], "universe": []})

    def trading(*args, **kwargs):
        calls.append(("trade", kwargs["timeout"]))
        return SimpleNamespace()

    monkeypatch.setenv("HL_PRIVATE_KEY", "synthetic-fixture-not-a-key")
    monkeypatch.setattr(eth_account.Account, "from_key", lambda _: SimpleNamespace(address="fake"))
    monkeypatch.setattr(hyperliquid.info, "Info", info)
    monkeypatch.setattr(hyperliquid.exchange, "Exchange", trading)
    HyperliquidExchange(timeout=3.0)
    assert calls == [("read", 3.0), ("trade", 3.0)]


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



def test_session_order_cap_is_removed_from_runtime_and_manifest():
    from dataclasses import fields

    from factorylab.runtime.worlds import ExchangeSpec

    rt = make_runtime()
    assert not hasattr(rt, '_execution_limit')
    assert not {'max_slippage_bps', 'max_order_notional_micro',
                'max_gross_notional_micro'} & {f.name for f in fields(ExchangeSpec)}
    h = _producing_decision(rt)
    result = rt._venue_write(h, 'venue.place_market', {
        'coin': 'BTC', 'side': 'buy', 'size': '.001'}, slot='output')
    assert result['status'] == 'filled'  # $60 fixture order exceeds the removed $20 cap.


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


def test_the_kill_wind_downs_reconciliation_still_reads_the_venue_for_it(monkeypatch):
    """The bound is a polling schedule, not a decision to stop caring: a dying runtime
    reads the venue once more for an order whose identity it never confirmed."""
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
    assert _order_rows(rt, "order.unresolved")
    spent = len(lookups)
    rt._finish_budget()
    assert len(lookups) == spent + 1
    assert rt._summary()["execution"]["statuses"]["uncertain"] == 1


def _uncertain_venue(rt, monkeypatch, lookups=None):
    """A venue that loses the submit and then never reports the order."""
    exchange = rt.exchange.target
    monkeypatch.setattr(exchange, "place",
                        lambda order: OrderResult(None, "uncertain", Decimal(0), None,
                                                  "submit timeout"))
    monkeypatch.setattr(exchange, "lookup",
                        lambda *a, **kw: ((lookups.append(a) if lookups is not None else None)
                                          or OrderResult(None, "uncertain", Decimal(0), None,
                                                         "order not observed")))
    return exchange


def _held_position(rt, handle, order_id="opener"):
    """Give a return an open perp lot, so a later close has a P&L to realise."""
    rt.consequences.order_result(
        handle, {"status": "filled", "order_id": order_id, "filled_size": "0.001"},
        {"size": "0.001"}, rt.n)
    rt.consequences.observe("Fill", {"order_id": order_id, "coin": "BTC", "is_buy": True,
                                     "size": "0.001", "px": "60000", "fee_usd": "0"}, rt.n)


def test_an_unresolved_order_releases_the_hold_as_a_censored_unknown_outcome(monkeypatch):
    """R4-C: what PR #105 left undecided. An order whose fill status the venue will not
    confirm gives its return no payoff anybody can be right or wrong about, so the
    return's ``return_paid_off`` settles censored for documented external
    unobservability -- no eligible sample, no standing, an ``unknown`` outcome for its
    owner -- and the hold on ``pending_orders`` is released, so every later return's
    outcome resolves again. The exposure survives the release: the coin stays shut to
    that seat while the intent reads uncertain, and a fill the venue finally admits to
    is still this return's money, late."""
    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    exchange = _uncertain_venue(rt, monkeypatch)
    h = _producing_decision(rt)
    _held_position(rt, h)
    rt.consequences.finish(h, 1_000)
    rt._venue_write(h, "venue.place_market", {"coin": "BTC", "side": "sell", "size": "0.001"},
                    slot="output")
    assert rt.consequences.pending_orders  # the hold #105 could not lift
    for _ in range(50):
        rt._reconcile_orders()

    released = _order_rows(rt, "order.unresolved_released")
    assert len(released) == 1
    assert released[0] == {"kind": "order.unresolved_released", "handle": h, "coin": "BTC",
                           "client_id": h, "reason": "external_unobservable",
                           **{k: v for k, v in released[0].items()
                              if k not in ("kind", "handle", "coin", "client_id", "reason")}}
    assert not rt.consequences.pending_orders

    # The outcome exists, carries its documented reason, and is nobody's payoff.
    payoff = rt.consequences.payoff(h)
    assert payoff is not None and payoff.censored == "external_unobservable"
    assert not payoff.marked and payoff.net_micro == 0
    assert rt.consequences.counts()["censored_outcomes"] == 1

    # And the returns that were queued behind the hold resolve normally again.
    other = _producing_decision(rt)
    rt.consequences.finish(other, 1_000)
    fixed = rt.consequences.resolve(rt.n)
    assert {p.handle for p in fixed} == {h, other}
    for p in fixed:
        rt._credit_consequence(p)
    items = [rt.outcomes.body(i["sha"]) for i in rt.outcomes.items["seed-decider"]
             if i["handle"] == h]
    assert len(items) == 1 and items[0]["outcome"]["outcome"] == "unknown"
    assert items[0]["outcome"]["return_paid_off"] is None
    assert items[0]["outcome"]["reason"] == "external_unobservable"
    # The venue's last answer rides with it, so the seat reads a fact, not a silence.
    assert items[0]["outcome"]["venue_answer"]["result"]["error"] == "order not observed"
    assert items[0]["delta_micro"] == 0  # a censored outcome moves no money

    # The coin stays shut to that seat while the venue has still said nothing.
    refused = rt._venue_write(other, "venue.place_market",
                              {"coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert refused["status"] == "rejected"
    assert "still uncertain" in refused["error"]

    # The venue finally admits to the order: the fill is still this return's, and its
    # money reaches the same seat late rather than never.
    monkeypatch.setattr(exchange, "lookup",
                        lambda *a, **kw: OrderResult("late-1", "filled", Decimal("0.001"),
                                                     Decimal("61000")))
    rt._recover_order(h)
    assert any(o.order_id == "late-1" and o.handle == h for o in rt.consequences.table.orders)
    rt.consequences.observe("Fill", {"order_id": "late-1", "coin": "BTC", "is_buy": False,
                                     "size": "0.001", "px": "61000", "fee_usd": "0"}, rt.n)
    rt._settle_late()
    late = [b for b in (rt.outcomes.body(i["sha"]) for i in rt.outcomes.items["seed-decider"]
                        if i["handle"] == h)
            if "late_realization_micro" in b["outcome"]]
    assert len(late) == 1 and late[0]["outcome"]["late_realization_micro"] == 1_000_000
    # The censored outcome itself is never reopened; only the money moved.
    assert rt.consequences.payoff(h) is payoff
