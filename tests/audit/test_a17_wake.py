"""A17: the wake is a read-only observatory of everything already public.

Essay I.III: the control tower reads the outcomes the factory produces, and
nothing else. Whatever the population can see is public to the experimenter;
learner state, router weights and propensities, private memories, raw request
and return text and per-decision scores stay sealed until death.
"""

import json
import shutil
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.wake import (
    PUBLIC_KIND,
    SECTIONS,
    VIEWS,
    _Observatory,
    _open_snapshot,
    _Snapshot,
    _venue,
    collect_wake,
    rail_for_model,
    render_wake,
)
from factorylab.runtime.worlds import load_manifest

# One window must close for the standing sections to exist; the scripted world
# closes its first at event 121 and exercises registration, retirement, an
# amendment and a refused transfer before then.
EVENTS = 150

# Keys the essay keeps private. They are asserted absent as *keys*: a public
# observation may legitimately be named "verdict_mean" in a charter card.
SEALED_KEYS = frozenset({
    "propensity", "propensities", "chosen", "weights", "gamma", "learner", "learner_id",
    "state", "memory", "memories", "prompt", "system_prompt", "prompt_text", "raw",
    "outputs", "output", "rationale", "text", "verdict", "payoff", "score", "scores",
    "skill", "handle", "assembly_id", "actor", "id_", "entry_px", "px", "size", "notional",
})
MARKER = "SEALED_ONLY_PRIVATE_TEXT"


@pytest.fixture(scope="module")
def scripted(scripted_run):
    """A shared scripted ledger; consumers must copy its directory before writing."""
    return scripted_run("scripted", EVENTS, 1).ledger_path


@pytest.fixture(scope="module")
def wake(scripted):
    return collect_wake(scripted)


