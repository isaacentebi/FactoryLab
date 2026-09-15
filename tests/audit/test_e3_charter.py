"""Edition 3 C3: one card, norms that carry their definitions, no privileged predicate.

The acceptance in the plan, verbatim: *a seat whose returns never own a lot but
whose forecasts and commitments resolve keeps standing; removing a card from the
charter removes its effect on standing entirely.*
"""

import tomllib
from pathlib import Path

import pytest

from factorylab.charter.charter import EDITION3_NORMS, Charter, MetricCard, Norm
from factorylab.charter.measurement import CardSamples, measure_card, preflight_measurement
from factorylab.charter.provenance import charter_digest, norms_raw
from factorylab.charter.windows import MetricWindow
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue, SettleStatus
from factorylab.runtime.cards import forecast_weight
from factorylab.runtime.worlds import _manifest_charter
from factorylab.settlement import (
    ConsequenceStanding,
    Forecast,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
    WindowFacts,
    open_forecast_decision,
)
from factorylab.settlement.fidelity import FidelityObjection, objection_schema, parse_objection
from factorylab.settlement.vocabulary import UNOBSERVABLE, evaluator_answer_schema
from factorylab.versioning.versions import lost_access

DRAFT = Path(__file__).resolve().parents[2] / "docs" / "charter" / "edition3-draft.toml"
EDITION2_DRAFT = Path(__file__).resolve().parents[2] / "docs" / "charter" / "edition2-draft.toml"


def read_ledger(ledger):
    """Every item of a ledger, which releases its seal key only after termination."""
    from factorylab.kernel.events import Bus
    from factorylab.kernel.termination import Termination

    marker = ledger.append({"kind": "test.marker"})
    Termination(ledger=ledger, bus=Bus(ledger), clock_ns=lambda: 100).kill("test audit")
    return [ledger.decrypt_item(seq) for seq in range(marker)]


def edition3_charter():
    return _manifest_charter(tomllib.loads(DRAFT.read_text())["charter"])


def paid_off_card(answers_for="producer"):
    """The removed edition 2 card, rebuilt here only to flip it in and out of a charter."""
    return MetricCard(
        id="card-consequence-paid-off",
        norm="consequential usefulness",
        description="Share of settled consequences whose return paid off.",
        units="fraction",
        window=MetricWindow("windows", 6, None),
        acceptable_region="at least 0.4",
        observation="consequence_paid_off_rate",
        answers_for=answers_for,
    )


# --------------------------------------------------------------- norms and definitions


def test_norm_definitions_round_trip_and_render():
    charter, _prices = edition3_charter()
    assert [str(n) for n in charter.norms] == [str(n) for n in EDITION3_NORMS]
    assert [n.definition for n in charter.norms] == [n.definition for n in EDITION3_NORMS]
    assert charter.norms[4].definition.startswith(
        "Measurements are defeasible evidence of the values")
    rendered = charter.render()
    for norm in charter.norms:
        assert f"- {norm.id}" in rendered and norm.definition in rendered
    # A norm is still exactly its name, so a card binds to it and a roster hashes it.
    assert charter.cards[0].norm in charter.norms
    assert norms_raw(charter.norms) == [n.as_dict() for n in EDITION3_NORMS]


def test_bare_string_norms_still_load_and_keep_their_historical_digest():
    raw = tomllib.loads(EDITION2_DRAFT.read_text())["charter"]
    before = charter_digest(raw)
    charter, _prices = _manifest_charter(raw)
    assert all(n.definition == "" for n in charter.norms)
    assert "\n  " not in charter.render().split("METRIC CARDS")[0].strip()
    # Loading changed nothing about the artifact the committee voted on.
    assert charter_digest(tomllib.loads(EDITION2_DRAFT.read_text())["charter"]) == before
    assert norms_raw(charter.norms) == list(raw["norms"])


def test_a_norm_table_is_validated_like_anything_else():
    with pytest.raises(ValueError, match="charter.norms"):
        _manifest_charter({"norms": [{"definition": "no id"}], "cards": []})
    with pytest.raises(ValueError, match="charter.norms"):
        _manifest_charter({"norms": [{"id": "x", "extra": "y"}], "cards": []})
    with pytest.raises(ValueError, match="unique"):
        Charter(1, (Norm("a", "one"), Norm("a", "another")), ())


# ------------------------------------------------------------------ the one card


def test_edition3_draft_loads_and_preflights_with_one_card():
    charter, prices = edition3_charter()
    assert [c.id for c in charter.cards] == ["censorship-bound"]
    card = charter.cards[0]
    assert card.observation == "avoidably_unresolved_share"
    assert card.acceptable_region == "at most 0.30"
    assert card.window == MetricWindow("forecasts", 25, "assembly")
    assert card.answers_for == "all"  # the commitment owner, whatever role it holds
    assert dict(prices) == {"censorship-bound": 0.10}
    preflight_measurement(card)


