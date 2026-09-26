"""The wallet moves only when money moves (Wave 11; essay II.II.b, II.IV.a).

Every debit names a real counterparty: a provider's bill, a seller's price, a
rail's fee. A scarce resource that costs nothing at the margin (retained bytes on
the world's own disk, a public read, code in the world's own jail) is a
constraint: a hard limit, or a price the charter may put on reward through λ,
never a money debit. These tests attempt the fictitious debits the kernel used to
make and assert the wallet did not move for them, and check that each pass-through
of a real bill (re-attributed between seats, never created or destroyed) leaves the
world's total debits equal to the real outflows.
"""

import json
from dataclasses import dataclass, field, replace

import pytest

from factorylab.kernel.budget import BudgetBook, SeatWallet, unlocked_balance
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.continuity import HARD_STATE_BYTES, STATE_TOO_LARGE
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import REMOVED_PRICE_KEYS, load_manifest, manifest_from_dict
from factorylab.world.metering import Meter
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

DAY_NS = 86_400 * 1_000_000_000
#: The reasons a nonzero debit may carry: each one names who was paid. A program
#: seat's ``model:program`` is not one of them: the jail pays no one.
REAL_OUTFLOWS = ("tool:web.search", "tool:connector.x402", "treasury:fees")


def _real_outflow(reason: str) -> bool:
    if reason == "model:program":
        return False
    return reason.startswith(("model:", *REAL_OUTFLOWS))


def _next_window(rt, *, advance_ns):
    """Let wall time pass and the price loop fall due, then open the next window."""
    rt.clock.now_ns += advance_ns
    rt.clockwork.force("price", rt.ticks_consumed)
    rt._manage_reserve_window()


def test_a_large_working_state_held_across_many_windows_moves_no_money():
    """The fictitious debit storage rent used to make: nothing is paid to anyone for
    bytes on the world's own disk, so however large the state and however long it is
    held, the wallet does not move, no seat's entitlement moves, and the decision that
    wrote it carries no liability for it."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    state = {"notes": "x" * (HARD_STATE_BYTES - 64)}
    head = rt.working_state.put("seed-decider", state, handle=handle)
    assert HARD_STATE_BYTES - 64 < head["bytes"] <= HARD_STATE_BYTES
    balance, entitlements = rt.wallet.balance, rt.budget.entitlements()
    for _ in range(12):
        _next_window(rt, advance_ns=30 * DAY_NS)
        assert rt.window.decisions.get(handle, {}).get("cost", 0) == 0
    assert rt.wallet.balance == balance
    assert rt.budget.entitlements() == entitlements
    assert not [i for i in ledger_items(rt) if i["kind"].startswith("wallet.")
                and i.get("reason") == "tool:state.storage"]
    assert not ledger_items(rt, "state.rent") and not ledger_items(rt, "state.rent_due")
    assert not [i for i in ledger_items(rt, "price.contribution") if i.get("storage")]
    # The size is observable where it was written, for the charter to price if it wants.
    assert ledger_items(rt, "state.put")[-1]["bytes"] == head["bytes"]


def test_the_working_state_hard_limit_still_refuses_and_leaves_the_head():
    """The constraint that replaces the rent is the 64 KiB hard cast (II.II.b)."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    head = rt.working_state.put("seed-decider", {"fact": "kept"}, handle=handle)
    with pytest.raises(ValueError, match=STATE_TOO_LARGE):
        rt.working_state.put("seed-decider", {"notes": "x" * HARD_STATE_BYTES}, handle=handle)
    assert rt.working_state.head("seed-decider") == head


def test_the_storage_section_states_the_limit_and_no_price():
    world = make_runtime()._world_block()
    assert world["storage"]["working_state_max_bytes"] == HARD_STATE_BYTES
    assert "costs no money" in world["storage"]["pricing"]
    assert "micro_per_byte_day" not in world["storage"]


def test_every_seeded_tool_is_free_and_a_free_call_moves_no_money():
    """A kernel read, a venue read, a catalogue search and a jailed calc pay no one."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._ensure_connector_tool()
    assert {tool_id: spec["price_micro_per_call"] for tool_id, spec in rt.tool_specs.items()
            if spec["price_micro_per_call"]} == {}
    handle = decision(rt)
    before = rt.wallet.balance
    for call in ({"tool": "venue.mids", "args": {}},
                 {"tool": "catalogue.search", "args": {"substring": "venue"}},
                 {"tool": "calc", "args": {"op": "notional", "size": "1", "price": "2"}},
                 {"tool": "world.read", "args": {"section": "prices"}}):
        result, cost = rt._run_tool("seed-decider", handle, call)
        assert cost == 0, (call, result)
    assert rt.wallet.balance == before


def _read_runtime(budget):
    """A scripted runtime whose read budget gives each reader slot ``budget`` weight."""
    rt = make_runtime()
    rt._manage_reserve_window()
    slots = rt.m.exchange.max_readers
    rt.m = replace(rt.m, exchange=replace(rt.m.exchange,
                                          public_read_weight_per_minute=budget * slots))
    assert rt.venue_read_share() == budget
    rt.clock.now_ns = 60_000_000_000 * 1_000
    return rt


def _read(rt, seat, tool, handle=None, *, fresh=True, **args):
    """One seat read; ``fresh`` drops the tick's answers first, so the read is sent."""
    if fresh:
        rt._tick_reads = None
    handle = handle or decision(rt, seat)
    return rt._run_tool(seat, handle, {"tool": tool, "args": args})[0]


