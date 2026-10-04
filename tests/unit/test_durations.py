import pytest

from alr.durations import format_duration, parse_duration


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
    ["", "30", "ten", "5 s", "1d", "s", "1.2.3s", "٣s", "１０m"],
    ids=[
        "empty",
        "unitless",
        "word",
        "space",
        "day",
        "unit only",
        "two dots",
        "arabic digit",
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
