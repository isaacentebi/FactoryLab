"""Fixtures and factories shared by every test directory."""

import ast
import fcntl
import hashlib
import json
import os
import pickle
import shutil
from dataclasses import dataclass, is_dataclass, replace
from pathlib import Path

import pytest

from factorylab.cortex.sandbox import jail_available
from factorylab.kernel.ledger import canonical
from factorylab.runtime.capital_loop import default_lock_dir as operator_default_lock_dir
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider


def _manifest_with_changes(manifest, changes):
    """Resolve nested dataclass overrides without modifying the supplied manifest."""
    return replace(manifest, **{
        name: _manifest_with_changes(getattr(manifest, name), value)
        if isinstance(value, dict) and is_dataclass(getattr(manifest, name)) else value
        for name, value in changes.items()
    })


@dataclass(frozen=True)
class ScriptedRun:
    """A read-only cached result; data access returns detached, consumer-owned values.

    Each field is pickled on its own, so reading ``summary`` decodes the summary and
    nothing else; every read decodes afresh, so a test that mutates what it got
    cannot change what the next reader gets.

    The ledger directory is shared evidence. Use copy_to before reopening for writes,
    resuming, killing, truncating, or changing any file, including its sidecars.
    """

    ledger_path: Path | None
    _fields: dict[str, bytes]

    def _field(self, name):
        return pickle.loads(self._fields[name])

    @property
    def summary(self):
        return self._field("summary")

    @property
    def entries(self):
        return self._field("entries")

    @property
    def requests(self):
        return self._field("requests")

    def copy_to(self, directory):
        """Return a private ledger with every sidecar and original permission preserved."""
        if self.ledger_path is None:
            raise ValueError("this Runtime evidence was captured from an in-memory ledger")
        shutil.copytree(self.ledger_path.parent, directory)
        return Path(directory) / self.ledger_path.name

    def runtime(self, manifest):
        """Return an independent in-memory runtime restored without replaying any event."""
        state = self._field("state")
        rt = Runtime(manifest, ledger_path=None, **state["config"])
        restore_runtime(rt, state)
        return rt