def _register(rt, seat_id):
    from factorylab.cortex.request import Return

    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "assembly", "id": seat_id, "model_id": "fake-haiku", "role": "producer",
        "accepts": ["Tick"], "system_prompt": "x", "max_tokens": 128}]}, 0, "ok"))
    assert seat_id in rt.assemblies, ledger_items(rt, "registration.rejected")
    return handle


def test_a_seat_s_venue_reads_are_capped_by_its_own_share_over_a_sliding_minute():
    """A free read still spends the venue's IP rate limit, which the kernel's own order
    and reconcile calls need, so it is a limit (II.II.b): a read the seat's share
    cannot cover is refused before it is sent, the share is counted over any sliding
    60 s, and it survives a checkpoint."""
    from factorylab.runtime.resume import restore_runtime, runtime_state
    from factorylab.world.venue_tools import public_read_weight

    rt = _read_runtime(50)
    sent = []
    call = rt.venue_tools.call
    rt.venue_tools.call = lambda tool, args: sent.append(tool) or call(tool, args)
    assert public_read_weight("venue.funding", {}) == 20
    assert public_read_weight("venue.candles", {"n": 61}) == 22
    assert public_read_weight("venue.funding_history", {"n": 999}) == 25  # out of range
    assert public_read_weight("venue.open_orders", {}) == 20
    assert public_read_weight("venue.positions", {}) == 6
    assert public_read_weight("venue.vault_positions", {}) == 40
    assert public_read_weight("venue.place_market", {}) is None  # a write is not a read
    for _ in range(2):  # 20 + 20 of 50
        assert "error" not in _read(rt, "seed-decider", "venue.funding")
    refused = _read(rt, "seed-decider", "venue.funding")
    assert refused["error"].startswith(rt.PUBLIC_READ_REFUSAL)
    assert "40 of 50" in refused["error"] and sent == ["venue.funding"] * 2
    assert ledger_items(rt, "tool.refused")[-1]["reason"] == refused["error"]
    rt.clock.now_ns += 30_000_000_000
    for _ in range(3):  # the 10 left still buys a cheap read at 6 or 2
        assert "error" not in _read(rt, "seed-decider", "venue.mids")
    assert "error" in _read(rt, "seed-decider", "venue.positions")  # 46 + 6 > 50
    rt2 = make_runtime()
    rt2.m = rt.m
    restore_runtime(rt2, runtime_state(rt))
    rt2.clock.now_ns = rt.clock.now_ns
    assert "error" in _read(rt2, "seed-decider", "venue.positions")
    # Sliding, not bucketed: 31 s later the two funding reads (40) have left the
    # minute but the mids reads 30 s after them (6) have not.
    rt.clock.now_ns += 31_000_000_000
    assert rt._venue_read_used("seed-decider") == 6
    assert "error" not in _read(rt, "seed-decider", "venue.funding")


def test_two_identical_seat_reads_in_one_tick_send_one_request():
    """Draining the simulated venue's local event queue after a read changes nothing
    the venue answers, so it keeps the tick's answer: the second identical read is
    answered from the first and sends nothing. Each read is charged to the seat's
    share all the same (a quota on reads asked)."""
    rt = _read_runtime(30)
    rt._tick_reads = None
    sent = []
    mids = rt.exchange.target.mids
    rt.exchange.target.mids = lambda: sent.append("mids") or mids()
    first = _read(rt, "seed-decider", "venue.mids", fresh=False)
    second = _read(rt, "seed-decider", "venue.mids", fresh=False)
    assert first == second and sent == ["mids"]
    assert rt._venue_read_used("seed-decider") == 4


def _rate_limited_runtime(monkeypatch, budget=100):
    """A runtime on a live adapter whose every info request answers HTTP 429."""
    import time

    from hyperliquid.utils.error import ClientError

    from factorylab.world.exchange import HyperliquidExchange

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    rt = _read_runtime(budget)
    venue = object.__new__(HyperliquidExchange)
    venue.coins, venue._listed_coins, venue._spot_names = ("BTC",), ("BTC",), {}
    venue.transient_failures = 0
    requests = []

    class Info:
        def __getattr__(self, name):
            def answer(*_args, **_kwargs):
                requests.append(name)
                raise ClientError(429, None, "rate limited", {})
            return answer

    venue._info = Info()
    rt.exchange.target = venue
    return rt, venue, requests


