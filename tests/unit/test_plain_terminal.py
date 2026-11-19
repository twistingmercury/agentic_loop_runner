"""Plain mode's output must not end a run whose terminal has gone while SIGHUP is ignored."""

import io
import os
import pty
import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from milhouse.plain.terminal import _VanishingTerminal, outliving_its_terminal


@contextmanager
def _ignoring_sighup() -> Iterator[None]:
    before = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, before)


def _terminal_text(slave: int) -> io.TextIOWrapper:
    """The slave end of a pseudo-terminal as a text stream, so that it reports a terminal."""
    return io.TextIOWrapper(io.FileIO(slave, "w", closefd=False), line_buffering=True)


def test_a_terminal_that_has_gone_does_not_fail_the_run_that_writes_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = pty.openpty()
    out = _terminal_text(slave)
    err = _terminal_text(slave)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        with _ignoring_sighup(), outliving_its_terminal():
            os.close(master)
            for number in range(200):
                print(f"line {number}", flush=True)
                print(f"error {number}", file=sys.stderr, flush=True)
    finally:
        os.close(slave)


def test_the_streams_are_put_back_after_the_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = pty.openpty()
    out = _terminal_text(slave)
    err = _terminal_text(slave)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        with _ignoring_sighup(), outliving_its_terminal():
            assert sys.stdout is not out
            assert sys.stderr is not err

        assert sys.stdout is out
        assert sys.stderr is err
    finally:
        os.close(master)
        os.close(slave)


def test_a_live_terminal_still_gets_the_text() -> None:
    master, slave = pty.openpty()
    out = _terminal_text(slave)
    err = _terminal_text(slave)
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        with _ignoring_sighup(), outliving_its_terminal():
            print("hello", flush=True)
    finally:
        sys.stdout, sys.stderr = saved

    try:
        assert b"hello" in os.read(master, 100)
    finally:
        os.close(master)
        os.close(slave)


def test_the_streams_are_left_alone_when_sighup_is_not_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master, slave = pty.openpty()
    out = _terminal_text(slave)
    monkeypatch.setattr(sys, "stdout", out)
    try:
        with outliving_its_terminal():
            assert sys.stdout is out
    finally:
        os.close(master)
        os.close(slave)


def test_a_stream_that_is_not_a_terminal_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    with _ignoring_sighup(), outliving_its_terminal():
        assert sys.stdout is plain


def test_the_first_write_to_a_gone_terminal_is_reported_done_and_later_ones_dropped() -> (
    None
):
    master, slave = pty.openpty()
    os.close(master)
    try:
        raw = _VanishingTerminal(slave)

        assert raw.write(b"abc") == 3
        assert raw.write(b"defg") == 4
    finally:
        os.close(slave)


def test_a_write_that_fails_for_another_reason_is_raised() -> None:
    master, slave = pty.openpty()
    raw = _VanishingTerminal(slave)
    os.close(slave)
    try:
        with pytest.raises(OSError):
            raw.write(b"abc")
    finally:
        os.close(master)
