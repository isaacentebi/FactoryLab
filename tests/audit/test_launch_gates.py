"""The four launch gates of edition 3, one class each, in the reviewer's own sentences.

`docs/plans/edition3-r3.md`, "Gates before the funded manifest", from GPT-6 Pro's
third reading §11 ("Launch gates, stop conditions, a week"):

    Four gates: financial reality (test with the real $120 principal; separate
    custody reconciliation, exact-once income, correct collateral scope, a bridge
    that replenishes only the destination); continuity and information boundaries
    (inject failures around state, inbox, acks, restore; every owner receives
    fills and refusals; missing data stays unavailable; no judge or wake gets
    private state); finality (partial fills, resting closes, unavailable mids,
    ledger failures, dropped acks, repeated kills, restarts; production stays
    dead; residual exposure reported under restricted authority; renaming files
    or unsetting a variable cannot revive an identity); selection and temporal
    behaviour (re-screen final prompts and routes; no synthetic work; real
    evidence windows; a usable way to decline or reshape evaluation spend without
    a quota).

This file is the gate, not a second implementation of the round-3 workstreams. A
sentence a workstream already proves is proved here by calling that test, so the
gate fails when the workstream's proof fails and never drifts away from it. A
sentence nothing covered is written here.

What the workstreams already prove, by gate:

**1. Financial reality.** Exact-once income is
`tests/audit/test_r3b_money.py::test_a_duplicate_receipt_books_once_and_a_conflicting_one_refuses`
(and the spool-then-chain pair beside it); the bridge that replenishes only its
destination is
`::test_a_confirmed_venice_purchase_moves_reserve_to_venice_credit_exactly`;
collateral scope from the view is
`::test_the_collateral_view_is_the_pot_the_venue_would_actually_charge`,
`::test_the_check_admits_what_the_venue_can_carry_and_refuses_what_it_cannot` and
`::test_spot_and_perp_are_checked_separately_against_their_own_balances`; a failed
venue read staying `unavailable` is
`::test_a_failed_venue_read_is_unavailable_and_never_the_wallet_balance`. New here:
the declared trading principal (`[venue] principal_usd`), now inert under architect
decision D1 (the venue account is the only limit; the gate is met by holding only the
proposed principal there), and the custody reconciliation across a fill, a funding
print and a confirmed Venice purchase, which no single test performed end to end.

**2. Continuity and information boundaries.** The four fault injections are
`tests/audit/test_gpt6_third_regressions.py::test_failed_state_evidence_leaves_the_head_where_it_was`
(a state write that fails at the ledger),
`::test_failed_inbox_evidence_publishes_no_item` (an inbox delivery that fails),
`::test_acknowledging_an_item_never_acknowledges_one_never_shown` (a dropped
acknowledgement) and
`tests/audit/test_r3c_death.py::test_a_refused_restore_leaves_every_runtime_field_exactly_as_it_was`
(a restore that is refused). Owner-addressed consequences are
`tests/audit/test_r3f_attention.py::test_a_refusal_lands_in_the_deciding_seats_inbox_with_an_id`
and `::test_a_fill_reaches_the_ordering_seat_not_whoever_was_awake`. Missing data
staying `unavailable` is
`test_gpt6_third_regressions.py::test_missing_state_is_unavailable_not_absent` and
`tests/audit/test_r3e_prompts.py`'s
`::test_a_missing_source_renders_unavailable_and_never_a_fabricated_number`.
New here: the whole-run scan — every rendered evaluator request and the wake JSON
of a world whose every seat returns a marked `working_state` and an `ack_through`.
`test_gpt6_third_regressions.py::test_forwarded_outputs_are_projected_before_they_cross_a_boundary`
proves the projection function; this proves the run.

**3. Finality.** Every sentence is `tests/audit/test_r3c_death.py`, called here:
resting closes and partial fills, unavailable mids, ledger failures, dropped
acknowledgements, repeated kills, restarts mid-wind-down, the executor's bounded
authority, and the two revivals that must fail (renaming the diary, unsetting
`FACTORYLAB_WITNESS_URL`). Nothing is duplicated.

**4. Selection and time.** The empty draw is
`tests/audit/test_r3d_evaluation.py::test_no_producer_return_exists_for_an_empty_draw`;
the tier that does not fire on arrivals is
`::test_three_simultaneous_arrivals_do_not_trigger_a_tier`;
the declinable commission is `::test_a_declined_commission_costs_only_the_call`.
New here: the two runs the plan asks for on the *final* manifest — `scripts/rehearsal.py
preflight` on a namespace copy passing every gate (the existing
`test_e3_world.py::test_edition3_preflight_passes_every_gate_up_to_the_namespace`
stops at the missing namespace on purpose), and `scripts/calibrate_seats.py
--cases --offline` passing.

Nothing here reaches a network, a venue, a key or a paid model. The one test that
needs testnet is skipped with its reason; the coordinator runs it by hand.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.kernel.artifacts import ArtifactStore
from factorylab.runtime import witness
from factorylab.runtime.continuity import OutcomeInbox
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
from tests.audit.test_r1_venue_collateral import place
from tests.runtime.test_connectors import ledger_items

# The workstream tests this gate calls rather than restates.
from tests.audit import test_gpt6_third_regressions as regressions  # isort: skip
from tests.audit import test_r3b_money as money  # isort: skip
from tests.audit import test_r3c_death as death  # isort: skip
from tests.audit import test_r3d_evaluation as evaluation  # isort: skip
from tests.audit import test_r3f_attention as attention  # isort: skip

#: The private content a seat writes into its own working state. It must reach no
#: evaluator and no wake page; a marker rather than a field name, because the field
#: names appear legitimately in the public return schema every seat is shown.
PRIVATE_MARKER = "PRIVATE-WORKING-STATE-MARKER-9d41"

#: The keys of an evaluator's INPUTS that carry the subject it is judging. The rest
#: of the block is the judge's own `seat`/`your_state` head and the public `world`
#: schema, which names both continuity fields as types for every seat to read.
SUBJECT_KEYS = ("producer", "producer_outputs", "verdict", "window", "commission")

#: A testnet account far above the principal edition 3 proposes to risk.
TESTNET_BALANCE = "966"
DECLARED_PRINCIPAL = "120"


@pytest.fixture
def fresh_inbox():
    """`test_r3f_attention`'s own inbox fixture, as a factory.

    Its tests each take a new inbox; calling two of them on one would let the
    first one's items answer the second's assertions.
    """
    def make():
        ledger = attention.Evidence()
        clock = SimpleNamespace(ns=0)
        store = ArtifactStore(ledger, root=None, clock_ns=lambda: clock.ns)
        return OutcomeInbox(store, ledger, lambda: clock.ns)

    return make


@pytest.fixture(autouse=True)
def _fresh_witness(monkeypatch):
    """`test_r3c_death`'s own autouse fixture, which an imported test no longer carries.

    No receiver in the environment, no inherited note and no remembered kill: each
    call of a finality test witnesses only its own death.
    """
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())
    witness.note_wind_down(wind_down=False, orders=0)


def principal_runtime(*, venue_usd: str = TESTNET_BALANCE,
                      principal: str | None = DECLARED_PRINCIPAL) -> Runtime:
    """A scripted world on a venue holding `venue_usd` that declares `principal`."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, exchange=replace(manifest.exchange,
                                                  principal_usd=principal))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=50_000_000,
                 ledger_path=None, drip=False, router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal(venue_usd)))
    rt._manage_reserve_window()
    return rt


