"""A capital-loop rehearsal run end to end, as the operator runs it, on honest wires.

The real ``run_rehearsal`` builds the real Runtime, whose seat asks for a real
``treasury.transfer to_venice``: the real HybridRail signs the testnet shadow send
through the Hyperliquid SDK and the mainnet top-up through the x402 client. Nothing is
faked but the bytes leaving the process: the SDK's HTTP post (the venue) and urllib
(Base mainnet's JSON-RPC and Venice), answered by the honest wire fakes of
``tests/world/test_venice_hybrid.py``. Every key is generated here and never funded.
"""

import base64
import json
import os
import shlex
import signal
import time
from pathlib import Path
from urllib import error

import pytest
from eth_account import Account

from factorylab.world.evm import BASE
from factorylab.world.models import ModelResponse
from factorylab.world.x402 import VENICE_URL
from tests.audit.test_edition4_rehearsal import StubProvider
from tests.world.test_venice_hybrid import PAYEE, SINK, BaseWire, VeniceWire, VenueWire

pytestmark = pytest.mark.gate

RESERVE = "0x1228e5620944a79D268Afc7522E00891526EdEBb"
TICK_NS = 10 * 1_000_000_000
TICKS = 4


class Converting(StubProvider):
    """The world's first call (a producer seat's) asks to convert $5 to Venice credit
    through the treasury tool; every other call answers nothing."""

    calls = 0

    def complete(self, request):
        self.calls += 1
        if self.calls > 1:
            return self.response
        return ModelResponse(request.model_id, json.dumps({
            "tool_calls": [{"tool": "treasury.transfer", "args": {
                "direction": "to_venice", "usd": "5", "reason": "think on Venice"}}]}),
            1, 1, "stop", cost_micro=1)


class Response:
    """What urllib's opener hands ``x402.http_request``."""

    def __init__(self, status, body, headers):
        self.status, self._body, self.headers = status, body, headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Wires:
    """urllib's opener: Base mainnet and Venice answer; every other host is offline."""

    def __init__(self, chain, venice):
        self.chain, self.venice = chain, venice

    def open(self, request, timeout=None):
        url, method = request.full_url, request.get_method()
        payload = json.loads(request.data) if request.data else None
        if url == BASE.rpc:
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": self.chain(payload)}
            return Response(200, json.dumps(body).encode(), {})
        if url.startswith(VENICE_URL):
            # urllib stores header names capitalized; the fake reads Venice's spelling.
            sent = {k.lower(): v for k, v in request.header_items()}
            headers = {name: sent[name.lower()] for name in (
                "X-402-Payment", "X-Sign-In-With-X") if name.lower() in sent}
            answer = self.venice(method, url, payload, headers)
            return Response(answer.status, json.dumps(answer.body).encode(),
                            dict(answer.headers))
        raise error.URLError("offline")


