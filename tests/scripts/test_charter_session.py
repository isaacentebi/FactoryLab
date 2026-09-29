"""The consolidated charter-time session, run offline (charter audit S1, U4, M5).

The population drafts cards from the norms, a sortition adopts the charter whole,
the export carries the digests the load path verifies, and λ is set against the
dollars it cost in rehearsal diaries.
"""

import json
import tomllib
from pathlib import Path

import pytest

from factorylab.charter.market import margin
from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts import charter_session
from scripts.rehearsal import voted_charter

pytestmark = pytest.mark.gate


def _diary():
    """Three windows' price.margin rows, as the runtime ledgers them."""
    def points(slope, dollars):
        return [{"v": v, "consequence": 0.2 + slope * v, "micro_usd": 100 + dollars * v,
                 "n": 2} for v in (0.0, 0.5, 1.0)]

    return [
        {"kind": "price.margin", "window": 1, "card_id": "cap", "lambda": 0.1,
         "points": points(0.3, 1000)},
        {"kind": "price.margin", "window": 2, "card_id": "cap", "lambda": 0.2,
         "points": points(0.1, 2000)},
        {"kind": "price.margin", "window": 3, "card_id": "cap", "lambda": 0.4,
         "points": points(0.0, 4100)},
        {"kind": "price.margin", "window": 3, "card_id": "floor", "lambda": 0.0,
         "points": points(0.1, 0)[:2]},
    ]


def test_the_report_is_the_runtime_s_own_margin_computation():
    """Cold review: the two paths disagreed and divided by average reward. The report
    now recomputes each window's margins from the ledgered points with the runtime's
    function, so it cannot disagree with it."""
    report = charter_session.lambda_report(_diary())
    assert report["source"] == "price.margin" and report["windows"] == 3
    cap = report["cards"]["cap"]
    assert [w["marginal_consequence"] for w in cap["windows"]] == pytest.approx([0.3, 0.1, 0.0])
    assert [w["micro_usd_per_violation"] for w in cap["windows"]] == pytest.approx(
        [1000, 2000, 4100])
    assert [w["micro_usd_per_violation"] for w in cap["windows"]] == pytest.approx(
        [margin(e["points"])["micro_usd_per_violation"] for e in _diary()[:3]])
    assert cap["identified_windows"] == 3
    assert cap["lambda_usd_correlation"] == pytest.approx(0.9993, abs=1e-3)
    floor = report["cards"]["floor"]
    assert floor["identified_windows"] == 0 and floor["lambda_usd_correlation"] is None
    assert charter_session.lambda_report([{"kind": "price.window", "window": 1}]) == {
        "source": "price.margin", "windows": 0, "cards": {}}


def test_the_dry_run_drafts_adopts_and_exports_a_charter_the_load_path_accepts(tmp_path):
    diary = tmp_path / "events.json"
    diary.write_text(json.dumps(_diary()))
    out = tmp_path / "session"
    code = charter_session.main(["session", "--world", "scripted", "--out-dir", str(out),
                                 "--dry-run", "--diary", str(diary)])
    assert code == 0
    evidence = json.loads((out / "session.json").read_text())
    manifest = load_manifest("scripted")
    # (a) The architect supplied the norms only; every seat proposed, a sortition voted.
    assert evidence["norms"] == list(manifest.charter.norms)
    proposals = evidence["draft"]["proposals"]
    assert {p["proposer"] for p in proposals} == {a.id for a in manifest.assemblies}
    passed = [p for p in proposals if p["problem"] is None]
    refused = [p for p in proposals if p["problem"] and p["problem"].startswith("preflight")]
    assert len(passed) == 4 and refused  # a second binding of one observation is refused
    assert all(len(p["ballots"]) == manifest.committee.seats for p in passed + refused)
    # (b) A fresh sortition adopted the charter whole, and saw the lambda-to-dollar report.
    assert evidence["approved"] is True
    assert len(evidence["adopt"]["ballots"]) == manifest.committee.seats
    assert evidence["lambda_dollars"][str(diary)]["cards"]["cap"]["identified_windows"] == 3
    # (c) The export binds the loaded cards and the roster that voted them.
    text = (out / "charter.toml").read_text()
    table = voted_charter(out / "charter.toml", manifest)
    assert table == tomllib.loads(text)["charter"]
    assert all("region" in card and "acceptable_region" not in card for card in table["cards"])
    world = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    world["charter"] = table
    loaded = manifest_from_dict(world)
    assert [c.id for c in loaded.charter.cards] == [p["card"]["id"] for p in passed]
    assert evidence["cost_micro"] == 0 and not any(c["error"] for c in evidence["calls"])
    # A second session into the same directory refuses to overwrite its evidence.
    with pytest.raises(FileExistsError):
        charter_session.main(["session", "--world", "scripted", "--out-dir", str(out),
                              "--dry-run"])


