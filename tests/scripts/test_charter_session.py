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
                                 "--dry-run", "--diary", str(diary), "--launch", "run"])
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
    # (c) The export binds the loaded cards and the roster that voted them, and both
    # name the launch the world was rendered for.
    text = (out / "charter.toml").read_text()
    assert evidence["launch"]["mode"] == "run"
    assert evidence["launch"]["command"] == "factorylab run"
    assert evidence["launch"]["rendered_manifest"]["name"] == manifest.name
    assert "# launch = run (factorylab run)\n" in text
    table = voted_charter(out / "charter.toml", manifest, "run")
    assert table["launch"] == "run"
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
                              "--dry-run", "--launch", "run"])


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
                                 "--dry-run", "--launch", "rehearsal"]) == 0
    evidence = json.loads((out / "session.json").read_text())
    manifest = load_manifest(str(world))
    assert evidence["approved"] is True
    assert {p["proposer"] for p in evidence["draft"]["proposals"]} == {
        a.id for a in manifest.assemblies}
    table = voted_charter(out / "charter.toml", manifest)
    assert table["norms"][-1]["id"] == "fidelity"
    # The card contract is the world's own section, never request text.
    assert evidence["launch"]["mode"] == "rehearsal"
    world_block = charter_session.launch_world(manifest, "rehearsal")
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


def _world(case, wires):
    """The manifest a case names: the hybrid world on the wires' throwaway reserve, the
    testnet world, or the testnet world with that reserve configured (not hybrid)."""
    from dataclasses import replace

    if case == "capital":
        return load_manifest(str(wires["world"]))
    testnet = load_manifest(TESTNET)
    if case == "testnet":
        return testnet
    return replace(testnet, treasury=replace(testnet.treasury,
                                             reserve_address=wires["reserve"].address))


def _launched_block(manifest, launch, tmp_path, *, as_launched=False):
    """The block a live seat reads when ``launch`` starts ``manifest``, built by that
    launch path's own code on the capital-loop run test's honest wire fakes (throwaway
    keys, never funded; no byte leaves the process; ``wired`` must be installed).

    ``run`` is ``factorylab run``'s construction: the Runtime bootstrap builds, its rail
    ``bootstrap.rail_class``'s choice. ``rehearsal`` and ``capital-loop`` are
    ``_rehearse``'s: the prepaid-provider slot, ``DeniedMarket``, the live admission
    clock, ``capital_loop`` for the hybrid world, and the runner's ``launch_guard``
    installed over the rail. ``as_launched`` builds on the runner's ``effective_manifest``
    as ``_rehearse`` does (for the rail it installs); otherwise on the manifest as given,
    which is what the session renders.
    """
    from factorylab.runtime.live import LiveClock
    from factorylab.runtime.loop import Runtime
    from factorylab.world.exchange import HyperliquidExchange
    from scripts.edition4_rehearsal import (
        Admission,
        AdmissionClock,
        DeniedMarket,
        effective_manifest,
        launch_guard,
    )

    capital_loop = launch == "capital-loop"
    launched = manifest
    kwargs = {}
    if launch != "run":
        if as_launched:
            launched = effective_manifest(manifest, native_completions=True,
                                          capital_loop=capital_loop)
        kwargs = {"market": DeniedMarket(), "kill_at_end": True, "capital_loop": capital_loop,
                  "clock_source": AdmissionClock(LiveClock(launched.tick_interval_ns, 1),
                                                 Admission(1_000_000, 1))}
    rt = Runtime(launched, events=1, seed=launched.seed, initial_balance_micro=None,
                 ledger_path=str(tmp_path / "ledger.jsonl"), router_gamma=0.1,
                 provider=charter_session.ManifestCatalogue(launched),
                 exchange=HyperliquidExchange(mainnet=False), **kwargs)
    if launch != "run":
        rt.treasury.rail.target = launch_guard(capital_loop)(rt.treasury.rail.target)
    try:
        return rt._world_block()
    finally:
        rt._ledger_lock.close()


def _transfer(block):
    """The published ``treasury.transfer`` entry and the directions it names, or None."""
    tools = [t for t in block["tools"] if t["id"] == "treasury.transfer"]
    if not tools:
        return None, ()
    named = tools[0]["description"].split("directions: ", 1)[1].split(".", 1)[0]
    return tools[0], tuple(named.split(", "))