def test_the_draft_loads_against_the_edition3_testnet_manifest():
    """The world W5 built is the roster this draft will be ratified on."""
    import tomllib as toml

    from factorylab.charter.book import validate_observation_bindings
    from factorylab.runtime.worlds import manifest_from_dict

    world = Path(__file__).resolve().parents[2] / "worlds" / "edition3-testnet.toml"
    raw = toml.loads(world.read_text())
    raw["charter"] = tomllib.loads(DRAFT.read_text())["charter"]
    manifest = manifest_from_dict(raw)
    assert [c.id for c in manifest.charter.cards] == ["censorship-bound"]
    assert [n.definition for n in manifest.charter.norms] == [
        n.definition for n in EDITION3_NORMS]
    validate_observation_bindings(list(manifest.charter.cards))
    for card in manifest.charter.cards:
        preflight_measurement(card)


def test_the_removed_cards_are_gone_from_the_draft():
    charter, _prices = edition3_charter()
    ids = {c.id for c in charter.cards}
    assert "card-consequence-paid-off" not in ids and "card-forecast-skill" not in ids


def measured(rows, card):
    samples = CardSamples(forecasts=rows)
    return measure_card(card, samples)


def forecast_row(handle, *, status="settled", excluded=None, assembly="judge-a"):
    return {"handle": handle, "assembly": assembly, "role": "evaluator",
            "subject_handle": "producer-1", "subject_assembly": "producer",
            "subject_role": "producer", "window": 1, "skill": 0.0,
            "predicate": "return_paid_off", "y": 1, "status": status, "verdict": None,
            "excluded": excluded}


def test_the_narrowed_observation_counts_only_attributable_unresolved_commitments():
    card = MetricCard(
        id="censorship-bound", norm="epistemic integrity",
        description="narrowed", units="fraction",
        window=MetricWindow("forecasts", 4, "assembly"),
        acceptable_region="at most 0.30",
        observation="avoidably_unresolved_share", answers_for="all",
    )
    rows = [forecast_row("a"), forecast_row("b", status="censored"),
            forecast_row("c"), forecast_row("d")]
    assert measured(rows, card) == {"judge-a": 0.25}
    # Documented external unobservability is excluded, not counted and not zeroed:
    # it leaves the sample, and the window reaches back for another eligible one.
    rows = [forecast_row("z"), forecast_row("a"),
            forecast_row("b", status="censored", excluded="external_unobservable"),
            forecast_row("c"), forecast_row("d")]
    assert measured(rows, card) == {"judge-a": 0.0}
    # No eligible sample is unmeasured, never zero.
    excluded_only = [forecast_row(h, status="censored", excluded="external_unobservable")
                     for h in "abcd"]
    assert measured(excluded_only, card) == {}
    # A commitment that is not yet due has no row at all, so it is never blamed.
    assert measured([forecast_row("a"), forecast_row("b")], card) == {}


def test_a_documented_unobservable_settlement_is_censored_but_excluded():
    ledger = Ledger(clock_ns=lambda: 100)
    queue = DecisionQueue(ledger, clock_ns=lambda: 100)
    book = ForecastBook(ledger)
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(book, queue, standing, PrevalenceBaseline(), Observer())
    seal(queue, book, about="return-1")
    (result,) = settler.settle_due(20, lambda forecast: UNOBSERVABLE)
    assert result.status is SettleStatus.CENSORED
    assert result.excluded == "external_unobservable"
    assert standing.snapshot() == {}
    # The measurement pass reads the documented reason once, where it writes the row.
    assert settler.excluded(result.handle) == "external_unobservable"
    assert settler.excluded(result.handle) is None


# ------------------------------------------------------- standing without a privilege


def build(charter, kinds):
    """A settler whose weights come from a charter, over a fresh book and standing."""
    ledger = Ledger(clock_ns=lambda: 100)
    queue = DecisionQueue(ledger, clock_ns=lambda: 100)
    book = ForecastBook(ledger)
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(book, queue, standing, PrevalenceBaseline(), Observer(),
                      weight_for=lambda f: forecast_weight(charter, kinds.get(f.about_handle)))
    return queue, book, standing, settler


def seal(queue, book, *, about, handle_q=0.9, due=10):
    handle = open_forecast_decision(
        queue, evaluator_id="judge-a", event_id=f"event-{about}", q=handle_q,
        deadline_ns=1_000, parent_handle=None, now_event=0, horizon=due)
    return book.seal(Forecast(handle, "judge-a", about, "wallet_up",
                              {"horizon_events": due}, handle_q, 0, due))


