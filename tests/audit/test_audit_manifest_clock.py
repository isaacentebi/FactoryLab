"""Cold audit, seat 4: the architect's one move (the manifest) and the resumed clock.

Each test reproduces one finding in docs/audits/v2/defects-fable.md and fails on the
audited commit.
"""

from dataclasses import replace

import pytest

from factorylab.runtime.live import LiveClock
from factorylab.runtime.worlds import TimingSpec, load_manifest

NS = 1_000_000_000


def test_manifest_validation_rejects_a_min_ratio_the_cascade_will_refuse():
    """Finding 8 (round-one Opus #11, still on main): validate() accepts timing.min_ratio in
    {1, 2}; cascade.release_threshold raises on the first Verdict. The documented pre-launch
    check (`factorylab manifest`) passes a manifest whose world dies at its first judgement."""
    base = load_manifest("scripted")
    for ratio in (1, 2):
        with pytest.raises(ValueError):
            replace(base, timing=TimingSpec(ratio, 0.2, 200)).validate()


def test_a_resumed_live_clock_does_not_sleep_through_a_backward_wall_clock_step():
    """Finding 9 (minor): LiveClock.restore continues from the ledger's last tick; if the host
    clock is behind it (a restored VM, an NTP step) the world sleeps for the whole gap with
    positions unmanaged. The sleep must be bounded by the tick interval."""
    now = 2_000_000 * NS
    sleeps = []
    saved = {"interval_ns": 60 * NS, "count": 3, "source": "wallclock", "index": 1,
             "last_ns": now + 6 * 3600 * NS}  # the diary's last tick is six hours ahead
    clock = LiveClock.restore(saved, now_ns=lambda: now, sleep=sleeps.append)
    next(clock.events())
    assert max(sleeps, default=0) <= 60


def test_a_resume_snapshot_pins_the_venue_account_it_will_reattach_to(tmp_path):
    """Finding 12 (round-one Opus #15, not in the fix list, still on main): the snapshot's
    adapter block records only the venue's name and determinism, never the account address the
    key file resolves to. A resume with the wrong key file silently reattaches another account;
    LiveRail pins the reserve address, the venue has no equivalent."""
    from decimal import Decimal

    from factorylab.runtime.loop import Runtime, ScriptedProvider
    from factorylab.runtime.resume import runtime_state
    from factorylab.world.exchange import FakeExchange

    class Venue(FakeExchange):
        name = "hyperliquid-testnet"
        address = "0x1228e5620944a79D268Afc7522E00891526EdEBb"

        def __init__(self):
            super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))

    class Provider:
        name = "recorded-provider"

        def complete(self, request):
            return ScriptedProvider().complete(request)

    base = load_manifest("scripted")
    m = replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)),
                drip=None)
    rt = Runtime(m, events=1, seed=1, initial_balance_micro=None, ledger_path=None, drip=False,
                 router_gamma=0.1, provider=Provider(), exchange=Venue())
    try:
        adapters = runtime_state(rt)["adapters"]["exchange"]
    finally:
        rt._ledger_lock.close()
    assert any("address" in key or "account" in key for key in adapters), adapters
