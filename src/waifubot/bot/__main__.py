"""`python -m waifubot.bot` — start the Telegram admin bot (polling)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from waifubot.bot.app import run_bot
from waifubot.config import Settings, load_env_file
from waifubot.errors import WaifuBotError

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_env_file(REPO_ROOT / ".env")
    try:
        settings = Settings.from_env(require_secrets=True)
        return run_bot(settings)
    except WaifuBotError as e:
        # Never log secrets (SPEC §12) — the message contains the field name only.
        print(f"error [{e.code}]: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