# =====================================================================================
# Gate 1. Financial reality.
# =====================================================================================


class TestGateOneFinancialReality:
    """"test with the real $120 principal; separate custody reconciliation, exact-once
    income, correct collateral scope, a bridge that replenishes only the destination"."""

    def test_a_declared_principal_is_inert_and_the_venue_is_the_only_limit(self):
        """Architect decision D1 retired the principal cap this gate used to prove.

        The reviewer offered two ways to launch at the proposed size: withdraw the
        rest of the testnet balance, or declare the principal so the runtime refuses
        to use more. The second is a limit supplied from outside, a Class-2
        imposition, and it is gone: the gate is now met by holding only the proposed
        principal at the venue. A world that still declares ``principal_usd`` loads,
        and the collateral view is the venue's own, unchanged.
        """
        rt = principal_runtime()
        view = rt._collateral_view("BTC")
        assert view["eligible_equity_usd"] == Decimal(TESTNET_BALANCE)
        assert "principal_cap_usd" not in view
        beyond = (Decimal(900) / rt.exchange.mids()["BTC"]).quantize(Decimal("0.00001"))
        assert place(rt, str(beyond))["status"] == "filled"

    def test_a_world_that_declares_no_principal_is_collateralised_by_the_venue_alone(self):
        rt = principal_runtime(principal=None)
        view = rt._collateral_view("BTC")
        assert view["eligible_equity_usd"] == Decimal(TESTNET_BALANCE)
        assert "principal_cap_usd" not in view
        beyond = (Decimal(900) / rt.exchange.mids()["BTC"]).quantize(Decimal("0.00001"))
        assert place(rt, str(beyond))["status"] == "filled"

    def test_every_custody_account_reconciles_with_the_treasury_and_the_venue(self):
        """"separate custody reconciliation", after a fill, a funding print and a bridge.

        One runtime, three money events of different kinds, and then every account
        the `you` block can show is read back against the thing that actually holds
        it: the treasury's cached pots for the provider credits and the reserve,
        the venue's own account for perps and spot. Nothing is derived twice and
        nothing is fabricated.
        """
        rt = principal_runtime()
        treasury = rt.treasury
        treasury.open_window(1)

        # (1) A fill: a position at the venue, inside the declared principal.
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

    def test_income_books_exactly_once_and_a_conflicting_receipt_refuses(self):
        """"exact-once income" -- `test_r3b_money`'s own proof, called rather than restated."""
        money.test_a_duplicate_receipt_books_once_and_a_conflicting_one_refuses()

    def test_a_spooled_receipt_is_only_a_claim_until_the_chain_read_confirms_it(self, tmp_path):
        """The other half of exact-once: a claim the chain has not confirmed is not income."""
        money.test_a_spooled_receipt_is_a_claim_until_the_chain_read_confirms_it(tmp_path)

    def test_a_chain_that_contradicts_a_claim_books_nothing(self, monkeypatch):
        money.test_a_chain_that_contradicts_a_claim_books_nothing(monkeypatch)

    def test_a_confirmed_bridge_credits_venice_and_replenishes_nothing_else(self):
        """"a bridge that replenishes only the destination"."""
        money.test_a_confirmed_venice_purchase_moves_reserve_to_venice_credit_exactly()

    def test_the_collateral_check_is_the_views_pool_with_spot_and_perp_separate(self):
        """"correct collateral scope", from the view, both markets."""
        money.test_the_collateral_view_is_the_pot_the_venue_would_actually_charge()
        money.test_the_check_admits_what_the_venue_can_carry_and_refuses_what_it_cannot()
        money.test_spot_and_perp_are_checked_separately_against_their_own_balances()

    def test_unknown_or_stale_collateral_blocks_new_risk_and_never_a_reduction(
            self, monkeypatch):
        money.test_unknown_collateral_blocks_new_risk_and_never_a_reduction(monkeypatch)
        money.test_a_stale_collateral_observation_blocks_new_risk()

    def test_a_failed_venue_read_is_unavailable_and_never_a_fabricated_account(
            self, monkeypatch):
        money.test_a_failed_venue_read_is_unavailable_and_never_the_wallet_balance(monkeypatch)

    def test_the_edition_3_manifest_still_loads_its_deprecated_principal(self):
        """The retired key still loads and still hashes; nothing enforces it (D1)."""
        m = load_manifest("edition3-testnet")
        assert m.exchange.principal_usd == DECLARED_PRINCIPAL
        assert m.exchange.start_cash_usd == DECLARED_PRINCIPAL
        assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
        # Hash-neutral where it is absent: a key added for this gate renames no world.
        payload = json.loads(load_manifest("edition2-testnet").canonical_json())
        assert "principal_usd" not in payload["exchange"]
        assert load_manifest("edition2-testnet").manifest_hash() == (
            "b184b1d8dc55daf56978ea51181be0d06e59493bef2727d97b2f67717efdbf8b")


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

    Sixty world events is enough for the routers to commission judges and metas
    many times over (the assertions check that they did), and the whole run is
    under half a minute with no network, no venue and no key.
    """
    sink: list[tuple[str, str, str]] = []
    path = tmp_path_factory.mktemp("launch-gates") / "leaky.jsonl"
    run_world(load_manifest("scripted"), events=60, seed=1, ledger_path=str(path),
              drip=False, provider=_seats_that_leak(sink)())
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
    """"inject failures around state, inbox, acks, restore; every owner receives fills
    and refusals; missing data stays unavailable; no judge or wake gets private state"."""

    # ---- inject failures around state, inbox, acks, restore -------------------------

    def test_a_state_write_that_fails_at_the_ledger_leaves_the_head_where_it_was(self):
        """"inject failures around state": the seat keeps what it had, nothing invented."""
        regressions.test_failed_state_evidence_leaves_the_head_where_it_was()

    def test_an_inbox_delivery_that_fails_publishes_no_item(self):
        """"inject failures around ... inbox": a failed delivery is not a delivery."""
        regressions.test_failed_inbox_evidence_publishes_no_item()

    def test_a_dropped_acknowledgement_acknowledges_nothing_the_seat_never_saw(
            self, fresh_inbox):
        """"inject failures around ... acks": an ack past a gap does not swallow the gap."""
        regressions.test_acknowledging_an_item_never_acknowledges_one_never_shown()
        attention.test_ack_through_an_old_id_leaves_newer_items_unread(fresh_inbox())
        attention.test_ack_through_cannot_acknowledge_an_item_the_seat_was_never_shown(
            fresh_inbox())

    def test_a_restore_that_is_refused_leaves_every_runtime_field_exactly_as_it_was(
            self, monkeypatch, tmp_path):
        """"inject failures around ... restore": a refusal is a no-op, on every path."""
        death.test_a_refused_restore_leaves_every_runtime_field_exactly_as_it_was(monkeypatch)
        death.test_a_restore_refused_on_the_witness_requirement_changes_nothing(monkeypatch)
        death.test_a_restore_refused_on_a_missing_artifact_changes_nothing(tmp_path)

    # ---- every owner receives its fills and refusals, with an outcome id ------------

    def test_every_owner_receives_its_fills_and_refusals_with_an_outcome_id(
            self, fresh_inbox):
        """"every owner receives fills and refusals" -- addressed, and exactly addressable."""
        attention.test_a_refusal_lands_in_the_deciding_seats_inbox_with_an_id(fresh_inbox())
        attention.test_a_fill_reaches_the_ordering_seat_not_whoever_was_awake(fresh_inbox())
        regressions.test_a_refusal_is_addressed_to_the_seat_that_decided_it()
        regressions.test_a_fill_is_addressed_to_the_seat_whose_order_it_was()
        regressions.test_every_fact_on_one_decision_stays_separately_addressable()

    def test_an_invocation_failure_leaves_the_fold_offered_for_the_next_wake(self):
        """A consequence that did not reach its owner is still owed to it."""
        attention.test_an_invocation_failure_leaves_the_fold_offered_and_the_next_wake_sees_it()

    # ---- missing data stays unavailable --------------------------------------------

    def test_missing_data_stays_unavailable(self):
        """"missing data stays unavailable" -- absent state, and an unreadable venue."""
        regressions.test_missing_state_is_unavailable_not_absent()
        regressions.test_missing_watcher_observation_retains_the_last_valid_value()

    # ---- no judge or wake gets private state ---------------------------------------

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
                       if desc.startswith(("Evaluate", "Assess"))]
        assert len(commissions) > 20, "the run commissioned no evaluation to scan"
        scanned = 0
        for desc, system, text in commissions:
            assert PRIVATE_MARKER not in system
            you, rest = text.split("\n\nWORLD UPDATE\n", 1)
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

    def test_the_projection_that_does_it_drops_exactly_those_two_fields(self):
        """The function under the scan, from the regression that pinned it."""
        regressions.test_forwarded_outputs_are_projected_before_they_cross_a_boundary()
        regressions.test_a_minimal_world_still_partitions_seat_privacy()


