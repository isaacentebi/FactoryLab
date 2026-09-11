import ast
from pathlib import Path


def test_kernel_has_no_upper_layer_imports_or_background_threads():
    kernel = Path(__file__).resolve().parents[2] / "factorylab" / "kernel"
    forbidden = {"cortex", "world", "runtime", "threading", "concurrent", "multiprocessing"}
    for path in kernel.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or "", *(alias.name for alias in node.names)]
            else:
                continue
            assert not any(set(module.split(".")) & forbidden for module in modules), path


def test_kernel_has_no_module_level_mutable_containers():
    kernel = Path(__file__).resolve().parents[2] / "factorylab" / "kernel"
    for path in kernel.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                assert not isinstance(node.value, (ast.List, ast.Dict, ast.Set)), path
