"""Bounded edition 4 rehearsal admission and provenance checks."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from factorylab.runtime.live import LiveClock
from factorylab.runtime.worlds import load_manifest
from factorylab.world.evm import RailError
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelRequest, ModelResponse
from scripts import edition4_rehearsal as rehearsal

WORLD = "worlds/edition3-rehearsal-5.toml"


@dataclass
class StubProvider:
    response: ModelResponse

    def affordable(self, _model_id, _ceiling):
        return True, ""

    def complete(self, _request):
        return self.response

    def balance_of(self, _model_id):
        return None

    def balance_micro(self):
        return None


def request():
    return ModelRequest("openai/gpt-5.6-luna", "", ({"role": "user", "content": "x"},), 1)


def test_effective_manifest_preserves_roster_charter_and_endowment():
    original = load_manifest(WORLD)
    effective = rehearsal.effective_manifest(original)

    assert rehearsal.roster_hash(original) == rehearsal.roster_hash(effective)
    assert original.charter == effective.charter
    assert original.initial_balance_micro == effective.initial_balance_micro == 300_000_000
    assert original.exchange.start_cash_usd == effective.exchange.start_cash_usd == "120"
    assert original.endowment == effective.endowment
    assert effective.tick_interval_ns == rehearsal.SHORT_TICK_NS
    assert effective.exchange.client_namespace != original.exchange.client_namespace
    assert effective.exchange.mainnet is False
    assert effective.treasury.reserve_address is None
    assert effective.treasury.cctp_forwarding == "never"
    assert effective.kill.wind_down is original.kill.wind_down is True


def test_admission_counts_attempts_and_stops_on_overrun_or_unknown_bill():
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))
    probe = rehearsal.PrepaidProvider(
        StubProvider(ModelResponse(request().model_id, "{}", 1, 1, "stop", cost_micro=10_000)),
        manifest,
        rehearsal.Admission(cap_micro=1_000_000, max_calls=3),
    )
    ceiling = probe._ceiling(request())
    result = probe.complete(request())
    assert result.cost_micro == 10_000
    assert probe.admission.attempted == 1
    assert probe.admission.overruns == 1
    assert probe.admission.stop_reason == "reported_overrun"
    assert probe.admission.report()["known_micro"] == 10_000
    assert ceiling < 10_000
    with pytest.raises(rehearsal.RehearsalRefused):
        probe.complete(request())

    unknown = rehearsal.PrepaidProvider(
        StubProvider(ModelResponse(request().model_id, "{}", 1, 1, "stop")),
        manifest,
        rehearsal.Admission(cap_micro=1_000_000, max_calls=3),
    )
    unknown.complete(request())
    assert unknown.admission.unknown_bills == 1
    assert unknown.admission.uncertain_micro == unknown._ceiling(request())
    assert unknown.admission.stop_reason == "unknown_bill"

    table = rehearsal.PrepaidProvider(
        StubProvider(ModelResponse(
            request().model_id, "{}", 1, 1, "stop", raw={"cost_source": "table"}, cost_micro=10
        )),
        manifest,
        rehearsal.Admission(cap_micro=1_000_000, max_calls=3),
    )
    table.complete(request())
    assert table.admission.report()["known_micro"] == 0
    assert table.admission.report()["uncertain_micro"] == max(10, table._ceiling(request()))
    assert table.admission.stop_reason == "non_authoritative_table_cost"


def test_every_treasury_direction_is_refused_before_prepare():
    rail = rehearsal.DeniedTransferRail(object())
    for direction in ("to_reserve", "to_venue", "to_venice", "spot_to_perps", "perps_to_spot"):
        with pytest.raises(RailError, match="treasury rail denied"):
            rail.preflight(direction, 1, {})


def test_runner_report_records_effective_manifest_and_uses_denied_market(monkeypatch, tmp_path):
    manifest_seen = {}
    market_seen = {}

    class FakeLedger:
        def _recovery_items(self):
            return [{"kind": "Launch"}, {"kind": "snapshot", "state": "omitted"}]

    class FakeRuntime:
        def __init__(self, manifest, **kwargs):
            manifest_seen["value"] = manifest
            market_seen["value"] = kwargs["market"]
            assert kwargs["kill_at_end"] is True
            self.ledger = FakeLedger()
            self.ticks_consumed = 1
            self.exchange = FakeExchange(
                seed=1, coins=("BTC", "ETH"), spot_pairs=(), start_cash_usd="120"
            )
            self.treasury = type("Treasury", (), {
                "rail": type("RailProxy", (), {
                    "target": type("Target", (), {})(),
                })(),
            })()

        def run(self):
            return {"terminated": True, "termination_reason": "explicit_kill:budget",
                    "stats": {"events": 1}}

    monkeypatch.setattr(rehearsal, "Runtime", FakeRuntime)
    provider = StubProvider(ModelResponse("openai/gpt-5.6-luna", "{}", 1, 1, "stop", cost_micro=1))
    out = tmp_path / "report.json"
    report = rehearsal.run_rehearsal(
        WORLD,
        out=out,
        duration_ns=rehearsal.SHORT_TICK_NS,
        cap_micro=10_000,
        max_calls=2,
        provider=provider,
        source_root="/Users/isaacentebi/Desktop/FactoryLab",
    )

    assert report["status"] == "completed"
    assert (out / "report.json").exists()
    assert (out / "events.json").exists()
    assert manifest_seen["value"].tick_interval_ns == rehearsal.SHORT_TICK_NS
    assert isinstance(market_seen["value"], rehearsal.DeniedMarket)
    assert report["cost"]["attempted"] == 0
    assert "x402" in report["denied_rails"]
    assert (
        report["preserved"]["roster_sha256"]["from"]
        == report["preserved"]["roster_sha256"]["to"]
    )


@pytest.mark.gate
def test_runner_consumes_injected_clock_and_persists_dead_diary(tmp_path):
    provider = StubProvider(
        ModelResponse("openai/gpt-5.6-luna", "{}", 1, 1, "stop", cost_micro=1)
    )
    exchange = FakeExchange(
        seed=1, coins=("BTC", "ETH"), spot_pairs=(), start_cash_usd="120"
    )
    clock = LiveClock(
        rehearsal.SHORT_TICK_NS, 1, now_ns=lambda: 1_000_000_000, sleep=lambda _seconds: None
    )
    report = rehearsal.run_rehearsal(
        WORLD,
        out=tmp_path / "run",
        duration_ns=rehearsal.SHORT_TICK_NS,
        provider=provider,
        exchange=exchange,
        clock_source=clock,
        source_root=Path(rehearsal.__file__).resolve().parents[1],
        observe=True,
    )

    assert report["status"] == "completed"
    assert report["timing"]["ticks"] == 1
    assert (tmp_path / "run" / "ledger.jsonl").exists()
    assert (tmp_path / "run" / "events.json").exists()
    observer = json.loads((tmp_path / "run" / "observer" / "report.json").read_text())
    assert observer["observer"]["ticks"] == report["timing"]["ticks"]
    assert observer["costs"]["basis"] == "admission_report"
    assert report["observer"]["scope"] == "rehearsal_only_recent_evidence_window"
