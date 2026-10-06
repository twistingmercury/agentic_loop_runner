"""Entry point for the alr command."""

import sys

from alr import cli


def main() -> None:
    sys.exit(cli.main())
