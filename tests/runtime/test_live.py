from decimal import Decimal

from factorylab.runtime.live import LiveClock, LiveVenue, Reconciler, build_provider
from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from factorylab.world.events import WorldEventKind
from factorylab.world.exchange import AccountState, Fill, FundingEvent, Order, OrderResult
from tests.seed_charter import seed_charter_table


class FakeTime:
    def __init__(self) -> None:
        self.t = 1_000_000_000_000
        self.slept: list[float] = []

    def now_ns(self) -> int:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += int(s * 1_000_000_000)


def test_live_clock_paces_against_wall_clock_without_real_sleep() -> None:
    ft = FakeTime()
    clock = LiveClock(interval_ns=2_000_000_000, count=3, now_ns=ft.now_ns, sleep=ft.sleep)
    evs = list(clock.events())
    assert [e.kind for e in evs] == [WorldEventKind.TICK] * 3
    assert evs[1].ts_ns - evs[0].ts_ns == 2_000_000_000
    assert ft.slept == [2.0, 2.0]


def test_restored_live_clock_keeps_indices_budget_and_outage_time():
    import json

    ft = FakeTime()
    clock = LiveClock(2_000_000_000, 4, now_ns=ft.now_ns, sleep=ft.sleep)
    stream = clock.events()
    next(stream)
    last = next(stream)
    saved = json.loads(json.dumps(clock.state()))
    ft.t += 100_000_000_000
    restored = LiveClock.restore(saved, now_ns=ft.now_ns, sleep=ft.sleep)
    events = list(restored.events())
    assert [e.payload["index"] for e in events] == [2, 3]
    assert events[0].ts_ns == last.ts_ns + 100_000_000_000
    assert events[1].ts_ns == events[0].ts_ns + 2_000_000_000


class StubExchange:
    """A read-only live venue stand-in with two fills appearing on the second tick."""

    name = "stub-testnet"

    def __init__(self, launch_ns=0) -> None:
        self.launch_ns = launch_ns
        self.calls = 0

    def mids(self):
        self.calls += 1
        return {"BTC": Decimal("70000") + self.calls, "ETH": Decimal("2500")}

    def funding(self):
        return [FundingEvent("BTC", Decimal("0.0001"), Decimal("0.001"), 0)]

    def account(self):
        return AccountState(Decimal("100"), Decimal("100"), (), Decimal(0))

    def instruments(self):
        return {"perp": [{"coin": "BTC", "lot_size": "0.00001", "tick_size": "0.1"},
                         {"coin": "ETH", "lot_size": "0.0001", "tick_size": "0.01"}],
                "spot": []}

    def place(self, order: Order) -> OrderResult:
        return OrderResult(None, "rejected", Decimal(0), None, "no signing key")

    def cancel(self, order_id: str) -> None:
        pass

    def fills(self, since_ns: int):
        if self.calls < 2:
            return []
        return [
            Fill(
                "f1",
                "BTC",
                True,
                Decimal("0.001"),
                Decimal("70000"),
                Decimal("0.02"),
                self.launch_ns + 5,
                Decimal("0"),
            ),
            Fill(
                "f2",
                "BTC",
                False,
                Decimal("0.001"),
                Decimal("70100"),
                Decimal("0.02"),
                self.launch_ns + 6,
                Decimal("0.1"),
            ),
        ]


def test_live_venue_emits_mids_funding_and_new_fills_once() -> None:
    v = LiveVenue(StubExchange(), last_fill_ns=0)
    first = v.on_tick(10)
    kinds = [e.kind for e in first]
    assert kinds.count(WorldEventKind.MARKET_MID) == 2 and WorldEventKind.FUNDING in kinds
    assert WorldEventKind.FILL not in kinds
    second = v.on_tick(20)
    fills = [e for e in second if e.kind is WorldEventKind.FILL]
    assert len(fills) == 2 and fills[1].payload["realized_usd"] == "0.1"
    third = v.on_tick(30)
    assert not [e for e in third if e.kind is WorldEventKind.FILL]  # not re-emitted


