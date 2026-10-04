import signal

import pytest

from alr import gocompat


@pytest.mark.parametrize(
    ("text", "quoted"),
    [
        ("make test", '"make test"'),
        ('echo "hi"', '"echo \\"hi\\""'),
        ("test -f a\\b", '"test -f a\\\\b"'),
        ("héllo", '"héllo"'),
        ("a\tb", '"a\\tb"'),
        ("", '""'),
    ],
)
def test_goquote(text: str, quoted: str) -> None:
    assert gocompat.goquote(text) == quoted


def test_os_error_text_matches_go_wording() -> None:
    exc = FileNotFoundError(2, "No such file or directory")
    assert (
        gocompat.os_error_text("open", "tasks.yaml", exc)
        == "open tasks.yaml: no such file or directory"
    )


@pytest.mark.parametrize(
    ("returncode", "text"),
    [
        (0, None),
        (1, "exit status 1"),
        (127, "exit status 127"),
        (-signal.SIGKILL, "signal: killed"),
        (-signal.SIGTERM, "signal: terminated"),
        (-signal.SIGINT, "signal: interrupt"),
    ],
)
def test_exit_error(returncode: int, text: str | None) -> None:
    assert gocompat.exit_error(returncode) == text


def test_not_found_error() -> None:
    assert (
        gocompat.not_found_error("claude")
        == 'exec: "claude": executable file not found in $PATH'
    )


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("90s", 90.0),
        ("10m", 600.0),
        ("1h30m", 5400.0),
        ("1.5h", 5400.0),
        ("500ms", 0.5),
        ("0", 0.0),
        ("-5s", -5.0),
        ("1m30.5s", 90.5),
    ],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert gocompat.parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "30", "ten", "5 s", "1d", "s", "1.2.3s"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        gocompat.parse_duration(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0s"),
        (0.0004, "0s"),
        (0.015, "15ms"),
        (1.0, "1s"),
        (2.5, "2.5s"),
        (1.234, "1.234s"),
        (60.0, "1m0s"),
        (90.5, "1m30.5s"),
        (3600.0, "1h0m0s"),
        (3723.0, "1h2m3s"),
    ],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert gocompat.format_duration(seconds) == text
