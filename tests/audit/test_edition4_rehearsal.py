"""Bounded edition 4 rehearsal admission and provenance checks."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from factorylab.runtime.live import LiveClock
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.evm import RailError
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import CatalogueEntry, ModelRequest, ModelResponse
from factorylab.world.openrouter import OpenRouterError
from scripts import edition4_rehearsal as rehearsal

WORLD = "worlds/edition6-testnet-rehearsal.toml"


@dataclass
class StubProvider:
    response: ModelResponse

    def catalogue(self):
        """Expose explicit fixture metadata for every model in the rehearsal roster."""
        rows = (
            ("deepseek/deepseek-v4.1-flash", "0.00000015", "0.00000060", 384_000),
            ("openai/gpt-5.6-sol", "0.00000200", "0.00001000", 128_000),
            ("venice:z-ai-glm-5-3-flash", "0.00000015", "0.00000050", 131_072),
            ("venice:qwen-3-8-flash", "0.00000014", "0.00000049", 131_072),
            # Edition 6's OpenRouter routes of the same two models.
            ("z-ai/glm-5.3-flash", "0.00000009", "0.00000030", 131_072),
            ("qwen/qwen3.8-flash", "0.00000015", "0.00000047", 131_072),
            ("openai/gpt-5.6-luna", "0.00000020", "0.00000120", 128_000),
            ("openai/gpt-5.6-luna:online", "0.00000020", "0.00000120", 128_000),
            # Edition 6's roster since #135, and the capital loop's Venice routes of it.
            ("openai/gpt-6-luna", "0.00000010", "0.00000050", 128_000),
            ("openai/gpt-6-sol", "0.00000200", "0.00001000", 128_000),
            ("minimax/minimax-m3", "0.00000030", "0.00000120", 131_072),
            ("xiaomi/mimo-v2.6-flash", "0.00000014", "0.00000028", 131_072),
            ("venice:openai-gpt-6-luna", "0.000000125", "0.000000625", 128_000),
            ("venice:deepseek-v4-1-flash", "0.000000375", "0.00000150", 384_000),
        )
        return [
            CatalogueEntry(
                id=model_id,
                name=f"fixture metadata for {model_id}",
                prompt_usd_per_token=prompt,
                completion_usd_per_token=completion,
                context_length=None,
                max_completion_tokens=limit,
            )
            for model_id, prompt, completion, limit in rows
        ]

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
        reasoning="off",
    )

    assert factored.prompt.mode == "compact"
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
    )

    effective = rehearsal.effective_manifest(supplied)

    assert effective.prompt == supplied.prompt


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


def test_a_probe_above_the_whole_cap_is_infeasible_and_admission_goes_on():
    admission = rehearsal.Admission(cap_micro=1_000, max_calls=10)
    assert admission.can_admit(1_001, probe=True) == (False, "quote_above_cap")
    assert admission.stop_reason is None
    admission.admit(1_000)


def test_a_probe_the_spent_cap_cannot_cover_ends_the_rehearsal():
    # Codex review of #136: once the cap excludes every seat, the run must end rather
    # than record NOOP decisions for the rest of its duration.
    admission = rehearsal.Admission(cap_micro=1_000, max_calls=10)
    admission.known_micro = 600
    assert admission.can_admit(500, probe=True) == (False, "quote_above_remaining_cap")
    assert admission.stop_reason == "quote_above_remaining_cap"


def test_an_actual_call_above_the_remaining_cap_ends_the_rehearsal():
    # Codex review of #136: a refused real call would otherwise enter the experiment
    # as the seat's failed return, and later events would go on around it.
    admission = rehearsal.Admission(cap_micro=1_000, max_calls=10)
    with pytest.raises(rehearsal.RehearsalRefused, match="quote_above_remaining_cap"):
        admission.admit(1_001)
    assert admission.stop_reason == "quote_above_remaining_cap"
    assert admission.can_admit(1, probe=True) == (False, "quote_above_remaining_cap")


def test_the_prepaid_provider_probes_feasibility_without_ending_the_rehearsal():
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))
    provider = rehearsal.PrepaidProvider(StubProvider(None), manifest,
                                         rehearsal.Admission(cap_micro=1_000, max_calls=3))
    model = manifest.assemblies[0].model_id
    assert provider.affordable(model, 1_001) == (False, "admission: quote_above_cap")
    assert provider.admission.stop_reason is None


def test_a_cap_below_every_seat_ends_the_rehearsal():
    # Codex review of #136: when every seated model's probe exceeds the whole cap, no
    # call can be admitted, so the run ends instead of recording NOOPs.
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))
    provider = rehearsal.PrepaidProvider(StubProvider(None), manifest,
                                         rehearsal.Admission(cap_micro=10, max_calls=3))
    seated = sorted({a.model_id for a in manifest.assemblies})
    for model in seated[:-1]:
        provider.affordable(model, 11)
        assert provider.admission.stop_reason is None
    provider.affordable(seated[-1], 11)
    assert provider.admission.stop_reason == "cap_below_every_seat"


def test_one_seat_above_the_cap_does_not_end_the_rehearsal():
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))
    provider = rehearsal.PrepaidProvider(StubProvider(None), manifest,
                                         rehearsal.Admission(cap_micro=10, max_calls=3))
    seated = sorted({a.model_id for a in manifest.assemblies})
    for model in seated[1:]:
        provider.affordable(model, 11)
    provider.affordable(seated[0], 5)
    assert provider.admission.stop_reason is None


def test_a_seat_that_once_fit_and_now_exceeds_the_cap_counts_as_excluded():
    # Codex review of #136: only each model's latest probe counts, so a world whose
    # every seat has since grown past the cap still ends.
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))
    provider = rehearsal.PrepaidProvider(StubProvider(None), manifest,
                                         rehearsal.Admission(cap_micro=10, max_calls=3))
    seated = sorted({a.model_id for a in manifest.assemblies})
    provider.affordable(seated[0], 5)
    for model in seated:
        provider.affordable(model, 11)
    assert provider.admission.stop_reason == "cap_below_every_seat"


def test_admission_uses_canonical_provider_failure_billing_classification():
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))

    class FailingProvider(StubProvider):
        def complete(self, _request):
            raise self.response

    rejected = rehearsal.PrepaidProvider(
        FailingProvider(OpenRouterError(402, "rejected", sent=True)),
        manifest,
        rehearsal.Admission(cap_micro=1_000_000, max_calls=3),
    )
    with pytest.raises(OpenRouterError):
        rejected.complete(request())
    assert rejected.admission.report()["uncertain_calls"] == 0
    assert rejected.admission.uncertain_micro == 0
    assert rejected.admission.stop_reason is None

    unknown = rehearsal.PrepaidProvider(
        FailingProvider(OpenRouterError(None, "connection dropped", sent=True)),
        manifest,
        rehearsal.Admission(cap_micro=1_000_000, max_calls=3),
    )
    ceiling = unknown._ceiling(request())
    with pytest.raises(OpenRouterError):
        unknown.complete(request())
    assert unknown.admission.report()["uncertain_calls"] == 1
    assert unknown.admission.uncertain_micro == ceiling
    assert unknown.admission.stop_reason == "unknown_bill_after_dispatch"
    assert not unknown.admission.can_admit(ceiling)[0]


def test_failed_dispatch_keeps_liability_without_retry_and_later_work_can_succeed():
    manifest = rehearsal.effective_manifest(load_manifest(WORLD))

    class Intermittent(StubProvider):
        calls = 0

        def complete(self, req):
            self.calls += 1
            if self.calls == 1:
                raise OpenRouterError(None, "lost response", sent=True)
            return self.response

    inner = Intermittent(ModelResponse(request().model_id, "{}", 1, 1, "stop", cost_micro=1))
    probe = rehearsal.PrepaidProvider(inner, manifest,
                                      rehearsal.Admission(1_000_000, 10,
                                                           recover_provider_failures=True))
    ceiling = probe._ceiling(request())
    with pytest.raises(OpenRouterError):
        probe.complete(request())
    assert inner.calls == 1  # No hidden retry or replacement answer.
    later = replace(request(), messages=({"role": "user", "content": "later work"},))
    assert probe.complete(later).cost_micro == 1
    assert inner.calls == 2
    assert probe.admission.uncertain_micro == ceiling
    assert probe.admission.known_micro == 1
    assert probe.admission.remaining_micro == 1_000_000 - ceiling - 1
    assert probe.admission.consecutive_failures == 0


@pytest.mark.parametrize("sent", [False, True])
def test_consecutive_provider_failures_stop_before_a_fourth_dispatch(sent):
    admission = rehearsal.Admission(1000, 10, recover_provider_failures=True)
    for _ in range(3):
        admission.admit(100)
        admission.attempted_call()
        admission.observe_exception(OpenRouterError(None, "failed", sent=sent), 100)
    assert admission.uncertain_micro == (300 if sent else 0)
    assert admission.stop_reason == "consecutive_provider_failures"
    with pytest.raises(rehearsal.RehearsalRefused):
        admission.admit(100)
    assert admission.attempted == 3


def test_one_unknown_bill_can_exhaust_cap_without_exhausting_failure_allowance():
    admission = rehearsal.Admission(100, 10, recover_provider_failures=True)
    admission.admit(100)
    admission.attempted_call()
    admission.observe_exception(OpenRouterError(None, "failed", sent=True), 100)
    assert admission.stop_reason == "cap_exhausted"
    assert not admission.can_admit(1)[0]


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
            assert kwargs["events"] == 60
            assert kwargs["clock_source"].base.count == 60
            assert kwargs["clock_source"].base.deadline_ns == 3_601_000_000_000
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
        duration_ns=60 * 60 * 1_000_000_000,
        target_ticks=60,
        cap_micro=10_000,
        max_calls=2,
        provider=provider,
        source_root="/Users/isaacentebi/Desktop/FactoryLab",
        now_ns=lambda: 1_000_000_000,
        minimum_ticks=60,
    )

    assert report["status"] == "completed"
    assert (out / "report.json").exists()
    assert (out / "events.json").exists()
    assert manifest_seen["value"].tick_interval_ns == rehearsal.SHORT_TICK_NS
    assert isinstance(market_seen["value"], rehearsal.DeniedMarket)
    assert report["cost"]["attempted"] == 0
    assert "x402" in report["denied_rails"]
    # The report names the roster the world file ratified and the one that ran. Edition 6's
    # rehearsal changes nothing a roster digest covers, so the two agree; the report says
    # so rather than asserting a change that did not happen.
    from factorylab.charter.provenance import roster_hash
    from factorylab.runtime.worlds import load_manifest

    assert report["preserved"]["roster_sha256"] == {
        "from": roster_hash(load_manifest(WORLD)),
        "to": roster_hash(manifest_seen["value"]),
    }
    assert report["behavioral_screen"]["status"] == "inconclusive"
    assert report["behavioral_screen"]["criteria_met"]["delivered_ticks"] is False
    critical = report["behavioral_screen"]["critical_path_io"]
    assert critical["provider_complete_calls"] == 2
    assert critical["exchange_account_calls"] == 4
    assert critical["exchange_mids_calls"] == 3
    assert critical["selected_total_calls"] == 9
    assert critical["selected_total_elapsed_ns"] == 105
    assert critical["selected_mean_elapsed_ns"] == "35/3"
    # Edition 6: the rehearsal keeps the roster.
    assert report["factors"]["roster_preserved"] is True
    assert report["factors"]["completion_allowance"] == "provider"
    assert report["factors"]["reasoning"]["actual_reasoning_provenance"] == {
        "status": "unknown",
        "reason": (
            "provider request configuration does not establish that the upstream model "
            "generated or exposed hidden reasoning"
        ),
    }
    assert report["protocol"] == {
        "duration_ns": 60 * 60 * 1_000_000_000,
        "target_ticks": 60,
        "cap_micro": 10_000,
        "max_calls": 2,
        "planned_tick_ceiling": 60,
        "minimum_delivered_ticks": 60,
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
        "--duration", "60m",
        "--ticks", "60",
        "--prompt", "compact",
        "--reasoning", "on",
        "--minimum-ticks", "60",
    ])

    assert code == 0
    assert seen["prompt_mode"] == "compact"
    assert seen["reasoning"] == "on"
    assert seen["target_ticks"] == 60
    assert seen["minimum_ticks"] == 60
    assert json.loads(capsys.readouterr().out)["behavioral_screen"]["status"] == "inconclusive"


def test_tick_target_requires_a_positive_count(tmp_path):
    with pytest.raises(ValueError, match="target_ticks must be a positive integer"):
        rehearsal.run_rehearsal(WORLD, out=tmp_path / "zero", target_ticks=0)


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
    assert "target_ticks" not in report["protocol"]
    assert report["timing"]["ticks"] == 1
    assert (tmp_path / "run" / "ledger.jsonl").exists()
    assert (tmp_path / "run" / "events.json").exists()
    observer = json.loads((tmp_path / "run" / "observer" / "report.json").read_text())
    assert observer["observer"]["ticks"] == report["timing"]["ticks"]
    assert observer["costs"]["basis"] == "admission_report"
    assert report["observer"]["scope"] == "rehearsal_only_recent_evidence_window"


@pytest.mark.gate
def test_world_continues_after_lost_provider_response_with_liability_reserved(tmp_path):
    class Intermittent(StubProvider):
        calls = 0

        def complete(self, req):
            self.calls += 1
            if self.calls == 2:
                raise OpenRouterError(None, "lost response", sent=True)
            return self.response

    provider = Intermittent(
        ModelResponse(request().model_id, "{}", 1, 1, "stop", cost_micro=1))
    report = rehearsal.run_rehearsal(
        WORLD, out=tmp_path / "run", target_ticks=5, minimum_ticks=5,
        provider=provider,
        exchange=FakeExchange(seed=1, coins=("BTC", "ETH"), start_cash_usd="120"),
        clock_source=ClockSource(0, rehearsal.SHORT_TICK_NS, 5),
        source_root=Path(rehearsal.__file__).resolve().parents[1],
    )
    assert report["status"] == "completed"
    assert report["summary"]["terminated"] and report["summary"]["seal_key_released"]
    assert report["summary"]["wallet_conservation"]
    assert report["timing"]["ticks"] == 5
    cost = report["cost"]
    assert provider.calls == cost["attempted"] > 2
    assert cost["uncertain_calls"] == 1 and cost["uncertain_micro"] > 0
    assert cost["known_micro"] == cost["known_calls"] == provider.calls - 1
    assert cost["stop_reason"] is None
    rows = json.loads((tmp_path / "run" / "events.json").read_text())
    failed = next(row for row in rows if row["kind"] == "invocation"
                  and row["status"] == "failed")
    assert sum(row["kind"] == "invocation" and row.get("handle") == failed["handle"]
               for row in rows) == 1
    assert sum(row["kind"] == "metering.uncertain" and row.get("handle") == failed["handle"]
               for row in rows) == 1
    assert not any(row["kind"] == "wallet.settle_uncertain" for row in rows)
    assert any(row["kind"] == "invocation" and row["seq"] > failed["seq"]
               and row["handle"] != failed["handle"] for row in rows)
    assert report["summary"]["execution"]["intents"] == 0


def test_native_completion_launch_removes_seat_caps_without_disabling_reasoning():
    original = load_manifest(WORLD)
    effective = rehearsal.effective_manifest(original, native_completions=True)
    assert all(a.max_tokens is None for a in effective.assemblies)
    assert effective.models == original.models
    assert effective.charter == original.charter
