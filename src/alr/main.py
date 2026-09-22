import sys

import yaml, pydantic

from alr.cli import get_args
from alr.tasks import parse_tasks, summarize_tasks
from alr.prompt import parse_prompt
from alr.looper import start


def main(argv=None):

    try:
        args = get_args(argv)
        task_list = parse_tasks(args.tasks)

        if args.dry_run:
            print("tasks yaml looks good!")
            summarize_tasks(task_list)
            return

        shared_prompt = parse_prompt(args.prompt)
        start(shared_prompt, task_list)
    except (FileNotFoundError, yaml.YAMLError, pydantic.ValidationError) as e:
        sys.exit(e)