# =====================================================================================
# Gate 3. Finality.
# =====================================================================================


class TestGateThreeFinality:
    """"partial fills, resting closes, unavailable mids, ledger failures, dropped acks,
    repeated kills, restarts; production stays dead; residual exposure reported under
    restricted authority; renaming files or unsetting a variable cannot revive an
    identity".

    Every sentence is `tests/audit/test_r3c_death.py`'s, called here so this gate
    fails with that workstream rather than beside it.
    """

    def test_production_dies_first_and_irrevocably_and_the_seal_comes_last(self):
        death.test_production_dies_before_the_first_operation_and_the_seal_comes_after_the_last()

    def test_a_resting_close_is_not_flat_and_a_partial_fill_is_not_flat(self):
        """"partial fills, resting closes"."""
        death.test_a_resting_close_is_not_flat_and_a_partial_fill_is_not_flat()
        regressions.test_wind_down_does_not_count_a_resting_close_as_closed()

    def test_an_unavailable_mid_is_unknown_exposure_and_never_an_empty_account(self):
        """"unavailable mids", with the other three exposure states beside it."""
        death.test_an_unavailable_mid_is_unknown_exposure_and_never_an_empty_account()
        death.test_dust_within_the_precommitted_bound_is_its_own_state()
        death.test_an_empty_account_after_the_passes_is_flat()

    def test_a_ledger_failure_during_the_wind_down_never_prevents_death(self, capsys):
        """"ledger failures": death does not depend on the diary accepting it."""
        death.test_a_diary_failure_during_the_wind_down_never_prevents_death()
        death.test_the_ledger_failure_reaches_stderr(capsys)
        regressions.test_wind_down_ledger_failure_does_not_escape_the_kill()

    def test_a_dropped_acknowledgement_is_reconciled_by_reading_and_never_resent(self):
        """"dropped acks": no operation is repeated because its answer was lost."""
        death.test_a_dropped_acknowledgement_is_reconciled_by_reading_and_never_resent()

    def test_a_repeated_kill_and_a_restart_mid_wind_down_repeat_no_operation(self, tmp_path):
        """"repeated kills, restarts"."""
        death.test_every_operation_has_a_durable_id_derived_from_the_launch_identity()
        death.test_a_repeated_kill_repeats_no_operation()
        death.test_a_restart_mid_wind_down_reconciles_the_diary_it_finds()
        death.test_a_second_kill_reads_the_real_diary_it_cannot_iterate(tmp_path)
        regressions.test_a_repeated_kill_does_not_repeat_the_external_close()

    def test_residual_exposure_is_reported_under_the_executors_restricted_authority(
            self, tmp_path):
        """"residual exposure reported under restricted authority"."""
        death.test_the_executor_can_only_reduce()
        death.test_the_executor_cannot_reach_the_population()
        death.test_the_witness_line_carries_both_states_and_the_operation_count(tmp_path)
        death.test_the_wake_shows_the_two_states_of_death_separately()

    def test_renaming_the_diary_cannot_revive_a_killed_identity(self, tmp_path, monkeypatch):
        """"renaming files ... cannot revive an identity"."""
        death.test_renaming_the_diary_does_not_revive_a_killed_identity(tmp_path, monkeypatch)

    def test_unsetting_the_receiver_cannot_remove_its_veto(self, tmp_path, monkeypatch):
        """"or unsetting a variable cannot revive an identity"."""
        death.test_unsetting_the_receiver_cannot_remove_its_veto(tmp_path, monkeypatch)

    def test_a_world_launched_without_a_receiver_keeps_the_weaker_guarantee(self, tmp_path):
        """The control: the veto belongs to the launch that had one, and to no other."""
        death.test_a_world_launched_without_a_receiver_keeps_the_weaker_guarantee(tmp_path)

    def test_retirement_is_final_at_the_budget_layer(self):
        """A late credit to a retired seat is the commons', and is ledgered as such."""
        death.test_a_late_credit_to_a_retired_seat_goes_to_the_commons_and_is_ledgered_as_such()
        regressions.test_a_late_credit_does_not_resurrect_a_retired_seat()


