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
_DURATION_PART = re.compile(r"([0-9]+\.?[0-9]*|\.[0-9]+)(ns|us|µs|μs|ms|s|m|h)")


def parse_duration(text: str) -> float:
    """Parse a duration string such as 90s or 1h30m into seconds."""
    sign = -1.0 if text.startswith("-") else 1.0
    body = text[1:] if text[:1] in ("+", "-") else text
    if body == "0":
        return 0.0

    if body == "":
        raise ValueError(f"invalid duration {text!r}")

    total = 0.0
    position = 0
    while position < len(body):
        part = _DURATION_PART.match(body, position)
        if part is None:
            raise ValueError(f"invalid duration {text!r}")

        total += float(part.group(1)) * _UNIT_SECONDS[part.group(2)]
        position = part.end()

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
