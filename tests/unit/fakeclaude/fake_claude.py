"""A scriptable stand-in for the `claude` executable, used only by the tests.

alr always starts claude with an argv it builds itself and the assembled
prompt on stdin, so nothing here can be driven by flags. Every behavior is
controlled through the environment variables below, which are the whole
interface.

    FAKECLAUDE_OUTPUT               if set (even to the empty string), its value
                                    is written verbatim to stdout before
                                    exiting, instead of the default result
                                    line; in stream mode it is the result
                                    event's text. When unset, the default is
                                    `{"state":"completed","error":""}` on a
                                    zero exit and nothing on a non-zero exit
    FAKECLAUDE_EXIT_CODE            exit code to return (default 0)
    FAKECLAUDE_RESUME_OUTPUT        used instead of FAKECLAUDE_OUTPUT, with the
                                    same rules, but only on a call whose argv
                                    contains "--resume"; when unset, that call
                                    falls back to FAKECLAUDE_OUTPUT. The empty
                                    string is a real value that writes nothing
    FAKECLAUDE_RESUME_EXIT_CODE     used instead of FAKECLAUDE_EXIT_CODE, but only
                                    on a call whose argv contains "--resume";
                                    when unset, that call falls back to
                                    FAKECLAUDE_EXIT_CODE
    FAKECLAUDE_STDERR               text written verbatim to stderr just before
                                    exiting
    FAKECLAUDE_STDERR_BYTES         byte count; that many bytes of 99-character
                                    lines are written to stderr after
                                    FAKECLAUDE_STDERR, for output larger than
                                    an environment variable can hold
    FAKECLAUDE_RECORD_FILE          path to append one JSON line per call:
                                    {"argv": [...], "stdin": "..."}, with argv
                                    excluding the program name; its line count
                                    is the number of calls
    FAKECLAUDE_READY_FILE           path to write this process's own PID once
                                    setup is complete and it is about to block
                                    or exit
    FAKECLAUDE_BLOCK                "1" to block forever, after reading stdin and
                                    completing setup (record, descendant spawn,
                                    ready file), instead of exiting
    FAKECLAUDE_BLOCK_ON             text; block as FAKECLAUDE_BLOCK does, but
                                    only when stdin contains it, so a test can
                                    block just one task's call
    FAKECLAUDE_DESCENDANT_PID_FILE  path to write the PID of a spawned blocking
                                    descendant process (unset: none is spawned)
    FAKECLAUDE_MODE                 "descendant" to ignore every other variable,
                                    read nothing, and block until killed; the
                                    spawned descendant is this script run in
                                    this mode
    FAKECLAUDE_BIG_EVENT            stream mode only: byte count; one extra user
                                    event, padded to at least that many bytes,
                                    is written just before the result event

Stream mode is on only when argv contains "--output-format" followed by
"stream-json". Stdout is then one JSON event per line instead of the plain
result line: a system init event, an assistant text event, and, only on a
zero exit, a result event.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

DEFAULT_RESULT = '{"state":"completed","error":""}'

_BIG_EVENT_PREFIX = '{"type":"user","message":{"content":[{"type":"text","text":"'
_BIG_EVENT_SUFFIX = '"}]}}'


def main() -> NoReturn:
    if os.environ.get("FAKECLAUDE_MODE") == "descendant":
        block_forever()

    args = sys.argv[1:]
    stdin = sys.stdin.buffer.read()
    record_call(args, stdin)
    spawn_descendant()
    ready_file = os.environ.get("FAKECLAUDE_READY_FILE")
    if ready_file:
        Path(ready_file).write_text(str(os.getpid()), encoding="utf-8")

    block_on = os.environb.get(b"FAKECLAUDE_BLOCK_ON", b"")
    if os.environ.get("FAKECLAUDE_BLOCK") == "1" or (block_on and block_on in stdin):
        block_forever()

    code = exit_code(args)
    if stream_mode(args):
        write_stream(args, code)
    else:
        write_output(args, code)

    sys.stdout.flush()
    sys.stderr.buffer.write(os.environb.get(b"FAKECLAUDE_STDERR", b""))
    write_stderr_bytes()
    sys.stderr.flush()
    sys.exit(code)


def write_stderr_bytes() -> None:
    raw = os.environ.get("FAKECLAUDE_STDERR_BYTES")
    if not raw:
        return

    try:
        size = int(raw)
    except ValueError as exc:
        fail(f"invalid FAKECLAUDE_STDERR_BYTES {raw!r}: {exc}")

    line = b"e" * 99 + b"\n"
    sys.stderr.buffer.write(line * (size // len(line)))


def record_call(args: list[str], stdin: bytes) -> None:
    record_file = os.environ.get("FAKECLAUDE_RECORD_FILE")
    if not record_file:
        return

    record = {"argv": args, "stdin": stdin.decode("utf-8", errors="replace")}
    with open(record_file, "ab") as file:
        file.write(json.dumps(record).encode("utf-8") + b"\n")


def spawn_descendant() -> None:
    pid_file = os.environ.get("FAKECLAUDE_DESCENDANT_PID_FILE")
    if not pid_file:
        return

    # The standard streams go to /dev/null so the descendant never holds the
    # session's pipes open.
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


def exit_code(args: list[str]) -> int:
    name = variable_name(args, "EXIT_CODE")
    raw = os.environ.get(name)
    if not raw:
        return 0

    try:
        return int(raw)
    except ValueError as exc:
        fail(f"invalid {name} {raw!r}: {exc}")


def variable_name(args: list[str], suffix: str) -> str:
    # A resumed call reads its own variable so a test can script a first
    # session and its follow-up differently; unset, it shares the plain one.
    resume_name = f"FAKECLAUDE_RESUME_{suffix}"
    if "--resume" in args and resume_name in os.environ:
        return resume_name

    return f"FAKECLAUDE_{suffix}"


def output_override(args: list[str]) -> bytes | None:
    name = variable_name(args, "OUTPUT")
    return os.environb.get(name.encode("utf-8"))


def stream_mode(args: list[str]) -> bool:
    return ("--output-format", "stream-json") in zip(args, args[1:])


def write_output(args: list[str], code: int) -> None:
    output = output_override(args)
    if output is not None:
        sys.stdout.buffer.write(output)
        return

    if code == 0:
        sys.stdout.buffer.write(DEFAULT_RESULT.encode("utf-8") + b"\n")


def write_stream(args: list[str], code: int) -> None:
    print('{"type":"system","subtype":"init"}')
    print(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}'
    )
    write_big_event()
    if code != 0:
        return

    output = output_override(args)
    text = DEFAULT_RESULT if output is None else output.decode("utf-8", "replace")
    print(f'{{"type":"result","subtype":"success","result":{json.dumps(text)}}}')


def write_big_event() -> None:
    raw = os.environ.get("FAKECLAUDE_BIG_EVENT")
    if not raw:
        return

    try:
        size = int(raw)
    except ValueError as exc:
        fail(f"invalid FAKECLAUDE_BIG_EVENT {raw!r}: {exc}")

    pad = max(size - len(_BIG_EVENT_PREFIX) - len(_BIG_EVENT_SUFFIX), 0)
    print(_BIG_EVENT_PREFIX + "x" * pad + _BIG_EVENT_SUFFIX)


def block_forever() -> NoReturn:
    while True:
        time.sleep(3600)


def fail(message: str) -> NoReturn:
    print(f"fakeclaude: {message}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
