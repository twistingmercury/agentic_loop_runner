import signal

from alr.engine.stop import Stop


def test_a_stop_defaults_to_sigint() -> None:
    assert Stop().exit_code() == 130


def test_a_sigterm_stop_exits_143() -> None:
    assert Stop(signal.SIGTERM).exit_code() == 143
