"""Chapter II §I.a: genesis seeds are not acts, but seed-shaped later rows are.

The fixture preserves two complete real rows (seq 4 and 165) from
scripted-s1/scripted-20260927-210633-s1/events.json: the first registration
and the first decision opening. No world, model, or venue is run.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from scripts import gauntlet as g

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/scripted_s1_genesis_rows.json"


@pytest.fixture
def rows():
    return g.load_events(FIXTURE)


def test_real_genesis_registration_is_not_an_act(rows):
    result = g.s1_draw_sovereignty(rows)
    assert result.status == g.PASS
    assert result.evidence["draws"] == 1
    assert result.evidence["acts"] == 0
    assert result.evidence["unreturned"] == []
    assert g.act_traces(rows, g.ACT_KINDS) == []


def test_same_seed_registration_after_first_open_fails(rows):
    seed, decision = rows
    late = {**seed, "seq": decision["seq"] + 1}
    result = g.s1_draw_sovereignty([seed, decision, late])
    assert result.status == g.FAIL
    assert result.evidence["acts"] == 1
    assert result.evidence["unreturned"] == [{
        "kind": "registry.register", "handle": None,
        "why": "no decision opened before it",
    }]


def test_genesis_only_diary_has_no_sovereignty_evidence(rows):
    result = g.s1_draw_sovereignty(rows[:1])
    assert result.status == g.UNSUPPORTED
    assert result.evidence["acts"] == 0


@pytest.mark.parametrize("change", [
    "missing_handle", "non_null_handle", "missing_contract", "null_contract",
    "missing_provenance", "non_seed_provenance", "other_act_kind",
])
def test_only_explicit_null_handle_seed_registrations_are_genesis(rows, change):
    seed, decision = deepcopy(rows)
    if change == "missing_handle":
        del seed["handle"]
    elif change == "non_null_handle":
        seed["handle"] = decision["handle"]
    elif change == "missing_contract":
        del seed["contract"]
    elif change == "null_contract":
        seed["contract"] = None
    elif change == "missing_provenance":
        del seed["contract"]["provenance"]
    elif change == "non_seed_provenance":
        seed["contract"]["provenance"] = decision["handle"]
    else:
        seed["kind"] = "order.intent"
    result = g.s1_draw_sovereignty([seed, decision])
    assert result.status == g.FAIL
    assert result.evidence["acts"] == 1


def test_any_first_decision_ends_genesis_even_without_a_handle(rows):
    seed, decision = rows
    malformed = {**decision, "handle": None}
    traces = g.act_traces([malformed, seed], g.ACT_KINDS)
    assert len(traces) == 1
    assert traces[0]["traced"] is False


def test_post_genesis_registration_with_own_ok_return_still_traces(rows):
    seed, decision = rows
    invocation = {"kind": "invocation", "handle": decision["handle"], "status": "ok"}
    registration = {**seed, "handle": decision["handle"],
                    "contract": {**seed["contract"], "provenance": decision["handle"]}}
    result = g.s1_draw_sovereignty([seed, decision, invocation, registration])
    assert result.status == g.PASS
    assert result.evidence["acts"] == 1


def test_seed_registration_after_the_first_world_event_fails_before_any_decision(rows):
    # Codex on #162: genesis is the boot, and it ends at the first delivered world event,
    # so a seed-shaped registration after that and before any decision opens is an act.
    seed, decision = rows
    first_event = {"kind": "event", "seq": decision["seq"] - 2, "payload": {}}
    late = {**seed, "seq": decision["seq"] - 1}
    result = g.s1_draw_sovereignty([seed, first_event, late, decision])
    assert result.status == g.FAIL
    assert result.evidence["unreturned"] == [{
        "kind": "registry.register", "handle": None,
        "why": "no decision opened before it",
    }]