def _keys(value):
    """Every key at every depth of the published document."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_a17_every_section_is_present_on_the_scripted_world(wake):
    assert set(wake) == {*VIEWS, *SECTIONS, "world", "manifest_hash", "uptime_ns",
                         "last_event_time_ns"}
    assert all(wake[section] != "unavailable" for section in SECTIONS)


def test_a17_roster_publishes_kinds_and_models_over_time(wake):
    roster = wake["roster"]
    assert {row["kind"] for row in roster["current"]} == {
        "producer", "evaluator", "meta", "antagonist",
    }
    assert all(row["model_id"] and row["count"] > 0 for row in roster["current"])
    assert roster["over_time"] and all(
        window["by_kind"] and window["by_model"] for window in roster["over_time"]
    )
    # Registration and retirement are announced to the whole population.
    assert {row["registered"] for row in roster["registered"]} >= {"assembly", "tool"}
    assert all(row["retired"] in ("assembly", "router") for row in roster["retired"])
    assert roster["retired"]


def test_a17_tools_and_observations_publish_id_description_version(wake):
    assert wake["tools"] and all(
        set(tool) == {"id", "description", "version"} and tool["description"]
        and isinstance(tool["version"], int) for tool in wake["tools"]
    )
    assert "spread-check" in {tool["id"] for tool in wake["tools"]}  # registered in-run
    assert wake["observations"] and all(
        set(row) == {"id", "description", "units"} and row["units"]
        for row in wake["observations"]
    )


def test_a17_charter_publishes_edition_norms_cards_and_amendment_history(wake):
    charter = wake["charter"]
    assert charter["edition"] >= 1 and len(charter["norms"]) == 4
    for card in charter["cards"]:
        assert set(card) == {"id", "norm", "observation", "answers_for", "lambda", "region"}
        assert card["norm"] in charter["norms"] and isinstance(card["lambda"], float)
        assert card["region"] is None or set(card["region"]) == {
            "card_id", "kind", "lo", "hi", "scale",
        }
    amendment = next(a for a in charter["amendments"] if a["id"] == "turnover-card")
    assert amendment["proposed"] and amendment["passed"]
    assert amendment["predicted_effect"]["direction"] == "decrease"
    assert amendment["add"] == ["turnover"] and amendment["refused"] is None


def test_a17_compute_publishes_spend_per_rail_and_invocations_per_kind(wake):
    compute = wake["compute"]
    day = next(iter(compute["spend_by_rail_per_day"].values()))
    assert set(day) == {"openrouter", "venice", "x402"} and sum(day.values()) > 0
    week = next(iter(compute["spend_by_rail_per_week"].values()))
    assert set(week) == {"openrouter", "venice", "x402"} and sum(week.values()) > 0
    invocations = next(iter(compute["invocations_by_kind_per_day"].values()))
    assert {"producer", "evaluator"} <= set(invocations) and sum(invocations.values()) > 0


def test_a17_rails_follow_the_model_id_namespace_registration_uses():
    assert rail_for_model("anthropic/claude@high") == "openrouter"
    assert rail_for_model("venice:llama-3.3-70b") == "venice"
    assert rail_for_model("x402:https://seller.example#model") == "x402"


def test_a17_pots_publish_the_three_pots_and_their_transfers(wake):
    pots = wake["pots"]
    assert set(pots["current"]) == {"venue", "reserve", "venice", "seed", "complete"}
    refused = next(t for t in pots["transfers"] if t["status"] == "refused")
    assert refused["direction"] == "to_venue" and refused["reason"]
    assert all(set(t) == {"ts_ns", "status", "direction", "amount_micro", "reason"}
               for t in pots["transfers"])


def test_a17_immune_publishes_flags_per_window(wake):
    assert wake["immune"]
    for window in wake["immune"]:
        assert set(window) == {"window", "flags", "responses"}
        assert set(window["flags"]) == {"stable_failure", "learning_death", "thrash"}


def test_a17_immune_responses_name_the_organs_answer_without_learner_state():
    observatory = _Observatory()
    for item in (
        {"kind": "immune.window", "window": 4, "flags": {"stable_failure": True,
                                                         "learning_death": True,
                                                         "thrash": False}},
        {"kind": "immune.gain", "pathology": "stable_failure", "window": 4,
         "router": "router:Tick", "gamma_before": [0.1], "gamma_after": [0.2]},
        {"kind": "immune.price_relief", "card_id": "cost_per_return", "window": 4,
         "lambda": 0.4, "effective_lambda": 0.2},
        {"kind": "immune.decay", "window": 4, "decay_before": 0.01, "decay_after": 0.02},
        {"kind": "novelty.grant", "window": 4},
    ):
        observatory.feed(item)
    window = observatory.windows[4]
    assert window["flags"]["stable_failure"] and window["flags"]["learning_death"]
    assert [row["response"] for row in window["responses"]] == [
        "router_gain", "price_relief", "price_decay", "novelty_grant",
    ]
    assert not _keys(window) & {"gamma", "gamma_after", "router", "lambda"}


def test_a17_portfolio_publishes_equity_and_pnl_and_no_position(wake):
    """A17 publishes no positions at all: a coin with a side is a position."""
    portfolio = wake["portfolio"]
    assert set(portfolio) == {"equity_micro", "realized_to_date_micro"}
    assert isinstance(portfolio["realized_to_date_micro"], int)
    assert "open_positions" not in json.dumps(wake)


def test_a17_five_aggregates_and_uptime_are_unchanged(scripted, wake):
    ledger, manifest = _open_snapshot(scripted)
    for view in VIEWS:
        assert wake[view] == ledger.public_aggregates(manifest)[view]
    assert wake["world"] == "scripted" and wake["uptime_ns"] == wake["last_event_time_ns"] > 0
    text = json.dumps(wake)
    assert all(assembly.id not in text for assembly in manifest.assemblies)


def test_a17_five_aggregates_publish_role_totals_without_assembly_names():
    ledger = Ledger()
    manifest = load_manifest("scripted")
    for assembly in manifest.assemblies:
        ledger.append({"kind": "invocation", "assembly_id": assembly.id, "role": assembly.role})
    result = _Snapshot.public_aggregates(ledger, manifest)
    assert sum(result["invocations_by_assembly"]["counts"].values()) == len(manifest.assemblies)
    text = json.dumps(result)
    assert all(assembly.id not in text for assembly in manifest.assemblies)
    assert len(result) == 5


def test_a17_no_sealed_field_appears_anywhere_in_the_output(scripted, tmp_path):
    """Private items exist in the diary; none of them, and no sealed key, is published."""
    directory = tmp_path / "private-world"
    shutil.copytree(scripted.parent, directory)
    scripted = directory / scripted.name
    manifest = load_manifest("scripted")
    writer = Ledger.reopen(scripted, manifest=json.loads(manifest.canonical_json()))
    writer.append({"kind": "invocation", "assembly_id": "eval-a", "role": "evaluator",
                   "handle": "decision-1", "outputs": MARKER, "cost": 1, "status": "ok"})
    writer.append({"kind": "decision.open", "handle": "decision-1", "score": 0.9,
                   "propensity": {"chosen": MARKER, "weights": [MARKER], "p": 0.5}})
    writer.append({"kind": "router.state", "learner_id": MARKER, "weights": [1.0, 2.0],
                   "memory": MARKER, "prompt": MARKER, "verdict": MARKER})
    data = collect_wake(scripted)
    page = render_wake(data)
    document = json.dumps(data)
    assert MARKER not in document and MARKER not in page
    assert not _keys(data) & SEALED_KEYS


def test_a17_window_close_item_is_public_and_carries_no_prompt_or_size(scripted):
    ledger, manifest = _open_snapshot(scripted)
    items = [item for item in ledger.items() if item.get("kind") == PUBLIC_KIND]
    assert items, "a closed window publishes exactly one public world block"
    for item in items:
        assert set(item) >= {"window", "window_end_event", "roster", "tools", "observations",
                             "charter", "pots", "portfolio"}
        assert not _keys(item) & SEALED_KEYS
        assert set(item["portfolio"]) == {"equity_micro", "realized_to_date_micro"}


def test_a17_venue_projection_omits_positions_and_entry_prices(monkeypatch):
    venue = SimpleNamespace(
        account=lambda: SimpleNamespace(equity_usd=Decimal("12.345678"), positions=(
            SimpleNamespace(coin="PRIVATE_COIN", entry_px=Decimal("123")),
        )),
        _guarded=lambda label, call: call(), _address="fixture",
        _info=SimpleNamespace(user_fills_by_time=lambda *a: [
            {"closedPnl": "0.001", "time": 1},
        ]),
    )
    monkeypatch.setattr("factorylab.world.exchange.HyperliquidExchange", lambda **kw: venue)
    assert _venue(load_manifest("scripted")) == {
        "equity_micro": 12_345_678, "realized_to_date_micro": 1000,
    }
