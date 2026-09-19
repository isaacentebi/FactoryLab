"""Bounded edition 4 rehearsal admission and provenance checks."""

import json
from dataclasses import dataclass, replace
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


def test_report_mean_is_exact_and_distinguishes_free_calls_from_missing_bills():
    admission = rehearsal.Admission(cap_micro=100, max_calls=5)
    assert admission.report()["known_mean_micro"] is None
    for cost in (0, 1, 1):
        admission.attempted_call()
        admission.observe(ModelResponse(request().model_id, "{}", 1, 1, "stop",
                                        cost_micro=cost), 100)
        if cost == 0:
            assert admission.report()["known_mean_micro"] == "0"
    assert admission.report()["known_mean_micro"] == "2/3"
    admission.attempted_call()
    admission.observe(ModelResponse(request().model_id, "{}", 1, 1, "stop",
                                    cost_micro=None), 10)
    report = admission.report()
    assert report["known_calls"] == 3 and report["uncertain_calls"] == 1
    assert report["known_mean_micro"] == "2/3"


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


def test_launch_factors_are_explicit_and_reasoning_changes_roster_with_provenance():
    original = load_manifest(WORLD)
    factored = rehearsal.effective_manifest(
        original,
        prompt_mode="compact",
        producer_feedback="realized",
        address_enabled=True,
        reasoning="off",
    )

    assert factored.prompt.mode == "compact"
    assert factored.evaluation.producer_feedback == "realized"
    assert factored.tools.address_enabled is True
    assert all(dict(model.reasoning) == {"enabled": False} for model in factored.models)
    assert factored.charter.norms == original.charter.norms
    assert factored.assemblies == original.assemblies
    assert rehearsal.roster_hash(factored) != rehearsal.roster_hash(original)

    enabled = rehearsal.effective_manifest(original, reasoning="on")
    assert dict(enabled.models[0].reasoning) == {"enabled": True}
    assert dict(enabled.models[1].reasoning) == {"effort": "low"}
    declared_on = replace(
        original,
        models=tuple(replace(model, reasoning=(("effort", "low"),))
                     for model in original.models),
    )
    supported = rehearsal.effective_manifest(declared_on, reasoning="on")
    assert all(dict(model.reasoning) == {"effort": "low"} for model in supported.models)


def test_omitted_factors_preserve_the_supplied_manifest_values():
    original = load_manifest(WORLD)
    supplied = replace(
        original,
        prompt=replace(original.prompt, mode="compact"),
        evaluation=replace(original.evaluation, producer_feedback="realized"),
        tools=replace(original.tools, address_enabled=True),
    )

    effective = rehearsal.effective_manifest(supplied)

    assert effective.prompt == supplied.prompt
    assert effective.evaluation.producer_feedback == "realized"
    assert effective.tools.address_enabled is True