def test_the_report_command_reads_a_jsonl_diary(tmp_path, capsys):
    diary = tmp_path / "ledger.jsonl"
    diary.write_text("\n".join(json.dumps(row) for row in _diary()) + "\n")
    assert charter_session.main(["report", "--diary", str(diary)]) == 0
    report = json.loads(capsys.readouterr().out)[str(diary)]
    assert report["cards"]["cap"]["identified_windows"] == 3


def test_the_dry_run_runs_on_edition6(tmp_path):
    """Cold review: the session could not build edition 6's world (provider-native
    completions); its launch world now reads the manifest's own catalogue."""
    out = tmp_path / "edition6"
    world = Path(__file__).parents[2] / "worlds/edition6-testnet-rehearsal.toml"
    assert charter_session.main(["session", "--world", str(world), "--out-dir", str(out),
                                 "--dry-run"]) == 0
    evidence = json.loads((out / "session.json").read_text())
    manifest = load_manifest(str(world))
    assert evidence["approved"] is True
    assert {p["proposer"] for p in evidence["draft"]["proposals"]} == {
        a.id for a in manifest.assemblies}
    table = voted_charter(out / "charter.toml", manifest)
    assert table["norms"][-1]["id"] == "fidelity"
    # The card contract is the world's own section, never request text.
    world_block = charter_session.launch_world(manifest)
    assert "region {rule, lo, hi}" in world_block["mechanics"]["committee"]["card_contract"]


CAPITAL_LOOP = "worlds/edition6-capital-loop.toml"
TESTNET = "worlds/edition6-testnet-rehearsal.toml"
#: The world block's sections that carry a venue read or the wall clock at the moment
#: the block is built. Every other section, the unread pots and every seat's row
#: included, is the launched runtime's own, byte for byte (Chapter II §I.b).
OBSERVED = {"account", "custody", "venue", "world_resources", "world_update", "clock_now"}
#: The custody accounts a live world reads from its venue, rail and providers.
READ_ACCOUNTS = ("openrouter_credit", "venice_credit", "venue_perps", "venue_spot",
                 "base_reserve")


def _launched_block(world, tmp_path, monkeypatch):
    """The block a live seat of ``world`` reads at launch, built as the rehearsal runner
    builds it (``scripts/edition4_rehearsal.py``, ``_rehearse``): the prepaid-provider
    slot, ``DeniedMarket``, a live Hyperliquid adapter, the live admission clock, and for
    the hybrid world ``capital_loop=True`` and ``CapitalLoopRail`` over the hybrid rail,
    otherwise ``DeniedTransferRail``. The wires are the capital-loop run test's honest
    fakes: throwaway keys, never funded, and no byte leaves the process. Returns the
    manifest it launched (the hybrid world's carries its throwaway reserve) and the block.
    """
    from factorylab.runtime.live import LiveClock
    from factorylab.runtime.loop import Runtime
    from factorylab.world.exchange import HyperliquidExchange
    from scripts.edition4_rehearsal import (
        Admission,
        AdmissionClock,
        CapitalLoopRail,
        DeniedMarket,
        DeniedTransferRail,
    )
    from tests.scripts.test_capital_loop_live_run import wired

    wires = wired(tmp_path, monkeypatch)
    capital_loop = world == CAPITAL_LOOP
    manifest = load_manifest(str(wires["world"]) if capital_loop else world)
    rt = Runtime(manifest, events=1, seed=manifest.seed, initial_balance_micro=None,
                 ledger_path=str(tmp_path / "ledger.jsonl"), router_gamma=0.1,
                 provider=charter_session.ManifestCatalogue(manifest), market=DeniedMarket(),
                 exchange=HyperliquidExchange(mainnet=False),
                 clock_source=AdmissionClock(LiveClock(manifest.tick_interval_ns, 1),
                                             Admission(1_000_000, 1)),
                 kill_at_end=True, capital_loop=capital_loop)
    target = rt.treasury.rail.target
    rt.treasury.rail.target = (CapitalLoopRail(target) if capital_loop
                               else DeniedTransferRail(target))
    try:
        return manifest, rt._world_block()
    finally:
        rt._ledger_lock.close()


