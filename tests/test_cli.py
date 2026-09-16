import pytest

from alr.cli import get_args


def test_get_args_dry_run_true():
    args = get_args(["-t", "test.yaml", "-d"])

    assert args.tasks == "test.yaml"
    assert args.dry_run
    assert args.prompt is None
    assert args.agent is None


def tests_get_args_dry_run_false():
    args = get_args(["-t", "test.yaml", "-p", "prompt.md", "-a", "claude"])

    assert args.tasks == "test.yaml"
    assert args.prompt == "prompt.md"
    assert args.agent == "claude"
    assert not args.dry_run


def test_missing_prompt_flag_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        get_args(["-t", "test.yaml"])

    assert exc.value.code == 2
    assert "--prompt" in capsys.readouterr().err


def test_missing_tasks_flag_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        get_args(["-p", "prompt.md"])

    assert exc.value.code == 2
    assert "--tasks" in capsys.readouterr().err


def test_missing_agent_flag_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        get_args(["-t", "test.yaml", "-p", "prompt.md"])

    assert exc.value.code == 2
    assert "--agent" in capsys.readouterr().err


def test_invalid_agent_flag_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        get_args(["-t", "test.yaml", "-p", "prompt.md", "-a", "deepseek"])

    assert exc.value.code == 2
    assert "--agent" in capsys.readouterr().err
