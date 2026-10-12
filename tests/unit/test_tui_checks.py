"""check_folder: what the folder dialog accepts."""

from pathlib import Path

import pytest
from helpers import write_nested_file

from alr.errors import (
    RefusedError,
    TaskFileError,
    UsageError,
)
from alr.tui.checks import check_folder, check_sandbox

TASKS = "shared:\n  prompt: p\ntasks:\n  - id: 1\n    name: A\n    prompt: p\n"


async def test_check_folder_returns_the_tasks_and_the_folders_sandbox(
    work_dir: Path,
) -> None:
    folder = work_dir / "run" / "feat"
    write_nested_file(folder / "tasks.yaml", TASKS)
    write_nested_file(folder / "sandbox.json", "{}")

    check = await check_folder(str(folder))

    assert check.tasks_file == str(folder / "tasks.yaml")
    assert check.sandbox_file == str(folder / "sandbox.json")
    assert [task.id for task in check.task_list.tasks] == [1]


async def test_check_folder_without_a_sandbox_json_has_no_sandbox_file(
    work_dir: Path,
) -> None:
    write_nested_file(work_dir / "run" / "tasks.yaml", TASKS)

    assert (await check_folder(str(work_dir / "run"))).sandbox_file == ""


async def test_check_folder_without_tasks_yaml_is_a_usage_error(work_dir: Path) -> None:
    with pytest.raises(UsageError, match="no tasks.yaml"):
        await check_folder(str(work_dir / "run"))


async def test_check_folder_with_a_broken_task_file_is_an_error(work_dir: Path) -> None:
    write_nested_file(work_dir / "run" / "tasks.yaml", "tasks: [")

    with pytest.raises(TaskFileError):
        await check_folder(str(work_dir / "run"))


async def test_check_folder_accepts_a_failed_task_so_the_view_can_offer_a_reset(
    work_dir: Path,
) -> None:
    failed = TASKS + "    state: failed\n    error: x\n"
    write_nested_file(work_dir / "run" / "tasks.yaml", failed)

    check = await check_folder(str(work_dir / "run"))

    assert [task.state for task in check.task_list.tasks] == ["failed"]


def test_check_sandbox_accepts_a_json_object(tmp_path: Path) -> None:
    path = tmp_path / "sandbox.json"
    path.write_text("{}", encoding="utf-8")

    check_sandbox(str(path))


@pytest.mark.parametrize("text", ["not json", "[]"], ids=["not json", "not an object"])
def test_check_sandbox_rejects_a_bad_settings_file(tmp_path: Path, text: str) -> None:
    path = tmp_path / "sandbox.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(RefusedError):
        check_sandbox(str(path))


def test_check_sandbox_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RefusedError):
        check_sandbox(str(tmp_path / "nope.json"))
