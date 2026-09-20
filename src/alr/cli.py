import argparse


def get_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-t",
        "--tasks",
        help="the formatted yaml file with the tasks to be looped through",
        required=True,
        type=str,
    )
    parser.add_argument(
        "-p",
        "--prompt",
        help="the markdown file that contains the shared prompt for each loop",
        type=str,
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        help="validate that the yaml task file is properly formatted and the tasks are valid",
        action="store_true",
    )
    parser.add_argument(
        "-a",
        "--agent",
        help="the coding agent to be used",
        choices=["claude", "codex"],
        type=str,
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        args.agent = None
        args.prompt = None
        return args

    missing: list[str] = []

    if args.prompt is None:
        missing.append("--prompt")

    if args.agent is None:
        missing.append("--agent")

    if missing:
        msg = ", ".join(missing)
        parser.error(f"{msg}: value is required")

    return args