def test_a_seat_read_under_a_429_sends_one_attempt_and_the_kernel_keeps_its_retries(
        monkeypatch):
    """Codex's scenario cannot happen: a seat read admitted on one attempt sends one,
    and is charged one, even when the venue answers 429; the kernel's own read in the
    same tick, under the same 429, still makes its three attempts."""
    from factorylab.world.exchange import VenueUnavailable

    rt, venue, requests = _rate_limited_runtime(monkeypatch)
    funding = _read(rt, "seed-decider", "venue.funding")
    # The venue did not answer: an error, never an empty list that would say it
    # reports no funding.
    assert "funding" not in funding and "VenueUnavailable" in funding["error"]
    assert "429" in funding["error"] and requests == ["meta_and_asset_ctxs"]
    assert rt._venue_read_used("seed-decider") == 20
    mids = _read(rt, "seed-decider", "venue.mids")
    assert "VenueUnavailable" in mids["error"] and "429" in mids["error"]
    assert requests == ["meta_and_asset_ctxs", "all_mids"]
    assert rt._venue_read_used("seed-decider") == 22
    assert not getattr(venue, "single_attempt", False)
    with pytest.raises(VenueUnavailable):
        rt.exchange.mids()  # the kernel's own read
    assert requests[2:] == ["all_mids"] * 3


def test_an_unanswered_account_read_is_an_error_to_a_seat_never_old_positions(
        monkeypatch):
    """The adapter falls back to its last complete account snapshot when the venue
    does not answer, and marks it stale for the kernel. A seat's venue.positions is
    told the venue did not answer; the old positions are never shown as this read's."""
    from factorylab.world.exchange import AccountState

    rt, venue, requests = _rate_limited_runtime(monkeypatch)
    venue._address = "0x" + "1" * 40
    venue._last_account = AccountState(equity_usd="100", cash_usd="100", positions=(),
                                       margin_used_usd="0",
                                       observed_at_ns=1)
    positions = _read(rt, "seed-decider", "venue.positions")
    assert "positions" not in positions and "VenueUnavailable" in positions["error"]
    assert "did not answer" in positions["error"] and requests == ["user_state"]
    assert rt.exchange.account().stale is True  # the kernel still reads it as stale


def test_an_unanswered_funding_read_emits_no_funding_event():
    """The tick's events: an unanswered funding read is no event this tick, which is
    true, never a zero rate."""
    from factorylab.runtime.live import LiveVenue
    from factorylab.world.exchange import VenueUnavailable

    class Silent:
        name = "stub"

        def mids(self):
            return {}

        def funding(self):
            raise VenueUnavailable("meta_and_asset_ctxs: ClientError 429")

    events = LiveVenue(Silent(), last_fill_ns=0).on_tick(1, include_fills=False)
    assert not [e for e in events if e.kind.name == "FUNDING"]


def test_a_schema_invalid_read_charges_nothing_on_any_adapter(monkeypatch):
    """Refused before admission, it sends nothing and is charged nothing, the same on
    the simulated venue (no weight counter) as on the live adapter."""
    bad = {"coin": "BTC", "n": 0}  # n has minimum 1
    simulated = _read_runtime(100)
    live, _venue, requests = _rate_limited_runtime(monkeypatch)
    for rt in (simulated, live):
        result = _read(rt, "seed-decider", "venue.funding_history", **bad)
        assert "error" in result and "funding_history" not in result
        assert rt._venue_read_used("seed-decider") == 0
    assert requests == []
    # A read that reached dispatch on the simulated venue is still charged.
    assert "error" not in _read(simulated, "seed-decider", "venue.funding_history",
                                coin="BTC", n=10)
    assert simulated._venue_read_used("seed-decider") == 21


def test_the_single_attempt_flag_never_outlives_the_seat_read(monkeypatch):
    """Whatever raises during a seat's read, before or inside the metered call, the
    flag is clear afterwards and a following kernel read still retries."""
    from factorylab.world.exchange import VenueUnavailable

    rt, venue, requests = _rate_limited_runtime(monkeypatch)
    rt.tool_specs["venue.mids"]["price_micro_per_call"] = "not a price"
    with pytest.raises(ValueError):
        _read(rt, "seed-decider", "venue.mids")
    assert not getattr(venue, "single_attempt", False)
    rt.tool_specs["venue.mids"]["price_micro_per_call"] = 0

    seen = []

    def broken_meter(_seat):
        seen.append(getattr(venue, "single_attempt", False))
        raise RuntimeError("meter unavailable")

    monkeypatch.setattr(rt, "_seat_meter", broken_meter)
    assert "meter unavailable" in _read(rt, "seed-decider", "venue.mids")["error"]
    assert seen == [True] and not getattr(venue, "single_attempt", False)
    with pytest.raises(VenueUnavailable):
        rt.exchange.mids()
    assert requests == ["all_mids"] * 3