def _cached_scripted_run(directory, manifest, events, seed, *, mode):
    """Publish only completed runs, once per key across all workers in this session."""
    identity = (manifest.canonical_json(), events, seed, jail_available(), mode)
    key = hashlib.sha256(canonical(identity)).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    run_directory = directory / key
    result_path = run_directory / "result.pickle"
    # This is the xdist shared-basetemp pattern, using the stdlib POSIX lock.
    # Keep locks outside the result directory so failed writers can be retried safely.
    with (directory / f"{key}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not result_path.exists():
            if run_directory.exists():
                shutil.rmtree(run_directory)
            run_directory.mkdir()
            path = run_directory / "ledger" / "scripted.jsonl"
            path.parent.mkdir()
            if mode == "run_world":
                result = {"summary": run_world(manifest, events=events, seed=seed,
                                               ledger_path=str(path))}
            else:
                from factorylab.world.scripted import _inputs_from_prompt

                entries, requests = [], []

                class RecordingProvider(ScriptedProvider):
                    def complete(self, request):
                        text = "\n".join(str(m.get("content", ""))
                                         for m in request.messages)
                        requests.append(_inputs_from_prompt(text))
                        return super().complete(request)

                # These consumers originally used in-memory ledgers. Preserve that call:
                # persisting every encrypted append here would add thousands of fsyncs.
                rt = Runtime(manifest, events=events, seed=seed, initial_balance_micro=None,
                             ledger_path=None, router_gamma=.1,
                             provider=RecordingProvider() if mode == "recorded_runtime" else None)
                append = rt.ledger.append

                def capture(item):
                    seq = append(item)
                    if item["kind"] != "snapshot":
                        entries.append(json.loads(canonical(dict(item, seq=seq))))
                    return seq

                rt.ledger.append = capture
                try:
                    summary = rt.run()
                finally:
                    rt.ledger.append = append
                    rt._ledger_lock.close()
                result = dict(summary=summary, entries=entries, requests=requests,
                              state=runtime_state(rt))
            fields = {name: pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
                      for name, value in result.items()}
            temporary = result_path.with_suffix(".tmp")
            temporary.write_bytes(pickle.dumps(fields, protocol=pickle.HIGHEST_PROTOCOL))
            temporary.replace(result_path)
        fields = _decoded_fields(result_path)
    path = run_directory / "ledger" / "scripted.jsonl" if mode == "run_world" else None
    return ScriptedRun(path, fields)


# One read of each cached result file per worker process: the outer pickle is a map of
# field name to that field's own pickle, so holding it costs bytes, not objects.
_FIELDS_BY_PATH: dict[Path, dict[str, bytes]] = {}


def _decoded_fields(result_path: Path) -> dict[str, bytes]:
    fields = _FIELDS_BY_PATH.get(result_path)
    if fields is None:
        fields = _FIELDS_BY_PATH[result_path] = pickle.loads(result_path.read_bytes())
    return fields


@pytest.fixture(scope="session")
def _scripted_run_cache(tmp_path_factory, request):
    """Use one directory per pytest invocation, shared only by that invocation's workers."""
    base = tmp_path_factory.getbasetemp()
    session_directory = base.parent if hasattr(request.config, "workerinput") else base
    return session_directory / "shared-scripted-runs"


@pytest.fixture(scope="session")
def scripted_run(_scripted_run_cache):
    """Share unmodified run_world calls by their fully resolved input identity."""
    def run(manifest_name_or_manifest, events, seed, *, manifest_changes=None):
        manifest = (load_manifest(manifest_name_or_manifest)
                    if isinstance(manifest_name_or_manifest, str) else manifest_name_or_manifest)
        if manifest_changes:
            manifest = _manifest_with_changes(manifest, manifest_changes)
        return _cached_scripted_run(_scripted_run_cache, manifest, events, seed, mode="run_world")

    return run


@pytest.fixture(scope="session")
def scripted_runtime_run(_scripted_run_cache):
    """Share direct Runtime evidence without changing run_world's separate launch checks."""
    def run(manifest, events, seed, *, record_requests=False):
        mode = "recorded_runtime" if record_requests else "runtime"
        return _cached_scripted_run(_scripted_run_cache, manifest, events, seed,
                                    mode=mode)

    return run


# Test tiers (see README "Try it" and AGENTS.md):
#   check  the developer inner loop, the default; no test here runs a world
#   gate   every test that runs a world or reads a shared scripted run
#   slow   kills and resumes real subprocesses
# ``fast`` and ``world`` are the old names of ``check`` and ``gate`` and are still set.
_SHARED_WORLD_FIXTURES = frozenset({"scripted_run", "scripted_runtime_run"})
_WORLD_CLI_COMMANDS = frozenset({"run", "resume"})
# A check-tier test whose call phase takes longer than this fails: it belongs in gate.
CHECK_LIMIT_ENV = "FACTORYLAB_CHECK_LIMIT_S"
CHECK_LIMIT_DEFAULT_S = 2.0


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _runs_world_here(call: ast.Call) -> bool:
    """Whether one call, read on its own, starts a world's event loop."""
    name = _call_name(call)
    if name == "run_world":
        return True
    if name == "Runtime":
        # ``Runtime(..., events=0)`` builds a runtime without running one; any other
        # events budget, or one passed positionally, is a world.
        events = next((k.value for k in call.keywords if k.arg == "events"), None)
        return not (isinstance(events, ast.Constant) and events.value == 0)
    if (name == "run" and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name) and call.func.value.id != "subprocess"
            and not call.args and not call.keywords):
        return True  # ``rt.run()``: the loop itself
    # The CLI, in process or as a child: ``main(["run", ...])`` or ``[..., "resume", ...]``.
    for argument in call.args:
        if isinstance(argument, (ast.List, ast.Tuple)):
            words = {e.value for e in argument.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if words & _WORLD_CLI_COMMANDS and (
                    name == "main" or any("factorylab" in word for word in words)):
                return True
    return False


def _world_functions(tree: ast.Module) -> set[str]:
    """Names of this module's functions that run a world, directly or through a helper."""
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    calls = {name: [n for n in ast.walk(node) if isinstance(n, ast.Call)]
             for name, node in functions.items()}
    world = {name for name, found in calls.items() if any(map(_runs_world_here, found))}
    changed = True
    while changed:
        changed = False
        for name, found in calls.items():
            if name not in world and any(_call_name(c) in world for c in found
                                         if isinstance(c.func, ast.Name)):
                world.add(name)
                changed = True
    return world


def _module_facts(path: Path) -> tuple[bool, set[str]]:
    tree = ast.parse(path.read_text())
    imports_run_world = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "factorylab.runtime.loop"
        and any(alias.name == "run_world" for alias in node.names)
        for node in ast.walk(tree)
    )
    return imports_run_world, _world_functions(tree)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Partition collected tests into tiers without changing selection or existing markers.

    A test is ``gate`` when it is marked so (or ``world``), when its module imports
    ``run_world``, when it uses a shared scripted run, or when its body, a helper in
    its module, or a fixture in its module runs a world (``Runtime`` with a nonzero
    events budget, ``rt.run()``, ``run_world``, or the CLI's ``run``/``resume``).
    Everything else that is not ``network`` or ``slow`` is ``check``. The static read
    can miss a world; the check-tier time limit below catches what it misses.
    """
    modules = {}
    for item in items:
        path = Path(item.path)
        if path not in modules:
            modules[path] = _module_facts(path)
        imports_run_world, world_functions = modules[path]
        if any(item.get_closest_marker(m) for m in ("network", "slow")):
            continue
        fixtures = set(getattr(item, "fixturenames", ()))
        name = getattr(item, "originalname", None) or item.name.split("[")[0]
        gate = (bool(item.get_closest_marker("gate") or item.get_closest_marker("world"))
                or imports_run_world
                or bool(_SHARED_WORLD_FIXTURES & fixtures)
                or name in world_functions
                or bool(world_functions & fixtures))
        if gate:
            item.add_marker(pytest.mark.gate)
            item.add_marker(pytest.mark.world)
        else:
            item.add_marker(pytest.mark.check)
            item.add_marker(pytest.mark.fast)


def _check_limit_s() -> float | None:
    raw = os.environ.get(CHECK_LIMIT_ENV, "").strip()
    if not raw:
        return CHECK_LIMIT_DEFAULT_S
    if raw.lower() in ("0", "off", "none"):
        return None
    return float(raw)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """A ``check`` test that runs longer than the limit fails, naming the fix."""
    outcome = yield
    report = outcome.get_result()
    limit = _check_limit_s()
    if (limit is None or report.when != "call" or not report.passed
            or item.get_closest_marker("check") is None or report.duration <= limit):
        return
    report.outcome = "failed"
    report.longrepr = (
        f"{item.nodeid} is in the check tier but its call took {report.duration:.2f}s "
        f"(limit {limit:.1f}s). The check tier is the inner loop and runs no world: mark "
        "this test @pytest.mark.gate (or make it faster). On a slow machine raise the "
        f"limit with {CHECK_LIMIT_ENV}=<seconds>, or disable it with {CHECK_LIMIT_ENV}=off.")


def make_runtime(*, balance=100_000_000, live=False, clock_source=None):
    """Return a scripted-world runtime with no ledger file, no network and no credentials."""
    manifest = load_manifest("scripted")
    if live:
        manifest = replace(manifest, exchange=replace(manifest.exchange, kind="hyperliquid"))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=balance,
                   ledger_path=None, router_gamma=.1,
                   exchange=FakeExchange(), provider=ScriptedProvider(),
                   clock_source=clock_source)


@pytest.fixture(autouse=True)
def _forget_in_process_kills():
    """Every test starts with no kill remembered in this process.

    The witness (edition 2, C4) remembers, in process memory, each identity it saw killed
    so a resume in the same process refuses it. Scripted fixtures are copies of one diary,
    so two tests in one worker share an identity; without this reset a kill in one test
    refuses a legitimate resume in the next. The local witness file is per tmp directory
    and needs no reset.
    """
    from factorylab.runtime import witness

    witness._killed_here.clear()
    yield
    witness._killed_here.clear()


@pytest.fixture(autouse=True)
def _capital_loop_locks_stay_in_tmp(monkeypatch, tmp_path_factory):
    """No test can touch the operator's real capital-loop lock or last-run record.

    ``ReserveLock`` without a ``lock_dir`` resolves ``default_lock_dir()``, the operator
    account's ``~/.factorylab/capital-loop``, which holds the live reserve's record. Every
    test gets its own temporary directory there instead, made only if a test asks.
    """
    from factorylab.runtime import capital_loop

    made = []

    def temporary():
        if not made:
            made.append(tmp_path_factory.mktemp("capital-loop-locks"))
        return made[0]

    monkeypatch.setattr(capital_loop, "default_lock_dir", temporary)


@pytest.fixture
def write_ahead(monkeypatch):
    """A real write-ahead guard made the default of every X402Client and X402Provider.

    Production signs an EIP-3009 authorization only through
    ``x402.sign_transfer_authorization`` with a guard, and every signer is handed one
    (``ReserveGuard``). A test that signs asks for this fixture: its clients get a real
    ``ReserveGuard``, whose lock and record live in the test's temporary lock directory
    (``_capital_loop_locks_stay_in_tmp``), so the test exercises the same chokepoint.
    """
    from factorylab.runtime.capital_loop import ReserveGuard
    from factorylab.world import market, x402

    default = ReserveGuard("test")
    for cls in (x402.X402Client, market.X402Provider):
        def init(self, *args, _original=cls.__init__, guard=None, **kwargs):
            _original(self, *args, guard=guard if guard is not None else default, **kwargs)

        monkeypatch.setattr(cls, "__init__", init)
    # These tests' fake wires answer only the calls they were written for; the chain
    # head the chokepoint records as start_block is a fixed block here. The head read
    # itself is tested where it matters (tests/world/test_signing_chokepoint.py).
    monkeypatch.setattr(x402.X402Client, "chain_head", lambda self: 1)
    return default


@pytest.fixture
def operator_lock_dir():
    """The real ``default_lock_dir`` the autouse fixture above replaces: call it only to
    compute a path, never to lock anything there."""
    return operator_default_lock_dir


# ---- No test touches the network


class NetworkForbidden(RuntimeError):
    """A test tried to reach a non-local host; it must use a fake (or be marked
    ``network``, which keeps it out of the check and gate tiers)."""


#: Hosts a test may reach: this machine only (a jail or a local server may use them).
_LOCAL_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _local_host(host) -> bool:
    import ipaddress

    if host in (None, "", b""):
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    host = str(host).strip("[]").lower()
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False  # a name that is not this machine's is resolved over the network


def _url_host(url) -> str | None:
    from urllib.parse import urlsplit

    return urlsplit(getattr(url, "full_url", url)).hostname


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Every outbound network path raises ``NetworkForbidden``, naming what it reached.

    Covers ``socket.getaddrinfo`` (a DNS lookup is already traffic),
    ``socket.socket.connect``/``connect_ex`` and ``socket.create_connection`` (loopback
    and unix sockets allowed), ``urllib.request.urlopen`` and every
    ``OpenerDirector.open``, which the project's own seams ``x402.http_request`` and
    ``polymarket.http_get_json`` both open through (a test that fakes the opener beneath
    a seam reaches nothing, and is not stopped). Each attempt is also remembered and
    fails the test at
    teardown, so code that catches the error (a rail that turns any transport failure
    into a retry) cannot hide it. A test marked ``network`` is left alone; such tests
    are never in the check or gate tiers.
    """
    if request.node.get_closest_marker("network"):
        yield
        return
    import socket
    import urllib.request

    attempts: list[str] = []

    def forbid(what: str):
        attempts.append(what)
        raise NetworkForbidden(f"tests may not touch the network: {what} "
                               "(use a fake, or mark the test @pytest.mark.network)")

    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if not _local_host(host):
            forbid(f"DNS lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def guarded(real):
        def connect(self, address):
            if self.family in (socket.AF_INET, socket.AF_INET6) and not _local_host(
                    address[0] if isinstance(address, tuple) else address):
                forbid(f"socket connect to {address!r}")
            return real(self, address)
        return connect

    real_create_connection = socket.create_connection

    def create_connection(address, *args, **kwargs):
        if not _local_host(address[0]):
            forbid(f"socket connection to {address!r}")
        return real_create_connection(address, *args, **kwargs)

    real_open = urllib.request.OpenerDirector.open

    def opener_open(self, fullurl, *args, **kwargs):
        if not _local_host(_url_host(fullurl)):
            forbid(f"urllib open of {getattr(fullurl, 'full_url', fullurl)}")
        return real_open(self, fullurl, *args, **kwargs)

    real_urlopen = urllib.request.urlopen

    def urlopen(url, *args, **kwargs):
        if not _local_host(_url_host(url)):
            forbid(f"urlopen of {getattr(url, 'full_url', url)}")
        return real_urlopen(url, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", opener_open)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    yield attempts  # the guard's own tests read (and clear) what it stopped
    if attempts:
        pytest.fail("the test touched the network: " + "; ".join(attempts), pytrace=False)
