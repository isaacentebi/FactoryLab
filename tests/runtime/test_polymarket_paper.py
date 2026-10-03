"""Polymarket paper orders in a running world (``[polymarket] venue = "paper"``).

The seats' reads and the kernel's settlement reads are the live public reads; the pot's
orders are simulated against the live books (``world/polymarket_paper.py``) and never
sent to Polymarket. Here the books are ``FakeClob``'s, served in process from a seeded
``FakePolymarket``; nothing touches a network or signs anything. AGENTS.md: every
kernel invariant gets a test that attempts to violate it, so each refused manifest is
attempted; the published text is checked factual and one with the other venue kinds;
the pot's custody reconciles; a checkpoint carries open orders, and a replay of the
journal reaches the run's state without reading again.
"""

import json
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from factorylab.runtime import polymarket
from factorylab.runtime.worlds import (
    KillSpec,
    PolymarketSpec,
    load_manifest,
    manifest_from_dict,
)
from factorylab.world.polymarket import PolymarketReader
from factorylab.world.polymarket_paper import PaperPolymarket
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_diary, _consequence_runtime
from tests.runtime.test_polymarket_surface import still_fake
from tests.world.fake_clob import FakeClob


def bind(rt, server):
    """Answer the world's public reads and its pot's reads from ``server``."""
    def get(url):
        return server("GET", url, {}, None)

    rt.polymarket.venue.target.get = get
    rt.polymarket.pot.target.reads.target.get = get


def paper_world(*, fake=None, collateral="50", kill=False, **spec):
    manifest = replace(load_manifest("scripted"), polymarket=PolymarketSpec(
        enabled=True, venue="paper", collateral_micro=int(Decimal(collateral) * 1_000_000),
        **spec))
    if kill:
        manifest = replace(manifest, kill=KillSpec(wind_down=True))
    rt = _consequence_runtime(manifest=manifest)
    rt._manage_reserve_window()
    server = FakeClob(fake if fake is not None else still_fake())
    bind(rt, server)
    return rt, server


def token(server, market="fake-1", side=0):
    return server.fake._markets[market]["tokens"][side]


def ask_at(server, price, market="fake-1"):
    server.fake._markets[market]["mid"] = Decimal(price) - server.fake.tick


def buy(rt, server, handle, *, price="0.39", size="10", slot="tool:0", market="fake-1"):
    call = {"tool": "polymarket.place_limit",
            "args": {"token_id": token(server, market), "side": "buy", "size": size,
                     "price": price}}
    return rt._run_tool("seed-decider", handle, call, slot=slot)[0]


def step(rt):
    rt.clock.now_ns += 1
    polymarket.tick(rt)


def kinds(rt):
    return [item["kind"] for item in _consequence_diary(rt)]


# --- the manifest ------------------------------------------------------------------------

def _raw():
    import tomllib
    from pathlib import Path

    return tomllib.loads(Path(__file__).parents[2].joinpath("worlds/scripted.toml").read_text())


@pytest.mark.parametrize(("block", "refused"), [
    # Live orders are the live venue's; the paper venue always takes writes.
    ({"orders": True}, "simulated venue always takes writes"),
    ({"orders": True, "principal_usd": "5", "funder": "0x" + "ab" * 20},
     "simulated venue always takes writes"),
    # The paper pot opens with declared simulated USDC.
    ({}, "needs a positive polymarket.collateral_usd"),
    ({"collateral_usd": "0"}, "needs a positive polymarket.collateral_usd"),
    # It has no wallet and signs nothing.
    ({"collateral_usd": "5", "funder": "0x" + "ab" * 20}, "the paper pot has none"),
    ({"collateral_usd": "5", "signature_type": 3}, "the paper pot has none"),
    # Its own reads land on Gamma /markets beside the public reads.
    ({"collateral_usd": "5", "read_requests_per_10s": 250, "order_requests_per_10s": 60},
     "at most 300"),
])
def test_a_paper_block_that_makes_no_sense_is_refused_at_load(block, refused):
    with pytest.raises(ValueError, match=refused):
        manifest_from_dict({**_raw(), "polymarket": {"enabled": True, "venue": "paper",
                                                     **block}})


def test_a_paper_world_loads_under_any_name_and_is_hashed():
    raw = _raw()
    on = manifest_from_dict({**raw, "polymarket": {"enabled": True, "venue": "paper",
                                                   "collateral_usd": "25"}})
    assert on.name != "funded" and on.polymarket.collateral_micro == 25_000_000
    live = manifest_from_dict({**raw, "polymarket": {"enabled": True, "venue": "live"}})
    assert on.manifest_hash() != live.manifest_hash()
    with pytest.raises(ValueError, match="fake, live or paper"):
        manifest_from_dict({**raw, "polymarket": {"enabled": True, "venue": "amoy"}})