def test_one_seat_exhausting_its_share_never_changes_another_seat_s_refusals():
    """AGENTS.md rule 4: no channel between seats. A reader's refusals depend on its own
    reads alone, never on another seat's reads."""
    def refusals(rt, seat):
        return ["error" in _read(rt, seat, "venue.funding") for _ in range(4)]

    quiet = _read_runtime(50)
    alone = refusals(quiet, "seed-observer")
    busy = _read_runtime(50)
    for _ in range(6):
        _read(busy, "seed-decider", "venue.funding")
    assert busy._venue_read_used("seed-decider") == 40
    assert refusals(busy, "seed-observer") == alone == [False, False, True, True]


def test_venue_instruments_sends_nothing_and_spends_no_share():
    rt = _read_runtime(2)
    for _ in range(5):
        assert "error" not in _read(rt, "seed-decider", "venue.instruments")
    assert rt._venue_read_used("seed-decider") == 0
    text = rt.tool_specs["venue.instruments"]["description"]
    assert "sends no request and spends none of your venue read share" in text


def test_a_live_read_is_charged_every_attempt_the_adapter_sent(monkeypatch):
    """Each attempt ``_guarded`` sends is weighed, a retry after a 429 included, and the
    items a request returned are counted once it answers."""
    import time

    from hyperliquid.utils.error import ClientError

    from factorylab.world.exchange import HyperliquidExchange, VenueUnavailable

    monkeypatch.setattr(time, "sleep", lambda _s: None)
    venue = object.__new__(HyperliquidExchange)
    answers = [ClientError(429, None, "slow down", {}), list(range(130))]

    def candles():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert venue._guarded("candles", candles) == list(range(130))
    assert venue.request_weight_sent() == 20 + 20 + 130 // 60  # two attempts, 130 items
    with pytest.raises(VenueUnavailable):
        venue._guarded("l2_snapshot", lambda: (_ for _ in ()).throw(
            ClientError(429, None, "slow down", {})))
    assert venue.request_weight_sent() == 42 + 3 * 2  # three attempts at 2
    # A seat's read is sent once: no retry can overshoot the share it was admitted on.
    venue.single_attempt = True
    with pytest.raises(VenueUnavailable):
        venue._guarded("l2_snapshot", lambda: (_ for _ in ()).throw(
            ClientError(429, None, "slow down", {})))
    assert venue.request_weight_sent() == 48 + 2
    # The runtime charges the seat exactly what the adapter reports it sent.
    rt = _read_runtime(100)
    reported = iter([0, 42])
    monkeypatch.setattr(rt, "_venue_weight_sent", lambda: next(reported))
    assert "error" not in _read(rt, "seed-decider", "venue.candles",
                                coin="BTC", interval="1m", n=10)
    # Charged its first attempt (21) on admission, and what the adapter reports it
    # sent beyond that (21 more) once it answered.
    assert rt._venue_read_used("seed-decider") == 42


def test_the_share_is_fixed_whatever_the_population_does():
    """Budget // venue.max_readers, not // live seats: registering or retiring seats
    changes no reader's share, and the refusal text carries no population count."""
    rt = _read_runtime(30)
    share = rt.venue_read_share()
    _register(rt, "newcomer")
    rt.retired_assemblies.add("seed-observer")
    assert rt.venue_read_share() == share
    refusal = [_read(rt, "seed-decider", "venue.funding") for _ in range(2)][-1]["error"]
    assert refusal.endswith("20 of 30 venue request weight in the last 60 s; "
                            "this read sends 20")


def test_a_registration_past_the_reader_cap_is_admitted_without_venue_reads():
    """The venue's IP limit bounds who reads it, never how many seats exist: past the
    last slot a seat registers all the same, holds no venue read tool, and its
    proposer's receipt says so. A slot that comes free goes to the queue first in,
    first out, before any registration that arrives after (the review's first and
    second: ``second`` waited, so it gets the slot ``first`` frees, and ``third``,
    registered later, waits behind it)."""
    rt = make_runtime()
    rt._manage_reserve_window()
    readers = len(rt.venue_readers)
    rt.m = replace(rt.m, exchange=replace(rt.m.exchange, max_readers=readers + 1))
    _register(rt, "first")
    assert "first" in rt.venue_readers
    handle = _register(rt, "second")
    assert "second" not in rt.venue_readers and len(rt.venue_readers) == readers + 1
    assert rt._run_tool("second", decision(rt, "second"),
                        {"tool": "venue.mids", "args": {}})[0] == {
        "error": "unknown or disallowed tool"}
    assert "calc" in rt._allowed_tools("second")  # everything but the venue reads
    receipt = rt.outcomes.get(rt.handle_to_assembly[handle], handle)["outcome"]
    assert receipt["kind"] == "registration_admitted" and receipt["id"] == "second"
    assert "no venue read slot is free" in receipt["venue_reads"]
    rt._retire_assembly("first", "vote-1")  # it read nothing: its slot is free at once
    assert "first" not in rt.venue_readers and "second" in rt.venue_readers
    assert rt.slot_waiting == []
    _register(rt, "third")
    assert "third" not in rt.venue_readers and rt.slot_waiting == ["third"]
    assert "error" not in _read(rt, "second", "venue.mids")
    slots = [(i["assembly_id"], i["slot"]) for i in ledger_items(rt, "venue.reader_slot")]
    assert slots == [("second", False), ("second", True), ("third", False)]


