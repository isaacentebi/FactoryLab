"""The look-ahead guard: a tape world's models and outside cannot know its future (C2).

A replayed market is in the past. A model trained on data that covers it, or a seat
that can search today's web, could know the price path it is about to be surprised
by, and evaluators graded on a consequence the web already knew would learn to consult
the web, not to judge (Chapter II §III.b). So a tape must postdate every model's
training cutoff on the menu, an unknown cutoff is admitted only when the operator says
so and the manifest records it, and a tape world has no web route, no connector fetch
and no live event-market reader.
"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.runtime.worlds import (
    TapeSpec,
    WebSpec,
    cutoff_end_ns,
    load_manifest,
    manifest_from_dict,
)
from factorylab.world.tape import Tape

TAPE = Tape.load(Path(__file__).parents[1] / "fixtures" / "tape" / "longrun1-2100.events.json")
WORLD = Path(__file__).parents[2] / "worlds" / "edition6-testnet-rehearsal.toml"


def _taped(*, allow=False, cutoffs=None, base=None):
    base = base or load_manifest("scripted")
    spec = TapeSpec.of(TAPE,
                    allow_unknown_cutoff=allow)
    # The scripted world buys web search on one route: a tape world lists none.
    models = tuple(replace(m, web=(), training_cutoff=(cutoffs or {}).get(m.id))
                   for m in base.models)
    return replace(base, models=models, web=WebSpec(),
                   exchange=replace(base.exchange, tape=spec, spot_pairs=()))


def test_a_tape_must_postdate_every_models_training_cutoff():
    ids = [m.id for m in load_manifest("scripted").models]
    # The fixture tape is 2026-09-24; data through the day before is fine.
    _taped(cutoffs=dict.fromkeys(ids, "2026-09-23")).validate()
    late = {**dict.fromkeys(ids, "2025-01-01"), ids[0]: "2026-09-24"}
    with pytest.raises(ValueError, match=f"look_ahead: model '{ids[0]}' was trained"):
        _taped(cutoffs=late).validate()
    # Allowing unknown cutoffs never admits a known one that covers the tape.
    with pytest.raises(ValueError, match="look_ahead"):
        _taped(cutoffs=late, allow=True).validate()
    assert cutoff_end_ns("2026-09-24") == 1790294400 * 10**9 > TAPE.start_ns


def test_an_unknown_cutoff_is_refused_unless_the_operator_admits_it_on_the_record():
    with pytest.raises(ValueError, match="states no training_cutoff"):
        _taped().validate()
    admitted = _taped(allow=True)
    admitted.validate()
    assert '"allow_unknown_cutoff":true' in admitted.canonical_json()
    assert admitted.manifest_hash() != replace(admitted, exchange=replace(
        admitted.exchange, tape=replace(admitted.exchange.tape,
                                        allow_unknown_cutoff=False))).manifest_hash()


def test_a_tape_world_has_no_web_route():
    base = _taped(allow=True)
    base.validate()
    plugin = replace(base.models[0], id="other/model", web=(("engine", "exa"),))
    searching = replace(base, models=(*base.models, plugin),
                        web=WebSpec(search_model="other/model", max_call_micro=1))
    with pytest.raises(ValueError, match="no \\[web\\] search route"):
        searching.validate()
    with pytest.raises(ValueError, match="lists no web route"):
        replace(base, models=(*base.models, plugin)).validate()
    online = replace(base.models[0], id=f"{base.models[0].id}:online")
    with pytest.raises(ValueError, match="lists no web route"):
        replace(base, models=(*base.models, online)).validate()


def test_a_training_cutoff_is_a_day_and_is_read_from_the_world_file():
    from tests.runtime.test_manifests import _base

    raw = _base()
    raw["models"][0]["training_cutoff"] = "2025-03-31"
    assert manifest_from_dict(raw).models[0].training_cutoff == "2025-03-31"
    raw["models"][0]["training_cutoff"] = "March 2025"
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        manifest_from_dict(raw)


def test_a_live_event_market_reader_is_refused_on_a_tape():
    from factorylab.runtime import polymarket
    from factorylab.world.polymarket import PolymarketReader

    reader = object.__new__(PolymarketReader)
    rt = SimpleNamespace(polymarket=SimpleNamespace(venue=SimpleNamespace(target=reader)),
                         _polymarket_ip_lock=None, m=_taped(allow=True), ledger_path="x")
    with pytest.raises(polymarket.LiveReaderRefused) as refused:
        polymarket.arm(rt)
    assert refused.value.code == "polymarket_live_on_a_tape"


def test_the_harness_turns_the_web_off_and_records_the_admission(tmp_path):
    from scripts import fastloop

    with pytest.raises(ValueError, match="states no training_cutoff"):
        fastloop.simulation_manifest(WORLD, 1, tape=TAPE)
    manifest = fastloop.simulation_manifest(WORLD, 1, tape=TAPE, allow_unknown_cutoff=True)
    assert manifest.web.search_model is None
    assert not [m for m in manifest.models if m.id.endswith(":online") or m.web]
    assert manifest.exchange.tape.allow_unknown_cutoff is True
    plain = fastloop.simulation_manifest(WORLD, 1)
    assert plain.web.search_model is not None  # off a tape nothing changes
    events = [{"kind": "event", "event": {"kind": "Launch", "payload": {
        "manifest": manifest_as_launched(manifest)}}}]
    card = fastloop.tape_card(events)
    assert card["web"] == "off" and card["allow_unknown_cutoff"] is True
    assert card["unknown_cutoffs"] == sorted(m.id for m in manifest.models)


def manifest_as_launched(manifest):
    import json

    return json.loads(manifest.canonical_json())


@pytest.mark.gate
def test_a_tape_world_publishes_no_connector_fetch_and_refuses_one(tmp_path):
    from decimal import Decimal

    from factorylab.runtime.loop import Runtime
    from factorylab.world.scripted import ScriptedProvider
    from factorylab.world.tape import TapeVenue

    manifest = _taped(allow=True)
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=.1, provider=ScriptedProvider(),
                 exchange=TapeVenue(TAPE, coins=manifest.exchange.coins,
                                    start_cash_usd=Decimal(100)))
    rt._ensure_connector_tool()
    assert "connector.fetch" not in rt.tool_specs
    answer, cost = rt._fetch_connector("seat", "h-1", {"id": "x", "path": "/"})
    assert cost == 0 and "no connector reads" in answer["error"]
    plain = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                    ledger_path=None, router_gamma=.1, provider=ScriptedProvider())
    plain._ensure_connector_tool()
    assert "connector.fetch" in plain.tool_specs