@pytest.mark.parametrize("world", [CAPITAL_LOOP, TESTNET])
def test_the_session_publishes_the_schematics_a_launched_seat_reads(world, tmp_path, monkeypatch):
    """Codex on #172: the session rendered the capital-loop world over a fake venue, and
    published the fake treasury's five transfer directions and its invented pots. The
    block is now the launched runtime's: every tool, contract and section a live seat of
    the world reads at launch, the same, and its observations unread (§I.b)."""
    manifest, launched = _launched_block(world, tmp_path, monkeypatch)
    rendered = charter_session.launch_world(manifest)
    assert set(rendered) == set(launched)
    assert rendered["tools"] == launched["tools"]
    for section in sorted(set(launched) - OBSERVED):
        assert rendered[section] == launched[section], section
    # Of the money, only the venue's equity was read at launch; the rest is the same.
    unread = {"trading_equity_usd"}
    assert ({k: v for k, v in rendered["world_resources"].items() if k not in unread}
            == {k: v for k, v in launched["world_resources"].items() if k not in unread})
    assert rendered["world_resources"]["trading_equity_usd"] is None
    transfer = [t for t in rendered["tools"] if t["id"] == "treasury.transfer"]
    if world == CAPITAL_LOOP:
        # The runner admits the conversion alone (CapitalLoopRail), so only it is published.
        assert [t["args"] for t in transfer] == [["direction", "reason", "usd"]]
        assert "directions: to_venice." in transfer[0]["description"]
    else:
        assert transfer == []  # DeniedTransferRail admits no direction


@pytest.mark.parametrize("world", [CAPITAL_LOOP, TESTNET])
def test_the_session_invents_no_observation(world):
    """Custody, the pots, the account and the listing are published as unread, never as
    a zero or a fake venue's cash: the render reads no venue and no rail."""
    block = charter_session.launch_world(load_manifest(world))
    custody = block["account"]["custody"]
    for name in READ_ACCOUNTS:
        account = custody[name]
        assert account["status"] == "unavailable" and account["reason"], name
        assert not {"balance_micro", "equity_usd", "cash_usd", "margin_used_usd",
                    "positions", "balances"} & set(account), name
    assert custody["pending_conversions"]["transfers"] == []  # none submitted: a fact
    assert block["custody"]["venue_accounts"]["venue_perps"]["status"] == "unavailable"
    assert block["account"]["status"] == "unavailable"
    assert block["venue"]["status"] == "unavailable" and "perp" not in block["venue"]
    pots = block["pots"]
    assert pots["venue"] is None and pots["reserve"] is None and pots["seed"] is None
    assert pots["sellers"] == {} and pots["complete"] is False and pots["total_micro"] is None
    assert pots["observed_at_ns"] is None
    resources = block["world_resources"]
    assert resources["trading_equity_usd"] is None
    inventory = resources["provider_inventory"]
    assert inventory["openrouter_usd"] is None and inventory["venice_usd"] is None
    assert not inventory["complete"]
    assert all(seat["your_resources"]["provider_credit_available_for_this_route_usd"] is None
               for seat in block["seats"])