def test_a_seat_sees_its_own_slot_and_nobody_else_s():
    """The slot is a fact in the seat's own row, which only that seat's request
    renders; no public section lists the slot holders."""
    from factorylab.cortex.request import Request

    rt = make_runtime()
    rt._manage_reserve_window()
    rt.m = replace(rt.m, exchange=replace(rt.m.exchange, max_readers=len(rt.venue_readers)))
    _register(rt, "late")
    rows = {row["seat_id"]: row for row in rt._seat_views()}
    assert rows["seed-decider"]["venue_read_slot"] is True
    assert rows["late"]["venue_read_slot"] is False
    world = rt._world_block()
    assert "venue_readers" not in json.dumps({k: v for k, v in world.items() if k != "seats"})
    text = Request("h", "d", {"world": world, "you": "late"}, {}, {"type": "object"}, 0,
                   1_000_000, None, "answer", "verdict", "late").prompt_text()
    assert text.count("venue_read_slot") == 1 and '"venue_read_slot":false' in text.replace(
        " ", "")


def test_an_identical_read_within_a_tick_is_answered_without_a_request():
    """The tick's answer to the identical venue request (the kernel's own included)
    answers the read: nothing is sent, and a venue write or a new tick ends it. The
    slot is charged for it as for any read, so the seat cannot tell the two apart."""
    rt = _read_runtime(30)
    sent = []
    mids = rt.exchange.target.mids
    rt.exchange.target.mids = lambda: sent.append("mids") or mids()
    kernel = rt.exchange.mids()  # the kernel's own read of the tick
    answered = _read(rt, "seed-decider", "venue.mids", fresh=False)
    assert answered == {"mids": {c: str(p) for c, p in kernel.items()}}
    assert sent == ["mids"] and rt._venue_read_used("seed-decider") == 2
    assert ledger_items(rt, "venue.read_answered")[-1]["tool"] == "venue.mids"
    rt.ticks_consumed += 1  # a new tick: the read is sent
    _read(rt, "seed-decider", "venue.mids", fresh=False)
    assert sent == ["mids", "mids"] and rt._venue_read_used("seed-decider") == 4
    rt.exchange.drain_events()  # a local event drain changes nothing the venue answers
    _read(rt, "seed-decider", "venue.mids", fresh=False)
    assert sent == ["mids", "mids"]
    rt.exchange.set_leverage("BTC", 2)  # a venue write moves the key
    _read(rt, "seed-decider", "venue.mids", fresh=False)
    assert sent == ["mids"] * 3
    text = rt.tool_specs["venue.mids"]["description"]
    assert "is answered from that answer, and sends no request" in text
    assert "charged to your share for every read" in text


def test_a_world_whose_share_cannot_cover_its_heaviest_read_is_refused():
    with pytest.raises(ValueError, match="cannot cover venue.funding_history at 25"):
        manifest_from_dict(_world(venue={"public_read_weight_per_minute": 390}))
    with pytest.raises(ValueError, match="max_readers must be a positive integer"):
        manifest_from_dict(_world(venue={"max_readers": 0}))
    with pytest.raises(ValueError, match="tools.max_seats was removed"):
        manifest_from_dict(_world(tools={"max_seats": 16}))
    with pytest.raises(ValueError, match="cannot cover"):
        Runtime(replace(load_manifest("scripted"), exchange=replace(
            load_manifest("scripted").exchange, max_readers=100)), events=0, seed=1,
            initial_balance_micro=1, ledger_path=None, router_gamma=.1)


def test_the_read_share_is_published_where_the_tool_is():
    text = make_runtime().tool_specs["venue.candles"]["description"]
    assert "Held by seats with a venue read slot (at most 16)" in text
    assert "fixed share of 30 venue request weight (480 over 16 slots)" in text
    assert "sent at most once and weighs 20 plus 1 per 60 candles asked" in text
    assert "the rest of the venue's 1200 a minute per IP is the kernel's" in text


def test_the_default_read_budget_leaves_the_kernel_most_of_the_venue_limit():
    from factorylab.world.venue_tools import (
        DEFAULT_PUBLIC_READ_WEIGHT_PER_MINUTE,
        VENUE_WEIGHT_PER_MINUTE,
    )

    assert load_manifest("scripted").exchange.public_read_weight_per_minute == (
        DEFAULT_PUBLIC_READ_WEIGHT_PER_MINUTE)
    assert DEFAULT_PUBLIC_READ_WEIGHT_PER_MINUTE * 2 < VENUE_WEIGHT_PER_MINUTE
    for bad in (0, 1200, "480", True):
        with pytest.raises(ValueError, match="public_read_weight_per_minute"):
            manifest_from_dict(_world(venue={"public_read_weight_per_minute": bad}))


