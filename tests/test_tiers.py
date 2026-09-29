"""The tier harness in tests/conftest.py: which tests run a world, the check limit, and
the gate file budget.

A unit test misread as a world leaves the inner loop silently; a world misread as a
unit test is caught at run time by the CPU limit; a gate file that creeps past its
budget fails the run unless it is excepted by name. These pin all three.
"""

import ast
from pathlib import Path

import pytest

from tests.conftest import (
    _STEPPED_A_WORLD,
    CHECK_WALL_CEILING_S,
    _check_limit_problem,
    _files_over_budget,
    _stepped_a_world_problem,
    _tiers_over_budget,
    _world_functions,
    soak_required,
)

_TESTS = Path(__file__).resolve().parent

SOURCE = '''
from factorylab.runtime.loop import Runtime, run_world

def built_not_run():
    rt = Runtime(m, events=40)
    rt._manage_reserve_window()

def _runtime(path):
    return Runtime(m, events=30, ledger_path=path)

def runs_its_loop():
    rt = Runtime(m, events=0)
    rt.run()

def runs_a_built_runtime():
    _runtime("p").run()

def runs_something_else():
    Executor(venue).run()
    subprocess.run(["git", "status"])

def runs_a_proof_not_a_world():
    runner = proof(tmp_path, fake)
    runner.run()

def resumes_a_world():
    resumed = resume_runtime(m, path)
    resumed.run()

def via_run_world():
    run_world(m, events=5, seed=1)

def via_the_cli():
    main(["run", "--world", "scripted"])

def via_a_helper():
    via_run_world()

def via_the_operator_rehearsal():
    rehearsal.run_rehearsal(world, out=out, capital_loop=True)

def relaunch(w):
    return rehearsal.run_rehearsal(w)

def via_a_rehearsal_helper():
    relaunch(w)

def imports_run_world_only():
    from factorylab.runtime.loop import run_world  # noqa: F401
'''


def test_only_a_running_loop_is_a_world_however_the_runtime_is_built():
    assert _world_functions(ast.parse(SOURCE)) == {
        "runs_its_loop", "runs_a_built_runtime", "via_run_world", "via_the_cli",
        "via_a_helper", "via_the_operator_rehearsal", "relaunch", "via_a_rehearsal_helper",
        "resumes_a_world"}  # a non-runtime object's .run() (a proof runner) stays check


def test_a_world_run_by_a_helper_of_another_test_module_is_a_world():
    """``from tests.gauntlet import populations as P`` then ``P.run(...)``, or a helper
    imported by name: the helper's own module decides; a helper that runs nothing, or a
    module outside ``tests/``, does not make a world."""
    helpers = _TESTS / "gauntlet" / "populations.py"
    assert "run" in _world_functions(ast.parse(helpers.read_text()))
    source = ast.parse('''
from scripts import gauntlet as g
from tests.gauntlet import populations as P
from tests.helpers import keep_every_checkpoint

def via_module_alias():
    P.run(P.sf1())

def via_a_world_free_helper():
    keep_every_checkpoint(monkeypatch)
    P.sf1()

def via_a_script():
    g.replay(events)
''')

    def world_functions_of(path):
        return _world_functions(ast.parse(path.read_text()))

    assert _world_functions(source, world_functions_of) == {"via_module_alias"}


def test_the_check_limit_is_cpu_with_a_separate_wall_ceiling():
    assert _check_limit_problem({"call": 1.9}, {"call": 9.0}, 2.0) is None
    assert "CPU" in _check_limit_problem({"call": 2.1}, {"call": 2.1}, 2.0)
    # A call that waits uses no CPU; the wall ceiling still fails it.
    assert "wall" in _check_limit_problem({"call": 0.1}, {"call": CHECK_WALL_CEILING_S + 1},
                                          2.0)
    assert _check_limit_problem({"call": 50.0}, {"call": 50.0}, None) is None
    # A world a fixture runs at setup is the test's own: setup and call are charged
    # together; teardown is not.
    assert "CPU" in _check_limit_problem({"setup": 1.5, "call": 1.0}, {}, 2.0)
    assert "wall" in _check_limit_problem({}, {"setup": 6.0, "call": 5.0}, 2.0)
    assert _check_limit_problem({"call": 1.0, "teardown": 5.0}, {}, 2.0) is None


def test_a_gate_file_over_its_budget_fails_unless_it_is_excepted_by_name():
    spent = {"tests/a.py": 61.0, "tests/b.py": 59.0, "tests/c.py": 300.0}
    assert _files_over_budget(spent, 60.0, {"tests/c.py": "why it cannot be smaller"}) == {
        "tests/a.py": 61.0}
    assert _files_over_budget(spent, None, {}) == {}


def test_a_tier_over_its_total_budget_fails_and_the_budget_can_be_switched_off():
    spent = {"gate": 600.0, "slow": 300.0, "check": 900.0}
    assert _tiers_over_budget(spent, {"gate": 560.0, "slow": 560.0}) == {"gate": 600.0}
    assert _tiers_over_budget({"gate": 560.0}, {"gate": 560.0}) == {}
    assert _tiers_over_budget(spent, {"gate": 560.0}, enabled=False) == {}


@pytest.mark.check  # deliberately: the guard must catch a check test that steps a world
def test_a_check_test_that_steps_a_one_event_world_fails_under_the_guard(request):
    """The runtime guard, live: building a runtime steps nothing; running a one-event
    world marks this test, and the check tier's report fails it with the fix. The mark
    is then cleared, so this test itself passes (it is the guard's own proof)."""
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest

    rt = Runtime(load_manifest("scripted"), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1)
    assert _stepped_a_world_problem(request.node) is None
    rt.run()
    assert _stepped_a_world_problem(request.node) == "stepped a world event"
    request.node.stash[_STEPPED_A_WORLD] = False


