"""Cold audit, seat 4: money paths that lose or misbook micro-dollars.

Each test reproduces one finding in docs/audits/v2/defects-fable.md and fails on the
audited commit. Nothing here touches a network; keys are throwaway.
"""

import json
from dataclasses import replace
from decimal import Decimal

from eth_account import Account

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.loop import run_world
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import FakeExchange
from factorylab.world.market import X402MeteredModel, X402Provider
from factorylab.world.metering import Meter
from factorylab.world.models import PriceTable, TokenPrice
from factorylab.world.scripted import ScriptedProvider
from factorylab.world.x402 import HTTPResponse


class RecordedProvider:
    name = "recorded-provider"

    def __init__(self):
        self.inner = ScriptedProvider()
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        return self.inner.complete(request)


class Venue(FakeExchange):
    def __init__(self):
        super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))


def _items(path, manifest):
    return Ledger.reopen(path, manifest=json.loads(manifest.canonical_json()))._recovery_items()


def test_replay_of_an_interrupted_event_does_not_charge_undispatched_model_calls(tmp_path):
    """Finding 4: a process death after decision.open but before io.call means the provider was
    never contacted; the journal knows this ("never dispatched") yet metering books the full
    ceiling as an uncertain bill. Real money is not owed to anyone."""
    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)))
    path = str(tmp_path / "w.jsonl")
    clock = ClockSource(1_000_000_000, 1_000_000_000, 8)
    run_world(m, events=8, seed=1, ledger_path=path, provider=RecordedProvider(),
              exchange=Venue(), clock_source=clock.events())
    diary = _items(path, m)
    snap = next(s["seq"] for s in diary if s["kind"] == "snapshot")
    call = next(i for i in diary if i["kind"] == "io.call" and i["name"] == "provider.complete"
                and i["seq"] > snap)
    balance_at_cut = next(i["balance_after"] for i in reversed(diary)
                          if i["seq"] < call["seq"] and i["kind"].startswith("wallet."))
    lines = (tmp_path / "w.jsonl").read_bytes().splitlines(keepends=True)
    (tmp_path / "w.jsonl").write_bytes(b"".join(lines[: call["seq"] + 1]))
    # A process dying at this call could not have written the head of a snapshot after it
    # (price windows close every few ticks now, time audit T1).
    (tmp_path / "w.jsonl.head").unlink(missing_ok=True)
    provider = RecordedProvider()
    rt = resume_runtime(m, path, provider=provider, exchange=Venue(),
                        clock_source=ClockSource(1_000_000_000, 1_000_000_000, 8).events(),
                        now_ns=10**15)
    try:
        evidence = rt.ledger._recovery_items()
        booked = [i for i in evidence if i["kind"] == "metering.uncertain"
                  and i["seq"] >= call["seq"]]
        assert provider.calls == 0  # the journal refused to dispatch it, correctly
        assert booked == [], "an undispatched call was booked as an uncertain vendor bill"
        assert rt.wallet.balance == balance_at_cut
    finally:
        rt._ledger_lock.close()


SELLER = "https://seller.example"
MODEL = f"x402:{SELLER}#glm"
QUOTE = {"scheme": "exact", "network": "eip155:8453",
         "asset": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "amount": "100000",
         "payTo": "0x8e3c3e9c91cc0161b5e1cf138180ef3641d2371e", "maxTimeoutSeconds": 60}


def _seller_that_drops_the_paid_request(method, url, payload, headers):
    if "base.org" in url:  # the reserve's USDC balance read
        return HTTPResponse(200, {"jsonrpc": "2.0", "id": 1, "result": hex(50_000_000)})
    if "PAYMENT-SIGNATURE" in headers:
        raise ConnectionError("socket closed after the signed authorization was sent")
    return HTTPResponse(402, {"x402Version": 2, "accepts": [QUOTE]})


def test_unknown_x402_payment_outcome_does_not_leak_a_wallet_hold():
    """Finding 5: PaymentOutcomeUnknown leaves the quote held forever; nothing in the runtime
    resolves x402.unresolved. Every such call shrinks `available` until compute insolvency."""
    key = Account.create().key.hex()
    provider = X402Provider(private_key=key, transport=_seller_that_drops_the_paid_request)
    provider.register(MODEL, 100_000)
    ledger = Ledger()
    wallet = Wallet(1_000_000, ledger)
    prices = PriceTable()
    prices.register(MODEL, TokenPrice(0, 0, 100_000))
    model = X402MeteredModel(provider, prices, Meter(wallet), record=ledger.append,
                             on_unaffordable=lambda h: None)
    asm = Assembly(AssemblySpec("seller-user", 1, MODEL), model)
    for n in range(3):
        req = Request(f"decision-{n}", "hi", {}, {}, {"type": "object"}, 10**12,
                      max(0, wallet.available), None, "json", "verdict", f"decision-{n}")
        ret = asm.invoke(req)
        assert ret.status == "failed"
    holds = wallet.state()["reservations"]
    assert holds == [], f"{len(holds)} holds of {sum(h.amount for h in holds)} micro never close"