class Wall:
    """The wall clock and its wait, as the runner reads them (``_wall_ns``, ``_sleep``).

    It starts at ``start_ns`` and moves only by the runner's own sleeps, so the run's
    live deadline clock paces itself exactly as it does against real time, without the
    test waiting. The ``ticks``-th sleep also carries the wall past any deadline: the
    run ends by its own deadline clock after that many ticks.
    """

    def __init__(self, start_ns, ticks=TICKS):
        self.now, self.ticks, self.sleeps = start_ns, ticks, 0

    def now_ns(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += int(seconds * 1_000_000_000)
        if self.sleeps % self.ticks == 0:
            self.now += 10 * 86_400 * 1_000_000_000  # the deadline passes


def world(tmp_path, reserve):
    """The capital-loop world with a throwaway reserve; its manifest name is its stem."""
    path = tmp_path / "edition6-capital-loop.toml"
    path.write_text(Path("worlds/edition6-capital-loop.toml").read_text().replace(
        RESERVE, reserve))
    return path


def repo_root():
    import factorylab

    return Path(factorylab.__file__).resolve().parents[1]


def wired(tmp_path, monkeypatch):
    from hyperliquid.api import API

    from factorylab.runtime.capital_loop import default_lock_dir
    from factorylab.world import x402

    main, reserve = Account.create(), Account.create()  # throwaway keys, never funded
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("HL_PRIVATE_KEY", main.key.hex())
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", reserve.key.hex())
    # $15 on a $8.266 floor: $6.734 above it, within the $10 total, covering one $5.
    chain = BaseWire(reserve.address, 15_000_000)
    venice = VeniceWire(chain, {reserve.address.lower(): 1_000_000})
    venue = VenueWire(main.address, SINK)
    monkeypatch.setattr(API, "post", lambda api, path, payload=None: venue.post(
        api, path, payload))
    monkeypatch.setattr(x402.request, "build_opener", lambda *handlers: Wires(chain, venice))
    # A capital-loop run takes no supplied clock: its live deadline clock reads the wall.
    wall = install_wall(monkeypatch, Wall(time.time_ns()))
    return {"chain": chain, "venice": venice, "venue": venue, "reserve": reserve,
            # The operator's lock directory, which this test's own (conftest) replaces.
            "world": world(tmp_path, reserve.address), "locks": default_lock_dir(),
            "wall": wall}


def install_wall(monkeypatch, wall):
    from scripts import edition4_rehearsal as rehearsal

    monkeypatch.setattr(rehearsal, "_wall_ns", wall.now_ns)
    monkeypatch.setattr(rehearsal, "_sleep", wall.sleep)
    return wall


def launch_kwargs(w):
    """What the harness supplies beside the operator's flags: no key file, no network,
    and no clock (the run's own live deadline clock reads ``w["wall"]``)."""
    from factorylab.world.exchange import HyperliquidExchange

    return {"provider": Converting(ModelResponse("openai/gpt-6-luna", "{}", 1, 1, "stop",
                                                 cost_micro=1)),
            "exchange": HyperliquidExchange(mainnet=False)}


def relaunch(w, out, tmp_path):
    """A second launch on the same reserve, stopped after its capital checks."""
    from scripts import edition4_rehearsal as rehearsal

    chain = w["chain"]
    # No key is needed: the launch stops after its capital checks, before any signer.
    # Time has passed with the chain: the wall clock (the only one a capital-loop run
    # reads) shows Base's newest block. Swapped in for this launch and put back.
    wall = rehearsal._wall_ns
    rehearsal._wall_ns = lambda: int(chain.block(chain.head)["timestamp"], 16) * 1_000_000_000
    try:
        return rehearsal.run_rehearsal(
            str(w["world"]), out=out, capital_loop=True, duration_ns=3_600 * 1_000_000_000,
            source_root=tmp_path, provider=object())
    finally:
        rehearsal._wall_ns = wall


@pytest.mark.parametrize("fate", ["settled", "dead"])
def test_a_run_ends_with_its_top_up_submitted_and_the_next_waits_for_the_chain(
        tmp_path, monkeypatch, capsys, fate):
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    if fate == "dead":
        # Venice acknowledges and never settles: the authorization can only expire.
        w["venice"].settle = lambda authorization: "0x" + "00" * 32
    out = tmp_path / "runs" / "first"
    report = rehearsal.run_rehearsal(str(w["world"]), out=out, capital_loop=True,
                                     duration_ns=3_600 * 1_000_000_000,
                                     source_root=repo_root(), **launch_kwargs(w))
    assert report["status"] == "completed", report.get("error")
    rows = json.loads((out / "events.json").read_text())
    signed = [r for r in rows if r["kind"] == "treasury.step_submitted"]
    # The seat's own tool call reached the treasury; both legs were signed and sent.
    assert any(r["kind"] == "treasury.intent" and r["direction"] == "to_venice"
               for r in rows)
    assert len(w["venue"].rows) == 1 and len(w["venice"].paid) == 1
    assert [s["state"]["reference"]["authorization"]["to"].lower() for s in signed] == [
        PAYEE]
    loud = report["capital_loop_outstanding"]
    assert [t["nonce"] for t in loud["top_ups_submitted"]] == [
        signed[0]["state"]["reference"]["authorization"]["nonce"]]
    assert rehearsal.exit_code(report) == 3
    assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err
    assert not any(r["kind"] == "treasury.financing" for r in rows)
    # The authorization was written ahead, outside the diary, before it was signed.
    from factorylab.runtime.capital_loop import ReserveLock, acknowledge, read_authorizations

    nonce = signed[0]["state"]["reference"]["authorization"]["nonce"]
    record = w["locks"] / f"{w['reserve'].address.lower()}.authorizations.jsonl"
    assert [(e["kind"], e["nonce"], e["run_dir"]) for e in read_authorizations(record)] == [
        ("authorization", nonce, str(out.resolve()))]
    # The next launch, wherever its --out is, waits for the chain.
    elsewhere = tmp_path / "elsewhere"
    refused = relaunch(w, elsewhere / "second", tmp_path)
    assert refused["refusal"]["reason"] == "previous_run_authorization_may_still_settle"
    assert refused["refusal"]["run_dir"] == str(out.resolve())
    if fate == "settled":
        w["chain"].advance(w["chain"].lag)  # the debit is finalized: it settled
        # Real money moved and the dead world never booked it: a recovery, exit 3,
        # until the operator has settled the books by hand and acknowledged it.
        recovery = relaunch(w, elsewhere / "recovery", tmp_path)
        assert recovery["refusal"]["reason"] == "recorded_authorization_settled_unbooked"
        assert rehearsal.exit_code(recovery) == 3
        with ReserveLock(w["reserve"].address, lock_dir=w["locks"]) as lock:
            acknowledge(lock, nonce, transport=rehearsal._http_request())
    else:
        w["chain"].advance(400)  # finalized Base is past validBefore, and it is unused
    admitted = relaunch(w, elsewhere / "third", tmp_path)
    assert admitted["refusal"]["reason"] == "source_root_mismatch"  # every check passed


def test_the_rail_stamps_validbefore_with_the_clock_the_bound_measured(
        tmp_path, monkeypatch):
    # Codex on PR #144: the bound sampled the injected clock while the rail stamped
    # validBefore with time.time_ns(). One clock, the runner's wall clock, serves both
    # (here a wall 100 s slow, to tell it from time.time_ns()).
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    lag_s = 100
    install_wall(monkeypatch, Wall(time.time_ns() - lag_s * 1_000_000_000))
    out = tmp_path / "runs" / "lagging"
    report = rehearsal.run_rehearsal(
        str(w["world"]), out=out, capital_loop=True, duration_ns=3_600 * 1_000_000_000,
        source_root=repo_root(), **launch_kwargs(w))
    assert report["status"] == "completed", report.get("error")
    rows = json.loads((out / "events.json").read_text())
    [top_up] = [r for r in rows if r["kind"] == "treasury.step_submitted"]
    created = top_up["state"]["reference"]["created_s"]
    assert abs(created - (time.time() - lag_s)) < 30  # the runner's clock, not time's
    assert int(top_up["state"]["reference"]["authorization"]["validBefore"]) == created + 300


def test_a_capital_loop_run_s_safety_path_reads_wall_time_mid_event(tmp_path, monkeypatch):
    """The run's LiveClock sits behind the rehearsal's ``AdmissionClock``. Every model
    call here takes a delivered tick of wall time, so before the next call of the same
    event the safety pass runs, at the wall's instant: the safety path reads wall time
    through the wrapper (before the fix it read the event's simulated instant, which
    never moves inside an event, so no pass ever ran)."""
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    kwargs = launch_kwargs(w)
    provider, reads = kwargs["provider"], []
    complete = provider.complete

    def thinking(request):
        w["wall"].now += TICK_NS  # the call takes a tick of wall time
        reads.append(w["wall"].now)
        return complete(request)

    provider.complete = thinking
    out = tmp_path / "runs" / "thinking"
    report = rehearsal.run_rehearsal(
        str(w["world"]), out=out, capital_loop=True, duration_ns=3_600 * 1_000_000_000,
        source_root=repo_root(), **kwargs)
    assert report["status"] == "completed", report.get("error")
    rows = json.loads((out / "events.json").read_text())
    passes = [r for r in rows if r["kind"] == "safety.pass"]
    assert passes and len(reads) > 1
    # Each pass ran at a wall instant a model call reached, mid-event.
    assert {p["ts"] for p in passes} <= set(reads)


@pytest.mark.parametrize("name", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_signal_mid_run_still_writes_the_report_warns_and_exits_3(
        tmp_path, monkeypatch, capsys, name):
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    settle = w["venice"].settle

    def settle_then_signal(authorization):
        tx = settle(authorization)  # real money would now be on its way
        os.kill(os.getpid(), getattr(signal, name))  # the operator stops the run
        return tx

    w["venice"].settle = settle_then_signal
    run = rehearsal.run_rehearsal
    monkeypatch.setattr(rehearsal, "run_rehearsal",
                        lambda *a, **k: run(*a, **k, **launch_kwargs(w)))
    before = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    out = tmp_path / "runs" / "stopped run; echo not a command"  # a shell would split it
    code = rehearsal.main(["--world", str(w["world"]), "--out", str(out), "--capital-loop",
                           "--duration", "60m", "--source-root", str(repo_root())])
    assert code == 3  # what the operator's shell sees
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "stopped" and report["stopped_by"] == name
    assert report["capital_loop_outstanding"]["top_ups_submitted"]
    printed = capsys.readouterr()
    assert "CAPITAL LOOP OUTSTANDING" in printed.err
    summary = json.loads(printed.out[printed.out.rindex('{\n  "status"'):])
    loud = summary["capital_loop_outstanding"]
    assert loud["next_step_argv"][-1] == str(out)
    assert shlex.split(loud["next_step"]) == loud["next_step_argv"]
    # The run's handlers are gone again, and the reserve is free.
    assert {n: signal.getsignal(n) for n in before} == before
    from factorylab.runtime.capital_loop import ReserveLock

    ReserveLock(w["reserve"].address, lock_dir=w["locks"]).close()


def operator_main(w, monkeypatch, out):
    """``main()`` as the operator runs it, with the harness's fakes added."""
    from scripts import edition4_rehearsal as rehearsal

    run = rehearsal.run_rehearsal
    monkeypatch.setattr(rehearsal, "run_rehearsal",
                        lambda *a, **k: run(*a, **k, **launch_kwargs(w)))
    return rehearsal.main(["--world", str(w["world"]), "--out", str(out), "--capital-loop",
                           "--duration", "60m", "--source-root", str(repo_root())])


def test_a_signal_as_the_finally_begins_or_during_the_report_cannot_skip_it(
        tmp_path, monkeypatch, capsys):
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    hold, report_outstanding = rehearsal._StopOnSignal.hold, rehearsal._report_outstanding

    def signal_then_hold(stops):
        # The run ended on its own; SIGTERM lands at the very start of its finally,
        # while the handler is still armed.
        os.kill(os.getpid(), signal.SIGTERM)
        return hold(stops)

    masked = []

    def signal_while_reporting(*args):
        # A second one mid-report: blocked, so it waits until the report is out.
        masked.append(signal.pthread_sigmask(signal.SIG_BLOCK, []))
        os.kill(os.getpid(), signal.SIGHUP)
        return report_outstanding(*args)

    monkeypatch.setattr(rehearsal._StopOnSignal, "hold", signal_then_hold)
    monkeypatch.setattr(rehearsal, "_report_outstanding", signal_while_reporting)
    out = tmp_path / "runs" / "late"
    assert operator_main(w, monkeypatch, out) == 3
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "stopped" and report["stopped_by"] == "SIGTERM"
    assert report["capital_loop_outstanding"]["top_ups_submitted"]
    assert "CAPITAL LOOP OUTSTANDING" in capsys.readouterr().err
    handled = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    assert handled <= masked[0]  # the report was written with all three held back
    assert not handled & signal.pthread_sigmask(signal.SIG_BLOCK, [])  # and released


def test_nohup_keeps_sighup_ignored_and_the_run_goes_on(tmp_path, monkeypatch, capsys):
    w = wired(tmp_path, monkeypatch)
    settle = w["venice"].settle

    def settle_then_hangup(authorization):
        tx = settle(authorization)
        os.kill(os.getpid(), signal.SIGHUP)  # the terminal closes under nohup
        return tx

    w["venice"].settle = settle_then_hangup
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)  # what nohup leaves
    try:
        code = operator_main(w, monkeypatch, tmp_path / "runs" / "nohup")
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN  # left, and left alone
    finally:
        signal.signal(signal.SIGHUP, previous)
    report = json.loads((tmp_path / "runs" / "nohup" / "report.json").read_text())
    assert report["status"] == "completed" and "stopped_by" not in report
    assert code == 3  # its top-up is still submitted at the end


def test_restore_puts_back_a_handler_installed_from_c_as_the_default():
    from scripts import edition4_rehearsal as rehearsal

    previous = signal.getsignal(signal.SIGHUP)
    try:
        stops = rehearsal._StopOnSignal()
        stops.arm()
        stops.previous[signal.SIGHUP] = None  # signal.signal's answer for a C handler
        stops.restore()  # must not raise
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_rehearse_without_stops_arms_nothing(tmp_path, monkeypatch):
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)

    def never(_stops):
        raise AssertionError("armed a handler no one would restore")

    monkeypatch.setattr(rehearsal._StopOnSignal, "arm", never)
    before = {n: signal.getsignal(n) for n in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    report = rehearsal._rehearse(str(w["world"]), held=[], stops=None,
                                 out=tmp_path / "runs" / "library", capital_loop=True,
                                 duration_ns=3_600 * 1_000_000_000, source_root=repo_root(),
                                 **launch_kwargs(w))
    assert report["status"] == "completed", report.get("error")
    assert {n: signal.getsignal(n) for n in before} == before


def test_the_wires_speak_venices_header_names():
    # Guard the harness itself: a payment header urllib title-cases must still reach the
    # Venice fake under the name it checks, or the tests above would prove nothing.
    captured = {}

    class Venice:
        def __call__(self, method, url, payload, headers):
            captured.update(headers)
            from factorylab.world.x402 import HTTPResponse

            return HTTPResponse(200, {})

    from urllib import request

    wires = Wires(None, Venice())
    req = request.Request(VENICE_URL + "/x402/top-up", data=b"{}", method="POST",
                          headers={"X-402-Payment": base64.b64encode(b"{}").decode()})
    wires.open(req)
    assert "X-402-Payment" in captured


def test_the_world_binds_its_rail_a_guard_for_every_reserve_key_signer(tmp_path, monkeypatch):
    # The review of 0b5b487 (a surviving mutant): bootstrap must bind the rail's guard,
    # not only the market's, and to every chain the rail signs a plain transaction on.
    from factorylab.runtime.capital_loop import ReserveGuard
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest
    from factorylab.world.clock import ClockSource
    from factorylab.world.treasury_rails import HybridRail

    w = wired(tmp_path, monkeypatch)
    kwargs = {**launch_kwargs(w), "clock_source": ClockSource(time.time_ns(), TICK_NS, TICKS)}
    run = tmp_path / "runs" / "bound"
    run.mkdir(parents=True)
    runtime = Runtime(load_manifest(str(w["world"])), events=TICKS, seed=1,
                      initial_balance_micro=None, router_gamma=0.1,
                      ledger_path=str(run / "ledger.jsonl"),
                      provider=kwargs["provider"], exchange=kwargs["exchange"],
                      clock_source=kwargs["clock_source"], capital_loop=True)
    rail = runtime.treasury.rail.target
    assert isinstance(rail, HybridRail)
    guard = rail.authorization_log
    assert isinstance(guard, ReserveGuard) and guard.origin == "treasury"
    assert guard.run_dir == run.resolve()
    signers = [getattr(rail, name) for name in ("hyper", "base", "venice_base")
               if getattr(rail, name, None) is not None]
    assert signers and all(chain.transaction_guard is guard for chain in signers)


def test_a_capital_loop_run_refuses_a_supplied_clock(tmp_path, monkeypatch):
    # Codex P2 on 98fa627: a harness clock that emits every tick at once would run the
    # funded loop faster than the settlement bound it was admitted on.
    from factorylab.world.clock import ClockSource
    from scripts import edition4_rehearsal as rehearsal

    w = wired(tmp_path, monkeypatch)
    with pytest.raises(rehearsal.RehearsalRefused, match="capital_loop_requires_the_live_clock"):
        rehearsal.run_rehearsal(
            str(w["world"]), out=tmp_path / "runs" / "harness", capital_loop=True,
            duration_ns=3_600 * 1_000_000_000, source_root=repo_root(),
            clock_source=ClockSource(time.time_ns(), TICK_NS, 360), **launch_kwargs(w))
    assert not (tmp_path / "runs" / "harness").exists()  # refused before anything
    assert w["venue"].rows == [] and w["venice"].paid == []
