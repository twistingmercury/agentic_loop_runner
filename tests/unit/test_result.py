import pytest

from alr.engine.result import last_result_line, outcome, parse_result
from alr.tasks import COMPLETED, FAILED

EXIT_ERROR = "exit status 1"


@pytest.mark.parametrize(
    ("line", "want"),
    [
        pytest.param(
            '{"state":"completed","error":""}', (COMPLETED, ""), id="valid completed"
        ),
        pytest.param(
            '{"state":"failed","error":"tests did not pass"}',
            (FAILED, "tests did not pass"),
            id="valid failed with error",
        ),
        pytest.param(
            '{"state":"  COMPLETED  ","error":""}',
            (COMPLETED, ""),
            id="uppercase and surrounding whitespace state is normalized",
        ),
        pytest.param(
            '{"state":"failed","error":"  boom  "}',
            (FAILED, "boom"),
            id="error field is trimmed",
        ),
        pytest.param(
            '{"state":"completed","error":"","extra":"field","nested":{"a":1}}',
            (COMPLETED, ""),
            id="unknown fields are ignored",
        ),
        pytest.param("{state: completed}", None, id="invalid json"),
        pytest.param('["completed",""]', None, id="json array is not an object"),
        pytest.param('"completed"', None, id="json string is not an object"),
        pytest.param("42", None, id="json number is not an object"),
        pytest.param('{"state":"in_progress","error":""}', None, id="unknown state"),
        pytest.param('{"error":"boom"}', None, id="missing state"),
        pytest.param("", None, id="empty line"),
    ],
)
def test_parse_result(line: str, want: tuple[str, str] | None) -> None:
    assert parse_result(line) == want


# gralph decodes the line with Go's encoding/json; these pin what that decoder
# does where Python's json module would otherwise decide differently.
@pytest.mark.parametrize(
    ("line", "want"),
    [
        pytest.param('{"state":"completed"}', (COMPLETED, ""), id="missing error"),
        pytest.param(
            '{"state":"failed","error":null}', (FAILED, ""), id="null error is empty"
        ),
        pytest.param('{"state":null,"error":""}', None, id="null state is empty"),
        pytest.param(
            '{"State":"completed","ERROR":"x"}',
            (COMPLETED, "x"),
            id="keys match ignoring case",
        ),
        pytest.param(
            '{"state":"completed","State":"failed","error":"x"}',
            (FAILED, "x"),
            id="the last of two keys differing in case wins",
        ),
        pytest.param(
            '{"state":"completed","State":null}',
            (COMPLETED, ""),
            id="a null does not clear an earlier value",
        ),
        pytest.param('{"state":1,"error":""}', None, id="state is not a string"),
        pytest.param(
            '{"state":"failed","error":["boom"]}', None, id="error is not a string"
        ),
        pytest.param(
            '{"state":"completed","error":"","extra":NaN}',
            None,
            id="NaN is not valid json",
        ),
        pytest.param("null", None, id="json null is not a result"),
        pytest.param("[" * 100_000, None, id="deeply nested line"),
    ],
)
def test_parse_result_matches_go_decoding(
    line: str, want: tuple[str, str] | None
) -> None:
    assert parse_result(line) == want


@pytest.mark.parametrize(
    ("output", "want"),
    [
        pytest.param("first\nsecond\n", "second", id="plain last line"),
        pytest.param(
            "result\n\n\n   \n", "result", id="trailing blank lines are skipped"
        ),
        pytest.param("first\r\nsecond\r\n", "second", id="crlf line endings"),
        pytest.param(
            '```json\n{"state":"completed","error":""}\n```\n',
            '{"state":"completed","error":""}',
            id="closing fence is skipped",
        ),
        pytest.param(
            "no newline at all", "no newline at all", id="no trailing newline"
        ),
        pytest.param("", "", id="empty output"),
    ],
)
def test_last_result_line(output: str, want: str) -> None:
    assert last_result_line(output) == want


@pytest.mark.parametrize(
    ("run_error", "last_line", "want_state", "want_error"),
    [
        pytest.param(
            None,
            '{"state":"completed","error":""}',
            COMPLETED,
            "",
            id="zero exit, completed result",
        ),
        pytest.param(
            None,
            '{"state":"failed","error":"lint failed"}',
            FAILED,
            "lint failed",
            id="zero exit, failed result with error",
        ),
        pytest.param(
            None,
            '{"state":"failed","error":""}',
            FAILED,
            "session reported failed with no error",
            id="zero exit, failed result with blank error",
        ),
        pytest.param(
            None,
            "",
            FAILED,
            "no valid result line in session output",
            id="zero exit, missing result line",
        ),
        pytest.param(
            None,
            "not json",
            FAILED,
            "no valid result line in session output",
            id="zero exit, invalid result line",
        ),
        pytest.param(
            EXIT_ERROR,
            '{"state":"failed","error":"tests failed: 3 cases"}',
            FAILED,
            "tests failed: 3 cases",
            id="non-zero exit, valid result with error",
        ),
        pytest.param(
            EXIT_ERROR,
            '{"state":"completed","error":"but actually not"}',
            FAILED,
            "but actually not",
            id="non-zero exit, valid completed result with error still wins",
        ),
        pytest.param(
            EXIT_ERROR,
            '{"state":"failed","error":""}',
            FAILED,
            "exit status 1",
            id="non-zero exit, valid result with blank error falls back to runErr",
        ),
        pytest.param(
            EXIT_ERROR,
            "",
            FAILED,
            "exit status 1",
            id="non-zero exit, no result line falls back to runErr",
        ),
    ],
)
def test_outcome(
    run_error: str | None, last_line: str, want_state: str, want_error: str
) -> None:
    assert outcome(run_error, last_line) == (want_state, want_error)


def test_parse_result_replaces_a_lone_surrogate_escape() -> None:
    # A lone surrogate cannot be written as UTF-8, so it must not reach the saved error.
    line = '{"state":"failed","error":"bad \\ud800 text"}'

    assert parse_result(line) == (FAILED, "bad \ufffd text")


def test_parse_result_keeps_a_surrogate_pair_escape() -> None:
    line = '{"state":"failed","error":"ok \\ud83d\\ude00"}'

    assert parse_result(line) == (FAILED, "ok \U0001f600")
