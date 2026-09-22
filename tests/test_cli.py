import pytest

from alr.cli import get_args


def test_get_args_dry_run_true():
    args = get_args(["-t", "test.yaml", "-d"])

    assert args.tasks == "test.yaml"
    assert args.dry_run
    assert args.prompt is None


def tests_get_args_dry_run_false():
    args = get_args(["-t", "test.yaml", "-p", "prompt.md"])

    assert args.tasks == "test.yaml"
    assert args.prompt == "prompt.md"
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