def _tripwires(monkeypatch, tmp_path):
    """Every way the render could reach the network, a signer, a key or money raises."""
    import socket

    from eth_account import Account
    from hyperliquid.api import API

    from factorylab.kernel import ledger as kernel_ledger
    from factorylab.kernel.wallet import Wallet
    from factorylab.runtime import bootstrap
    from factorylab.world import treasury, treasury_rails, x402
    from factorylab.world.exchange import HyperliquidExchange

    touched = []

    def trip(name):
        def tripped(*_args, **_kwargs):
            touched.append(name)
            raise AssertionError(f"the schematics render touched {name}")
        return tripped

    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                        (socket, "create_connection"), (socket, "getaddrinfo"),
                        (API, "post"), (x402.request, "urlopen"),
                        (x402.request, "build_opener"),
                        (Account, "from_key"), (Account, "sign_message"),
                        (Account, "sign_transaction"), (Account, "sign_typed_data"),
                        (HyperliquidExchange, "__init__"),
                        (treasury_rails.LiveRail, "__init__"),
                        (treasury_rails.HybridRail, "__init__"),
                        (treasury.UnconfiguredRail, "__init__"),
                        (x402.X402Client, "__init__"), (bootstrap, "build_provider"),
                        (bootstrap, "live_exchange"), (treasury.Treasury, "transfer"),
                        (Wallet, "reserve"), (Wallet, "settle"),
                        (kernel_ledger.Ledger, "reopen")):
        monkeypatch.setattr(owner, name, trip(f"{getattr(owner, '__name__', owner)}.{name}"))
    ledgers = []
    made = kernel_ledger.Ledger.__init__

    def in_memory(self, path=None, *args, **kwargs):
        ledgers.append(path)
        if path is not None:
            trip("a ledger file")()
        made(self, path, *args, **kwargs)

    monkeypatch.setattr(kernel_ledger.Ledger, "__init__", in_memory)
    for key in ("RESERVE_PRIVATE_KEY", "HL_PRIVATE_KEY", "VENICE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    return touched, ledgers


@pytest.mark.parametrize("world", [CAPITAL_LOOP, TESTNET])
def test_the_render_calls_no_network_signs_nothing_and_writes_no_ledger(
        world, tmp_path, monkeypatch):
    manifest = load_manifest(str(Path(__file__).parents[2] / world))
    touched, ledgers = _tripwires(monkeypatch, tmp_path)
    block = charter_session.launch_world(manifest)
    assert block["tools"] and touched == []
    assert ledgers and all(path is None for path in ledgers)  # the diary is in memory only
    assert list(tmp_path.iterdir()) == []  # nothing was written beside the process


@pytest.mark.gate  # it attempts a world event, which the check tier's detector counts
def test_the_schematics_path_cannot_run_a_world():
    """The non-arming construction (factorylab/runtime/published.py) is refused every
    step of a world's life, and admits only inert adapters."""
    from factorylab.kernel.events import Event, EventKind
    from factorylab.runtime.live import LiveClock
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.published import (
        InertMarket,
        InertRail,
        InertRender,
        InertVenue,
        render_schematics,
    )
    from factorylab.world.exchange import FakeExchange

    world = load_manifest(CAPITAL_LOOP)
    rail = charter_session.launch_rail(world)
    assert isinstance(rail, InertRail) and rail.ALLOWED == ("to_venice",)

    def build(manifest=world, **changes):
        kwargs = {"events": 1, "seed": None, "initial_balance_micro": None,
                  "ledger_path": None, "router_gamma": 0.1,
                  "provider": charter_session.ManifestCatalogue(manifest),
                  "market": InertMarket(), "exchange": InertVenue(manifest.exchange),
                  "_schematics_rail": rail, **changes}
        return Runtime(manifest, **kwargs)

    rt = build()
    assert rt.schematics_only and rt.live
    for step in (rt.run, rt._run, rt._launch):
        with pytest.raises(InertRender):
            step()
    with pytest.raises(InertRender):
        rt._process_event(Event("tick", EventKind.TICK, 0, {}, "kernel"))
    assert not rt.started and rt.n == 0
    # Nothing that could arm, sign, write a diary or pace a world is admitted beside it.
    wired = type("Wired", (InertVenue,), {})(world.exchange)  # could carry a client
    for changes in ({"exchange": FakeExchange()}, {"exchange": wired}, {"capital_loop": True},
                    {"ledger_path": "ledger.jsonl"}, {"provider": None},
                    {"provider": charter_session.ScriptedCharterProvider(world)},
                    {"market": None}, {"clock_source": LiveClock(10**9, 1)}):
        with pytest.raises(ValueError, match="schematics_only requires"):
            build(**changes)
    with pytest.raises(ValueError, match="a live manifest"):
        scripted = load_manifest("scripted")
        build(scripted, exchange=InertVenue(world.exchange))
    # The rail it publishes can move nothing, and the venue answers no read.
    from factorylab.world.evm import RailError

    for step in ("plan", "preflight", "prepare", "send", "poll"):
        with pytest.raises(RailError):
            getattr(rail, step)("to_venice")
    venue = InertVenue(world.exchange)
    with pytest.raises(RuntimeError, match="not yet read"):
        venue.account()
    for write in ("place", "cancel", "close", "set_leverage", "vault_transfer"):
        with pytest.raises(InertRender):
            getattr(venue, write)()
    # The public path hands back the block alone, never a runtime that could be run.
    block = render_schematics(world, rail=rail)
    assert isinstance(block, dict) and "tools" in block


def test_the_plain_runtime_still_refuses_the_capital_loop_world():
    """Bootstrap refuses a live capital-loop world outside the rehearsal runner's
    ``--capital-loop``; the schematics render does not relax it."""
    from factorylab.runtime.loop import Runtime
    from factorylab.world.evm import RailError

    world = load_manifest(CAPITAL_LOOP)
    with pytest.raises(RailError):
        Runtime(world, events=1, seed=None, initial_balance_micro=None, ledger_path=None,
                router_gamma=0.1)
    assert world.exchange.kind == "hyperliquid"  # the session's manifest stays live
