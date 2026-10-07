"""Duration strings such as 90s or 1h30m, as the --gate-timeout flag and the task file write them."""

import re

_UNIT_SECONDS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "μs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}

# "ms" is listed before "m" so the longer unit wins. [0-9] and not \d, which
# also matches digits from other scripts.
_PART = re.compile(r"([0-9]+\.?[0-9]*|\.[0-9]+)(ns|us|µs|μs|ms|s|m|h)")
_WHOLE = re.compile(f"(?:{_PART.pattern})+")


def parse_duration(text: str) -> float:
    """Parse a duration string such as 90s or 1h30m into seconds."""
    sign = -1.0 if text.startswith("-") else 1.0
    body = text[1:] if text[:1] in ("+", "-") else text
    if body == "0":
        return 0.0

    if _WHOLE.fullmatch(body) is None:
        raise ValueError(f"invalid duration {text!r}")

    # Added one at a time: sum() compensates its rounding on Python 3.12 and
    # up, which would change the last digit of a long duration.
    total = 0.0
    for number, unit in _PART.findall(body):
        total += float(number) * _UNIT_SECONDS[unit]

    return sign * total


def format_duration(seconds: float) -> str:
    """Format seconds as 1h2m3.5s, rounded to the millisecond."""
    millis = int(seconds * 1000 + 0.5)
    if millis == 0:
        return "0s"

    if millis < 1000:
        return f"{millis}ms"

    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole, fraction = divmod(rest, 1000)
    secs = str(whole)
    if fraction:
        secs += "." + f"{fraction:03d}".rstrip("0")

    if hours:
        return f"{hours}h{minutes}m{secs}s"

    if minutes:
        return f"{minutes}m{secs}s"

    return f"{secs}s"


def format_elapsed(seconds: float) -> str:
    """Format seconds for the view: 12.3s under a minute, else 1h 2m 3s with whole seconds."""
    tenths = int(seconds * 10 + 0.5)
    # Compared in tenths so 59.96 carries into a minute and never prints 60.0s.
    if tenths < 600:
        return f"{tenths / 10:.1f}s"

    whole = int(seconds + 0.5)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"

    return f"{minutes}m {secs}s"
