"""The tier harness in tests/conftest.py: which tests run a world, the check limit, and
the gate file budget.

A unit test misread as a world leaves the inner loop silently; a world misread as a
unit test is caught at run time by the CPU limit; a gate file that creeps past its
budget fails the run unless it is excepted by name. These pin all three.
"""

import ast
from pathlib import Path

from tests.conftest import (
    CHECK_WALL_CEILING_S,
    _check_limit_problem,
    _files_over_budget,
    _world_functions,
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

def via_run_world():
    run_world(m, events=5, seed=1)

def via_the_cli():
    main(["run", "--world", "scripted"])

def via_a_helper():
    via_run_world()

def imports_run_world_only():
    from factorylab.runtime.loop import run_world  # noqa: F401
'''


def test_only_a_running_loop_is_a_world_however_the_runtime_is_built():
    assert _world_functions(ast.parse(SOURCE)) == {
        "runs_its_loop", "runs_a_built_runtime", "via_run_world", "via_the_cli",
        "via_a_helper"}


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
    assert _check_limit_problem(1.9, 9.0, 2.0) is None
    assert "CPU" in _check_limit_problem(2.1, 2.1, 2.0)
    # A call that waits uses no CPU; the wall ceiling still fails it.
    assert "wall" in _check_limit_problem(0.1, CHECK_WALL_CEILING_S + 1, 2.0)
    assert _check_limit_problem(50.0, 50.0, None) is None


def test_a_gate_file_over_its_budget_fails_unless_it_is_excepted_by_name():
    spent = {"tests/a.py": 61.0, "tests/b.py": 59.0, "tests/c.py": 300.0}
    assert _files_over_budget(spent, 60.0, {"tests/c.py": "why it cannot be smaller"}) == {
        "tests/a.py": 61.0}
    assert _files_over_budget(spent, None, {}) == {}
