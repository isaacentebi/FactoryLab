"""Fixtures and factories shared by every test directory."""

import ast
import fcntl
import hashlib
import inspect
import json
import os
import pickle
import resource
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
                entries = []
                # These consumers originally used in-memory ledgers. Preserve that call:
                # persisting every encrypted append here would add thousands of fsyncs.
                rt = Runtime(manifest, events=events, seed=seed, initial_balance_micro=None,
                             ledger_path=None, router_gamma=.1)
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
                result = dict(summary=summary, entries=entries, state=runtime_state(rt))
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
    def run(manifest, events, seed):
        return _cached_scripted_run(_scripted_run_cache, manifest, events, seed,
                                    mode="runtime")

    return run


# Test tiers (see README "Try it" and AGENTS.md):
#   check  the developer inner loop, the default; no test here runs a world
#   gate   every test that runs a world or reads a shared scripted run
#   slow   kills and resumes real subprocesses
# ``fast`` and ``world`` are the old names of ``check`` and ``gate`` and are still set.
_SHARED_WORLD_FIXTURES = frozenset({"scripted_run", "scripted_runtime_run", "shared_run"})
_WORLD_CLI_COMMANDS = frozenset({"run", "resume"})
#: Functions outside ``tests/`` that run a world's loop when called: the loop's own entry
#: and the operator rehearsal's (``scripts/edition4_rehearsal.run_rehearsal``).
_WORLD_ENTRY_POINTS = frozenset({"run_world", "run_rehearsal"})
_GATE_MARKS = ("gate", "world")
_TESTS_ROOT = Path(__file__).resolve().parent
# A check-tier test whose setup and call use more CPU than this fails: it belongs in gate.
# CPU, not wall time, so a loaded machine cannot fail a test; the wall ceiling still
# fails a check test that sleeps or waits.
CHECK_LIMIT_ENV = "FACTORYLAB_CHECK_LIMIT_S"
CHECK_LIMIT_DEFAULT_S = 2.0
CHECK_WALL_CEILING_S = 10.0
# A gate file whose tests together use more CPU than this (setup, call and teardown,
# summed across workers: its serial cost) fails the run, unless it is listed below with
# the reason it cannot be smaller.
GATE_FILE_BUDGET_ENV = "FACTORYLAB_GATE_FILE_BUDGET_S"
GATE_FILE_BUDGET_DEFAULT_S = 60.0
GATE_FILE_BUDGET_EXCEPTIONS: dict[str, str] = {
    "tests/runtime/test_retained_state_crash.py": (
        "crash anywhere resumes to the uninterrupted run (a storage contract): each of the "
        "eleven crash-point rows runs a world to its crash and resumes it to the end, over "
        "the 30-tick horizon, the shortest that reaches every crash point; the rows share "
        "one reference run (87 s serial CPU at 0bd468f5+lane P)"),
    "tests/runtime/test_bounded_memory.py": (
        "the diary grows linearly (a 60/120-tick pair: at 30/60 the ratio sits at 2.2 "
        "against the 2.3 bound) and pruning changes nothing a reader sees (a 120-tick world "
        "with and without pruning: at 60 ticks only 45% of returns are slim, under the "
        "test's own non-vacuity bound); every other test reads these shared worlds (76 s)"),
    "tests/runtime/test_settled_release.py": (
        "wave 17b: release changes nothing a reader sees, over a pair of 150-event worlds, "
        "the smallest whose decisions pass the release horizon and the charter's ten margin "
        "windows (its module docstring), plus one crash probe resumed to the end (105 s); "
        "not yet ledgered by the test audit"),
}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _runs_world_here(call: ast.Call, builders=frozenset({"Runtime"})) -> bool:
    """Whether one call, read on its own, starts a world's event loop.

    Building a ``Runtime`` is not a world, whatever its events budget: only running
    its loop is (``rt.run()``, ``Runtime(...).run()`` or ``f(...).run()`` where ``f``
    is one of ``builders``, ``run_world``, or the CLI).
    """
    name = _call_name(call)
    if name in _WORLD_ENTRY_POINTS:
        return True
    if (name == "run" and isinstance(call.func, ast.Attribute)
            and not call.args and not call.keywords):
        receiver = call.func.value
        if isinstance(receiver, ast.Name):
            return receiver.id != "subprocess"  # ``rt.run()``: the loop itself
        if isinstance(receiver, ast.Call):
            return _call_name(receiver) in builders
    # The CLI, in process or as a child: ``main(["run", ...])`` or ``[..., "resume", ...]``.
    for argument in call.args:
        if isinstance(argument, (ast.List, ast.Tuple)):
            words = {e.value for e in argument.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if words & _WORLD_CLI_COMMANDS and (
                    name == "main" or any("factorylab" in word for word in words)):
                return True
    return False


def _test_module_path(module: str | None) -> Path | None:
    """The file of a module under ``tests/``, or None for any other module."""
    if not module or not (module == "tests" or module.startswith("tests.")):
        return None
    base = _TESTS_ROOT.parent.joinpath(*module.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _imported_world_calls(tree: ast.Module, world_functions_of) -> frozenset[str]:
    """The call spellings (``fn`` or ``alias.fn``) that reach a world function of another
    module under ``tests/``, as this module imports it."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            source = _test_module_path(node.module)
            for alias in node.names:
                submodule = _test_module_path(f"{node.module}.{alias.name}")
                if submodule is not None:  # ``from tests.gauntlet import populations as P``
                    found |= {f"{alias.asname or alias.name}.{name}"
                              for name in world_functions_of(submodule)}
                elif source is not None and alias.name in world_functions_of(source):
                    found.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                source = _test_module_path(alias.name)
                if source is not None and alias.asname:
                    found |= {f"{alias.asname}.{name}" for name in world_functions_of(source)}
    return frozenset(found)


def _spelling(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return f"{func.value.id}.{func.attr}"
    return None


def _world_functions(tree: ast.Module, world_functions_of=None) -> set[str]:
    """Names of this module's functions that run a world, directly, through a helper of
    this module, or through a helper imported from another module under ``tests/``."""
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    calls = {name: [n for n in ast.walk(node) if isinstance(n, ast.Call)]
             for name, node in functions.items()}
    imported = (_imported_world_calls(tree, world_functions_of)
                if world_functions_of is not None else frozenset())
    # A helper that builds a Runtime: ``_runtime(path).run()`` runs its loop.
    builders = frozenset({"Runtime"} | {name for name, found in calls.items()
                                        if any(_call_name(c) == "Runtime" for c in found)})
    world = {name for name, found in calls.items()
             if any(_runs_world_here(c, builders) or _spelling(c) in imported
                    for c in found)}
    changed = True
    while changed:
        changed = False
        for name, found in calls.items():
            if name not in world and any(_call_name(c) in world for c in found
                                         if isinstance(c.func, ast.Name)):
                world.add(name)
                changed = True
    return world


def _fixture_runs_world(fixturedef, world_functions_of) -> bool:
    """Whether a fixture defined under ``tests/`` runs a world when it is set up."""
    func = inspect.unwrap(getattr(fixturedef, "func", None) or (lambda: None))
    code = getattr(func, "__code__", None)
    if code is None:
        return False
    path = Path(code.co_filename).resolve()
    if not path.is_relative_to(_TESTS_ROOT):
        return False
    return func.__name__ in world_functions_of(path)


def _world_fixtures(item, world_functions_of) -> list:
    """The fixtures this test uses whose setup runs a world."""
    info = getattr(item, "_fixtureinfo", None)
    definitions = info.name2fixturedefs if info is not None else {}
    return [definition for name in getattr(item, "fixturenames", ())
            for definition in definitions.get(name, ())
            if _fixture_runs_world(definition, world_functions_of)]


def _runs_world(item, world_functions_of, world_fixtures) -> bool:
    """The static per-test read: its body, a helper of its module, or a fixture it uses."""
    if _SHARED_WORLD_FIXTURES & set(getattr(item, "fixturenames", ())):
        return True
    name = getattr(item, "originalname", None) or item.name.split("[")[0]
    return name in world_functions_of(Path(item.path)) or bool(world_fixtures)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Partition collected tests into tiers, one test at a time.

    A test is ``gate`` when the test itself (or its class) is marked ``gate`` (or
    ``world``), when it uses a shared scripted run, or when its body, a helper in its
    module, or a fixture it uses runs a world (``rt.run()``, ``run_world``, or the CLI's
    ``run``/``resume``). A test marked ``check`` is ``check``. Everything else that is
    not ``network`` or ``slow`` is ``check``.

    A gate test that shares a class- or module-scoped world fixture is grouped with its
    module (``xdist_group``; ``--dist loadgroup`` in pyproject): the module runs on one
    worker, so its shared reference run is built once, never once per worker.

    A module's ``pytestmark = gate`` does not make every test in it gate: it is removed
    and each test is read on its own, so the unit tests beside a world test stay in the
    inner loop. The static read can miss a world; the check-tier CPU limit below
    catches what it misses.
    """
    facts: dict[Path, set[str]] = {}

    def world_functions_of(path: Path) -> set[str]:
        path = path.resolve()
        if path not in facts:
            facts[path] = set()  # an import cycle reads this module as world-free
            facts[path] = _world_functions(ast.parse(path.read_text()), world_functions_of)
        return facts[path]

    stripped = set()
    for item in items:
        module = item.getparent(pytest.Module)
        if module is not None and id(module) not in stripped:
            stripped.add(id(module))
            module.own_markers[:] = [m for m in module.own_markers
                                     if m.name not in _GATE_MARKS]
    for item in items:
        if any(item.get_closest_marker(m) for m in ("network", "slow")):
            continue
        world_fixtures = _world_fixtures(item, world_functions_of)
        if any(item.get_closest_marker(m) for m in _GATE_MARKS):
            gate = True
        elif item.get_closest_marker("check"):
            gate = False
        else:
            gate = _runs_world(item, world_functions_of, world_fixtures)
        if gate:
            item.add_marker(pytest.mark.gate)
            item.add_marker(pytest.mark.world)
            if any(d.scope in ("class", "module", "package") for d in world_fixtures):
                item.add_marker(pytest.mark.xdist_group(item.nodeid.split("::", 1)[0]))
        else:
            item.add_marker(pytest.mark.check)
            item.add_marker(pytest.mark.fast)


def _seconds_from_env(name: str, default: float) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if raw.lower() in ("0", "off", "none"):
        return None
    return float(raw)


def _check_limit_problem(cpu_s: float, wall_s: float, limit_s: float | None) -> str | None:
    """Why a passing check-tier call breaks the tier's limits, or None if it does not."""
    if limit_s is None:
        return None
    if cpu_s > limit_s:
        return f"used {cpu_s:.2f}s of CPU (limit {limit_s:.1f}s)"
    if wall_s > CHECK_WALL_CEILING_S:
        return f"took {wall_s:.2f}s of wall time (ceiling {CHECK_WALL_CEILING_S:.0f}s)"
    return None


def _files_over_budget(cpu_by_file: dict[str, float], budget_s: float | None,
                       exceptions: dict[str, str]) -> dict[str, float]:
    """The gate files whose serial CPU is over the budget and not excepted by name."""
    if budget_s is None:
        return {}
    return {path: s for path, s in cpu_by_file.items()
            if s > budget_s and path not in exceptions}


_PHASE_CPU = pytest.StashKey[dict]()
_PHASE_WALL = pytest.StashKey[dict]()


def _cpu_s() -> float:
    """This process's CPU, plus that of every child it has waited for."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def _measured(item, when):
    start = _cpu_s()
    try:
        return (yield)
    finally:
        item.stash.setdefault(_PHASE_CPU, {})[when] = _cpu_s() - start


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    return (yield from _measured(item, "setup"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    return (yield from _measured(item, "call"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item):
    return (yield from _measured(item, "teardown"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Every report carries its phase's CPU and its tier; a ``check`` test whose setup
    and call together are over the limits fails, naming the fix.

    Setup counts: a world a fixture runs is the test's world. A module- or class-scoped
    fixture charges the first test that sets it up, which is the one to mark gate.
    """
    report = yield
    report.factorylab_cpu_s = item.stash.get(_PHASE_CPU, {}).get(call.when, 0.0)
    item.stash.setdefault(_PHASE_WALL, {})[call.when] = report.duration
    report.factorylab_tier = ("gate" if item.get_closest_marker("gate")
                              else "check" if item.get_closest_marker("check") else None)
    if report.when != "call" or not report.passed or report.factorylab_tier != "check":
        return report
    cpu, wall = item.stash[_PHASE_CPU], item.stash[_PHASE_WALL]
    problem = _check_limit_problem(cpu.get("setup", 0.0) + cpu.get("call", 0.0),
                                   wall.get("setup", 0.0) + wall.get("call", 0.0),
                                   _seconds_from_env(CHECK_LIMIT_ENV, CHECK_LIMIT_DEFAULT_S))
    if problem is not None:
        report.outcome = "failed"
        report.longrepr = (
            f"{item.nodeid} is in the check tier but its setup and call {problem}. The check tier "
            "is the inner loop and runs no world: mark this test @pytest.mark.gate (or "
            f"make it faster). Raise the CPU limit with {CHECK_LIMIT_ENV}=<seconds>, or "
            f"disable both limits with {CHECK_LIMIT_ENV}=off.")
    return report


class _GateBudget:
    """Sums each gate file's serial CPU from the reports and fails a run whose file is
    over the budget: a slow world is shrunk, shared or excepted by name, never let creep.
    """

    def __init__(self):
        self.cpu: dict[str, float] = {}
        self.wall: dict[str, float] = {}
        self.over: dict[str, float] = {}

    def pytest_runtest_logreport(self, report):
        if getattr(report, "factorylab_tier", None) != "gate":
            return
        path = report.nodeid.split("::", 1)[0]
        self.cpu[path] = self.cpu.get(path, 0.0) + (report.factorylab_cpu_s or 0.0)
        self.wall[path] = self.wall.get(path, 0.0) + report.duration

    def pytest_sessionfinish(self, session):
        self.over = _files_over_budget(
            self.cpu, _seconds_from_env(GATE_FILE_BUDGET_ENV, GATE_FILE_BUDGET_DEFAULT_S),
            GATE_FILE_BUDGET_EXCEPTIONS)
        if self.over and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if not self.cpu:
            return
        write = terminalreporter.write_line
        terminalreporter.section("gate files by serial CPU")
        for path, cpu in sorted(self.cpu.items(), key=lambda kv: -kv[1])[:15]:
            note = " (excepted)" if path in GATE_FILE_BUDGET_EXCEPTIONS else ""
            write(f"{cpu:8.1f}s cpu {self.wall[path]:8.1f}s wall  {path}{note}")
        write(f"{sum(self.cpu.values()):8.1f}s cpu {sum(self.wall.values()):8.1f}s wall  "
              f"all {len(self.cpu)} gate files")
        for path, cpu in sorted(self.over.items()):
            write(f"FAILED gate budget: {path} used {cpu:.1f}s of CPU, over the "
                  f"{GATE_FILE_BUDGET_ENV} budget; shrink or share its world, or list it "
                  "in GATE_FILE_BUDGET_EXCEPTIONS with the reason", red=True)


def pytest_configure(config):
    # Only the process that sees every report judges the budget: the controller under
    # xdist, or the one process without it.
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(_GateBudget(), "factorylab-gate-budget")


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


def _ip_literal(host) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(str(host).strip("[]").split("%", 1)[0])
        return True
    except ValueError:
        return False


def _url_host(url) -> str | None:
    from urllib.parse import urlsplit

    return urlsplit(getattr(url, "full_url", url)).hostname


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Every outbound network path raises ``NetworkForbidden``, naming what it reached.

    Covers ``socket.getaddrinfo`` (a DNS lookup is already traffic),
    ``socket.socket.connect``/``connect_ex``, a datagram's ``sendto``/``sendmsg`` to an
    address, and ``socket.create_connection`` (loopback and unix sockets allowed),
    ``urllib.request.urlopen`` and every ``OpenerDirector.open``, which the project's
    own seams ``x402.http_request`` and ``polymarket.http_get_json`` both open through
    (a test that fakes the opener beneath a seam reaches nothing, and is not stopped).
    Each attempt is also remembered and fails the test at teardown, so code that
    catches the error (a rail that turns any transport failure into a retry) cannot
    hide it.

    Isolation covers this test process and the known subprocess seams of
    ``factorylab/`` and ``scripts/``, whose children the in-process patches cannot
    reach:

    * ``connector.resolve_addresses`` resolves a name in a ``python -I`` child: a name
      that is neither local nor an IP literal is refused (unless the test replaced
      ``subprocess.run`` with a fake, when nothing leaves);
    * ``scripts/rehearsal.py`` ``run_command`` and ``scripts/fastloop.py`` ``run_seeds``
      start child worlds, which may call providers or venues: refused.

    The other subprocesses cannot reach the network: ``cortex.sandbox`` runs population
    code jailed with no network grant (bubblewrap ``--unshare-all`` on Linux, a
    sandbox-exec profile without one on macOS), and ``runtime.release`` asks a local
    ``git rev-parse HEAD``. A subprocess a test starts itself, and OS-level isolation,
    are out of scope. A test marked ``network`` is left alone; such tests
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

    def remote(sock, address) -> bool:
        return sock.family in (socket.AF_INET, socket.AF_INET6) and not _local_host(
            address[0] if isinstance(address, tuple) else address)

    real_sendto, real_sendmsg = socket.socket.sendto, socket.socket.sendmsg

    def sendto(self, data, *args):
        # sendto(data, address) or sendto(data, flags, address): a datagram needs no
        # DNS lookup or connect to leave for a numeric address.
        if args and remote(self, args[-1]):
            forbid(f"socket sendto {args[-1]!r}")
        return real_sendto(self, data, *args)

    def sendmsg(self, buffers, *args, **kwargs):
        address = kwargs.get("address", args[2] if len(args) > 2 else None)
        if address is not None and remote(self, address):
            forbid(f"socket sendmsg to {address!r}")
        return real_sendmsg(self, buffers, *args, **kwargs)

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
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    monkeypatch.setattr(socket.socket, "sendmsg", sendmsg)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", opener_open)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    import subprocess

    from factorylab.world import connector
    from scripts import fastloop, rehearsal

    real_run, real_resolve = subprocess.run, connector.resolve_addresses

    def resolve_addresses(host, *args, **kwargs):
        # The child does the DNS lookup; only a real subprocess.run starts one.
        if (subprocess.run is real_run and not _local_host(host)
                and not _ip_literal(host)):
            forbid(f"resolve_addresses({host!r}) in a python -I child")
        return real_resolve(host, *args, **kwargs)

    def child_world(name):
        def refuse(*args, **kwargs):
            forbid(f"{name}: a child world outside this guard")
        return refuse

    monkeypatch.setattr(connector, "resolve_addresses", resolve_addresses)
    monkeypatch.setattr(rehearsal, "run_command", child_world("scripts/rehearsal.py run_command"))
    monkeypatch.setattr(fastloop, "run_seeds", child_world("scripts/fastloop.py run_seeds"))
    yield attempts  # the guard's own tests read (and clear) what it stopped
    if attempts:
        pytest.fail("the test touched the network: " + "; ".join(attempts), pytrace=False)