@pytest.mark.parametrize("case, launch", [("capital", "capital-loop"), ("testnet", "rehearsal"),
                                          ("testnet", "run"), ("reserve", "run")])
def test_the_session_publishes_the_schematics_a_launched_seat_reads(
        case, launch, tmp_path, monkeypatch):
    """Codex on #172: the session rendered the capital-loop world over a fake venue, and
    published the fake treasury's five transfer directions and its invented pots. The
    block is now the launched runtime's: every tool, contract and section a live seat of
    the world reads at that launch, the same, and its observations unread (§I.b)."""
    from tests.scripts.test_capital_loop_live_run import wired

    manifest = _world(case, wired(tmp_path, monkeypatch))
    launched = _launched_block(manifest, launch, tmp_path, as_launched=True)
    rendered = charter_session.launch_world(manifest, launch)
    assert set(rendered) == set(launched)
    assert rendered["tools"] == launched["tools"]
    for section in sorted(set(launched) - OBSERVED):
        assert rendered[section] == launched[section], section
    # Of the money, only the venue's equity was read at launch; the rest is the same.
    unread = {"trading_equity_usd"}
    assert ({k: v for k, v in rendered["world_resources"].items() if k not in unread}
            == {k: v for k, v in launched["world_resources"].items() if k not in unread})
    assert rendered["world_resources"]["trading_equity_usd"] is None


@pytest.mark.parametrize("case, launch, directions", [
    # CapitalLoopRail over HybridRail: the conversion alone.
    ("capital", "capital-loop", ("to_venice",)),
    # The rehearsal runner: DeniedTransferRail over the rail of its effective manifest.
    ("testnet", "rehearsal", ()),
    ("reserve", "rehearsal", ()),
    # factorylab run: bootstrap's rail. UnconfiguredRail without a reserve, LiveRail with
    # one (every direction but to_venice on a testnet venue).
    ("testnet", "run", ("spot_to_perps", "perps_to_spot")),
    ("reserve", "run", ("to_reserve", "to_venue", "spot_to_perps", "perps_to_spot")),
])
def test_each_launch_publishes_the_transfer_contract_its_launch_path_installs(
        case, launch, directions, tmp_path, monkeypatch):
    """Codex on 85dc6fa: a testnet world was always rendered on the runner's denied rail,
    though ``factorylab run`` installs LiveRail or UnconfiguredRail. The session now
    renders the rail of the launch it names, and it is the one that launch installs."""
    from tests.scripts.test_capital_loop_live_run import wired

    manifest = _world(case, wired(tmp_path, monkeypatch))
    installed = _launched_block(manifest, launch, tmp_path, as_launched=True)
    rendered = charter_session.launch_world(manifest, launch)
    assert _transfer(rendered) == _transfer(installed)
    assert _transfer(rendered)[1] == directions
    assert rendered["compute_supply"] == installed["compute_supply"]


def test_a_session_names_its_launch_and_a_hybrid_world_has_one(tmp_path, capsys):
    """No default launch: a non-hybrid world without --launch is refused, and a hybrid
    Venice world is refused any launch but capital-loop. Nothing is written."""
    from scripts.edition4_rehearsal import RehearsalRefused

    out = tmp_path / "session"
    for world, launch, refusal in ((TESTNET, None, "launch_required"),
                                   (CAPITAL_LOOP, "run", "launch_refused"),
                                   (CAPITAL_LOOP, "rehearsal", "launch_refused")):
        argv = ["session", "--world", world, "--out-dir", str(out), "--dry-run"]
        with pytest.raises(SystemExit) as stopped:
            charter_session.main(argv + ([] if launch is None else ["--launch", launch]))
        assert stopped.value.code == 2 and refusal in capsys.readouterr().err
        assert not out.exists()
    with pytest.raises(ValueError, match="launch_required"):
        charter_session.launch_world(load_manifest(TESTNET), None)
    with pytest.raises(ValueError, match="launch_refused"):
        charter_session.launch_world(load_manifest(CAPITAL_LOOP), "run")
    # The runner refuses what it cannot launch: capital-loop needs a hybrid world, and
    # neither runner mode runs a simulated one.
    with pytest.raises(RehearsalRefused, match="capital_loop_requires_hybrid_venice_world"):
        charter_session.launch_world(load_manifest(TESTNET), "capital-loop")
    with pytest.raises(RehearsalRefused, match="testnet_hyperliquid_required"):
        charter_session.launch_world(load_manifest("scripted"), "rehearsal")
    assert charter_session.launch_mode(load_manifest(CAPITAL_LOOP), None) == "capital-loop"


