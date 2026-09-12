"""Cold audit, seat 4: recorded failures that wedge a world permanently.

Each test reproduces one finding in docs/audits/v2/defects-fable.md. They fail on
the audited commit; a fix makes them pass. Nothing here touches a network.
"""

import json
from dataclasses import replace
from decimal import Decimal

from factorylab.runtime.loop import run_world
from factorylab.runtime.resume import resume_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.clock import ClockSource
from factorylab.world.exchange import FakeExchange, VenueUnavailable
from factorylab.world.scripted import ScriptedProvider


class RecordedProvider:
    """A non-deterministic (journaled) provider: the live path's shape, scripted replies."""

    name = "recorded-provider"

    def __init__(self):
        self.inner = ScriptedProvider()

    def complete(self, request):
        return self.inner.complete(request)


class FlakyVenue(FakeExchange):
    """A live-shaped venue whose mids() fails exactly once, on the third poll."""

    def __init__(self, healthy: bool = False):
        super().__init__(seed=1, coins=("BTC",), start_cash_usd=Decimal("100"))
        self.polls = 0
        self.healthy = healthy

    def mids(self):
        self.polls += 1
        if not self.healthy and self.polls == 3:
            raise VenueUnavailable("all_mids: ReadTimeout: three retries exhausted")
        return super().mids()


def _live_manifest():
    base = load_manifest("scripted")
    return replace(base, exchange=replace(base.exchange, kind="hyperliquid", coins=("BTC",)),
                   drip=None)


def _clock():
    return ClockSource(1_000_000_000, 1_000_000_000, 6).events()


def test_transient_venue_outage_is_weather_not_death(tmp_path):
    """Finding 1 (part 1): one failed mids() poll on a tick must not stop the world."""
    m = _live_manifest()
    summary = run_world(m, events=6, seed=1, ledger_path=str(tmp_path / "w.jsonl"),
                        provider=RecordedProvider(), exchange=FlakyVenue(), clock_source=_clock())
    assert summary["stats"]["events"] >= 6


def test_recorded_venue_failure_does_not_wedge_resume_forever(tmp_path):
    """Finding 1 (part 2): once journaled, the failure replays on every resume; a healthy venue
    must be able to continue the world."""
    m = _live_manifest()
    path = str(tmp_path / "w.jsonl")
    try:
        run_world(m, events=6, seed=1, ledger_path=path, provider=RecordedProvider(),
                  exchange=FlakyVenue(), clock_source=_clock())
    except RuntimeError:
        pass  # the crash itself is the first part of the finding
    summary = resume_world(m, path, provider=RecordedProvider(), exchange=FlakyVenue(healthy=True),
                           clock_source=_clock(), now_ns=10**15)
    assert summary["stats"]["events"] >= 6
    assert summary["stats"]["resumes"] == 1


class SurrogateProvider(ScriptedProvider):
    """The 12th producer reply carries a lone UTF-16 high surrogate (an emoji cut by max_tokens
    or a hostile seller); json.loads accepts the escape, the ledger's canonical encoder does not."""

    def complete(self, req):
        response = super().complete(req)
        if self._producer_calls == 12 and not getattr(self, "_done", False):
            self._done = True
            body = json.loads(response.text)
            body["rationale"] = "cut mid-emoji \ud83d"
            return replace(response, text=json.dumps(body))
        return response


def test_lone_surrogate_in_a_reply_does_not_crash_the_ledger(tmp_path):
    """Finding 2: a string the JSON parser accepts must not be able to crash Bus.publish."""
    m = load_manifest("scripted")
    summary = run_world(m, events=40, seed=1, ledger_path=str(tmp_path / "w.jsonl"),
                        provider=SurrogateProvider())
    assert summary["stats"]["events"] >= 40


def test_lone_surrogate_does_not_wedge_resume(tmp_path):
    """Finding 2 (part 2): the reply is journaled, so every resume replays the same crash."""
    m = load_manifest("scripted")
    path = str(tmp_path / "w.jsonl")
    try:
        run_world(m, events=40, seed=1, ledger_path=path, provider=SurrogateProvider())
    except UnicodeEncodeError:
        pass
    summary = resume_world(m, path, provider=SurrogateProvider())
    assert summary["stats"]["events"] >= 40


class BigRegionProvider(ScriptedProvider):
    """The scripted amendment's card is restated with a bound that parses to infinity."""

    def complete(self, req):
        response = super().complete(req)
        body = json.loads(response.text)
        for proposal in body.get("register", []):
            if proposal.get("kind") == "amendment":
                for card in proposal.get("add", []):
                    card["acceptable_region"] = "at most 1e400"
        return replace(response, text=json.dumps(body))


def test_overflowing_card_region_is_refused_or_unpriced_not_fatal(tmp_path):
    """Finding 3: an accepted amendment whose region overflows must carry no price, not crash
    the boundary that activates it (and every resume after it)."""
    m = load_manifest("scripted")
    summary = run_world(m, events=140, seed=1, ledger_path=str(tmp_path / "w.jsonl"),
                        provider=BigRegionProvider())
    assert summary["stats"]["events"] >= 140
    assert "turnover" not in summary["prices"]["cards"] or (
        summary["prices"]["cards"]["turnover"]["updates"] == 0
    )