def test_every_owner_of_checkpointed_state_requires_soak_and_a_doc_does_not():
    """The soak-required modules are read from the code: each that writes something the
    checkpoint carries (Sol on #179: the fee-history, measured-consequence and forecast
    releases were missing from a hand list), beside the kernel, the learners, the
    version organ, the checkpoint and the gauntlet."""
    owners = ["factorylab/runtime/venue.py", "factorylab/runtime/markets.py",
              "factorylab/settlement/forecast.py", "factorylab/runtime/settled.py",
              "factorylab/runtime/loop.py", "factorylab/runtime/governance.py",
              "factorylab/runtime/pricing.py", "factorylab/runtime/immune.py",
              "factorylab/runtime/routing.py", "factorylab/charter/controller.py",
              "factorylab/runtime/resume.py", "factorylab/kernel/ledger.py",
              "factorylab/learners/exp3.py", "factorylab/versioning/versions.py",
              "tests/gauntlet/test_thrash.py", "scripts/gauntlet.py"]
    others = ["README.md", "AGENTS.md", "factorylab/world/venice.py",
              "factorylab/cortex/sandbox.py", "tests/runtime/test_resume.py",
              "factorylab/runtime/worlds.py"]
    assert soak_required(owners + others) == owners


def _soak_session(tmp_path, *, marks, tiers, collected, passed, status=None, **options):
    from types import SimpleNamespace

    from tests import conftest

    plugin = conftest._SoakRequirement(tmp_path)
    plugin._record = lambda: tmp_path / "passes"
    option = SimpleNamespace(**{"keyword": "", "markexpr": marks, **options})
    config = SimpleNamespace(args_source=pytest.Config.ArgsSource.TESTPATHS, option=option)
    session = SimpleNamespace(config=config, exitstatus=status or pytest.ExitCode.OK)
    plugin.pytest_sessionstart(session)
    plugin.collected = set(collected)
    for nodeid in passed:
        plugin.pytest_runtest_logreport(SimpleNamespace(
            when="call", passed=True, nodeid=nodeid, factorylab_tier=tiers))
    if not passed:  # a setup report: the tier ran, no test body did
        plugin.pytest_runtest_logreport(SimpleNamespace(
            when="setup", passed=True, nodeid="t", factorylab_tier=tiers))
    plugin.pytest_sessionfinish(session)
    return session.exitstatus, plugin


def test_a_soak_pass_is_recorded_only_for_the_whole_tier_every_test_passing(
        tmp_path, monkeypatch):
    """Sol on #179: --setup-only recorded a pass for a soak test that asserts False. A
    tree is certified only by a whole soak run in which every collected test's call
    passed, on one tree from start to end."""
    from tests import conftest

    monkeypatch.setattr(conftest, "_tree_hash", lambda root: "tree-a")
    ids = ["tests/a.py::soak_one", "tests/a.py::soak_two"]
    record = tmp_path / "passes"
    for options in ({"setuponly": True}, {"collectonly": True}, {"keyword": "one"},
                    {"deselect": ["tests/a.py::soak_two"]}, {"ignore": ["tests/b.py"]},
                    {"lf": True}, {"exitfirst": True}):
        _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=ids,
                      **options)
        assert not record.exists(), options
    _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=[],
                  setuponly=False)
    _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=ids[:1])
    _soak_session(tmp_path, marks="soak or slow", tiers="soak", collected=ids, passed=ids)
    assert not record.exists()
    trees = iter(["tree-a", "tree-b"])  # edited while it ran
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: next(trees))
    _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=ids)
    assert not record.exists()
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: "tree-a")
    _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=ids)
    assert record.read_text() == "tree-a\n"


def test_a_whole_gate_on_a_soak_required_change_fails_until_soak_passed_on_its_tree(
        tmp_path, monkeypatch):
    """A whole gate run on a tree that changed a soak-required path fails until that
    tree is certified, fails when the tree changes while it runs (Sol on #179), and a
    change to no such path passes."""
    from tests import conftest

    tree = {"now": "tree-a"}
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: tree["now"])
    changed = ["factorylab/kernel/ledger.py", "README.md"]
    monkeypatch.setattr(conftest, "_changed_since_main", lambda root: changed)
    ids = ["tests/a.py::soak_one"]

    def gate():
        return _soak_session(tmp_path, marks="gate", tiers="gate", collected=["g"],
                             passed=["g"])

    status, plugin = gate()
    assert status == pytest.ExitCode.TESTS_FAILED and "ledger.py" in plugin.problem
    _soak_session(tmp_path, marks="soak", tiers="soak", collected=ids, passed=ids)
    assert gate()[0] == pytest.ExitCode.OK
    tree["now"] = "tree-b"  # never certified
    assert gate()[0] == pytest.ExitCode.TESTS_FAILED
    changed[:] = ["README.md"]
    assert gate()[0] == pytest.ExitCode.OK
    # Certified at the start, edited before the end: no one tree's result.
    tree["now"] = "tree-a"
    changed[:] = ["factorylab/kernel/ledger.py"]
    from types import SimpleNamespace

    plugin = conftest._SoakRequirement(tmp_path)
    plugin._record = lambda: tmp_path / "passes"
    session = SimpleNamespace(
        config=SimpleNamespace(args_source=pytest.Config.ArgsSource.TESTPATHS,
                               option=SimpleNamespace(keyword="", markexpr="gate")),
        exitstatus=pytest.ExitCode.OK)
    plugin.pytest_sessionstart(session)
    plugin.pytest_runtest_logreport(SimpleNamespace(when="call", passed=True, nodeid="g",
                                                    factorylab_tier="gate"))
    tree["now"] = "tree-c"
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert "changed while the gate ran" in plugin.problem