def test_dangerous_or_unsupported_worlds_are_refused_before_runtime():
    original = load_manifest(WORLD)
    with pytest.raises(rehearsal.RehearsalRefused, match="testnet_hyperliquid_required"):
        rehearsal.effective_manifest(
            replace(original, exchange=replace(original.exchange, mainnet=True))
        )
    with pytest.raises(rehearsal.RehearsalRefused, match="unsupported_or_x402_model_rail"):
        rehearsal.effective_manifest(
            replace(original, models=(replace(original.models[0], provider="x402"),))
        )
    with pytest.raises(rehearsal.RehearsalRefused,
                       match="reasoning_on_requires_declared_tier_support"):
        rehearsal.effective_manifest(
            replace(original, models=(replace(original.models[0], reasoning=()),)),
            reasoning="on",
        )


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
            return [
                {"kind": "Launch"},
                {"kind": "consequence.finding", "handle": "ok", "status": "supported",
                 "evidence": ["receipt:1"]},
                {"kind": "consequence.finding", "handle": "bad", "status": "contrary",
                 "evidence": []},
                {"kind": "consequence.unknown", "handle": "censored"},
                {"kind": "snapshot", "state": "omitted"},
            ]

    class FakeRuntime:
        def __init__(self, manifest, **kwargs):
            manifest_seen["value"] = manifest
            market_seen["value"] = kwargs["market"]
            assert kwargs["kill_at_end"] is True
            self.ledger = FakeLedger()
            self.ticks_consumed = 1
            self.grounded_pending = {"pending": object()}
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
                    "outstanding_decisions": 2,
                    "stats": {"events": 1, "decisions": 3},
                    "process_io_metrics": {
                        "provider": {"complete": {"calls": 2, "elapsed_ns": 50}},
                        "exchange": {
                            "account": {"calls": 4, "elapsed_ns": 20},
                            "mids": {"calls": 3, "elapsed_ns": 35},
                        },
                        "scope": "stub process",
                    }}

    monkeypatch.setattr(rehearsal, "Runtime", FakeRuntime)
    provider = StubProvider(ModelResponse("openai/gpt-5.6-luna", "{}", 1, 1, "stop", cost_micro=1))
    out = tmp_path / "report.json"
    report = rehearsal.run_rehearsal(
        WORLD,
        out=out,
        duration_ns=2 * rehearsal.SHORT_TICK_NS,
        cap_micro=10_000,
        max_calls=2,
        provider=provider,
        source_root="/Users/isaacentebi/Desktop/FactoryLab",
        minimum_ticks=2,
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
    assert report["behavioral_screen"]["status"] == "inconclusive"
    assert report["behavioral_screen"]["criteria_met"]["delivered_ticks"] is False
    assert report["behavioral_screen"]["delivered"]["grounded"] == {
        "assessed": 1,
        "supported": 1,
        "contrary": 0,
        "unknown": 0,
        "censored": 1,
        "outstanding": 1,
        "malformed_or_uncited_excluded": 1,
    }
    critical = report["behavioral_screen"]["critical_path_io"]
    assert critical["provider_complete_calls"] == 2
    assert critical["exchange_account_calls"] == 4
    assert critical["exchange_mids_calls"] == 3
    assert critical["selected_total_calls"] == 9
    assert critical["selected_total_elapsed_ns"] == 105
    assert critical["selected_mean_elapsed_ns"] == "35/3"
    assert report["factors"]["roster_preserved"] is True
    assert report["factors"]["reasoning"]["actual_reasoning_provenance"] == {
        "status": "unknown",
        "reason": (
            "provider request configuration does not establish that the upstream model "
            "generated or exposed hidden reasoning"
        ),
    }
    assert report["protocol"] == {
        "duration_ns": 2 * rehearsal.SHORT_TICK_NS,
        "cap_micro": 10_000,
        "max_calls": 2,
        "planned_tick_ceiling": 2,
        "minimum_delivered_ticks": 2,
        "minimum_assessed_grounded_samples": 0,
        "minimum_contrary_grounded_samples": 0,
        "no_live_parameter_changes": True,
        "no_horizon_extension": True,
    }


def test_cli_passes_frozen_factors_and_reports_an_incomplete_screen(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_run(world, **kwargs):
        seen.update({"world": world, **kwargs})
        return {
            "status": "completed",
            "cost": {"attempted": 4},
            "behavioral_screen": {"status": "inconclusive"},
        }

    monkeypatch.setattr(rehearsal, "run_rehearsal", fake_run)
    code = rehearsal.main([
        "--out", str(tmp_path / "run"),
        "--duration", "30m",
        "--prompt", "compact",
        "--producer-feedback", "realized",
        "--address-enabled",
        "--reasoning", "on",
        "--minimum-ticks", "60",
        "--minimum-grounded-samples", "12",
        "--minimum-contrary-samples", "2",
    ])

    assert code == 0
    assert seen["prompt_mode"] == "compact"
    assert seen["producer_feedback"] == "realized"
    assert seen["address_enabled"] is True
    assert seen["reasoning"] == "on"
    assert seen["minimum_ticks"] == 60
    assert seen["minimum_grounded_samples"] == 12
    assert seen["minimum_contrary_samples"] == 2
    assert json.loads(capsys.readouterr().out)["behavioral_screen"]["status"] == "inconclusive"


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
        minimum_ticks=1,
    )

    assert report["status"] == "completed"
    assert report["timing"]["ticks"] == 1
    assert (tmp_path / "run" / "ledger.jsonl").exists()
    assert (tmp_path / "run" / "events.json").exists()
    observer = json.loads((tmp_path / "run" / "observer" / "report.json").read_text())
    assert observer["observer"]["ticks"] == report["timing"]["ticks"]
    assert observer["costs"]["basis"] == "admission_report"
    assert report["observer"]["scope"] == "rehearsal_only_recent_evidence_window"
