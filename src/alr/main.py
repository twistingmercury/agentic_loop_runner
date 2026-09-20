import sys

import yaml, pydantic

from alr.cli import get_args
from alr.tasks import parse_tasks


def main(argv=None):
    try:
        args = get_args(argv)
        _ = parse_tasks(args.tasks)
    except (FileNotFoundError, yaml.YAMLError, pydantic.ValidationError) as e:
        sys.exit(e)