def _world(**tables):
    from tests.seed_charter import seed_charter_table

    return {"name": "x", "initial_balance_usd": "10", "charter": seed_charter_table(),
            "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
            "assemblies": [{"id": "a", "model_id": "m"}],
            "novelty": {"share": 0.1}, "immune": {"price_step": 0.05},
            "timing": {"world_repricing": "1h"}, **tables}


@pytest.mark.parametrize("table,key", sorted(REMOVED_PRICE_KEYS))
def test_a_manifest_naming_a_removed_price_is_refused_by_name(table, key):
    """R8: a world file that still prices a call no one is paid for is refused, never
    loaded as though the price applied."""
    with pytest.raises(ValueError, match=f"{table}.{key} was removed"):
        manifest_from_dict(_world(**{table: {key: "0"}}))


@pytest.mark.parametrize("table", ["storage", "notes"])
def test_a_manifest_naming_storage_rent_is_refused(table):
    with pytest.raises(ValueError, match=rf"\[{table}\] was removed"):
        manifest_from_dict(_world(**{table: {"micro_per_byte_day": "0.04"}}))


# --- pass-throughs: a real bill re-attributed between seats -----------------------------


@pytest.fixture
def clock():
    return lambda: 100


@pytest.fixture
def ledger(clock):
    return Ledger(clock_ns=clock)


def _book(ledger, clock, amount=10_000):
    wallet = Wallet(amount, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.genesis(["seat", "parent"])  # 4 000 each, 2 000 unallocated
    return wallet, book


def _conserved(book, wallet) -> bool:
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(wallet) - book.holds()) and book.check_invariant()


def _commits(ledger):
    return [i for i in ledger._recovery_items() if i["kind"] == "budget" and i["op"] == "commit"]


def test_a_bill_the_commons_helps_pay_is_debited_once_at_its_real_amount(ledger, clock):
    """P: protected exploration or an overrun beyond the seat's entitlement is borne by
    the pool. The seat's share and the commons' share sum to the one bill, and the
    wallet moves by exactly that bill."""
    wallet, book = _book(ledger, clock)
    seat = SeatWallet(wallet, book, "seat", protected=lambda h, r: 5_000)
    bill = 6_500
    Meter(seat).run(handle="h", reason="model:m", ceiling=7_000, execute=lambda: "ok",
                    cost_of=lambda _: bill)
    [commit] = _commits(ledger)
    assert commit["own"] + commit["commons"] == commit["amount"] == bill
    assert commit["commons"] > 0
    assert wallet.balance == 10_000 - bill and _conserved(book, wallet)


def test_a_child_bill_charged_to_its_parent_is_debited_once_at_its_real_amount(ledger, clock):
    """P: a child request is its parent's subcontracting, so the parent's entitlement
    pays; the caller's does not move, and the wallet moves by exactly the bill."""
    wallet, book = _book(ledger, clock)
    child = SeatWallet(wallet, book, "seat", payer=lambda h: "parent")
    bill = 700
    Meter(child).run(handle="child", reason="model:m", ceiling=1_000, execute=lambda: "ok",
                     cost_of=lambda _: bill)
    assert book.entitlement("parent") == 4_000 - bill and book.entitlement("seat") == 4_000
    assert wallet.balance == 10_000 - bill and _conserved(book, wallet)


def test_a_bridge_moves_nothing_and_the_bill_it_backs_is_debited_once(ledger, clock):
    """P: the routing bridge backs a stale estimate from the pool for one call. The
    bridge itself moves no money; only the provider's bill does."""
    wallet, book = _book(ledger, clock)
    book.debit("seat", 3_900, "test")  # 100 left: the rendered request is dearer
    before = (wallet.balance, book.entitlements(), book.unallocated())
    backed = book.bridge("seat", "h", 400, "routing estimate")
    assert backed == 400 and (wallet.balance, book.entitlements(), book.unallocated()) == before
    seat = SeatWallet(wallet, book, "seat", protected=lambda h, r: backed)
    Meter(seat).run(handle="h", reason="model:m", ceiling=500, execute=lambda: "ok",
                    cost_of=lambda _: 450)
    [commit] = _commits(ledger)
    assert commit["own"] == 100 and commit["commons"] == 350
    assert wallet.balance == 10_000 - 450 and _conserved(book, wallet)


def test_a_bill_no_seat_authored_lands_on_the_pool_once(ledger, clock):
    """P: work on the shared meter is paid from the unallocated pool."""
    wallet, book = _book(ledger, clock)
    pool = book.unallocated()
    Meter(wallet).run(handle="kernel", reason="model:m", ceiling=300, execute=lambda: "ok",
                      cost_of=lambda _: 250)
    assert wallet.balance == 10_000 - 250 and book.unallocated() == pool - 250
    assert _conserved(book, wallet)


