import pytest

from alr.durations import format_duration, format_elapsed, parse_duration


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
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "text",
    ["", "30", "1d", "1.2.3s", "１０m"],
    ids=[
        "empty",
        "unitless",
        "day",
        "two dots",
        "fullwidth digit",
    ],
)
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


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
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.0, "0.0s"),
        (0.4, "0.4s"),
        (12.3, "12.3s"),
        (59.9, "59.9s"),
        (59.96, "1m 0s"),
        (60.0, "1m 0s"),
        (90.0, "1m 30s"),
        (2703.0, "45m 3s"),
        (3599.6, "1h 0m 0s"),
        (3725.5, "1h 2m 6s"),
        (7200.0, "2h 0m 0s"),
        (3605.0, "1h 0m 5s"),
        (-1, "0.0s"),
        (-0.04, "0.0s"),
        (float("nan"), "0.0s"),
        (float("inf"), "0.0s"),
        (float("-inf"), "0.0s"),
    ],
)
def test_format_elapsed(seconds: float, text: str) -> None:
    assert format_elapsed(seconds) == text