def settle_one(charter, kinds, about):
    queue, book, standing, settler = build(charter, kinds)
    seal(queue, book, about=about)
    settler.settle_due(20, lambda f: WindowFacts(1, 2, 1, ()))
    return standing.snapshot().get("judge-a", {})


def test_a_seat_whose_returns_never_own_a_lot_keeps_standing():
    """The acceptance: forecasts and commitments that resolve are standing, on their own."""
    charter, _prices = edition3_charter()
    kinds = {"verdict-1": "Verdict"}
    row = settle_one(charter, kinds, "verdict-1")
    # No payoff outcome was ever settled for this seat: under edition 2 this was
    # empty, because only return_paid_off trained standing.
    assert row["settled"] == 1 and row["weight_sum"] == 1.0
    assert row["skill"] > 0 and row["weight"] > 0.5


def test_a_card_answers_for_moves_standing_and_removing_it_removes_the_effect():
    """Flip one card in the charter and diff the standing updates it produced."""
    charter, _prices = edition3_charter()
    scoped = Charter(charter.edition, charter.norms, (*charter.cards, paid_off_card("producer")))
    kinds = {"return-1": "ProducerReturn", "verdict-1": "Verdict"}

    without = {about: settle_one(charter, kinds, about) for about in kinds}
    with_card = {about: settle_one(scoped, kinds, about) for about in kinds}
    # The card names producers, so it weights a claim about a producer's return
    # (both cards cover it: 2/2) and halves a claim about a judge's (1/2).
    assert with_card["return-1"]["weight_sum"] == 1.0
    assert with_card["verdict-1"]["weight_sum"] == 0.5
    assert with_card["verdict-1"]["skill"] == pytest.approx(without["verdict-1"]["skill"])
    assert forecast_weight(scoped, "Verdict") == 0.5
    assert forecast_weight(scoped, "ProducerReturn") == 1.0

    # Remove the card again: every update is exactly what it was without it.
    removed = Charter(scoped.edition, scoped.norms,
                      tuple(c for c in scoped.cards if c.id != "card-consequence-paid-off"))
    after = {about: settle_one(removed, kinds, about) for about in kinds}
    assert after == without
    assert forecast_weight(removed, "Verdict") == forecast_weight(removed, "ProducerReturn") == 1.0


def test_a_charter_with_no_card_weights_every_claim_equally():
    charter, _prices = edition3_charter()
    empty = Charter(charter.edition, charter.norms, ())
    assert forecast_weight(empty, "Verdict") == forecast_weight(empty, "Exposure") == 1.0
    assert forecast_weight(empty, None) == 1.0


def test_return_paid_off_is_still_a_kernel_fact_and_an_ordinary_predicate():
    from factorylab.settlement.vocabulary import RETURN_PAID_OFF, SEED_VOCABULARY

    assert RETURN_PAID_OFF.id == "return_paid_off"
    # Cash settlement is immutable and the fact is still the kernel's; what is gone is
    # its privilege, so it sits in the vocabulary like any other predicate.
    assert RETURN_PAID_OFF.provenance == "seed" and RETURN_PAID_OFF.code is None
    assert RETURN_PAID_OFF.id not in {p.id for p in SEED_VOCABULARY}


# ------------------------------------------------------------- the fidelity objection


def objection_payload(**changes):
    payload = {"value": "fidelity", "measurement": "censorship-bound",
               "evidence": "every commitment settled and none of them mattered to anyone",
               "uncertainty": 0.25}
    payload.update(changes)
    return payload


def test_the_evaluator_answer_schema_carries_the_objection():
    schema = evaluator_answer_schema({"type": "array"}, {"type": "array"})
    field = schema["properties"]["fidelity_objection"]
    assert field == objection_schema()
    assert sorted(field["required"]) == ["evidence", "measurement", "uncertainty", "value"]
    # It is optional: a judge that has no objection is not compelled to invent one.
    assert "fidelity_objection" not in schema["required"]


def test_an_objection_is_validated_against_the_charter():
    charter, _prices = edition3_charter()
    objection = parse_objection(objection_payload(), charter=charter)
    assert isinstance(objection, FidelityObjection)
    assert objection.confidence == 0.75
    with pytest.raises(ValueError, match="not a charter norm"):
        parse_objection(objection_payload(value="thrift"), charter=charter)
    with pytest.raises(ValueError, match="live card or a known observation"):
        parse_objection(objection_payload(measurement="vibes"), charter=charter)
    for bad in ({"value": "fidelity"}, "an objection", objection_payload(uncertainty=2),
                objection_payload(evidence=" ")):
        with pytest.raises(ValueError):
            parse_objection(bad, charter=charter)
    # The observation a card names is a measurement too, so the claim can be challenged.
    assert parse_objection(objection_payload(measurement="avoidably_unresolved_share"),
                           charter=charter).measurement == "avoidably_unresolved_share"


