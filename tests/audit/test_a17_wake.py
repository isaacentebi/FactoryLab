"""A17: outcomes escape the sealed wake while identities and positions do not."""

import json
from decimal import Decimal
from types import SimpleNamespace

from factorylab.kernel.ledger import Ledger
from factorylab.runtime.wake import _Snapshot, _venue
from factorylab.runtime.worlds import load_manifest


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
