import sys

import pydantic
import yaml

from alr.cli import get_args
from alr.looper import start
from alr.prompt import parse_prompt
from alr.tasks import parse_tasks, summarize_tasks, validate_tasks, TaskList


def main(argv=None):
    try:
        args = get_args(argv)
        task_list = parse_tasks(args.tasks)

        if args.dry_run:
            dry_run(task_list)
            return

        if not validate_tasks(task_list):
            print("Some tasks failed previous runs:")
            summarize_tasks(task_list)
            sys.exit(1)

        shared_prompt = parse_prompt(args.prompt)
        start(shared_prompt, task_list, args.tasks)
    except (
        OSError,
        yaml.YAMLError,
        pydantic.ValidationError,
        ValueError,
    ) as e:
        sys.exit(e)


def dry_run(task_list: TaskList):
    if validate_tasks(task_list):
        print("All tasks are valid!")
    else:
        print("Some tasks failed previous runs:")

    summarize_tasks(task_list)
