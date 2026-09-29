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
import tempfile
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

#: Directories a test module made at import, for the whole session: removed at its end
#: (``_TemporaryDirectories``), in each process that imported the module.
_SESSION_DIRECTORIES: list[Path] = []


def session_directory(prefix: str) -> Path:
    """A fresh directory that lives until this test session ends, then is removed.

    For a module-level stand-in file (a fixture cannot run at import); anything a test
    makes for itself belongs in ``tmp_path``.
    """
    path = Path(tempfile.mkdtemp(prefix=prefix))
    _SESSION_DIRECTORIES.append(path)
    return path


class _TemporaryDirectories:
    """Every directory ``tempfile.mkdtemp`` made in this process during the run (a
    ``TemporaryDirectory`` makes its own through it) must be gone when the session ends:
    a test run leaves nothing behind in the system temporary directory. Session
    directories are removed first; any other survivor fails the run, named.
    """

    def __init__(self):
        self.made: list[str] = []
        self.original = None
        self.left: list[str] = []

    def pytest_configure(self, config):
        self.original = original = tempfile.mkdtemp
        made = self.made

        def recorded(*args, **kwargs):
            path = original(*args, **kwargs)
            made.append(path)
            return path

        tempfile.mkdtemp = recorded

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session):
        for path in _SESSION_DIRECTORIES:
            shutil.rmtree(path, ignore_errors=True)
        _SESSION_DIRECTORIES.clear()
        if self.original is not None:
            tempfile.mkdtemp = self.original
        self.left = sorted({*self.left, *(path for path in self.made
                                           if os.path.exists(path))})
        if hasattr(session.config, "workeroutput"):  # an xdist worker tells its controller
            session.config.workeroutput["factorylab_left"] = self.left
        if self.left and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    @pytest.hookimpl(optionalhook=True)
    def pytest_testnodedown(self, node, error):
        """The controller hears what each xdist worker left behind."""
        self.left.extend(getattr(node, "workeroutput", {}).get("factorylab_left", ()))

    def pytest_terminal_summary(self, terminalreporter):
        for path in self.left:
            terminalreporter.write_line(
                f"FAILED temporary directory left behind: {path} (use tmp_path, a "
                "TemporaryDirectory context, or tests.conftest.session_directory)", red=True)


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


