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


def test_a_change_to_the_kernel_pricing_or_recovery_requires_soak_and_a_doc_does_not():
    changed = ["factorylab/kernel/ledger.py", "factorylab/runtime/settled.py",
               "factorylab/runtime/resume.py", "factorylab/runtime/loop.py",
               "factorylab/runtime/governance.py", "tests/gauntlet/test_thrash.py",
               "factorylab/learners/exp3.py", "README.md", "factorylab/world/venice.py",
               "tests/runtime/test_resume.py"]
    assert soak_required(changed) == changed[:7]


def test_a_whole_gate_on_a_soak_required_change_fails_until_soak_passed_on_its_tree(
        tmp_path, monkeypatch):
    """The soak tier, enforced: a whole soak run that passes records its tree; a whole
    gate run on a tree that changed a soak-required path fails until that record holds
    its tree, and a change to no such path passes."""
    from types import SimpleNamespace

    from tests import conftest

    trees = iter(["tree-a", "tree-a", "tree-a", "tree-a", "tree-b", "tree-b"])
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: next(trees))
    changed = ["factorylab/kernel/ledger.py", "README.md"]
    monkeypatch.setattr(conftest, "_changed_since_main", lambda root: changed)

    def run(marks, tiers):
        plugin = conftest._SoakRequirement(tmp_path)
        plugin._record = lambda: tmp_path / "passes"
        config = SimpleNamespace(
            args_source=pytest.Config.ArgsSource.TESTPATHS,
            option=SimpleNamespace(keyword="", markexpr=marks),
            pluginmanager=SimpleNamespace(get_plugin=lambda name: SimpleNamespace(tiers=tiers)))
        session = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.OK)
        plugin.pytest_sessionstart(session)
        plugin.pytest_sessionfinish(session)
        return session.exitstatus, plugin

    status, plugin = run("gate", {"gate": 900.0})
    assert status == pytest.ExitCode.TESTS_FAILED and "ledger.py" in plugin.problem
    status, _ = run("soak", {"soak": 1000.0})  # records tree-a
    assert status == pytest.ExitCode.OK and (tmp_path / "passes").read_text() == "tree-a\n"
    status, _ = run("gate", {"gate": 900.0})
    assert status == pytest.ExitCode.OK
    status, _ = run("gate", {"gate": 900.0})  # tree-b: soak never passed on it
    assert status == pytest.ExitCode.TESTS_FAILED
    changed[:] = ["README.md"]
    assert run("gate", {"gate": 900.0})[0] == pytest.ExitCode.OK
