"""A scriptable stand-in for the `claude` executable, used only by the tests.

alr always starts claude with an argv it builds itself and the assembled
prompt on stdin, so nothing here can be driven by flags. Every behavior is
controlled through environment variables instead. This one script honors both
sets gralph's two fake claudes use, so tests ported from either keep working.

The looper set:

    FAKE_CLAUDE_EXIT      exit code to return (default "0")
    FAKE_CLAUDE_RECORD    path to append one entry per call: the exact stdin
                          bytes followed by RECORD_SEPARATOR, so a test can
                          recover each call's stdin verbatim and in call order
    FAKE_CLAUDE_BLOCK     "1" to write the ready file (if set) and then block
                          forever, instead of reading stdin, recording it, and
                          exiting
    FAKE_CLAUDE_BLOCK_ON  text; when stdin contains it, write the ready file
                          (if set) and block forever after reading stdin,
                          before recording it, so a test can block only one
                          task's call
    FAKE_CLAUDE_READY     path to write a ready marker to just before blocking
    FAKE_CLAUDE_OUTPUT    if set (even to the empty string), its value is
                          written verbatim to stdout before exiting, instead
                          of the default result line. In stream mode it is the
                          result event's text instead. When unset, the default
                          is `{"state":"completed","error":""}` on a zero exit
                          and nothing on a non-zero exit.
    FAKE_CLAUDE_ARGS      path to append one line per call: the argv, without
                          the program name, joined by spaces
    FAKE_CLAUDE_STDERR    text written verbatim to stderr just before exiting

Stream mode is on only when argv contains "--output-format" followed by
"stream-json". Stdout is then one JSON event per line instead of the plain
result line: a system init event, an assistant text event, and, only on a
zero exit, a result event. One variable applies only in stream mode:

    FAKE_CLAUDE_BIG_EVENT byte count; when set, one extra user event, padded
                          to at least that many bytes, is written just before
                          the result event

The e2e set:

    FAKECLAUDE_MODE                empty for normal operation, or "descendant"
                                   to ignore every other variable, read
                                   nothing, and block until killed
    FAKECLAUDE_RECORD_FILE         path to append one JSON line per call:
                                   {"argv": [...], "stdin": "..."}, with argv
                                   excluding the program name
    FAKECLAUDE_ATTEMPT_LOG_FILE    path to append one line to per call, used to
                                   count calls across a run
    FAKECLAUDE_EXIT_CODE           exit code to use (default "0"); it wins over
                                   FAKE_CLAUDE_EXIT when both are set
    FAKECLAUDE_STDERR_MSG          text to print to stderr, with a newline,
                                   before exiting
    FAKECLAUDE_DESCENDANT_PID_FILE path to write the PID of a spawned blocking
                                   descendant process (empty: none is spawned)
    FAKECLAUDE_BLOCK               "1" to block forever after reading stdin
                                   and completing setup (descendant spawn,
                                   ready file) instead of exiting
    FAKECLAUDE_READY_FILE          path to write this process's own PID once
                                   setup is complete and it is about to block
                                   or exit
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

RECORD_SEPARATOR = b"\x00---FAKE-CLAUDE-RECORD-SEPARATOR---\x00"
DEFAULT_RESULT = '{"state":"completed","error":""}'

_BIG_EVENT_PREFIX = '{"type":"user","message":{"content":[{"type":"text","text":"'
_BIG_EVENT_SUFFIX = '"}]}}'


def main() -> NoReturn:
    if os.environ.get("FAKECLAUDE_MODE") == "descendant":
        block_forever()

    args = sys.argv[1:]
    record_args(args)
    if os.environ.get("FAKE_CLAUDE_BLOCK") == "1":
        ready_then_block()

    stdin = sys.stdin.buffer.read()
    block_on = os.environb.get(b"FAKE_CLAUDE_BLOCK_ON", b"")
    if block_on and block_on in stdin:
        ready_then_block()

    record_call(args, stdin)
    spawn_descendant()
    ready_file = os.environ.get("FAKECLAUDE_READY_FILE")
    if ready_file:
        Path(ready_file).write_text(str(os.getpid()), encoding="utf-8")

    if os.environ.get("FAKECLAUDE_BLOCK") == "1":
        block_forever()

    message = os.environ.get("FAKECLAUDE_STDERR_MSG")
    if message:
        print(message, file=sys.stderr)

    code = exit_code()
    if stream_mode(args):
        write_stream(code)
    else:
        write_output(code)

    sys.stdout.flush()
    sys.stderr.buffer.write(os.environb.get(b"FAKE_CLAUDE_STDERR", b""))
    sys.stderr.flush()
    sys.exit(code)


def record_args(args: list[str]) -> None:
    args_file = os.environ.get("FAKE_CLAUDE_ARGS")
    if not args_file:
        return

    append(args_file, os.fsencode(" ".join(args) + "\n"))


def record_call(args: list[str], stdin: bytes) -> None:
    record_file = os.environ.get("FAKE_CLAUDE_RECORD")
    if record_file:
        append(record_file, stdin + RECORD_SEPARATOR)

    json_record_file = os.environ.get("FAKECLAUDE_RECORD_FILE")
    if json_record_file:
        record = {"argv": args, "stdin": stdin.decode("utf-8", errors="replace")}
        append(json_record_file, json.dumps(record).encode("utf-8") + b"\n")

    attempt_log = os.environ.get("FAKECLAUDE_ATTEMPT_LOG_FILE")
    if attempt_log:
        append(attempt_log, b"attempt\n")


def spawn_descendant() -> None:
    pid_file = os.environ.get("FAKECLAUDE_DESCENDANT_PID_FILE")
    if not pid_file:
        return

    # The standard streams go to /dev/null, as Go's exec.Command does by
    # default, so the descendant never holds the session's pipes open.
    descendant = subprocess.Popen(
        [sys.executable, __file__],
        env={**os.environ, "FAKECLAUDE_MODE": "descendant"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        Path(pid_file).write_text(str(descendant.pid), encoding="utf-8")
    except OSError as exc:
        descendant.kill()
        fail(f"write descendant pid file: {exc}")


def exit_code() -> int:
    for name in ("FAKECLAUDE_EXIT_CODE", "FAKE_CLAUDE_EXIT"):
        raw = os.environ.get(name)
        if not raw:
            continue

        try:
            return int(raw)
        except ValueError as exc:
            fail(f"invalid {name} {raw!r}: {exc}")

    return 0


def stream_mode(args: list[str]) -> bool:
    return ("--output-format", "stream-json") in zip(args, args[1:])


def write_output(code: int) -> None:
    output = os.environb.get(b"FAKE_CLAUDE_OUTPUT")
    if output is not None:
        sys.stdout.buffer.write(output)
        return

    if code == 0:
        sys.stdout.buffer.write(DEFAULT_RESULT.encode("utf-8") + b"\n")


def write_stream(code: int) -> None:
    print('{"type":"system","subtype":"init"}')
    print(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}'
    )
    write_big_event()
    if code != 0:
        return

    text = os.environ.get("FAKE_CLAUDE_OUTPUT", DEFAULT_RESULT)
    print(f'{{"type":"result","subtype":"success","result":{json.dumps(text)}}}')


def write_big_event() -> None:
    raw = os.environ.get("FAKE_CLAUDE_BIG_EVENT")
    if not raw:
        return

    try:
        size = int(raw)
    except ValueError as exc:
        fail(f"invalid FAKE_CLAUDE_BIG_EVENT {raw!r}: {exc}")

    pad = max(size - len(_BIG_EVENT_PREFIX) - len(_BIG_EVENT_SUFFIX), 0)
    print(_BIG_EVENT_PREFIX + "x" * pad + _BIG_EVENT_SUFFIX)


def append(path: str, data: bytes) -> None:
    with open(path, "ab") as file:
        file.write(data)


def ready_then_block() -> NoReturn:
    ready_file = os.environ.get("FAKE_CLAUDE_READY")
    if ready_file:
        Path(ready_file).write_text("ready", encoding="utf-8")

    block_forever()


def block_forever() -> NoReturn:
    while True:
        time.sleep(3600)


def fail(message: str) -> NoReturn:
    print(f"fakeclaude: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
