"""Two launch gates of edition 3 that no workstream test proves on its own.

`docs/plans/edition3-r3.md`, "Gates before the funded manifest". Financial reality:
every custody account reconciles with the treasury and the venue after a fill, a
funding print and a confirmed Venice purchase. Information boundaries: over one whole
scripted run in which every seat keeps a marked private state, no evaluator request
and no wake output carries it.

Nothing here reaches a network, a venue, a key or a paid model.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from factorylab.runtime.custody import ACCOUNTS, custody_view
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.wake import collect_wake, render_wake
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import (
    ScriptedProvider,
    _description_from_prompt,
    _inputs_from_prompt,
)
from tests.helpers import place
from tests.runtime.test_connectors import ledger_items

#: The private content a seat writes into its own working state. It must reach no
#: evaluator and no wake page; a marker rather than a field name, because the field
#: names appear legitimately in the public return schema every seat is shown.
PRIVATE_MARKER = "PRIVATE-WORKING-STATE-MARKER-9d41"

#: The keys of an evaluator's INPUTS that carry the subject it is judging. The rest
#: of the block is the judge's own `seat`/`your_state` head and the public `world`
#: schema, which names both continuity fields as types for every seat to read.
SUBJECT_KEYS = ("producer", "producer_outputs", "verdict", "window", "commission")

#: A testnet account far above the $120 edition 3 proposes to risk.
TESTNET_BALANCE = "966"


def custody_runtime(*, venue_usd: str = TESTNET_BALANCE) -> Runtime:
    """A scripted world on a venue holding `venue_usd`."""
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=50_000_000,
                 ledger_path=None, router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal(venue_usd)))
    rt._manage_reserve_window()
    return rt


# =====================================================================================
# Gate 1. Financial reality.
# =====================================================================================


class TestGateOneFinancialReality:
    """"separate custody reconciliation"."""

    def test_every_custody_account_reconciles_with_the_treasury_and_the_venue(self):
        """"separate custody reconciliation", after a fill, a funding print and a bridge.

        One runtime, three money events of different kinds, and then every account
        the `you` block can show is read back against the thing that actually holds
        it: the treasury's cached pots for the provider credits and the reserve,
        the venue's own account for perps and spot. Nothing is derived twice and
        nothing is fabricated.
        """
        rt = custody_runtime()
        treasury = rt.treasury
        treasury.open_window(1)

        # (1) A fill: a position at the venue, well inside the venue account.
        size = (Decimal(200) / rt.exchange.mids()["BTC"]).quantize(Decimal("0.00001"))
        assert place(rt, str(size))["status"] == "filled"

        # (2) A funding print: venue time crosses a funding boundary and the
        # position pays, on the venue account and nowhere else.
        authority_before = rt.wallet.balance
        rt._settle_exchange_effects(
            rt.exchange.target.advance(rt.exchange.target._now_ns + 2 * 60 * 60 * 10**9))
        funding = [i for i in ledger_items(rt, "venue.settled") if i["reason"] == "funding"]
        assert funding and all(i["custody"] == "venue_perps" for i in funding)
        assert rt.wallet.balance == authority_before  # authority is not venue cash

        # (3) A confirmed Venice purchase: reserve becomes prepaid Venice credit.
        treasury.transfer("to_reserve", "20", handle="fund-reserve", now_ns=1)
        treasury.tick(2)
        assert treasury.transfer("to_venice", "5", handle="population",
                                 now_ns=3)["status"] == "submitted"
        assert treasury.tick(4)[0]["status"] == "confirmed"

        rt.ticks_consumed += 1  # a fresh tick, so the account memo is re-read
        view = custody_view(rt)
        pots = treasury.pots()
        account = rt.exchange.account()

        assert set(ACCOUNTS) <= set(view)
        assert all(view[name]["status"] == "observed" for name in ACCOUNTS)
        # Provider credit and the reserve are the treasury's cached reads, exactly.
        assert view["openrouter_credit"]["balance_micro"] == pots["seed"]
        assert view["venice_credit"]["balance_micro"] == pots["sellers"]["venice"]
        assert view["base_reserve"]["balance_micro"] == pots["reserve"]
        # The venue's two accounts are the venue's own answer, split by custody.
        assert view["venue_perps"]["equity_usd"] == str(account.perps_equity_usd)
        assert view["venue_perps"]["cash_usd"] == str(account.cash_usd)
        assert view["venue_perps"]["margin_used_usd"] == str(account.margin_used_usd)
        assert [p["coin"] for p in view["venue_perps"]["positions"]] == [
            p.coin for p in account.positions]
        assert [b["coin"] for b in view["venue_spot"]["balances"]] == [
            b.coin for b in account.spot_balances]
        # The bridge landed, and it landed once: nothing is in flight.
        assert view["pending_conversions"]["transfers"] == []
        assert pots["converted_from_principal_micro"] == 5_000_000
        assert pots["earned_micro"] == 0  # principal converted is financing, not income
        # The compute wallet is here as authority and never as an asset.
        assert view["authority"]["kind"] == "authority"
        assert view["authority"]["balance_micro"] == rt.wallet.balance
        assert "balance_micro" not in view["venue_perps"]


# =====================================================================================
# Gate 2. Continuity and information boundaries.
# =====================================================================================


def _seats_that_leak(sink: list) -> type:
    """A provider that records every rendered request and marks every working state."""

    class Leaky(ScriptedProvider):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            sink.append((_description_from_prompt(text), req.system, text))
            response = super().complete(req)
            try:
                payload = json.loads(response.text)
            except json.JSONDecodeError:  # pragma: no cover - scripted replies are JSON
                return response
            if not isinstance(payload, dict):  # pragma: no cover
                return response
            # Every seat keeps something private and acknowledges something.
            payload["working_state"] = {"note": PRIVATE_MARKER}
            payload["ack_through"] = "outcome:1"
            return ModelResponse(response.model_id, json.dumps(payload),
                                 response.input_tokens, response.output_tokens,
                                 response.stop_reason)

    return Leaky


@pytest.fixture(scope="module")
def leaky_world(tmp_path_factory):
    """One scripted run in which every seat writes a marked private state.

    Thirty world events is enough for the routers to commission judges and metas
    many times over (the assertions check that they did), with no network, no venue
    and no key.
    """
    sink: list[tuple[str, str, str]] = []
    path = tmp_path_factory.mktemp("launch-gates") / "leaky.jsonl"
    run_world(load_manifest("scripted"), events=30, seed=1, ledger_path=str(path),
              provider=_seats_that_leak(sink)())
    return sink, path


def _private_keys(value) -> list[str]:
    """Every private continuity field found anywhere inside a decoded structure."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("working_state", "ack_through"):
                found.append(key)
            found.extend(_private_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_private_keys(item))
    return found


