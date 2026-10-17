"""System control and restart coordination service (R49, R37).

Contract:
- Superadmin controls for graceful worker restart and whole-bot restart (Settings -> System).
- Bot restart coordinates: confirmation -> graceful drain -> exit code 75 (for systemd) or os.execv.
- Rejects duplicate restart requests with explanation (R7).
- Preserves panel coordinates across restart via `restart.pending` marker.
- On startup, recovers marker, updates original panel to "restarted ✅", and cleans up marker.
- All actions are audited (R44).
"""

import contextlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.services.audit import record_audit


class RestartAlreadyInProgressError(Exception):
    """Raised when a restart is requested while one is already pending."""


class RestartController:
    """Manages bot restart lifecycle, pending markers, and startup panel updates."""

    def __init__(self, marker_path: str | Path = "data/restart.pending") -> None:
        self.marker_path = Path(marker_path)

    def is_restart_pending(self) -> bool:
        """Checks if a restart request is currently in progress."""
        return self.marker_path.is_file()

    def get_restart_pending(self) -> dict[str, Any] | None:
        """Reads the restart.pending marker payload if present."""
        if not self.is_restart_pending():
            return None
        try:
            return json.loads(self.marker_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set_restart_pending(
        self,
        requested_by: int,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Persists the restart.pending marker atomically."""
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.marker_path.with_suffix(".tmp")
        data = {
            "requested_by": requested_by,
            "chat_id": chat_id,
            "message_id": message_id,
            "at": time.time(),
        }
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        tmp_path.replace(self.marker_path)

    def clear_restart_pending(self) -> None:
        """Clears the restart marker after successful startup recovery."""
        if self.marker_path.is_file():
            with contextlib.suppress(OSError):
                self.marker_path.unlink()

    async def restart_workers(
        self,
        worker_pool: Any,
        actor_id: int = 0,
        session: AsyncSession | None = None,
    ) -> bool:
        """Gracefully restarts the worker pool while the bot continues answering."""
        if session is not None:
            await record_audit(
                session=session,
                admin_id=actor_id,
                action="system_restart_workers",
                target="worker_pool",
                old_val=None,
                new_val="restarted",
            )

        if worker_pool is not None and hasattr(worker_pool, "restart"):
            await worker_pool.restart()
            return True
        return True

    async def request_bot_restart(
        self,
        requested_by: int,
        chat_id: int,
        message_id: int,
        shutdown_service: Any = None,
        session: AsyncSession | None = None,
        exit_code: int = 75,
        exit_fn: Callable[[int], None] | None = None,
    ) -> None:
        """Initiates graceful bot restart (R49)."""
        if self.is_restart_pending():
            raise RestartAlreadyInProgressError(
                "فرآیند راه‌اندازی مجدد در حال حاضر در حال اجرا است."
            )

        # Store pending marker
        self.set_restart_pending(
            requested_by=requested_by,
            chat_id=chat_id,
            message_id=message_id,
        )

        if session is not None:
            await record_audit(
                session=session,
                admin_id=requested_by,
                action="system_restart_bot",
                target="bot_process",
                old_val=None,
                new_val={"exit_code": exit_code, "chat_id": chat_id, "message_id": message_id},
            )

        # Execute graceful drain if shutdown service is wired
        if shutdown_service is not None and hasattr(shutdown_service, "graceful_shutdown"):
            with contextlib.suppress(Exception):
                await shutdown_service.graceful_shutdown(reason="admin_restart")

        # Trigger process exit with code 75 for supervisor (systemd RestartForceExitStatus=75)
        if exit_fn is not None:
            exit_fn(exit_code)
        else:
            sys.exit(exit_code)

    async def handle_startup_recovery(
        self,
        telegram_gateway: Any,
        recovered_jobs_count: int = 0,
        locale: str = "fa",
    ) -> dict[str, Any] | None:
        """Inspects and handles pending restart marker on bot startup."""
        marker = self.get_restart_pending()
        if marker is None:
            return None

        chat_id = marker.get("chat_id")
        message_id = marker.get("message_id")

        if chat_id and message_id and telegram_gateway is not None:
            text = (
                f"✅ <b>راه‌اندازی مجدد با موفقیت انجام شد</b> (restarted ✅)\n\n"
                f"• کارهای بازیابی شده: <b>{recovered_jobs_count}</b>\n"
                f"• وضعیت سیستم: آماده پردازش"
                if locale == "fa"
                else f"✅ <b>Bot restarted successfully</b> (restarted ✅)\n\n"
                f"• Recovered jobs: <b>{recovered_jobs_count}</b>\n"
                f"• System status: ready"
            )

            try:
                if hasattr(telegram_gateway, "edit_message_text"):
                    await telegram_gateway.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                    )
            except Exception:
                pass

        # Clean up marker once processed
        self.clear_restart_pending()
        return marker