# =====================================================================================
# Gate 4. Selection and time.
# =====================================================================================


class TestGateFourSelectionAndTime:
    """"re-screen final prompts and routes; no synthetic work; real evidence windows;
    a usable way to decline or reshape evaluation spend without a quota"."""

    def test_no_producer_return_exists_for_an_empty_draw(self):
        """"no synthetic work": an unselected draw is not a return to be graded."""
        evaluation.test_no_producer_return_exists_for_an_empty_draw()
        regressions.test_an_empty_router_draw_is_not_graded_as_a_producer_return()

    def test_a_tier_does_not_fire_on_three_simultaneous_arrivals(self):
        """"real evidence windows": a cascade releases on elapsed time and completed evidence."""
        evaluation.test_three_simultaneous_arrivals_do_not_trigger_a_tier()
        evaluation.test_the_window_releases_on_elapsed_time_with_its_completed_evidence()
        evaluation.test_a_window_of_unfinished_evidence_reports_nothing_upward()

    def test_a_seat_can_decline_a_commission_at_the_cost_of_the_call(self):
        """"a usable way to decline or reshape evaluation spend without a quota"."""
        evaluation.test_a_declined_commission_costs_only_the_call()
        attention.test_a_declined_commission_is_not_malformed()
        regressions.test_defer_covers_routine_world_wakes_and_not_paid_commissions()

    def test_the_final_manifests_prompts_preflight_against_the_ratified_charter(
            self, tmp_path):
        """"re-screen final prompts and routes": preflight, on a namespace copy, passes.

        `test_e3_world.py::test_edition3_preflight_passes_every_gate_up_to_the_namespace`
        stops at the base manifest's deliberately absent exchange client namespace,
        which is the one thing a rehearsal copy supplies. This is that copy: the
        same world with a namespace, so preflight runs to the end and returns the
        evidence a rehearsal is bound by. Nothing is written into `worlds/`, and
        `prepare` is not used, because it would draw a fresh namespace into the
        repository and consume it.
        """
        from scripts.draft_edition1 import roster_hash
        from scripts.rehearsal import preflight

        world = Path("worlds/edition3-testnet.toml")
        charter = Path("docs/charter/edition3-ratified.toml")
        namespace = "0" * 32
        copy = tmp_path / "edition3-gate.toml"
        text = re.sub(r'^name = "[^"]+"$', f'name = "{copy.stem}"', world.read_text(),
                      count=1, flags=re.MULTILINE)
        text = text.replace("[exchange]", f'[exchange]\nclient_namespace = "{namespace}"', 1)
        copy.write_text(text)

        evidence = preflight(copy, charter)
        assert evidence["world"] == copy.stem
        assert evidence["client_namespace"] == namespace
        assert evidence["roster_sha256"] == roster_hash(load_manifest(str(world)))
        assert [card["id"] for card in evidence["cards"]] == ["censorship-bound"]
        assert evidence["tick_interval_ns"] == load_manifest(str(world)).tick_interval_ns
        assert evidence["claims"].startswith("execution and feedback check")

    def test_the_calibration_gate_runs_offline_on_the_final_manifest_and_passes(
            self, tmp_path, capsys):
        """The plan's R3-E acceptance, on the world that launches, with no paid call.

        `scripts/calibrate_seats.py --cases --offline` scores every route on the
        final manifest's menu against the bounded case set and exits non-zero when
        the gate fails, which is the coordinator's evidence. The offline provider
        is the deterministic oracle in the script itself: no key, no network, and
        no model is called.
        """
        from scripts.calibrate_seats import main

        out = tmp_path / "cases.json"
        assert main(["--world", "edition3-testnet", "--all-menu", "--cases",
                     "--offline", "--out", str(out)]) == 0
        report = json.loads(out.read_text())
        assert report["passed"] is True and report["paid"] is False
        assert report["offline"] is True
        assert report["world"] == "edition3-testnet"
        menu = {model.id for model in load_manifest("edition3-testnet").models}
        assert set(report["routes"]) == menu
        gate = report["gate"]
        for candidate, row in report["routes"].items():
            assert row["passed"] is True, candidate
            assert row["cases"] >= gate["min_cases_per_route"], candidate
            assert row["failed_critical"] == [] and row["unavailable"] == [], candidate
            assert row["valid_share"] >= gate["min_valid_share"], candidate
        assert report["production_effects"] is False and report["scripted_exchange"] is True
        assert report["manifest"]["manifest_sha256"] == (
            load_manifest("edition3-testnet").manifest_hash())
        capsys.readouterr()  # the script's own table is evidence, not test output

