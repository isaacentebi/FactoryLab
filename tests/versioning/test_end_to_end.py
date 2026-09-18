import ast
from pathlib import Path


def test_package_import_boundary():
    package = Path(__file__).resolve().parents[2] / "factorylab" / "versioning"
    for source in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith("factorylab."):
                    assert (name in ("factorylab.kernel.events", "factorylab.charter.controller")
                            or name.startswith("factorylab.versioning"))
