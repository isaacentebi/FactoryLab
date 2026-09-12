"""Cold-audit regressions use only synthetic venues, models and credentials."""

from dataclasses import replace
from types import SimpleNamespace

from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.live import LiveClock
from factorylab.runtime.loop import Runtime, ScriptedProvider
from factorylab.runtime.worlds import load_manifest
from factorylab.world.events import WorldEvent, WorldEventKind
from factorylab.world.exchange import FakeExchange


def make_runtime(*, balance=100_000_000, live=False, clock_source=None):
    manifest = load_manifest("scripted")
    if live:
        manifest = replace(manifest, exchange=replace(manifest.exchange, kind="hyperliquid"),
                           drip=None)
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=balance,
                   ledger_path=None, drip=False, router_gamma=.1,
                   exchange=FakeExchange(), provider=ScriptedProvider(),
                   clock_source=clock_source)


def test_both_live_fill_cursors_and_launch_snapshot_start_at_launch():
    rt = make_runtime(live=True, clock_source=LiveClock(10, 0, now_ns=lambda: 12345))
    assert rt.consequence_fills.since_ns == rt.venue.last_fill_ns == 12345
    assert rt.venue.last_funding_ns == 12345
    rt.run()
    items = rt.ledger._recovery_items()
    launch = next(i for i in items if i["kind"] == "event" and i["event"]["kind"] == "Launch")
    assert launch["event"]["ts_ns"] == 12345
    snapshot = next(i for i in items if i["kind"] == "snapshot")
    assert snapshot["state"]["clock_ns"] == 12345


def test_replacement_router_does_not_train_on_a_retired_learner_return():
    rt = make_runtime()
    old = rt.routers["Tick"][0]
    action = next(a for a in old.universe if a != "NOOP")
    handle = rt.queue.open(
        actor=old.learner.id, event_id="test",
        propensity=PropensityRecord((action,), (1.0,), action, 1, old.learner.id, "state"),
        channel="test", deadline_ns=100, parent_handle=None, cost_ceiling=0,
    )
    fresh = rt._build_router("Tick", "exp3", .1)
    before = fresh.learner.state()
    rt.queue.settle(handle, channel="test", score=1.0, status=SettleStatus.SETTLED,
                    definition_version="1", sampling_ref=None)
    rt._deliver_returns()
    assert before == fresh.learner.state()
    assert old.learner.id != fresh.learner.id
    assert rt.queue.history(handle)[0].score == 1.0
    again = rt._build_router("Tick", "exp3", .1)
    assert len({old.learner.id, fresh.learner.id, again.learner.id}) == 3


def test_swap_epoch_preserves_immune_gamma_and_retires_the_old_identity(monkeypatch):
    from factorylab.runtime.immune import _gain, gamma

    rt = make_runtime()
    old = rt._build_router("Tick", "blum_mansour", .2)
    _gain(rt, "stable_failure", 1)
    adjusted = gamma(old.learner)
    assert adjusted > old.seed_gamma
    monkeypatch.setattr(rt, "_universe_for", lambda _: [*old.universe, "new-action"])
    rt._open_epoch("Tick")
    fresh = rt.routers["Tick"][0]
    assert fresh.learner.id != old.learner.id
    assert gamma(fresh.learner) == adjusted and fresh.seed_gamma == .2
    assert fresh.epoch == old.epoch + 1 and rt.delivered_seen[fresh.learner.id] == 0
    assert any(item["kind"] == "actor.retire" and item["actor"] == old.learner.id
               for item in rt.ledger._recovery_items())


def test_unaffordable_committee_vote_counts_toward_insolvency(monkeypatch):
    rt = make_runtime(balance=5)
    monkeypatch.setattr(rt.charter_book, "abstain", lambda *_: None)
    monkeypatch.setattr(rt.charter_book, "tally", lambda *_: "failed")
    am = SimpleNamespace(id="test", proposed_prices=(), add=(), replace=(), remove=(),
                         predicted_effect="", tick_interval=None)
    committee = SimpleNamespace(seats=[("seat", next(iter(rt.assemblies)))])
    rt._hold_vote(am, committee)
    assert rt._compute_routed and rt._compute_unaffordable
    rt._record_insolvency_event(SimpleNamespace(id="vote-event"))
    assert rt.insolvency_count == 1


def test_fatal_fill_does_not_drop_later_fills_or_funding():
    rt = make_runtime(balance=10)
    events = [WorldEvent(WorldEventKind.FILL, 1, "test", {
        "order_id": str(i), "coin": "BTC", "is_buy": True, "size": "1", "px": "1",
        "fee_usd": "0.000020", "realized_usd": "0",
    }) for i in range(2)]
    events.append(WorldEvent(WorldEventKind.FUNDING, 1, "test", {
        "coin": "BTC", "paid_usd": "0.000005",
    }))
    rt._settle_exchange_effects(events)
    assert rt.wallet.balance == -35
    assert rt.stats.fills == 2 and rt.funding_to_date == -5
    assert rt.window.fills == 2 and rt.window.notional_micro == 2_000_000
    assert rt.fees_to_date == 40 and rt.realized_to_date == 0
    assert [str(event.kind) for event in rt.internal] == ["Fill", "Fill", "Funding"]
    evidence = rt.ledger._recovery_items()
    settlements = [item for item in evidence if item["kind"] == "wallet.settle"]
    assert [item["amount"] for item in settlements] == [-20, -20, -5]
    assert [item["balance_after"] for item in settlements] == [-10, -30, -35]
    assert sum(item["kind"] == "consequence.fill" for item in evidence) == 2
    assert sum(item["kind"] == "consequence.funding" for item in evidence) == 1
    assert rt.wallet.dead and rt.wallet.check_conservation()
    assert rt._check_termination()


def test_public_population_tool_is_debited_to_the_caller(monkeypatch):
    from factorylab.cortex.tools import PopulationTool, as_spec

    rt = make_runtime()
    tool = PopulationTool("shared-tool", "test", {"type": "object", "properties": {}},
                          'print("{}")', 1, "author-handle")
    rt.population_tools[tool.id] = tool
    rt.tool_specs[tool.id] = as_spec(tool, 50)
    rt.tool_owner[tool.id] = "different-author"
    monkeypatch.setattr(rt.tool_runner.target, "run", lambda *_: {"ok": True})
    before = rt.wallet.balance
    output, cost = rt._run_tool("seed-decider", "caller-handle", {"tool": tool.id, "args": {}})
    assert output == {"ok": True} and cost == 50 and rt.wallet.balance == before - 50
    commits = [i for i in rt.ledger._recovery_items() if i["kind"] == "wallet.commit"]
    assert commits[-1]["handle"] == "caller-handle"
    assert tool.id in rt._allowed_tools("different-author")