def test_the_funded_paper_world_is_funded_with_paper_orders_and_its_digests_verify():
    import tomllib
    from pathlib import Path

    worlds = Path(__file__).parents[2] / "worlds"
    funded = tomllib.loads((worlds / "funded.toml").read_text())
    paper = tomllib.loads((worlds / "funded-paper.toml").read_text())
    assert paper.pop("name") == "funded-paper" and funded.pop("name") == "funded"
    assert paper.pop("polymarket") == {
        "enabled": True, "venue": "paper", "collateral_usd": "100", "max_order_usd": "10",
        "max_open_usd": "100", "max_orders_per_window": 20}
    assert funded.pop("polymarket") == {"enabled": True, "venue": "live"}
    assert paper == funded
    manifest = load_manifest("funded-paper")
    manifest.check_ratified_digest()
    assert not manifest.exchange.mainnet and not manifest.polymarket.orders


# --- what a seat sees -------------------------------------------------------------------

def test_the_paper_venue_publishes_the_simulated_venue_s_physics_and_names_itself():
    """One order physics on every venue kind: the tools render as the simulated venue's
    apart from the venue's name, and the world.read order section apart from the flags
    naming the venue kind and the rule stating what the paper venue reads and cannot
    see. The texts state facts (AGENTS.md rules 1 and 3)."""
    from tests.runtime.test_polymarket_surface import world

    rt, _server = paper_world(principal_micro=40_000_000, order_requests_per_10s=7)
    sim = world(principal_micro=40_000_000, order_requests_per_10s=7,
                collateral_micro=50_000_000)
    names = polymarket.VENUE_NAMES

    def rendered(world_rt, venue):
        return {tool: json.loads(json.dumps(spec).replace(names[venue], "<venue>"))
                for tool, spec in world_rt.tool_specs.items() if tool.startswith("polymarket.")}

    tools = rendered(rt, "paper")
    assert set(polymarket.WRITES) | {polymarket.ACCOUNT, polymarket.OPEN_ORDERS} <= set(tools)
    assert tools == rendered(sim, "fake")
    description = rt.tool_specs["polymarket.place_limit"]["description"]
    assert names["paper"] in description and "sends none of them to Polymarket" in description
    paper_facts, sim_facts = (world_rt.institution_section("admission")["tools"][
        "polymarket_orders"] for world_rt in (rt, sim))
    assert (paper_facts.pop("paper_orders"), sim_facts.pop("paper_orders")) == (True, False)
    assert paper_facts["live_orders"] is False
    assert paper_facts == sim_facts
    assert paper_facts["collateral_micro"] == 50_000_000
    rule = paper_facts["rules"]["paper"]
    for fact in ("never sent to Polymarket", "no queue position", "counted again",
                 "order_requests_per_10s"):
        assert fact in rule


# --- the order path -----------------------------------------------------------------------

def test_a_paper_order_is_an_intent_first_rests_fills_on_the_live_book_and_reconciles():
    rt, server = paper_world()
    handle = collateral_decision(rt)
    step(rt)  # the pot's opening
    ask_at(server, "0.41")
    crossing = buy(rt, server, handle, price="0.41", slot="tool:0")
    assert crossing["status"] == "rejected" and "crosses book" in crossing["error"]
    result = buy(rt, server, handle, price="0.39", slot="tool:1")
    assert result["status"] == "resting"
    assert rt.polymarket.order_ids == {result["order_id"]: f"{handle}:tool:1"}
    # The crossing order never existed: it uses none of the principal.
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal("3.9")
    ask_at(server, "0.38")
    step(rt)
    assert rt.polymarket.filled == {result["order_id"]: "10"}
    pots = polymarket.pots_view(rt)
    assert pots["polymarket"] == 50_000_000 and pots["polymarket_usdc"] == 46_100_000
    assert rt.polymarket.drifting is False
    # The market resolves YES: the payout reaches the pot as resolved tokens and is
    # booked late to the decision that held them.
    server.fake._markets["fake-1"].update(closed=True, winner=0)
    step(rt)
    assert polymarket.custody_books(rt)["booked_micro"] == 6_100_000
    assert polymarket.pots_view(rt)["polymarket"] == 56_100_000
    seen = kinds(rt)
    assert seen.index("polymarket.intent") < seen.index("polymarket.acknowledged")
    assert "polymarket.fill" in seen and "polymarket.resolution" in seen
    assert "polymarket.drift" not in seen


