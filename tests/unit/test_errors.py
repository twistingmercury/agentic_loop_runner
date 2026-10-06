import pytest

from alr.errors import RunCancelledError, RunError, quote


@pytest.mark.parametrize(
    ("text", "quoted"),
    [
        ('echo "hi"', '"echo \\"hi\\""'),
        ("héllo", '"héllo"'),
    ],
)
def test_quote(text: str, quoted: str) -> None:
    assert quote(text) == quoted


def test_a_cancelled_run_is_a_run_error() -> None:
    assert issubclass(RunCancelledError, RunError)
