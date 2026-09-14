"""A rerun of one manifest must not reuse a previous run's venue order identities.

The manifest's ``client_namespace`` is fixed and decision handles restart at
``decision-1`` on a fresh ledger, so the namespace alone cannot separate two runs
of the same world. A launch nonce, drawn once per launch and carried by the
``Launch`` event, makes the per-run venue identity unique; resume restores it so a
continued world keeps the identities it already submitted.
"""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from factorylab.kernel.events import EventKind
from factorylab.runtime.live import LiveClock
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, HyperliquidExchange, OrderResult
from factorylab.world.scripted import ScriptedProvider
from tests.audit.test_r3_b_authority import _producing_decision
from tests.conftest import make_runtime

NAMESPACE = "a" * 32
HANDLE = "decision-1"


def _adapter(nonce: str | None, namespace: str | None = NAMESPACE) -> HyperliquidExchange:
    """A signing-free adapter: only identity derivation and status parsing are exercised."""
    exchange = HyperliquidExchange.__new__(HyperliquidExchange)
    if namespace is not None:
        exchange._client_namespace = namespace
    if nonce is not None:
        exchange._launch_nonce = nonce
    exchange._address = "0x" + "11" * 20
    return exchange


class IdentityVenue(FakeExchange):
    """A deterministic venue that derives client order IDs the Hyperliquid way."""

    client_id = HyperliquidExchange.client_id


def _live_runtime(namespace: str | None = NAMESPACE) -> Runtime:
    """A live-kind scripted world whose venue adapter derives real client order IDs."""
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        exchange=replace(manifest.exchange, kind="hyperliquid", client_namespace=namespace),
        drip=None,
    )
    venue = IdentityVenue()
    if namespace is not None:
        venue._client_namespace = namespace
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=100_000_000,
                   ledger_path=None, drip=False, router_gamma=0.1,
                   exchange=venue, provider=ScriptedProvider())


def test_two_fresh_runs_of_one_manifest_produce_disjoint_client_order_ids():
    first, second = _live_runtime(), _live_runtime()
    assert first.launch_nonce and first.launch_nonce != second.launch_nonce
    assert (first.exchange.target.client_id(HANDLE).to_raw()
            != second.exchange.target.client_id(HANDLE).to_raw())


def test_the_launch_nonce_is_ledgered_at_genesis_with_the_manifest():
    rt = _live_runtime()
    rt._launch()
    launch = next(item for item in rt.ledger._recovery_items()
                  if item.get("kind") == "event" and item["event"]["kind"] == str(EventKind.LAUNCH))
    assert launch["event"]["payload"]["launch_nonce"] == rt.launch_nonce
    assert launch["event"]["payload"]["manifest_hash"] == rt.m.manifest_hash()


def test_a_resumed_world_reproduces_its_own_client_order_ids():
    original = _live_runtime()
    original._launch()
    identity = original.exchange.target.client_id(HANDLE).to_raw()
    state = runtime_state(original)
    continued = _live_runtime()
    assert continued.exchange.target.client_id(HANDLE).to_raw() != identity
    restore_runtime(continued, state)
    assert continued.launch_nonce == original.launch_nonce
    assert continued.exchange.target.client_id(HANDLE).to_raw() == identity


def test_a_checkpoint_without_a_launch_nonce_keeps_its_historical_identities():
    original = _live_runtime()
    state = runtime_state(original)
    state["runtime"]["$map"] = [pair for pair in state["runtime"]["$map"]
                                if pair[0] != "launch_nonce"]
    legacy = _live_runtime()
    restore_runtime(legacy, state)
    assert legacy.launch_nonce is None
    assert (legacy.exchange.target.client_id(HANDLE).to_raw()
            == _adapter(None).client_id(HANDLE).to_raw())


def test_a_lookup_returning_another_launchs_order_is_not_ours_and_stays_uncertain():
    run_a, run_b = _adapter("launch-a"), _adapter("launch-b")
    foreign = run_a.client_id(HANDLE).to_raw()
    assert foreign != run_b.client_id(HANDLE).to_raw()
    run_b._info = SimpleNamespace(query_order_by_cloid=lambda address, cloid: {
        "status": "order",
        "order": {"status": "filled",
                  "order": {"oid": 60069513073, "origSz": "0.11", "sz": "0", "cloid": foreign}},
    })
    result = run_b.lookup(HANDLE)
    assert result.status == "uncertain" and result.filled_size == Decimal(0)
    assert "identity" in (result.error or "")


def test_an_uncertain_order_resolved_by_a_foreign_identity_is_ledgered_uncertain(monkeypatch):
    rt = make_runtime(live=True, clock_source=LiveClock(1, 0, now_ns=lambda: 0))
    exchange = rt.exchange.target
    run_a = _adapter("launch-a")
    run_b = _adapter("launch-b")
    foreign = run_a.client_id(HANDLE).to_raw()
    run_b._info = SimpleNamespace(query_order_by_cloid=lambda address, cloid: {
        "status": "order",
        "order": {"status": "filled",
                  "order": {"oid": 60069513073, "origSz": "0.001", "sz": "0", "cloid": foreign}},
    })
    monkeypatch.setattr(exchange, "place", lambda order: OrderResult(
        None, "uncertain", Decimal(0), None, "submit timeout"))
    monkeypatch.setattr(exchange, "lookup", lambda client_id, **kw: run_b.lookup(client_id, **kw))
    handle = _producing_decision(rt)
    result = rt._venue_write(handle, "venue.place_market", {
        "coin": "BTC", "side": "buy", "size": "0.001"}, slot="output")
    assert result["status"] == "uncertain"
    assert rt.stats.fills == 0
    reasons = [item["result"].get("error", "") for item in rt.ledger._recovery_items()
               if item.get("kind") == "order.uncertain"]
    assert any("identity" in reason for reason in reasons)
