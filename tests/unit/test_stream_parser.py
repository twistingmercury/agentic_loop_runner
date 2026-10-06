"""Port of gralph's TestParseStreamLine, plus the malformed shapes Python must survive."""

import pytest

from alr.stream.parser import parse_stream_line

DONE = '{"state": "completed", "error": ""}'


def assistant(*blocks: str) -> str:
    return '{"type":"assistant","message":{"content":[' + ",".join(blocks) + "]}}"


def tool(name: str, tool_input: str) -> str:
    return f'{{"type":"tool_use","name":"{name}","input":{tool_input}}}'


ACTIVITY_CASES = [
    pytest.param(
        assistant('{"type":"text","text":"Reading the spec."}'),
        ["Reading the spec."],
        id="text block single line",
    ),
    pytest.param(
        assistant('{"type":"text","text":"First line.\\n\\n   \\nSecond line.\\n"}'),
        ["First line.", "Second line."],
        id="text block multi-line skips blank lines",
    ),
    pytest.param(
        assistant(tool("Bash", '{"command":"make test"}')),
        ["→ Bash make test"],
        id="Bash tool uses command",
    ),
    pytest.param(
        assistant(tool("Bash", '{"command":"cat <<EOF > f\\nhello\\nEOF"}')),
        ["→ Bash cat <<EOF > f"],
        id="multi-line Bash command is cut to its first line",
    ),
    pytest.param(
        assistant(tool("Read", '{"file_path":"/repo/a.go"}')),
        ["→ Read /repo/a.go"],
        id="Read tool uses file_path",
    ),
    pytest.param(
        assistant(tool("Edit", '{"file_path":"/repo/b.go","old_string":"x"}')),
        ["→ Edit /repo/b.go"],
        id="Edit tool uses file_path",
    ),
    pytest.param(
        assistant(tool("Write", '{"file_path":"/repo/c.go","content":"y"}')),
        ["→ Write /repo/c.go"],
        id="Write tool uses file_path",
    ),
    pytest.param(
        assistant(tool("Grep", '{"pattern":"func main"}')),
        ["→ Grep func main"],
        id="Grep tool uses pattern",
    ),
    pytest.param(
        assistant(tool("Glob", '{"pattern":"**/*.go"}')),
        ["→ Glob **/*.go"],
        id="Glob tool uses pattern",
    ),
    pytest.param(
        assistant(tool("TodoWrite", '{"command":"ignored","todos":[]}')),
        ["→ TodoWrite"],
        id="unknown tool has no target and no trailing space",
    ),
    pytest.param(
        assistant(tool("Bash", '"make test"')),
        ["→ Bash"],
        id="tool input of the wrong kind has no target",
    ),
    pytest.param(
        assistant(tool("Bash", "{}")),
        ["→ Bash"],
        id="known tool with missing target has no trailing space",
    ),
    pytest.param(
        assistant('{"type":"text","text":"x\\ud800y"}'),
        ["x\ufffdy"],
        id="lone surrogate in text becomes U+FFFD",
    ),
    pytest.param(
        assistant(tool("Read", '{"file_path":"a\\udfffb"}')),
        ["→ Read a\ufffdb"],
        id="lone surrogate in a tool target becomes U+FFFD",
    ),
    pytest.param(
        assistant('{"type":"text","text":"pair \\ud83d\\ude00"}'),
        ["pair \U0001f600"],
        id="surrogate pair in text is kept",
    ),
    pytest.param(
        assistant(
            '{"type":"text","text":"Checking."}',
            tool("Read", '{"file_path":"x.go"}'),
            '{"type":"thinking","thinking":"hmm"}',
        ),
        ["Checking.", "→ Read x.go"],
        id="mixed blocks keep order",
    ),
]


@pytest.mark.parametrize(("line", "want"), ACTIVITY_CASES)
def test_assistant_line_gives_activity(line: str, want: list[str]) -> None:
    assert parse_stream_line(line.encode()) == (want, "", False)


def test_result_event() -> None:
    text = f"Done.\n{DONE}"
    line = '{"type":"result","subtype":"success","is_error":false,"result":"Done.\\n{\\"state\\": \\"completed\\", \\"error\\": \\"\\"}"}'

    assert parse_stream_line(line.encode()) == ([], text, True)


IGNORED = [
    pytest.param(
        '{"type":"user","message":{"content":[{"type":"tool_result","content":"file body"}]}}',
        id="user event",
    ),
    pytest.param(
        '{"type":"something_new","message":{"content":[{"type":"text","text":"hi"}]}}',
        id="unknown event type",
    ),
    pytest.param('{"type":"assistant",', id="invalid json"),
    pytest.param("", id="empty line"),
    pytest.param("[1, 2]", id="not an object"),
    pytest.param('{"message":{}}', id="no type"),
    pytest.param('{"type":["assistant"]}', id="type of the wrong kind"),
    pytest.param(b'{"type":"assistant","x":"\xff"}', id="bytes that are not UTF-8"),
    pytest.param(b"[" * 200000, id="deeply nested garbage"),
    pytest.param(assistant('{"type":"text","text":5}'), id="text of the wrong kind"),
]


@pytest.mark.parametrize("line", IGNORED)
def test_other_lines_give_nothing(line: str | bytes) -> None:
    data = line.encode() if isinstance(line, str) else line
    assert parse_stream_line(data) == ([], "", False)


MALFORMED = [
    pytest.param('{"type":"assistant","message":"hi"}', id="message not an object"),
    pytest.param(
        '{"type":"assistant","message":{"content":"hi"}}', id="content not a list"
    ),
    pytest.param(
        '{"type":"assistant","message":{"content":["hi",7,null]}}',
        id="blocks not objects",
    ),
]


@pytest.mark.parametrize("line", MALFORMED)
def test_malformed_assistant_line_counts_as_empty(line: str) -> None:
    assert parse_stream_line(line.encode()) == ([], "", False)


def test_result_that_is_not_text_is_empty() -> None:
    assert parse_stream_line(b'{"type":"result","result":null}') == ([], "", True)