@pytest.fixture(scope="session")
def shared_result(_scripted_run_cache):
    """``shared_result(key, factory)``: ``factory()``'s picklable result, computed once
    per key per pytest invocation and shared by every xdist worker (the first to ask
    runs it and publishes it; the others wait on the same POSIX lock and read it).

    For an uninterrupted reference world several tests of one module compare against,
    so each worker does not rerun it. The result is read afresh by each worker; a test
    that mutates what it got changes nothing another worker reads.
    """
    directory = _scripted_run_cache / "shared-results"
    memo = {}

    def get(key, factory):
        if key in memo:
            return memo[key]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{key}.pickle"
        with (directory / f"{key}.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not path.exists():
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(pickle.dumps(factory(), protocol=pickle.HIGHEST_PROTOCOL))
                temporary.replace(path)
        memo[key] = pickle.loads(path.read_bytes())
        return memo[key]

    return get


# Test tiers (see README "Try it" and AGENTS.md):
#   check  the developer inner loop, the default; no test here runs a world
#   gate   every test that runs a world or reads a shared scripted run
#   slow   kills and resumes real subprocesses
#   soak   the long runs (``-m soak``): the checkpoint plateau and the gauntlet's long
#          worlds, whose short lengths run in gate. REQUIRED for a change to any of
#          ``SOAK_REQUIRED_PATHS``, before any world launch, and on request (AGENTS.md,
#          Verify gate); a whole gate run enforces it (``_SoakRequirement``)
# ``fast`` and ``world`` are the old names of ``check`` and ``gate`` and are still set.
_SHARED_WORLD_FIXTURES = frozenset({"scripted_run", "scripted_runtime_run", "shared_run",
                                    "shared_result"})
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
#: Each tier's whole serial CPU (setup, call and teardown of every test in it, summed
#: across workers). The per-file budget above bounds no total; this does, so a tier
#: cannot creep: a run whose tier spends more fails, naming the files that cost most.
#: At ``-n 2`` a tier's wall is about half its serial cost. Set over what each tier
#: measured on a loaded host at test-speed-2 (gate 1,025 s, slow 223 s); the slow
#: tier's is inside five minutes of wall, the gate's is not yet (600 s of CPU is where
#: it should come down to).
#: ``FACTORYLAB_TIER_BUDGET_S=off`` disables it (a profiled or instrumented run).
TIER_BUDGET_ENV = "FACTORYLAB_TIER_BUDGET_S"
TIER_BUDGETS_S = {"gate": 1150.0, "slow": 300.0}
GATE_FILE_BUDGET_EXCEPTIONS: dict[str, str] = {
    # Serial CPU measured at -n 2 on a loaded host (load average 8-10), test-speed-2.
    "tests/gauntlet/test_thrash.py": (
        "42-54 s: TH-1 at 100 events (detector, price, charge, frontier, physics) with its "
        "missing-price and missing-charge controls at 100, TH-2 at 100 and its reversion "
        "world at 150 (the router loop's short lifespan needs it), TH-3 at 150 (two "
        "charter boundaries) with its always-ready control at 100. The original 300/360 "
        "worlds, TH-1e/f, the frozen integral and TH-4's null run in the soak tier"),
    "tests/runtime/test_settled_release.py": (
        "87-90 s: wave 17b. Release changes nothing a reader sees, over a released and a "
        "kept 150-event world (at 120 and 100 events the release share, the "
        "venue-confirmed released orders and the invariant's candidate pool are absent), "
        "the tally checked against the full scan after every event; the crash probe "
        "resumes the released world's own diary, copied mid-pass after tick 140"),
    "tests/runtime/test_retained_state_crash.py": (
        "Crash/resume storage contract: thirteen crash-point rows retain event, unlink, "
        "artifact-write and every checkpoint/io protocol step over 20 ticks (12 misses "
        "the final-write crash). All rows retain full prefix/trail/summary/cleanup "
        "assertions; matrix and eviction rows each share their reference. Late-world "
        "divergence remains owned by test_checkpoint_coverage's 100-tick world"),
    "tests/runtime/test_evaluation_layer.py": (
        "75-88 s: tier recursion on two seeds and a fourth tier at 100 events (120 in the "
        "soak tier), the multi-judge, adversarial, two-router and heavy-chaos worlds at "
        "their lane E sizes; family-disjoint judging reads the shared 100-event run"),
    "tests/scripts/test_class2_audit.py": (
        "80 s: the Class 2 release audit's CLI, run on real git histories (about 2,500 git "
        "calls, the tool's own provenance and calibration reads) and the seat-text scan; "
        "gate as a whole (GATE_MODULE), on one worker"),
    "tests/runtime/test_bounded_memory.py": (
        "65-76 s: the diary grows linearly (a 60/120-tick pair: at 30/60 the ratio sits at "
        "2.2 against the 2.3 bound) and pruning changes nothing a reader sees (a 120-tick "
        "world with and without pruning, the second in memory: at 60 ticks only 45% of "
        "returns are slim, under the test's own non-vacuity bound)"),
    "tests/gauntlet/test_stable_failure.py": (
        "57-64 s: SF-1 at 100 events (a-d, f, physics) with its two 52-event controls, the "
        "transient world at 150 with its control, SF-2 at 100 and the 220-event "
        "intermittent world, several running observations in the jail. The original "
        "228/250 worlds and SF-1e run in the soak tier"),
    "tests/audit/test_class2_rendered.py": (
        "56-66 s: every launchable world's launch path run for 60 ticks on the scripted "
        "population, each once and shared by the file's tests, and every request it sends "
        "rendered and linted; since wave 16b the edition6 worlds reach the counter-verdict "
        "request inside those 60 ticks, whose surfaces the audit must read"),
    "tests/gauntlet/test_overfitting.py": (
        "77-89 s: four 100-event worlds (of2, of4, OF-2c's two controls) and two of1 "
        "worlds, the fewest in which a seated adversary's holdout is proposed, trialled, "
        "balloted and activated; about half the CPU is the jail's interpreters, one per "
        "registered observation at each price close"),
}


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


#: Calls that return a runtime ready to run: its constructor and a resume.
_RUNTIME_BUILDERS = frozenset({"Runtime", "resume_runtime"})


def _runs_world_here(call: ast.Call, builders=_RUNTIME_BUILDERS,
                     runtimes=frozenset()) -> bool:
    """Whether one call, read on its own, starts a world's event loop.

    Building a ``Runtime`` is not a world, whatever its events budget: only running
    its loop is (``rt.run()`` where ``rt`` is one of ``runtimes``, the variables this
    function assigned from a builder; ``Runtime(...).run()`` or ``f(...).run()`` where
    ``f`` is one of ``builders``; ``run_world``; or the CLI). Any other object's
    ``.run()`` is not read as a world: the runtime world guard catches a world this
    static read misses.
    """
    name = _call_name(call)
    if name in _WORLD_ENTRY_POINTS:
        return True
    if (name == "run" and isinstance(call.func, ast.Attribute)
            and not call.args and not call.keywords):
        receiver = call.func.value
        if isinstance(receiver, ast.Name):
            return receiver.id in runtimes  # ``rt.run()``: the loop itself
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
    builders = _RUNTIME_BUILDERS | {name for name, found in calls.items()
                                    if any(_call_name(c) in _RUNTIME_BUILDERS
                                           for c in found)}

    def runtimes(node) -> frozenset[str]:
        """The names this function binds to a builder's result (``rt = Runtime(...)``)."""
        return frozenset(
            target.id for assign in ast.walk(node) if isinstance(assign, ast.Assign)
            and isinstance(assign.value, ast.Call) and _call_name(assign.value) in builders
            for target in assign.targets if isinstance(target, ast.Name))

    world = {name for name, found in calls.items()
             if any(_runs_world_here(c, builders, runtimes(functions[name]))
                    or _spelling(c) in imported for c in found)}
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


def _shares_a_module_fixture(item) -> bool:
    """Whether this test uses a class-, module- or package-scoped fixture defined under
    ``tests/``: something built once and read by several tests of its module."""
    info = getattr(item, "_fixtureinfo", None)
    for definitions in (info.name2fixturedefs.values() if info is not None else ()):
        for definition in definitions:
            code = getattr(inspect.unwrap(getattr(definition, "func", None)
                                          or (lambda: None)), "__code__", None)
            if (definition.scope in ("class", "module", "package") and code is not None
                    and Path(code.co_filename).resolve().is_relative_to(_TESTS_ROOT)):
                return True
    return False


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

    A gate test that shares a class- or module-scoped fixture of ``tests/`` (a world, a
    rendered corpus) is grouped with its module (``xdist_group``; ``--dist loadgroup`` in
    pyproject): the module runs on one worker, so what it shares is built once, never
    once per worker.

    A module's ``pytestmark = gate`` does not make every test in it gate: it is removed
    and each test is read on its own, so the unit tests beside a world test stay in the
    inner loop. A module whose every test shares a setup over the check limit (an
    autouse module fixture) says so with ``GATE_MODULE = "<why>"``: all its tests are
    gate. The static read can miss a world; the check-tier CPU limit below catches
    what it misses.
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
        if item.get_closest_marker("soak"):
            # A soak row (a long param of a gate test) is soak only: -m gate never
            # selects it, whatever its test is marked.
            item.own_markers[:] = [m for m in item.own_markers if m.name not in _GATE_MARKS]
            continue
        if any(item.get_closest_marker(m) for m in ("network", "slow")):
            continue
        world_fixtures = _world_fixtures(item, world_functions_of)
        if (any(item.get_closest_marker(m) for m in _GATE_MARKS)
                or getattr(getattr(item, "module", None), "GATE_MODULE", None)):
            gate = True
        elif item.get_closest_marker("check"):
            gate = False
        else:
            gate = _runs_world(item, world_functions_of, world_fixtures)
        if gate:
            item.add_marker(pytest.mark.gate)
            item.add_marker(pytest.mark.world)
            if _shares_a_module_fixture(item):
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


def _check_limit_problem(cpu: dict[str, float], wall: dict[str, float],
                         limit_s: float | None) -> str | None:
    """Why a passing check-tier test breaks the tier's limits, or None if it does not.

    ``cpu`` and ``wall`` map a phase to its seconds; setup and call are charged
    together, so a world a fixture runs is the test's world.
    """
    if limit_s is None:
        return None
    cpu_s = cpu.get("setup", 0.0) + cpu.get("call", 0.0)
    wall_s = wall.get("setup", 0.0) + wall.get("call", 0.0)
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


def _tiers_over_budget(cpu_by_tier: dict[str, float], budgets: dict[str, float],
                       enabled: bool = True) -> dict[str, float]:
    """The tiers whose summed serial CPU is over their budget (none when disabled)."""
    if not enabled:
        return {}
    return {tier: s for tier, s in cpu_by_tier.items()
            if tier in budgets and s > budgets[tier]}


_PHASE_CPU = pytest.StashKey[dict]()
_PHASE_WALL = pytest.StashKey[dict]()


def _cpu_s() -> float:
    """This process's CPU, plus that of every child it has waited for."""
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


#: Whether a world event was stepped during this test's setup or call.
_STEPPED_A_WORLD = pytest.StashKey[bool]()


class _WorldGuard:
    """Records, per test, whether a world stepped an event during its setup or call.

    Every world, whatever starts it (``run_world``, ``Runtime.run``, a resume's replay,
    the CLI or an operator script in process), steps each event through
    ``Runtime._process_event``: for this session that method is wrapped to mark the
    test whose setup or call is running. The static classifier reads source and can
    miss a world behind a wrapper; this reads what ran. It lives in test code only:
    the class is patched for the session and put back at its end.
    """

    def __init__(self):
        self.current = None  # the item whose setup or call is running, or None
        self.original = None

    def pytest_sessionstart(self, session):
        from factorylab.runtime import loop

        self.original = original = loop.Runtime._process_event
        guard = self

        def stepping(rt, event):
            if guard.current is not None:
                guard.current.stash[_STEPPED_A_WORLD] = True
            return original(rt, event)

        loop.Runtime._process_event = stepping

    def pytest_sessionfinish(self, session):
        from factorylab.runtime import loop

        if self.original is not None:
            loop.Runtime._process_event = self.original


def _stepped_a_world_problem(item) -> str | None:
    """Why a check-tier test broke the tier's rule that it runs no world, or None."""
    if item.stash.get(_STEPPED_A_WORLD, False):
        return "stepped a world event"
    return None


def _measured(item, when):
    guard = item.config.pluginmanager.get_plugin("factorylab-world-guard")
    start = _cpu_s()
    if guard is not None and when in ("setup", "call"):
        guard.current = item
    try:
        return (yield)
    finally:
        if guard is not None:
            guard.current = None
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
    report.factorylab_tier = next((tier for tier in ("gate", "check", "slow", "soak")
                                   if item.get_closest_marker(tier)), None)
    if report.when != "call" or not report.passed or report.factorylab_tier != "check":
        return report
    problem = _stepped_a_world_problem(item) or _check_limit_problem(
        item.stash[_PHASE_CPU], item.stash[_PHASE_WALL],
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
    Sums each tier's serial CPU too and fails a run whose tier is over its budget.
    """

    def __init__(self):
        self.cpu: dict[str, float] = {}
        self.wall: dict[str, float] = {}
        self.over: dict[str, float] = {}
        self.tiers: dict[str, float] = {}
        self.tiers_over: dict[str, float] = {}
        self.slow_files: dict[str, float] = {}

    def pytest_runtest_logreport(self, report):
        tier = getattr(report, "factorylab_tier", None)
        cpu = report.factorylab_cpu_s or 0.0
        if tier is not None:
            self.tiers[tier] = self.tiers.get(tier, 0.0) + cpu
        path = report.nodeid.split("::", 1)[0]
        if tier == "slow":
            self.slow_files[path] = self.slow_files.get(path, 0.0) + cpu
        if tier != "gate":
            return
        self.cpu[path] = self.cpu.get(path, 0.0) + cpu
        self.wall[path] = self.wall.get(path, 0.0) + report.duration

    def pytest_sessionfinish(self, session):
        self.over = _files_over_budget(
            self.cpu, _seconds_from_env(GATE_FILE_BUDGET_ENV, GATE_FILE_BUDGET_DEFAULT_S),
            GATE_FILE_BUDGET_EXCEPTIONS)
        self.tiers_over = _tiers_over_budget(
            self.tiers, TIER_BUDGETS_S, _seconds_from_env(TIER_BUDGET_ENV, 1.0) is not None)
        if (self.over or self.tiers_over) and session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if not self.cpu:
            self._tier_summary(terminalreporter)
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
        self._tier_summary(terminalreporter)

    def _tier_summary(self, terminalreporter):
        write = terminalreporter.write_line
        for tier, cpu in sorted(self.tiers.items()):
            if tier in TIER_BUDGETS_S:
                write(f"{tier} tier: {cpu:.1f}s of CPU (budget {TIER_BUDGETS_S[tier]:.0f}s)")
        for tier, cpu in sorted(self.tiers_over.items()):
            files = self.cpu if tier == "gate" else self.slow_files
            top = ", ".join(f"{path} {s:.0f}s" for path, s in
                            sorted(files.items(), key=lambda kv: -kv[1])[:5])
            write(f"FAILED {tier} tier budget: its tests used {cpu:.1f}s of CPU, over "
                  f"TIER_BUDGETS_S[{tier!r}] = {TIER_BUDGETS_S[tier]:.0f}s; shrink or share "
                  f"a world, never raise the budget to fit; the costliest files: {top}",
                  red=True)


#: The paths whose change requires a soak pass (AGENTS.md, Verify gate): a whole-gate run
#: on a tree that changed one of them since ``origin/main`` fails unless the soak tier
#: passed whole on this exact tree (``_SoakRequirement``).
SOAK_REQUIRED_PATHS = (
    "factorylab/kernel/", "factorylab/runtime/pricing.py", "factorylab/charter/controller.py",
    "factorylab/runtime/immune.py", "factorylab/versioning/", "factorylab/runtime/routing.py",
    "factorylab/learners/", "factorylab/runtime/settled.py", "factorylab/runtime/resume.py",
    "factorylab/runtime/loop.py", "factorylab/runtime/governance.py", "tests/gauntlet/",
    "scripts/gauntlet.py",
)
#: The file, in the repository's common git directory (shared by its worktrees), listing
#: the tree hashes on which the whole soak tier passed.
SOAK_PASSES = "factorylab-soak-passes"


def _git(root: Path, *args: str, env: dict | None = None) -> str | None:
    import subprocess

    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, env=env, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout


def _tree_hash(root: Path) -> str | None:
    """The git tree of the working tree as it stands, uncommitted and untracked (not
    ignored) files included, written through a private index: the index is untouched."""
    with tempfile.TemporaryDirectory(prefix="factorylab-soak-") as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}
        if _git(root, "read-tree", "HEAD", env=env) is None:
            return None
        if _git(root, "add", "-A", env=env) is None:
            return None
        tree = _git(root, "write-tree", env=env)
    return tree.strip() if tree else None


def _changed_since_main(root: Path) -> list[str] | None:
    """Every path the working tree changed since ``origin/main``, or None without git."""
    changed = _git(root, "diff", "--name-only", "origin/main")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if changed is None or untracked is None:
        return None
    return sorted({*changed.split(), *untracked.split()})


def soak_required(paths: list[str]) -> list[str]:
    """The paths among ``paths`` whose change requires a soak pass."""
    return [path for path in paths if path.startswith(SOAK_REQUIRED_PATHS)]


class _SoakRequirement:
    """The soak tier, enforced: a whole ``soak`` run that passes records its tree hash;
    a whole ``gate`` run on a tree that changed a soak-required path since
    ``origin/main`` fails unless that tree's hash is recorded. A run that names files or
    ``-k`` is neither. Without git (or ``origin/main``) nothing is judged, and says so.
    """

    def __init__(self, root: Path):
        self.root, self.tree, self.problem, self.note = root, None, None, None

    @staticmethod
    def _whole(config) -> bool:
        return (config.args_source is pytest.Config.ArgsSource.TESTPATHS
                and not config.option.keyword)

    def _record(self) -> Path | None:
        common = _git(self.root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return Path(common.strip()) / SOAK_PASSES if common else None

    def pytest_sessionstart(self, session):
        marks = session.config.option.markexpr or ""
        if self._whole(session.config) and ("gate" in marks or "soak" in marks):
            self.tree = _tree_hash(self.root)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session):
        budget = session.config.pluginmanager.get_plugin("factorylab-gate-budget")
        tiers = budget.tiers if budget is not None else {}
        if self.tree is None or not tiers:
            return
        record = self._record()
        if record is None:
            return
        if "soak" in tiers and "gate" not in tiers:
            if (session.exitstatus == pytest.ExitCode.OK
                    and _tree_hash(self.root) == self.tree):
                with record.open("a") as passes:
                    passes.write(self.tree + "\n")
                self.note = f"soak passed on tree {self.tree[:12]}: recorded"
            return
        if "gate" not in tiers:
            return
        changed = _changed_since_main(self.root)
        if changed is None:
            self.note = "soak requirement not judged: no git or no origin/main"
            return
        required = soak_required(changed)
        passed = record.read_text().split() if record.exists() else []
        if required and self.tree not in passed:
            self.problem = (
                f"soak required: this tree changes {', '.join(required[:6])}"
                f"{' and more' if len(required) > 6 else ''} since origin/main, and the "
                f"soak tier has not passed on it (tree {self.tree[:12]}); run "
                "`uv run pytest -m soak -n 2` on this tree, then the gate again")
            if session.exitstatus == pytest.ExitCode.OK:
                session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if self.note:
            terminalreporter.write_line(self.note)
        if self.problem:
            terminalreporter.write_line(f"FAILED {self.problem}", red=True)


def pytest_configure(config):
    # Every process that runs tests watches its worlds and its temporary directories.
    config.pluginmanager.register(_WorldGuard(), "factorylab-world-guard")
    directories = _TemporaryDirectories()
    # Registered while configuring: pytest calls its (historic) pytest_configure now.
    config.pluginmanager.register(directories, "factorylab-temporary-directories")
    # Only the process that sees every report judges the budget: the controller under
    # xdist, or the one process without it.
    if not hasattr(config, "workerinput"):
        config.pluginmanager.register(_GateBudget(), "factorylab-gate-budget")
        config.pluginmanager.register(_SoakRequirement(config.rootpath),
                                      "factorylab-soak-requirement")


def make_runtime(*, balance=100_000_000, live=False, clock_source=None):
    """Return a scripted-world runtime with no ledger file, no network and no credentials."""
    manifest = load_manifest("scripted")
    if live:
        manifest = replace(manifest, exchange=replace(manifest.exchange, kind="hyperliquid"))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=balance,
                   ledger_path=None, router_gamma=.1,
                   exchange=FakeExchange(), provider=ScriptedProvider(),
                   clock_source=clock_source)


@pytest.fixture(scope="session", autouse=True)
def _jail_probed_once():
    """The jail probe runs once per test process: whether confined code can run is a
    fact about this host, and each probe starts a jailed interpreter (about 40 ms, three
    per ``Runtime`` built, most of a unit test's cost).

    The kept answer is the real probe's, on the host's own seams. A test that replaces
    ``shutil.which`` or ``subprocess.Popen`` (a jail missing or unable to start) gets a
    fresh probe through its replacement, and one that patches ``jail_probe`` itself
    replaces this for its own duration (``jail_available`` reads it at call time).
    """
    import shutil

    from factorylab.cortex import sandbox
    from factorylab.runtime import loop

    real, answers = sandbox.jail_probe, []
    seams = (shutil.which, sandbox.subprocess.Popen)

    def probed():
        if (shutil.which, sandbox.subprocess.Popen) != seams:
            return real()
        if not answers:
            answers.append(real())
        return answers[0]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sandbox, "jail_probe", probed)
        patch.setattr(loop, "jail_probe", probed)
        yield


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
