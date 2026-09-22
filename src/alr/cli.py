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
    args = parser.parse_args(argv)

    if args.dry_run:
        args.prompt = None
        return args

    if args.prompt is None:
        parser.error("{The argument '--prompt [-p] is required")

    return args