def test_reconciler_snapshot_never_compares_authority_with_money() -> None:
    """The compute wallet is authority, not cash: the snapshot reports it beside the pots
    and never as a drift against them (the $300-against-$1,020 false alarm)."""
    from factorylab.kernel.ledger import Ledger

    class P:
        def balance_micro(self):
            return 40_000_000

    ledger = Ledger()
    snap = Reconciler.snapshot(100_000_000, P(), StubExchange(), ledger=ledger)
    assert snap["openrouter_remaining_micro"] == 40_000_000
    assert snap["venue_equity_usd"] == "100"
    assert snap["pots_micro"] == 140_000_000 and snap["wallet_micro"] == 100_000_000
    assert "discrepancy_micro" not in snap and "within_tolerance" not in snap
    assert not [i for i in ledger.items() if i.get("kind") == "reconcile.drift"]


def test_build_provider_needs_key_for_openrouter(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    m = load_manifest("testnet")
    try:
        build_provider(m)
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a RuntimeError without the key")
    assert build_provider(load_manifest("scripted")) is None


def test_build_provider_hands_each_adapter_the_manifests_schema_routes(monkeypatch) -> None:
    """A route's contract reaches the adapter that sends it (models.contract, §II.b)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("VENICE_API_KEY", "test-key-not-real")
    manifest = load_manifest("edition6-testnet-rehearsal")
    provider = build_provider(manifest)
    assert provider.openrouter._schema_models == manifest.schema_contract_models()
    assert provider.venice._schema_models == manifest.schema_contract_models()
    assert "openai/gpt-6-luna" not in provider.openrouter._schema_models
    assert build_provider(load_manifest("testnet")).openrouter._schema_models == frozenset()


def test_runtime_runs_a_live_shaped_world_with_stub_venue_and_scripted_models() -> None:
    d = {
        "name": "stubnet",
        "seed": 5,
        "initial_balance_usd": "20",
        "tick_interval": "1s",
        "exchange": {"kind": "hyperliquid", "mainnet": False, "coins": ["BTC", "ETH"]},
        # Three fake families: a world that seeds judging holds the evaluator population
        # Chapter II requires, and no judge reads its author's family (Wave 5a).
        "models": [
            {
                "id": model_id,
                "provider": "fake",
                "input_usd_per_mtok": "1",
                "output_usd_per_mtok": "5",
            }
            for model_id in ("fake-haiku", "fake-opus", "fake-sonnet", "fake-gemini")
        ],
        "assemblies": [
            {
                "id": "seed-decider",
                "role": "producer",
                "model_id": "fake-haiku",
                "accepts": ["Tick", "Fill"],
            },
            {
                "id": "eval-a",
                "role": "evaluator",
                "model_id": "fake-opus",
                "accepts": ["ProducerReturn"],
            },
            {
                "id": "eval-b",
                "role": "evaluator",
                "model_id": "fake-sonnet",
                "accepts": ["ProducerReturn"],
            },
            # Off every (judge, producer) chain's families (the #132 review, item 3).
            {"id": "meta-a", "role": "meta", "model_id": "fake-gemini", "accepts": ["Verdict"]},
        ],
        "novelty": {"share": 0.1},
        "charter": seed_charter_table(),
        "immune": {"price_step": 0.05},
    }
    m = manifest_from_dict(d)
    ft = FakeTime()
    clock = LiveClock(
        interval_ns=1_000_000_000, count=25, now_ns=ft.now_ns, sleep=ft.sleep
    ).events()
    s = run_world(m, events=25, seed=5, exchange=StubExchange(ft.now_ns()), clock_source=clock)
    st = s["stats"]
    assert s["live"] is True and s["terminated"] is False
    assert st["reconciliations"] >= 2  # every 10 ticks
    assert st["fills"] == 2 and st["orders_rejected"] == st["orders_placed"]
    assert st["verdicts"] >= 1 and st["forecasts_sealed"] >= 1
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