def test_the_paper_pot_is_valued_at_its_exact_cost_never_a_repeating_average():
    """Sol P0, round 2: 5 tokens at 0.38 and 12 at 0.39 cost exactly 6.58 USD, whose
    average price repeats; the pot is valued at its integer cost, so no micro-USD is
    lost or invented in custody, the pots or the reconciliation."""
    rt, server = paper_world(collateral="100")
    handle = collateral_decision(rt)
    step(rt)
    ask_at(server, "0.41")
    first = buy(rt, server, handle, price="0.38", size="5", slot="tool:0")
    ask_at(server, "0.37")
    step(rt)
    ask_at(server, "0.41")
    second = buy(rt, server, handle, price="0.39", size="12", slot="tool:1")
    ask_at(server, "0.37")
    step(rt)
    assert rt.polymarket.filled == {first["order_id"]: "5", second["order_id"]: "12"}
    (position,) = rt.polymarket.account()["positions"]
    assert position["cost_micro"] == 6_580_000 and position["size"] == "17"
    pots = polymarket.pots_view(rt)
    assert pots["polymarket"] == 100_000_000 and pots["polymarket_usdc"] == 93_420_000
    assert pots["polymarket_tokens"][0]["cost_micro"] == 6_580_000
    assert polymarket.held_at_cost(rt.polymarket.account()) == Decimal(100)
    assert rt.polymarket.drifting is False


def test_no_order_of_a_paper_world_is_ever_sent_to_polymarket():
    rt, server = paper_world()
    handle = collateral_decision(rt)
    step(rt)
    ask_at(server, "0.41")
    result = buy(rt, server, handle)
    assert rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel", "args": {
        "order_id": result["order_id"]}}, slot="tool:1")[0]["status"] == "cancelled"
    step(rt)
    assert server.calls and all(method == "GET" for method, _path in server.calls)
    assert {path for _m, path in server.calls} <= {"/book", "/markets/fake-1", "/markets"}
    assert server.fake._all_orders == {}


def test_a_kill_cancels_paper_orders_and_reports_held_tokens_as_residual():
    rt, server = paper_world(kill=True)
    handle = collateral_decision(rt)
    step(rt)
    ask_at(server, "0.41")
    buy(rt, server, handle, price="0.39", slot="tool:0")
    ask_at(server, "0.38")
    step(rt)  # ten held
    ask_at(server, "0.41")
    buy(rt, server, handle, price="0.30", slot="tool:1")  # resting
    report = rt.kill("test")["polymarket"]
    assert report["cancelled"] == 1 and report["open_orders"] == 0
    assert [p["size"] for p in report["residual"]] == ["10"]
    assert report["exposure_state"] == "wind_down_pending"


def test_a_paper_world_reads_the_network_so_it_runs_only_on_the_wall_clock(tmp_path):
    """The paper venue's reads are live: the world is admitted as a live reader (a
    ledger, the wall clock, the host's IP lock), never on a simulated clock."""
    from factorylab.runtime.loop import Runtime
    from factorylab.world.exchange import FakeExchange

    manifest = replace(load_manifest("scripted"), polymarket=PolymarketSpec(
        enabled=True, venue="paper", collateral_micro=5_000_000))
    rt = Runtime(manifest, events=4, seed=1, initial_balance_micro=None,
                 ledger_path=str(tmp_path / "world.jsonl"), exchange=FakeExchange())
    assert isinstance(rt.polymarket.venue.target, PolymarketReader)
    with pytest.raises(polymarket.LiveReaderRefused,
                       match=polymarket.LIVE_REQUIRES_THE_WALL_CLOCK):
        rt.run()
    assert rt._polymarket_ip_lock is None


def test_offline_a_paper_world_keeps_the_paper_pot_and_reads_the_simulated_market():
    """Sol P0, round 1: offline (``simulate_reads``) the simulated market answers the
    reads, and the pot's matching and custody stay the paper pot's: a fill is bounded by
    the ask's size and shared across the pot's orders, as published."""
    rt, _server = paper_world(collateral="30")
    published = json.dumps(rt.tool_specs, sort_keys=True)
    polymarket.simulate_reads(rt)
    surface = rt.polymarket
    assert surface.paper and surface.venue.deterministic
    assert isinstance(surface.pot.target, PaperPolymarket)
    assert not isinstance(surface.venue.target, PolymarketReader)  # offline: no IP lock
    assert surface.account()["usdc"] == "30"
    assert json.dumps(rt.tool_specs, sort_keys=True) == published
    fake = surface.venue.target
    fake.markets = tuple({**m, "resolves_after_s": None} for m in fake.markets)
    fake.step_ticks, fake.depth_shares = 0, Decimal(1)  # one token at the ask
    handle = collateral_decision(rt)
    yes = fake.market("fake-1")["outcomes"][0]["token_id"]
    placed = [rt._run_tool("seed-decider", handle, {"tool": "polymarket.place_limit", "args": {
        "token_id": yes, "side": "buy", "size": "10", "price": price}}, slot=slot)[0]
        for price, slot in (("0.30", "tool:0"), ("0.31", "tool:1"))]
    assert [r["status"] for r in placed] == ["resting", "resting"]
    fake._markets["fake-1"]["mid"] = Decimal("0.21")  # the ask, 0.22, one token deep
    rt.clock.now_ns += 1
    polymarket.tick(rt)
    # One token at the ask: the higher-priced buy takes it, the other nothing.
    assert rt.polymarket.filled == {placed[1]["order_id"]: "1"}
    account = rt.polymarket.account()
    assert account["usdc"] == "29.69" and account["positions"][0]["size"] == "1"
    assert rt.polymarket.drifting is False and fake._positions == {}
    # A resume of the offline run restores the simulated market beside the pot.
    from factorylab.runtime.resume import restore_runtime, runtime_state

    twin, _ = paper_world(collateral="30")
    restore_runtime(twin, runtime_state(rt))
    polymarket.simulate_reads(twin)
    assert twin.polymarket.venue.target._markets == fake._markets
    assert twin.polymarket.account() == rt.polymarket.account()