@pytest.mark.parametrize("world, launch", [(CAPITAL_LOOP, "capital-loop"),
                                           (TESTNET, "rehearsal"), (TESTNET, "run")])
def test_the_session_invents_no_observation(world, launch):
    """Custody, the pots, the account and the listing are published as unread, never as
    a zero or a fake venue's cash: the render reads no venue and no rail."""
    block = charter_session.launch_world(load_manifest(world), launch)
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


@pytest.mark.parametrize("world, launch", [(CAPITAL_LOOP, "capital-loop"),
                                           (TESTNET, "rehearsal"), (TESTNET, "run")])
def test_the_render_calls_no_network_signs_nothing_and_writes_no_ledger(
        world, launch, tmp_path, monkeypatch):
    manifest = load_manifest(str(Path(__file__).parents[2] / world))
    touched, ledgers = _tripwires(monkeypatch, tmp_path)
    block = charter_session.launch_world(manifest, launch)
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
    rail = charter_session.launch_rail(
        charter_session.launched_manifest(world, "capital-loop"), "capital-loop")
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


def _bound(world, launch, tmp_path):
    """A copy of ``world`` whose charter states ``launch`` (``charter.launch``)."""
    text = (Path(__file__).parents[2] / world).read_text()
    path = tmp_path / Path(world).name
    path.write_text(text.replace("\n[charter]\n", f'\n[charter]\nlaunch = "{launch}"\n', 1))
    return path


def test_the_charter_states_its_launch_inside_the_digest_the_load_path_verifies():
    """The export writes charter.launch into the table, so the charter digest covers
    it; the manifest reads it as admission provenance, outside the manifest hash."""
    from factorylab.charter.provenance import charter_content, charter_digest

    base = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    table = dict(base["charter"], launch="rehearsal")
    bound = manifest_from_dict({**base, "charter": table})
    plain = manifest_from_dict(base)
    assert bound.charter_launch == "rehearsal" and plain.charter_launch is None
    assert bound.charter_content_sha256 == charter_digest(charter_content(table))
    assert bound.charter_content_sha256 != plain.charter_content_sha256
    assert bound.manifest_hash() == plain.manifest_hash()
    with pytest.raises(ValueError, match="charter.launch must be one of"):
        manifest_from_dict({**base, "charter": dict(base["charter"], launch="wake")})


def test_each_launcher_admits_its_own_mode_and_refuses_another():
    from factorylab.runtime.worlds import CHARTER_LAUNCHES, CharterLaunchRefused

    base = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    unbound = manifest_from_dict(base)
    for voted in CHARTER_LAUNCHES:
        bound = manifest_from_dict({**base, "charter": dict(base["charter"], launch=voted)})
        for launch in CHARTER_LAUNCHES:
            unbound.check_launch(launch)  # an unratified world states none: as today
            if launch == voted:
                bound.check_launch(launch)
                continue
            with pytest.raises(CharterLaunchRefused) as refused:
                bound.check_launch(launch)
            assert refused.value.reason == "charter_launch_mismatch"