def test_moving_entitlement_between_seats_moves_no_money(ledger, clock):
    """An endowment, a trial paid to the pool and a grant reclassify money the wallet
    already holds: they net to zero across the world and are never a debit."""
    wallet, book = _book(ledger, clock)
    book.transfer("parent", "seat", 1_000, "trial:assembly")
    book.debit("seat", 500, "trial:tool")
    book.grant("parent", 500, "test")
    assert wallet.balance == 10_000
    assert sum(book.entitlements().values()) + book.unallocated() == 10_000
    assert not [i for i in ledger._recovery_items() if i["kind"] == "wallet.commit"]


@dataclass
class BillingProvider(ScriptedProvider):
    """The scripted world, billing every completion the way OpenRouter reports a cost.

    ``bills`` is the counterparty's own record: what it charged, call by call.
    """

    bills: list = field(default_factory=list)

    def complete(self, req):
        response = super().complete(req)
        bill = 7 * response.input_tokens + 11 * response.output_tokens + 3
        self.bills.append(bill)
        return replace(response, cost_micro=bill)


@pytest.mark.gate
def test_a_world_s_total_debits_equal_its_real_outflows():
    """Across a whole scripted world, every nonzero debit names a real counterparty,
    the model and search debits sum to exactly what the provider billed, every
    re-attribution to a seat or to the commons books the same amount the wallet
    debited, and the wallet fell by exactly the real outflows."""
    provider = BillingProvider()
    rt = Runtime(load_manifest("scripted"), events=60, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1, provider=provider)
    items = []
    append = rt.ledger.append

    def capture(item):
        items.append(dict(item))
        return append(item)

    rt.ledger.append = capture
    start = rt.wallet.balance
    rt.run()
    commits = [i for i in items if i["kind"] == "wallet.commit"]
    assert commits and provider.bills
    fictitious = [c for c in commits if c["amount"] and not _real_outflow(c["reason"])]
    assert fictitious == []
    billed = sum(c["amount"] for c in commits
                 if c["reason"].startswith(("model:", "tool:web.search")))
    assert billed == sum(provider.bills)
    # Every seat-attributed commit re-attributes exactly what the wallet debited.
    debited = {c["reservation_id"]: c["amount"] for c in commits}
    for row in (i for i in items if i["kind"] == "budget" and i["op"] == "commit"):
        assert row["own"] + row["commons"] == row["amount"] == debited[row["reservation_id"]]
    # The wallet fell by the real outflows and nothing else; money that arrived
    # (settlements, drips) is added back so the identity holds in any world.
    arrived = sum(i["amount"] for i in items if i["kind"] in ("wallet.settle", "wallet.drip"))
    assert start + arrived - rt.wallet.balance == sum(c["amount"] for c in commits)
    assert rt.wallet.check_conservation() and rt.budget.check_invariant()


@pytest.mark.gate
def test_a_program_seat_s_decisions_commit_zero():
    """A registered program seat is routed and returns like a model seat, and every
    one of its calls commits 0: the jail pays no one."""
    from tests.audit.test_e2_programs import Proposer
    from tests.cortex.test_jail import require_jail

    require_jail()
    rt = Runtime(load_manifest("scripted"), events=40, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1, provider=Proposer())
    items = []
    append = rt.ledger.append

    def capture(item):
        items.append(dict(item))
        return append(item)

    rt.ledger.append = capture
    rt.run()
    calls = [i for i in items if i["kind"] == "program.call"]
    commits = [i for i in items if i["kind"] == "wallet.commit"
               and i["reason"] == "model:program"]
    assert calls and commits and len(commits) >= len(calls)
    assert {i["cost"] for i in calls} == {0} and {i["amount"] for i in commits} == {0}


# --- the venue's per-IP limit: slot shares, one seat a slot at a time ----------------------


def test_the_seats_counted_venue_weight_never_passes_the_read_budget():
    """Hyperliquid admission against the requests the venue actually receives: every
    slot seat reads as hard as it can for three world minutes; each read that reaches
    the venue is weighed as the venue documents it, and in no sliding minute do the
    seats together send more than ``public_read_weight_per_minute`` (480), which leaves
    the kernel its 720 of the 1200. Each seat's own weight stays within its share."""
    from factorylab.world.venue_tools import VENUE_WEIGHT_PER_MINUTE, public_read_weight

    rt = _read_runtime(30)
    budget = rt.m.exchange.public_read_weight_per_minute
    target = rt.exchange.target
    received = []  # (world ns, weight, seat) of every read the venue answered
    reading = [None]
    for tool_id, method in (("venue.funding", "funding"), ("venue.mids", "mids")):
        inner = getattr(target, method)

        def weighed(*a, _inner=inner, _tool=tool_id, **k):
            received.append((rt.clock.now_ns, public_read_weight(_tool, {}), reading[0]))
            return _inner(*a, **k)

        setattr(target, method, weighed)
    seats = [seat for seat in rt.venue_readers if seat is not None]
    refused = 0
    for second in range(180):
        for seat in seats:
            reading[0] = seat
            tool = "venue.funding" if second % 2 else "venue.mids"
            refused += "error" in _read(rt, seat, tool)
        rt.clock.now_ns += 1_000_000_000
    assert refused and received
    minute = 60_000_000_000
    for start, _w, _s in received:
        window = [(w, s) for ts, w, s in received if start - minute < ts <= start]
        assert sum(w for w, _s in window) <= budget < VENUE_WEIGHT_PER_MINUTE
        for seat in seats:
            assert sum(w for w, s in window if s == seat) <= rt.venue_read_share()


