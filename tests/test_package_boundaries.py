import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "factorylab"


def _imported(package: str) -> set[str]:
    """Return every module name a package imports; a relative import names the package."""
    modules = set()
    for path in (PACKAGE / package).glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = f"factorylab.{package}." if node.level else ""
                modules.add(prefix + (node.module or ""))
    return modules


def test_world_imports_nothing_from_the_factory_but_the_one_money_conversion():
    """A rail may price a call; it may not reach the wallet, the diary or the loop."""
    internal = {m for m in _imported("world") if m.startswith("factorylab.")}
    assert {m for m in internal if not m.startswith("factorylab.world")} == {
        "factorylab.kernel.money"
    }


def test_learners_import_nothing_from_the_factory():
    """A learner is replaceable by the population, so it can know nothing about physics."""
    internal = {m for m in _imported("learners") if m.startswith("factorylab.")}
    assert not {m for m in internal if not m.startswith("factorylab.learners")}


def test_learners_import_only_the_standard_library():
    outside = {m.split(".")[0] for m in _imported("learners") if not m.startswith("factorylab.")}
    assert outside <= sys.stdlib_module_names