def test_a_ratified_charter_that_states_no_launch_is_refused():
    """A manifest carrying ratification digests but no charter.launch launches under
    no launcher, and a funded (mainnet) admission requires the key."""
    from dataclasses import replace

    from factorylab.charter.provenance import charter_content, charter_digest
    from factorylab.runtime.worlds import CHARTER_LAUNCHES, CharterLaunchRefused

    base = tomllib.loads((Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    ratified = manifest_from_dict({**base, "charter": dict(
        base["charter"], ratified_sha256=charter_digest(charter_content(base["charter"])))})
    for launch in CHARTER_LAUNCHES:
        with pytest.raises(CharterLaunchRefused, match="charter_launch_missing"):
            ratified.check_launch(launch)
    from factorylab.charter.provenance import roster_hash

    funded = replace(ratified, exchange=replace(ratified.exchange, client_namespace="f" * 32),
                     charter_roster_sha256=roster_hash(ratified))
    with pytest.raises(ValueError, match="mainnet requires charter.launch"):
        funded._validate_funded_admission()
    replace(funded, charter_launch="run")._validate_funded_admission()


def test_factorylab_run_refuses_a_charter_voted_for_the_rehearsal_runner(tmp_path, capsys):
    from factorylab.runtime.cli import ARGUMENT_EXIT, main
    from factorylab.runtime.loop import run_world
    from factorylab.runtime.worlds import CharterLaunchRefused

    world = _bound("worlds/scripted.toml", "rehearsal", tmp_path)
    assert main(["run", "--world", str(world), "--events", "1"]) == ARGUMENT_EXIT
    assert capsys.readouterr().err.splitlines() == ["factorylab run: charter_launch_mismatch"]
    with pytest.raises(CharterLaunchRefused):
        run_world(load_manifest(str(world)), events=1)


@pytest.mark.gate  # a matching launch runs a world
def test_factorylab_run_admits_a_charter_voted_for_it(tmp_path, capsys):
    from factorylab.runtime.cli import main

    world = _bound("worlds/scripted.toml", "run", tmp_path)
    assert main(["run", "--world", str(world), "--events", "1"]) == 0


@pytest.mark.parametrize("world, voted, capital_loop", [
    (TESTNET, "run", False), (TESTNET, "capital-loop", False),
    (CAPITAL_LOOP, "rehearsal", True), (CAPITAL_LOOP, "run", True)])
def test_the_rehearsal_runner_refuses_a_charter_voted_for_another_launch(
        world, voted, capital_loop, tmp_path):
    """Refused at the manifest, before any key, lock, chain read or venue."""
    from scripts import edition4_rehearsal as rehearsal

    out = tmp_path / "run"
    report = rehearsal.run_rehearsal(_bound(world, voted, tmp_path), out=out,
                                     capital_loop=capital_loop, provider=object())
    assert report["status"] == "failed"
    assert report["refusal"]["reason"] == "charter_launch_mismatch"


@pytest.mark.gate  # run_rehearsal runs a real world
@pytest.mark.parametrize("capital_loop", [True, False])
def test_the_rehearsal_runner_admits_a_charter_voted_for_it(capital_loop, tmp_path, monkeypatch):
    from scripts import edition4_rehearsal as rehearsal
    from tests.scripts.test_capital_loop_live_run import launch_kwargs, repo_root, wired

    w = wired(tmp_path, monkeypatch)
    source = w["world"] if capital_loop else Path(__file__).parents[2] / TESTNET
    voted = "capital-loop" if capital_loop else "rehearsal"
    path = tmp_path / "bound" / Path(source).name
    path.parent.mkdir()
    path.write_text(Path(source).read_text().replace(
        "\n[charter]\n", f'\n[charter]\nlaunch = "{voted}"\n', 1))
    report = rehearsal.run_rehearsal(str(path), out=tmp_path / "runs" / "bound",
                                     capital_loop=capital_loop,
                                     duration_ns=3_600 * 1_000_000_000,
                                     source_root=repo_root(), **launch_kwargs(w))
    assert report["status"] == "completed", report.get("error")


def test_a_runner_launch_renders_the_manifest_the_runner_launches(tmp_path):
    """Codex on 88cecea: the runners hand Runtime effective_manifest(...), so the ballots
    read that world. worlds/testnet.toml declares a 120 s tick and a reserve; under the
    rehearsal runner it runs a 10 s tick on a stripped treasury, and says so. Under
    ``run`` it is the file as given. The roster digest is the file's, as the load path
    hashes it."""
    from factorylab.charter.provenance import roster_hash

    given = load_manifest("worlds/testnet.toml")
    assert given.tick_interval_ns == 120 * 10**9 and given.treasury.reserve_address
    rehearsed = charter_session.launch_world(given, "rehearsal")
    run = charter_session.launch_world(given, "run")
    assert rehearsed["tick_intervals"]["declared_ns"] == 10 * 10**9
    assert run["tick_intervals"]["declared_ns"] == 120 * 10**9
    treasury = rehearsed["mechanics"]["treasury"]
    assert treasury["cctp_forwarding"] == "never"
    assert run["mechanics"]["treasury"]["cctp_forwarding"] == "on_empty_gas"
    assert _transfer(rehearsed) == (None, ())
    assert _transfer(run)[1] == ("to_reserve", "to_venue", "spot_to_perps", "perps_to_spot")
    out = tmp_path / "session"
    assert charter_session.main(["session", "--world", "worlds/testnet.toml", "--out-dir",
                                 str(out), "--dry-run", "--launch", "rehearsal"]) == 0
    evidence = json.loads((out / "session.json").read_text())
    assert evidence["roster_sha256"] == roster_hash(given)
    assert evidence["launch"]["rendered_manifest"]["tick_interval_ns"] == 10 * 10**9
    # The charter loads into the file it was voted for, and its digests are the ones
    # the load path checks: the cards' content digest and the file's roster.
    table = voted_charter(out / "charter.toml", given, "rehearsal")
    raw = tomllib.loads((Path(__file__).parents[2] / "worlds/testnet.toml").read_text())
    loaded = manifest_from_dict({**raw, "charter": dict(
        table, ratified_sha256=evidence["charter_sha256"],
        roster_sha256=evidence["roster_sha256"])})
    assert loaded.charter_content_sha256 == evidence["charter_sha256"]
    assert roster_hash(loaded) == loaded.charter_roster_sha256
    assert loaded.charter_launch == "rehearsal"
    loaded.check_launch("rehearsal")
    with pytest.raises(ValueError, match="voted for launch rehearsal"):
        voted_charter(out / "charter.toml", given, "run")


def _diary_of(world, tmp_path, *, bound_by_run=True):
    """A one-event diary of ``world``: launched by ``run_world`` (which checks ``run``),
    or by the bare Runtime a runner builds (which binds no launch of its own)."""
    from factorylab.runtime.loop import Runtime, run_world

    manifest, path = load_manifest(str(world)), tmp_path / "world.jsonl"
    if bound_by_run:
        run_world(manifest, events=1, seed=1, ledger_path=str(path))
    else:
        Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                ledger_path=str(path), router_gamma=0.1).run()
    return manifest, path


@pytest.mark.gate  # launches a world to resume
def test_factorylab_resume_refuses_a_world_bound_to_the_rehearsal_runner(tmp_path, capsys):
    """Codex on 43799ed: resume bypassed the launch binding. ``factorylab resume`` is the
    ``run`` launch, so a world whose charter was voted for the runner is refused, with
    the diary untouched."""
    from factorylab.runtime.cli import main

    world = _bound("worlds/scripted.toml", "rehearsal", tmp_path)
    _manifest, path = _diary_of(world, tmp_path, bound_by_run=False)
    before = path.read_bytes()
    assert main(["resume", "--world", str(world), "--ledger", str(path)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "factorylab resume: charter_launch_mismatch"]
    assert path.read_bytes() == before


@pytest.mark.gate  # launches a world to resume
@pytest.mark.parametrize("launched, edited", [("run", None), (None, "run")])
def test_a_charter_launch_edited_after_launch_is_refused_on_resume(launched, edited, tmp_path):
    """charter.launch is outside the manifest hash the diary binds, so the launched
    value rides in the checkpoint: an edit is refused before anything is restored, and
    the one item written is the refusal itself."""
    from factorylab.kernel.ledger import Ledger
    from factorylab.runtime.resume import ResumeError, resume_world

    source = (_bound("worlds/scripted.toml", launched, tmp_path) if launched
              else Path(__file__).parents[2] / "worlds/scripted.toml")
    manifest, path = _diary_of(source, tmp_path)
    edited_path = tmp_path / "edited" / "scripted.toml"
    edited_path.parent.mkdir()
    edited_path.write_text(
        (Path(__file__).parents[2] / "worlds/scripted.toml").read_text().replace(
            "\n[charter]\n", f'\n[charter]\nlaunch = "{edited}"\n', 1) if edited
        else (Path(__file__).parents[2] / "worlds/scripted.toml").read_text())
    changed = load_manifest(str(edited_path))
    assert changed.manifest_hash() == manifest.manifest_hash()
    assert changed.charter_launch != manifest.charter_launch

    def kinds():
        return [i["kind"] for i in Ledger.reopen(str(path), manifest=json.loads(
            manifest.canonical_json()))._recovery_items()]

    before = kinds()
    with pytest.raises(ResumeError) as refused:
        resume_world(changed, str(path))
    assert refused.value.code == "charter_launch_changed"
    assert kinds() == before + ["failed_resume"]


@pytest.mark.gate  # launches and resumes a world
def test_a_matching_resume_continues(tmp_path, capsys):
    from factorylab.runtime.cli import main
    from factorylab.runtime.resume import resume_world

    world = _bound("worlds/scripted.toml", "run", tmp_path)
    manifest, path = _diary_of(world, tmp_path)
    assert resume_world(manifest, str(path))["manifest_hash"] == manifest.manifest_hash()
    assert main(["resume", "--world", str(world), "--ledger", str(path)]) == 0
    assert "charter_launch" not in capsys.readouterr().err


def _reprice_a_seated_model(text):
    """``text`` with one seated model's input price changed: a roster edit."""
    import re

    seated = load_manifest_text(text).assemblies[0].model_id
    at = text.index(f'id = "{seated}"')
    price = re.compile(r'input_usd_per_mtok = "[^"]*"').search(text, at)
    return text[:price.start()] + 'input_usd_per_mtok = "999"' + text[price.end():]


def load_manifest_text(text):
    return manifest_from_dict(tomllib.loads(text))


def _ratified_copy(world, launch, tmp_path, *, edited_to=None, roster=False,
                   repriced=False):
    """A copy of ``world`` whose charter states ``launch`` and carries the digests its
    ratification recorded (``charter.ratified_sha256``, and with ``roster`` its
    ``roster_sha256``); ``edited_to`` then rewrites the launch, and ``repriced`` a
    seated model, leaving those digests untouched, as an edit after ratification would."""
    from factorylab.charter.provenance import charter_content, charter_digest, roster_hash

    text = (Path(__file__).parents[2] / world).read_text()
    voted = text.replace("\n[charter]\n", f'\n[charter]\nlaunch = "{launch}"\n', 1)
    digest = charter_digest(charter_content(tomllib.loads(voted)["charter"]))
    pin = f'roster_sha256 = "{roster_hash(load_manifest_text(voted))}"\n' if roster else ""
    final = text.replace("\n[charter]\n", f'\n[charter]\nlaunch = "{edited_to or launch}"\n'
                         f'ratified_sha256 = "{digest}"\n{pin}', 1)
    if repriced:
        final = _reprice_a_seated_model(final)
    edited = edited_to or repriced
    path = tmp_path / ("edited" if edited else "ratified") / Path(world).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(final)
    return path


def test_a_ratified_charter_edited_after_ratification_is_refused_on_every_network(tmp_path):
    """Codex on e4ea1df: on a testnet manifest, a launch rewritten under an untouched
    ratified digest was trusted. The loaded charter must hash to the ratified digest on
    every network before any launch reads its charter.launch."""
    from dataclasses import replace

    from factorylab.runtime.worlds import CharterDigestMismatch

    unedited = load_manifest(str(_ratified_copy(TESTNET, "rehearsal", tmp_path)))
    assert unedited.charter_launch == "rehearsal" and not unedited.exchange.mainnet
    unedited.check_launch("rehearsal")
    edited = _ratified_copy(TESTNET, "rehearsal", tmp_path, edited_to="run")
    with pytest.raises(CharterDigestMismatch, match="charter_digest_mismatch"):
        load_manifest(str(edited))
    # A manifest built in code, never through the load path, is refused at launch too.
    rebound = replace(unedited, charter_launch="run", charter_content_sha256="0" * 64)
    with pytest.raises(CharterDigestMismatch):
        rebound.check_launch("run")


def test_every_launcher_refuses_a_ratified_charter_edited_after_ratification(
        tmp_path, capsys):
    from factorylab.runtime.cli import ARGUMENT_EXIT, main
    from scripts import edition4_rehearsal as rehearsal

    edited = _ratified_copy(TESTNET, "rehearsal", tmp_path, edited_to="run")
    assert main(["run", "--world", str(edited), "--events", "1"]) == ARGUMENT_EXIT
    assert capsys.readouterr().err.splitlines() == ["factorylab run: charter_digest_mismatch"]
    report = rehearsal.run_rehearsal(_ratified_copy(TESTNET, "run", tmp_path / "r",
                                                    edited_to="rehearsal"),
                                     out=tmp_path / "rehearsal", provider=object())
    assert report["refusal"]["reason"] == "charter_digest_mismatch"
    report = rehearsal.run_rehearsal(_ratified_copy(CAPITAL_LOOP, "rehearsal", tmp_path / "c",
                                                    edited_to="capital-loop"),
                                     out=tmp_path / "capital", capital_loop=True,
                                     provider=object())
    assert report["refusal"]["reason"] == "charter_digest_mismatch"


@pytest.mark.gate  # launches and resumes a world
def test_resume_refuses_a_ratified_charter_edited_after_launch_and_the_unedited_one_runs(
        tmp_path, capsys):
    from factorylab.runtime.cli import main

    ratified = _ratified_copy("worlds/scripted.toml", "run", tmp_path)
    ledger = tmp_path / "world.jsonl"
    assert main(["run", "--world", str(ratified), "--events", "1", "--ledger",
                 str(ledger)]) == 0
    capsys.readouterr()
    edited = _ratified_copy("worlds/scripted.toml", "run", tmp_path, edited_to="rehearsal")
    before = ledger.read_bytes()
    assert main(["resume", "--world", str(edited), "--ledger", str(ledger)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "factorylab resume: charter_digest_mismatch"]
    assert ledger.read_bytes() == before
    assert main(["resume", "--world", str(ratified), "--ledger", str(ledger)]) == 0


@pytest.mark.parametrize("world, launch", [(TESTNET, "rehearsal"),
                                           (CAPITAL_LOOP, "capital-loop")])
def test_a_ratified_roster_changed_after_ratification_is_refused_on_every_network(
        world, launch, tmp_path, capsys):
    """Codex on 2c65b63: the roster pin was enforced on mainnet only, so a capital-loop
    world (testnet venue, mainnet USDC) launched a changed model under its ratification.
    It is refused at load and by every launcher; the unedited artifact loads and passes
    its launch."""
    from factorylab.runtime.cli import ARGUMENT_EXIT, main
    from factorylab.runtime.worlds import CharterRosterMismatch
    from scripts import edition4_rehearsal as rehearsal

    unedited = load_manifest(str(_ratified_copy(world, launch, tmp_path, roster=True)))
    assert unedited.charter_roster_sha256 is not None and not unedited.exchange.mainnet
    unedited.check_launch(launch)
    changed = _ratified_copy(world, launch, tmp_path / "x", roster=True, repriced=True)
    with pytest.raises(CharterRosterMismatch, match="charter_roster_mismatch"):
        load_manifest(str(changed))
    assert main(["run", "--world", str(changed), "--events", "1"]) == ARGUMENT_EXIT
    assert capsys.readouterr().err.splitlines() == ["factorylab run: charter_roster_mismatch"]
    report = rehearsal.run_rehearsal(changed, out=tmp_path / "rehearsed",
                                     capital_loop=launch == "capital-loop", provider=object())
    assert report["refusal"]["reason"] == "charter_roster_mismatch"
    # A manifest built in code is refused at launch too.
    from dataclasses import replace

    with pytest.raises(CharterRosterMismatch):
        replace(unedited, charter_roster_sha256="0" * 64).check_launch(launch)


@pytest.mark.gate  # launches and resumes a world
def test_resume_refuses_a_roster_changed_after_launch_and_the_unedited_one_runs(
        tmp_path, capsys):
    from factorylab.runtime.cli import main

    ratified = _ratified_copy("worlds/scripted.toml", "run", tmp_path, roster=True)
    ledger = tmp_path / "world.jsonl"
    assert main(["run", "--world", str(ratified), "--events", "1", "--ledger",
                 str(ledger)]) == 0
    capsys.readouterr()
    changed = _ratified_copy("worlds/scripted.toml", "run", tmp_path, roster=True,
                             repriced=True)
    before = ledger.read_bytes()
    assert main(["resume", "--world", str(changed), "--ledger", str(ledger)]) == 1
    assert capsys.readouterr().err.splitlines() == [
        "factorylab resume: charter_roster_mismatch"]
    assert ledger.read_bytes() == before
    assert main(["resume", "--world", str(ratified), "--ledger", str(ledger)]) == 0