def test_a_freed_slot_waits_until_its_last_read_has_slid_out():
    """Rule 5: a newcomer never inherits a predecessor's reads, so a retired seat's slot
    is not given again until its last read has left the minute: a seat registered
    meanwhile has no slot (and no venue reads), gets it then, and the assignment is
    ledgered."""
    rt = _read_runtime(30)
    slots = len(rt.venue_readers)  # every slot held, each still with a share of 30
    rt.m = replace(rt.m, exchange=replace(rt.m.exchange, max_readers=slots,
                                          public_read_weight_per_minute=30 * slots))
    assert "error" not in _read(rt, "seed-observer", "venue.funding")  # 20 of 30
    slot = rt.venue_readers.index("seed-observer")
    rt._retire_assembly("seed-observer", "vote-1")
    assert rt.venue_readers[slot] is None
    _register(rt, "newcomer")
    assert "newcomer" not in rt.venue_readers and rt.slot_waiting == ["newcomer"]
    assert "venue.funding" not in rt._allowed_tools("newcomer")
    rt.clock.now_ns += 30_000_000_000
    rt._assign_waiting_readers()
    assert "newcomer" not in rt.venue_readers
    rt.clock.now_ns += 30_000_000_000
    rt._assign_waiting_readers()
    assert rt.venue_readers[slot] == "newcomer" and not rt.slot_waiting
    assert rt._venue_read_used("newcomer") == 0
    assert "error" not in _read(rt, "newcomer", "venue.funding")
    refused = _read(rt, "newcomer", "venue.funding")
    # The refusal speaks of the seat's own reads only.
    assert refused["error"].endswith("20 of 30 venue request weight in the last 60 s; "
                                     "this read sends 20")
    slots = [i for i in ledger_items(rt, "venue.reader_slot")
             if i["assembly_id"] == "newcomer"]
    assert [i["slot"] for i in slots] == [False, True]


def test_two_seats_reading_the_same_thing_in_a_tick_are_each_charged_alike():
    """Rule 4: a seat cannot tell a tick's answer from a sent read. Both seats are
    charged the read, their answers carry no marker and have the same shape, and only
    one request reaches the venue."""
    rt = _read_runtime(30)
    rt._tick_reads = None
    sent = []
    funding = rt.exchange.target.funding
    rt.exchange.target.funding = lambda *a, **k: sent.append(1) or funding(*a, **k)
    first = _read(rt, "seed-decider", "venue.funding", fresh=False)
    second = _read(rt, "seed-observer", "venue.funding", fresh=False)  # the tick's answer
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert rt._venue_read_used("seed-decider") == rt._venue_read_used("seed-observer") == 20
    assert sent == [1]


def test_a_registration_s_reads_are_its_own():
    """The review's reuse scenario (cold #2): the read shares are keyed by the
    registration (id and version), never the id string. ``reader`` v1 spends its share
    and retires; its owner registers v2, whose share is untouched. (No round in flight
    straddles versions: a live id is never re-versioned and an invocation runs within
    one event. A seed's own id, seed-observer's in the review, is owned by itself
    alone, and a seat cannot endow itself, so no one ever takes a retired seed's id.)"""
    rt = _read_runtime(30)
    _register(rt, "reader")  # by seed-decider
    assert "error" not in _read(rt, "reader", "venue.funding")  # 20 of 30
    v1 = rt.assemblies["reader"].spec.version
    rt._retire_assembly("reader", "vote-1")
    _register(rt, "reader")  # its owner, seed-decider, again
    assert rt.assemblies["reader"].spec.version == v1 + 1
    assert rt._reader_id("reader") == f"reader#{v1 + 1}"
    assert rt._venue_read_used("reader") == 0
    assert f"reader#{v1}" in rt.venue_read_use  # v1's own row, until its window passes
    rt.clock.now_ns += 60_000_000_000
    rt._prune_read_use()
    assert f"reader#{v1}" not in rt.venue_read_use
    # The seed's own id: no one else may take it, and it cannot endow itself.
    rt._retire_assembly("seed-observer", "vote-2")
    from factorylab.cortex.request import Return

    handle = decision(rt, "seed-decider")
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "assembly", "id": "seed-observer", "model_id": "fake-haiku",
        "role": "producer", "accepts": ["Tick"], "system_prompt": "x",
        "max_tokens": 128}]}, 0, "ok"))
    assert "seed-observer" in rt.retired_assemblies
    assert ledger_items(rt, "registration.rejected")[-1]["reason"].endswith(
        "a retired id takes its next version only from its owner")