# --- checkpoints and replay -----------------------------------------------------------------

def test_a_checkpoint_carries_open_paper_orders_and_the_twin_fills_them():
    from factorylab.runtime.resume import decode, restore_runtime, runtime_state

    rt, server = paper_world()
    handle = collateral_decision(rt)
    step(rt)
    ask_at(server, "0.41")
    result = buy(rt, server, handle)
    state = runtime_state(rt)
    assert decode(state["polymarket"])["venue"]["resting"] == [result["order_id"]]
    twin, _ = paper_world()
    bind(twin, server)
    assert twin.polymarket.account()["open_orders"] == []
    restore_runtime(twin, state)
    assert isinstance(twin.polymarket.pot.target, PaperPolymarket)
    assert twin.polymarket.account() == rt.polymarket.account()
    assert twin.polymarket.intents == rt.polymarket.intents
    ask_at(server, "0.38")
    sent = len(server.calls)
    step(twin)
    # A resumed pot counts its whole read allowance as sent at the resume (the process
    # that died may have sent it): nothing is read, so nothing fills yet.
    assert twin.polymarket.filled == {} and len(server.calls) == sent
    budget = twin.polymarket.pot.target.reads.target.budget
    budget.wall = lambda: time.time_ns() + 10_000_000_000  # ten seconds later
    step(twin)
    assert twin.polymarket.filled == {result["order_id"]: "10"}
    assert twin.polymarket.account()["usdc"] == "46.1"


def test_a_resumed_paper_pot_counts_its_read_allowance_as_spent():
    rt, _server = paper_world(order_requests_per_10s=5)
    saved = rt.polymarket.state()
    rt.polymarket.restore(saved)  # what a resume does
    with pytest.raises(polymarket_budget_spent()):
        rt.polymarket.pot.target.reads.target.mark_book("1")


def polymarket_budget_spent():
    from factorylab.world.polymarket_clob import BudgetSpent

    return BudgetSpent


def test_a_replay_of_a_paper_world_s_journal_reaches_its_state_without_reading_again():
    """Chapter II §II: the factory never rewinds, and a resume replays the diary. The
    pot's reads are journaled and its state is re-run on them: replaying the journal
    from the checkpoint reproduces every answer, fill, resolution and ledger row, and
    the network is never asked."""
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt, server = paper_world()
    handle = collateral_decision(rt)
    rt.clock.now_ns = 1
    polymarket.tick(rt)
    before = runtime_state(rt)
    journal = rt.ledger
    prefix = len(journal.ledger._recovery_items())
    journal.active = True
    try:
        def drive(move):
            answers = []
            move(lambda: ask_at(server, "0.41"))
            answers.append(buy(rt, server, handle, price="0.39", slot="tool:0"))
            answers.append(buy(rt, server, handle, price="0.35", size="5", slot="tool:1"))
            move(lambda: ask_at(server, "0.38"))
            rt.clock.now_ns = 2
            polymarket.tick(rt)
            move(lambda: server.fake._markets["fake-1"].update(closed=True, winner=1))
            for now in (3, 4):
                rt.clock.now_ns = now
                polymarket.tick(rt)
            return (answers, rt.polymarket.state(), polymarket.custody_books(rt),
                    rt.polymarket.account())

        expected = drive(lambda change: change())
        rows = journal.ledger._recovery_items()[prefix:]
        assert any(r.get("name") == "polymarket.advance" for r in rows)
        restore_runtime(rt, before)
        rt.polymarket.pot.target.reads.target.get = (
            lambda url: pytest.fail(f"a replay read {url}"))
        calls = len(server.calls)
        journal.tail = iter(rows)
        journal.recovering = True
        assert drive(lambda change: None) == expected
        assert journal.peek() is None and len(server.calls) == calls
        state = expected[1]["venue"]
        assert state["resting"] == [] and state["cash_micro"] == 46_100_000
    finally:
        journal.recovering = False
