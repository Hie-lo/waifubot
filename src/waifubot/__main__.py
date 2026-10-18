"""Entrypoint for executing waifubot package directly via `python -m waifubot`."""

import sys

from waifubot.cli import main

if __name__ == "__main__":
    sys.exit(main())
