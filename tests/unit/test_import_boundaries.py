"""engine, plain, and stream stay apart, so each path can be read on its own."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "alr"

FORBIDDEN = {
    "engine": ("alr.plain", "alr.stream", "alr.tui"),
    "plain": ("alr.stream", "alr.tui"),
    "stream": ("alr.plain", "alr.tui"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)

        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)

    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_does_not_import_across_the_line(package: str) -> None:
    for path in sorted((SRC / package).rglob("*.py")):
        for name in imported_modules(path):
            for banned in FORBIDDEN[package]:
                assert not (name == banned or name.startswith(banned + ".")), (
                    f"{path} imports {name}"
                )


def test_engine_never_prints() -> None:
    for path in sorted((SRC / "engine").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "print(" not in source, f"{path} prints"
        assert "sys.stdout" not in source, f"{path} writes to stdout"
        assert "sys.stderr" not in source, f"{path} writes to stderr"
