import shutil
import subprocess
from pathlib import Path

import pytest

from alr import looper
from alr.tasks import parse_tasks, save_tasks, TaskState

TEST_YAML = Path(__file__).parent / "data" / "tasks.yaml"
SHARED_PROMPT = "Shared prompt text"


@pytest.fixture
def yaml_file(tmp_path):
    """A copy of the test tasks with no failed tasks: completed, pending, pending."""
    path = tmp_path / "tasks.yaml"
    shutil.copy(TEST_YAML, path)

    task_list = parse_tasks(path)
    task_list.tasks[1].state = TaskState.PENDING
    save_tasks(path, task_list)

    return path


def fake_claude(monkeypatch, returncode=0, raises=None):
    """Replace subprocess.run so no real agent launches; record each prompt sent."""
    prompts = []

    def fake_run(args, input, **kwargs):
        prompts.append(input)
        if raises:
            raise raises
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr="boom")

    monkeypatch.setattr(looper.subprocess, "run", fake_run)
    return prompts


def test_start_runs_pending_and_skips_completed(yaml_file, monkeypatch):
    prompts = fake_claude(monkeypatch)

    looper.start(SHARED_PROMPT, parse_tasks(yaml_file), yaml_file)

    # Task 1 was already completed, so only tasks 2 and 3 launch, in list order.
    assert len(prompts) == 2
    assert "2: Write hello world app" in prompts[0]
    assert "3: Standup project structure" in prompts[1]


def test_start_combines_shared_and_task_prompt(yaml_file, monkeypatch):
    prompts = fake_claude(monkeypatch)
    task_list = parse_tasks(yaml_file)

    looper.start(SHARED_PROMPT, task_list, yaml_file)

    assert prompts[0] == f"{SHARED_PROMPT}\n\n{task_list.tasks[1]}\n"


def test_start_saves_completed_states(yaml_file, monkeypatch):
    fake_claude(monkeypatch)

    looper.start(SHARED_PROMPT, parse_tasks(yaml_file), yaml_file)

    saved = parse_tasks(yaml_file)
    assert [task.state for task in saved.tasks] == [TaskState.COMPLETED] * 3


def test_start_saves_failed_and_stops(yaml_file, monkeypatch):
    prompts = fake_claude(monkeypatch, returncode=1)

    with pytest.raises(ChildProcessError):
        looper.start(SHARED_PROMPT, parse_tasks(yaml_file), yaml_file)

    # The run stops at the first failure; task 3 never launches.
    assert len(prompts) == 1
    saved = parse_tasks(yaml_file)
    assert [task.state for task in saved.tasks] == [
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.PENDING,
    ]


def test_start_ctrl_c_leaves_task_pending(yaml_file, monkeypatch):
    fake_claude(monkeypatch, raises=KeyboardInterrupt())
    original = yaml_file.read_text()

    with pytest.raises(KeyboardInterrupt):
        looper.start(SHARED_PROMPT, parse_tasks(yaml_file), yaml_file)

    assert yaml_file.read_text() == original
