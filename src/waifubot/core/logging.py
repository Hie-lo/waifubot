"""Structured JSON logging and contextual log tracking (R46).

Contract:
- Logs are structured JSON (file) + formatted console output.
- Logs carry `job_id`, `admin_id`, `provider`, `slot` context when known (via contextvars).
- Never records chat message text, persona text or user-private content (R46, R66).
- Captures recent error records in an in-memory ring buffer for /status and health checks.
"""

import json
import logging
import sys
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Context variables for logging metadata (R46)
log_job_id: ContextVar[str | None] = ContextVar("log_job_id", default=None)
log_admin_id: ContextVar[int | None] = ContextVar("log_admin_id", default=None)
log_provider: ContextVar[str | None] = ContextVar("log_provider", default=None)
log_slot: ContextVar[int | None] = ContextVar("log_slot", default=None)

# In-memory ring buffer for recent errors
_RECENT_ERRORS: deque[dict[str, Any]] = deque(maxlen=20)


@contextmanager
def bind_log_context(
    job_id: str | None = None,
    admin_id: int | None = None,
    provider: str | None = None,
    slot: int | None = None,
) -> Generator[None]:
    """Binds operational context variables for the duration of the context block."""
    tokens = []
    if job_id is not None:
        tokens.append((log_job_id, log_job_id.set(job_id)))
    if admin_id is not None:
        tokens.append((log_admin_id, log_admin_id.set(admin_id)))
    if provider is not None:
        tokens.append((log_provider, log_provider.set(provider)))
    if slot is not None:
        tokens.append((log_slot, log_slot.set(slot)))

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JsonFormatter(logging.Formatter):
    """Outputs single-line structured JSON logs with attached context (R46)."""

    def format(self, record: logging.LogRecord) -> str:
        now = datetime.fromtimestamp(record.created, tz=UTC)
        payload: dict[str, Any] = {
            "ts": record.created,
            "time": now.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Inject context variables if present
        job_id = log_job_id.get()
        if job_id:
            payload["job_id"] = job_id
        admin_id = log_admin_id.get()
        if admin_id:
            payload["admin_id"] = admin_id
        provider = log_provider.get()
        if provider:
            payload["provider"] = provider
        slot = log_slot.get()
        if slot is not None:
            payload["slot"] = slot

        # Attach exception info if present
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        # Track error records in memory buffer
        if record.levelno >= logging.ERROR:
            _RECENT_ERRORS.append(payload)

        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter displaying operational context."""

    def format(self, record: logging.LogRecord) -> str:
        ctx_parts = []
        job_id = log_job_id.get()
        if job_id:
            ctx_parts.append(f"job:{job_id}")
        admin_id = log_admin_id.get()
        if admin_id:
            ctx_parts.append(f"admin:{admin_id}")
        provider = log_provider.get()
        if provider:
            ctx_parts.append(f"provider:{provider}")
        slot = log_slot.get()
        if slot is not None:
            ctx_parts.append(f"slot:{slot}")

        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""
        msg = f"{record.levelname:<5} [{record.name}]{ctx_str} {record.getMessage()}"

        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        # Track error in memory buffer if not already done by JsonFormatter
        if record.levelno >= logging.ERROR and not any(
            h.__class__ is JsonFormatter for h in record.__dict__.get("handlers", [])
        ):
            _RECENT_ERRORS.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                    "job_id": job_id,
                }
            )

        return msg


def get_recent_errors() -> list[dict[str, Any]]:
    """Returns a copy of recently recorded error log records."""
    return list(_RECENT_ERRORS)


def clear_recent_errors() -> None:
    """Clears the in-memory error log buffer."""
    _RECENT_ERRORS.clear()


def setup_logging(
    log_file: str | Path | None = None,
    level: int = logging.INFO,
    to_console: bool = True,
) -> None:
    """Configures the root logger with JSON file output and console formatting."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for h in list(root.handlers):
        root.removeHandler(h)

    if to_console:
        c_handler = logging.StreamHandler(sys.stdout)
        c_handler.setFormatter(ConsoleFormatter())
        c_handler.setLevel(level)
        root.addHandler(c_handler)

    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        f_handler = logging.FileHandler(file_path, encoding="utf-8")
        f_handler.setFormatter(JsonFormatter())
        f_handler.setLevel(level)
        root.addHandler(f_handler)
