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
    SOAK_COLLECTED,
    SOAK_INVENTORY,
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


def test_any_change_but_a_markdown_file_requires_soak():
    """One rule (Sol on #179: a world's toml changed a checkpoint's retained history and
    required nothing): every changed path requires soak except a ``*.md``."""
    required = ["worlds/scripted.toml", "pyproject.toml", "uv.lock", "tests/conftest.py",
                "tests/runtime/test_checkpoint_plateau.py", "scripts/fastloop.py",
                "deploy/start.sh", "factorylab/runtime/clockwork.py",
                "tests/soak_inventory.txt"]
    exempt = ["README.md", "AGENTS.md", "docs/manifest.md", "deploy/README.md"]
    assert soak_required(required + exempt) == required


def test_the_soak_inventory_is_what_the_soak_marker_selects(request):
    """tests/soak_inventory.txt names, sorted, every test pytest's own collection of the
    whole repository marks soak: adding, renaming or removing a soak test without its
    inventory line fails here (a deliberate change is a reviewed diff of that file)."""
    collected = request.config.stash.get(SOAK_COLLECTED, None)
    if collected is None:
        pytest.skip("this session did not collect the whole repository")
    assert SOAK_INVENTORY.read_text().splitlines() == collected


def _report(nodeid, tier, *, when="call", outcome="passed", reported=None, **extra):
    """A test report as the conftest's makereport leaves it: ``reported`` is the id
    xdist reports (``…@group`` under loadgroup), ``nodeid`` the collected one."""
    from types import SimpleNamespace

    return SimpleNamespace(when=when, passed=outcome == "passed",
                           failed=outcome == "failed", skipped=outcome == "skipped",
                           nodeid=reported or nodeid, factorylab_nodeid=nodeid,
                           factorylab_tier=tier, **extra)


def _soak_session(tmp_path, *, marks, tiers, passed, status=None,
                  args=("-m", "soak", "-n", "2"), environ=None, reports=(), **options):
    from types import SimpleNamespace

    from tests import conftest

    plugin = conftest._SoakRequirement(tmp_path)
    plugin._record = lambda: tmp_path / "passes"
    option = SimpleNamespace(**{"keyword": "", "markexpr": marks, **options})
    config = SimpleNamespace(
        args_source=pytest.Config.ArgsSource.TESTPATHS, option=option,
        invocation_params=SimpleNamespace(args=tuple(args)))
    session = SimpleNamespace(config=config, exitstatus=status or pytest.ExitCode.OK)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(conftest.os, "environ", environ or {})
        plugin.pytest_sessionstart(session)
        for nodeid in passed:
            plugin.pytest_runtest_logreport(_report(nodeid, tiers))
        if not passed and not reports:  # a setup report: the tier ran, no body did
            plugin.pytest_runtest_logreport(_report("t", tiers, when="setup"))
        for report in reports:
            plugin.pytest_runtest_logreport(report)
        plugin.pytest_sessionfinish(session)
    return session.exitstatus, plugin


