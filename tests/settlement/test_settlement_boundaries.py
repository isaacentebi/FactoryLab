import ast
import sys
from pathlib import Path


def test_settlement_imports_only_itself_kernel_and_standard_library():
    package = Path(__file__).resolve().parents[2] / "factorylab" / "settlement"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, path
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.startswith("factorylab."):
                    assert module.split(".")[1] in {"kernel", "settlement"}, (path, module)
                else:
                    assert module.split(".")[0] in sys.stdlib_module_names, (path, module)


def test_settlement_has_no_module_level_mutable_containers():
    package = Path(__file__).resolve().parents[2] / "factorylab" / "settlement"
    for path in package.glob("*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                assert not isinstance(node.value, (ast.List, ast.Dict, ast.Set)), path