class TestGateTwoContinuityAndInformationBoundaries:
    """"no judge or wake gets private state"."""


    def test_the_run_actually_planted_a_private_state_in_every_seat(self, leaky_world):
        """A scan that found nothing because nothing was there proves nothing."""
        sink, path = leaky_world
        assert len(sink) > 100
        own = [text for _, _, text in sink if PRIVATE_MARKER in text]
        assert own, "no seat was ever shown its own working state"

    def test_no_evaluator_request_carries_any_seats_working_state_or_ack_through(
            self, leaky_world):
        """"no judge ... gets private state", scanned over every request of a whole run.

        A seat's own `YOU` block legitimately carries that seat's own working
        state, and the public return schema every seat is shown legitimately
        names both continuity fields as types. So the scan is on the two things
        that would be a disclosure: the marked *content* of a working state
        appearing anywhere outside the `YOU` block of the seat it belongs to, and
        either field appearing as a *value* inside the inputs a judge is
        commissioned on -- which is where a forwarded producer return arrives.
        """
        sink, _ = leaky_world
        commissions = [(desc, system, text) for desc, system, text in sink
                       if desc.startswith(("Give verdict", "Assess"))]
        assert len(commissions) > 20, "the run commissioned no evaluation to scan"
        scanned = 0
        for desc, system, text in commissions:
            assert PRIVATE_MARKER not in system
            # Judges see the work, not the producer's world, so a commission may
            # carry no WORLD UPDATE; its YOU block then ends where REQUEST begins.
            marker = ("\n\nWORLD UPDATE\n" if "\n\nWORLD UPDATE\n" in text
                      else "\n\nREQUEST\n")
            you, rest = text.split(marker, 1)
            # Nothing a seat kept privately reaches the evaluator's world or inputs.
            assert PRIVATE_MARKER not in rest, desc
            assert text.count(PRIVATE_MARKER) <= 1, desc  # its own head, at most
            # And the forwarded subject carries neither continuity field as a value.
            inputs = _inputs_from_prompt(text)
            subject = {k: v for k, v in inputs.items() if k in SUBJECT_KEYS}
            assert subject, desc
            assert _private_keys(subject) == [], desc
            scanned += 1
        assert scanned == len(commissions)

    def test_no_wake_output_carries_any_seats_working_state_or_ack_through(
            self, leaky_world):
        """"no ... wake gets private state", over the JSON and the page it renders."""
        _, path = leaky_world
        data = collect_wake(path)
        page = render_wake(data)
        assert _private_keys(data) == []
        blob = json.dumps(data)
        assert PRIVATE_MARKER not in blob and PRIVATE_MARKER not in page
        assert "working_state" not in blob and "working_state" not in page
        assert "ack_through" not in blob and "ack_through" not in page
        # The wake did publish the run: an empty page would pass the scan vacuously.
        assert data["returns"] and data["invocations_by_assembly"]