def test_only_the_whole_soak_inventory_passing_certifies_its_tree(tmp_path, monkeypatch):
    """A soak pass is recorded only for exactly ``-m soak`` (with ``-n`` and xdist's
    ``-p``), no PYTEST_ADDOPTS or PYTEST_PLUGINS, no narrowing option in effect from any
    source, and the tests whose call passed exactly the inventory, on one tree. Sol's
    narrowings on #179 (ini python_functions or testpaths, collect_ignore, a plugin
    that drops tests, addopts=--lf) leave a passing run short of the inventory."""
    from tests import conftest

    monkeypatch.setattr(conftest, "_tree_hash", lambda root: "tree-a")
    ids = ["tests/a.py::soak_one", "tests/a.py::soak_two"]
    inventory = tmp_path / "inventory.txt"
    inventory.write_text("\n".join(ids) + "\n")
    monkeypatch.setattr(conftest, "SOAK_INVENTORY", inventory)
    record = tmp_path / "passes"
    refused = [
        dict(passed=ids[:1]),  # python_functions, testpaths, collect_ignore narrowed it
        dict(passed=[*ids, "tests/a.py::soak_three"]),  # a test the inventory lacks
        dict(passed=[]), dict(status=pytest.ExitCode.TESTS_FAILED),
        dict(environ={"PYTEST_PLUGINS": "dropper"}),
        dict(environ={"PYTEST_ADDOPTS": "-o python_functions=soak_one"}),
        dict(lf=True), dict(failedfirst=True), dict(stepwise=True),
        dict(deselect=["tests/a.py::soak_two"]), dict(ignore=["tests/b.py"]),
        dict(ignore_glob=["tests/b*"]), dict(keyword="one"), dict(collectonly=True),
        dict(override_ini=["python_functions=soak_one"]),
        dict(args=("-m", "soak", "tests/a.py")), dict(args=("-m", "soak or slow")),
        # Sol's final review: a soak test missing from the inventory that is skipped or
        # xfailed, and an inventoried one that xpasses.
        dict(reports=[_report("tests/a.py::soak_new", "soak", when="setup",
                              outcome="skipped")]),
        dict(reports=[_report("tests/a.py::soak_new", "soak", outcome="skipped",
                              wasxfail="")]),
        dict(passed=ids[:1], reports=[_report(ids[1], "soak", wasxfail="")]),
    ]
    for case in refused:
        _, plugin = _soak_session(tmp_path, **{"marks": "soak", "tiers": "soak",
                                               "passed": ids, **case})
        assert not record.exists(), case
        # A -k run is not whole: it is not judged at all, so it says nothing.
        assert (plugin.note or "soak run not recorded").startswith("soak run not recorded")
    trees = iter(["tree-a", "tree-b"])  # edited while it ran
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: next(trees))
    _soak_session(tmp_path, marks="soak", tiers="soak", passed=ids)
    assert not record.exists()
    monkeypatch.setattr(conftest, "_tree_hash", lambda root: "tree-a")
    for args in (("-m", "soak"), ("-m", "soak", "-n", "2"), ("-m", "soak", "-n2"),
                 ("-m", "soak", "-p", "xdist.looponfail")):
        _soak_session(tmp_path, marks="soak", tiers="soak", passed=ids, args=args)
    assert record.read_text() == "tree-a\n" * 4
    # An id with a space is one inventory line; an xdist-grouped test is compared on its
    # collected id, not the ``@group`` one it reports under.
    spaced = ["tests/a.py::soak[slow world]", "tests/a.py::soak_grouped"]
    inventory.write_text("\n".join(spaced) + "\n")
    _soak_session(tmp_path, marks="soak", tiers="soak", passed=[], reports=[
        _report(spaced[0], "soak"),
        _report(spaced[1], "soak", reported=spaced[1] + "@long_world")])
    assert record.read_text() == "tree-a\n" * 5


def test_a_rename_is_both_its_endpoints_and_a_path_is_read_whole(tmp_path):
    """Sol's final review: ``archive.py`` renamed to ``docs/archive.md`` showed only its
    Markdown destination, and names with spaces or accents were split or quoted. Both
    endpoints of a rename are changes, and every path is read whole."""
    import subprocess

    from tests import conftest

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c",
                        "user.email=t@t", *args], check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "archive.py").write_text("x = 1\n" * 20)
    git("add", "archive.py")
    git("commit", "-q", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    (tmp_path / "docs").mkdir()
    git("mv", "archive.py", "docs/archive.md")
    for name in ("Guide for developers.md", "café.md"):
        (tmp_path / "docs" / name).write_text("words\n")
    changed = conftest._changed_since_main(tmp_path)
    assert changed == ["archive.py", "docs/Guide for developers.md", "docs/archive.md",
                       "docs/café.md"]
    assert soak_required(changed) == ["archive.py"]


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
        return _soak_session(tmp_path, marks="gate", tiers="gate", passed=["g"],
                             args=("-m", "gate", "-n", "2"))

    status, plugin = gate()
    assert status == pytest.ExitCode.TESTS_FAILED and "ledger.py" in plugin.problem
    inventory = tmp_path / "inventory.txt"
    inventory.write_text("\n".join(ids) + "\n")
    monkeypatch.setattr(conftest, "SOAK_INVENTORY", inventory)
    _soak_session(tmp_path, marks="soak", tiers="soak", passed=ids)
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
                               option=SimpleNamespace(keyword="", markexpr="gate"),
                               invocation_params=SimpleNamespace(args=("-m", "gate"))),
        exitstatus=pytest.ExitCode.OK)
    plugin.pytest_sessionstart(session)
    plugin.pytest_runtest_logreport(SimpleNamespace(when="call", passed=True, failed=False,
                                                    nodeid="g", factorylab_tier="gate"))
    tree["now"] = "tree-c"
    plugin.pytest_sessionfinish(session)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    assert "changed while the gate ran" in plugin.problem
