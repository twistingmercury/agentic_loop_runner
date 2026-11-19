"""engine, plain, record, and stream stay apart, so each path can be read on its own."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "milhouse"

FORBIDDEN = {
    "engine": ("milhouse.plain", "milhouse.stream", "milhouse.tui", "milhouse.record"),
    "plain": ("milhouse.stream", "milhouse.tui"),
    "record": (
        "milhouse.engine",
        "milhouse.plain",
        "milhouse.stream",
        "milhouse.tui",
        "milhouse.cli",
    ),
    "stream": ("milhouse.plain", "milhouse.tui"),
    "tui": ("milhouse.plain", "milhouse.cli"),
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


def test_no_module_runs_git() -> None:
    # milhouse is git-blind: a commit is an action the user writes, never milhouse's own call.
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert '"git"' not in source, f"{path} names git as an executable"
