from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


def _covered_evaluators(standing: dict) -> list[str]:
    per = standing.get("evaluators", standing)
    return [e for e, v in per.items() if isinstance(v, dict) and v.get("settled", 0) > 0]


def test_scripted_world_phase2_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=400, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # producers are judged, evaluators are judged, forecasts are sealed and settled
    assert st["verdicts"] >= 20 and st["conformities"] >= 10
    assert st["forecasts_sealed"] >= 20 and st["forecasts_settled"] >= 20
    assert st["max_settlement_latency_events"] >= 1
    # consequence standing exists with coverage for at least two evaluators
    assert len(_covered_evaluators(s["standing"])) >= 2
    # a scripted proposal registered a new assembly, opened an epoch, and it was invoked
    assert st["registrations_accepted"] >= 1 and st["epochs"] >= 1
    assert st["registrations_rejected"] >= 1  # the model proposal has no catalogue here
    counts = s["aggregates"]["invocations_by_assembly"]["counts"]
    assert counts.get("funding-watcher", 0) >= 1
    assert st["routers_replaced"] >= 1
    assert s["evaluation_boundary"] == "producer → evaluator → meta"
    assert st["sample_propensity"] is not None
    assert sum(s["aggregates"]["spend_by_capability"]["spend"].values()) > 0


def test_every_producer_decision_is_judged_or_censored() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=120, seed=4)
    st = s["stats"]
    judged = st["verdicts"] + st["censored"] + st["exposures_settled"]
    assert judged + s["outstanding_decisions"] >= st["producer_returns"]


def test_scripted_world_starves_but_cannot_die_from_compute_alone() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=600, seed=2, initial_balance_micro=200_000, drip=False)
    assert s["terminated"] is False and 0 < s["wallet_balance_micro"] < 200_000
    assert s["stats"]["exclusions"] > 0


def test_crash_world_dies_and_releases_seal_spec_condition_3() -> None:
    m = load_manifest("scripted-crash")
    s = run_world(m, events=600, seed=2)
    assert s["terminated"] is True and s["termination_reason"] == "balance_zero"
    assert s["seal_key_released"] is True
    assert s["wallet_balance_micro"] <= 0
    assert s["wallet_conservation"] is True


def test_determinism_same_seed_same_summary() -> None:
    m = load_manifest("scripted")
    a = run_world(m, events=60, seed=7)
    b = run_world(m, events=60, seed=7)
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b


def test_scripted_world_phase3_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=500, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # tool calls executed and results returned to the calling assembly
    assert st["tool_calls"] >= 10
    assert st["tool_call_failures"] < st["tool_calls"]
    # a population tool was registered and then called
    assert st["population_tools_registered"] >= 1 and "spread-check" in s["tools"]
    # an online variant is registered as a purchasable and an assembly was built on it
    assert "fake-haiku:online" in s["aggregates"]["invocations_by_assembly"]["counts"] or (
        "web-observer" in s["aggregates"]["invocations_by_assembly"]["counts"]
    )
    # an amendment was proposed, voted, passed and activated: evaluators now see edition 2
    assert st["amendments_proposed"] >= 1 and st["votes_cast"] >= 3
    assert st["amendments_passed"] >= 1 and st["amendments_activated"] >= 1
    assert s["charter_edition"] >= 2
    # prices (spec v0.6 section 8.1): a 2-minute window over 500 one-second ticks closes four
    # windows, and every closed window hands the well_formed_rate card one observation
    cards = s["prices"]["cards"]
    closed = st["reserve_windows"] - 1
    assert closed >= 3 and st["price_updates"] >= 2 and st["price_skipped"] == 0
    assert cards["well_formed_rate"]["updates"] == closed
    assert cards["well_formed_rate"]["lambda"] == 0.0  # scripted returns are all well formed
    assert st["last_window_values"]["well_formed_rate"] == 1.0
    # the amendment's turnover card ("below 5", ratio units) is registered once edition 2 is
    # live and is violated by two orders of magnitude every window, so its price saturates at
    # lambda_max and every producer verdict settles at 0 while it stands; evaluators pay for
    # forecast skill below zero. This run therefore shows penalized settlements, not a world
    # with no violated card.
    assert "turnover" in cards and cards["turnover"]["lambda"] == 1.0
    assert cards["turnover"]["saturations"] >= 1
    assert st["last_window_values"]["turnover"] > 5
    assert st["penalized_settlements"] >= 1
    assert cards["cost_per_return"]["updates"] == closed - 1  # no median before the first window


def test_scripted_amendment_lambda_is_voted_adopted_and_visible(monkeypatch):
    from factorylab.runtime.loop import Runtime, ScriptedProvider, _inputs_from_prompt

    requests = []

    class RecordingProvider(ScriptedProvider):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            requests.append(_inputs_from_prompt(text))
            return super().complete(req)

    rt = Runtime(load_manifest("scripted"), events=260, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=True, router_gamma=0.1, provider=RecordingProvider())
    entries = []
    append = rt.ledger.append

    def capture(entry):
        result = append(entry)
        entries.append(dict(entry))
        return result

    monkeypatch.setattr(rt.ledger, "append", capture)
    result = rt.run()
    assert result["ledger_verify"] and result["wallet_conservation"]
    votes = [req["amendment"] for req in requests if "amendment" in req]
    assert votes and all(am["add"][0]["lambda"] == 0.6 for am in votes)
    proposed = [(i, e) for i, e in enumerate(entries) if e["kind"] == "price.proposed"]
    assert len(proposed) == 1
    index, item = proposed[0]
    assert item["card_id"] == "turnover" and item["amendment_id"] == "turnover-card"
    assert item["lambda_after"] == 0.6
    assert any(e["kind"] == "price.region" and e["card_id"] == "turnover"
               for e in entries[:index])
    worlds = [req["world"] for req in requests if req.get("world", {}).get("charter_edition") == 2]
    first = next(c for c in worlds[0]["card_prices"] if c["card_id"] == "turnover")
    assert first["lambda"] == 0.6
    updates = [e for e in entries[index + 1:]
               if e["kind"] == "price.update" and e["card_id"] == "turnover"]
    assert updates and updates[0]["lambda_before"] == 0.6