def test_an_objection_is_ledgered_and_scored_like_a_verdict_and_settles_nothing():
    charter, _prices = edition3_charter()
    ledger = Ledger(clock_ns=lambda: 100)
    queue = DecisionQueue(ledger, clock_ns=lambda: 100)
    book = ForecastBook(ledger)
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(book, queue, standing, PrevalenceBaseline(), Observer())
    entries = [{"handle": "judge-1", "outputs": {"verdict": 0.9,
                                                 "fidelity_objection": objection_payload()}}]
    recorded = settler.record_objection("judge-1", entries, charter,
                                        evaluator_id="judge-a", about_handle="return-1")
    assert recorded == FidelityObjection(**objection_payload())
    result = settler.settle_verdict(evaluator_id="judge-a", about_handle="return-1",
                                    q=0.9, share=0.8, judge_handle="judge-1")
    # The objection claimed blame with confidence 0.75 and blame of 0.8 landed:
    # scored by the same proper score as the verdict, against the same fact.
    assert result.objection == recorded
    assert result.objection_brier == pytest.approx(1 - (0.75 - 0.8) ** 2)
    assert result.brier == pytest.approx(1 - (0.9 - 0.2) ** 2)
    # It is a verdict score and nothing else: no decision settled, no coverage earned.
    assert standing.snapshot()["judge-a"]["verdict_n"] == 2
    assert standing.coverage("judge-a") == 0.0
    # One objection is scored once.
    assert settler.objection("judge-1") is None
    rows = read_ledger(ledger)
    (row,) = [r for r in rows if r["kind"] == "fidelity.objection"]
    assert row["accepted"] and row["value"] == "fidelity" and row["uncertainty"] == 0.25
    assert row["evaluator_id"] == "judge-a" and row["about_handle"] == "return-1"
    assert not [r for r in rows if r["kind"] == "decision.settle"]


def test_a_malformed_objection_is_refused_in_the_ledger_and_scores_nothing():
    charter, _prices = edition3_charter()
    ledger = Ledger(clock_ns=lambda: 100)
    queue = DecisionQueue(ledger, clock_ns=lambda: 100)
    standing = ConsequenceStanding(min_coverage=0.0)
    settler = Settler(ForecastBook(ledger), queue, standing, PrevalenceBaseline(), Observer())
    entries = [{"handle": "judge-1", "outputs": {"fidelity_objection": {"value": "fidelity"}}}]
    assert settler.record_objection("judge-1", entries, charter) is None
    result = settler.settle_verdict(evaluator_id="judge-a", about_handle="return-1",
                                    q=0.9, share=0.0, judge_handle="judge-1")
    assert result.objection is None and result.objection_brier is None
    assert standing.snapshot()["judge-a"]["verdict_n"] == 1
    (row,) = [r for r in read_ledger(ledger) if r["kind"] == "fidelity.objection"]
    assert row["accepted"] is False and "missing" in row["reason"]


# ------------------------------------------------------------------- learning death


def window(index, **access):
    profile = {"registrations": 0.0, "revision": 0.0, "paid_off": None, "realized_pnl": None}
    profile.update({f"access:{name}": value for name, value in access.items()})
    return {"index": index, "charter_edition": 1, "profile": profile, "regions": {},
            "access": {"affordable_seat": "cheapest seat costs 900 micro-USD; the richest "
                                          "seat holds 10"}}


def test_the_diagnosis_says_which_access_is_lost_and_why():
    tail = [window(i, affordable_seat=0.0, registration_route=0.0, revision_route=1.0)
            for i in range(3)]
    lost = lost_access(tail)
    assert [row["access"] for row in lost] == ["affordable_seat", "registration_route"]
    assert lost[0]["diagnosis"] == "no affordable seat"
    assert lost[0]["why"].startswith("cheapest seat costs")
    assert lost[1]["diagnosis"] == "no route to registration"
    # A population that can still afford to look has lost nothing, whatever it did.
    assert lost_access([window(i, affordable_seat=1.0) for i in range(3)]) == []
    # An access nobody measured is unknown, never reported as lost.
    assert lost_access([window(i) for i in range(3)]) == []


def test_the_gone_frontier_rule_is_unchanged_and_carries_the_access_evidence():
    from factorylab.versioning.versions import diagnose

    tail = [window(i, affordable_seat=0.0, registration_route=0.0, revision_route=0.0)
            for i in range(3)]
    diagnosed = diagnose(tail, k=2, registration_bins=(0.5,), revision_bins=(0.5,))
    assert diagnosed["flags"]["learning_death"] is True  # quiet, flat, nothing holding
    assert [row["diagnosis"] for row in diagnosed["frontier"]["lost_access"]] == [
        "no affordable seat", "no route to registration", "no route to revision"]
