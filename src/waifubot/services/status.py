"""System observability and status reporting service (R46).

Contract:
- Gathers real-time runtime metrics: queue depth, running jobs, slots, effective concurrency,
  free RAM, recent errors, worker configuration, uptime, and last restart time.
- Exposes formatted messages for Telegram /status and data dictionaries for CLI health checks.
"""

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waifubot.core.logging import get_recent_errors
from waifubot.db.models import JobRecord
from waifubot.services.settings import SettingsService

# Track process initialization time
PROCESS_START_TIME: float = time.time()


def get_process_uptime() -> float:
    """Returns uptime in seconds since process start."""
    return max(0.0, time.time() - PROCESS_START_TIME)


def format_uptime(seconds: float) -> str:
    """Formats seconds into human-readable duration string."""
    total_secs = int(seconds)
    days, rem = divmod(total_secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_free_ram_mb() -> int:
    """Returns available system RAM in megabytes without requiring external dependencies."""
    # 1. Try psutil if installed
    try:
        import psutil

        vm = psutil.virtual_memory()
        return int(vm.available // (1024 * 1024))
    except (ImportError, Exception):
        pass

    # 2. Try Linux /proc/meminfo (zero dependencies on Linux/WSL)
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    except Exception:
        pass

    # 3. Fallback
    return 1024


async def collect_system_status(
    session: AsyncSession,
    settings_service: SettingsService,
    slot_pool: Any = None,
) -> dict[str, Any]:
    """Collects comprehensive system observability metrics."""
    # Query queue depth and running jobs
    q_depth_stmt = select(func.count(JobRecord.id)).where(
        JobRecord.status.in_(["pending", "retry_wait"])
    )
    running_stmt = select(func.count(JobRecord.id)).where(JobRecord.status == "running")

    queue_depth = (await session.execute(q_depth_stmt)).scalar_one() or 0
    running_jobs = (await session.execute(running_stmt)).scalar_one() or 0

    running_slots = 0
    if slot_pool is not None:
        if hasattr(slot_pool, "active_count"):
            running_slots = slot_pool.active_count
        elif hasattr(slot_pool, "active_slots"):
            running_slots = len(slot_pool.active_slots)

    uptime_sec = get_process_uptime()

    worker_config = {
        "worker_count": settings_service.get("worker_count", 2),
        "max_concurrent_jobs": settings_service.get("max_concurrent_jobs", 2),
        "max_browser_contexts": settings_service.get("max_browser_contexts", 2),
        "provider_concurrency": settings_service.get("provider_concurrency", 2),
    }

    return {
        "uptime_seconds": uptime_sec,
        "uptime_formatted": format_uptime(uptime_sec),
        "last_restart_at": PROCESS_START_TIME,
        "queue_depth": queue_depth,
        "running_jobs": running_jobs,
        "running_slots": running_slots,
        "effective_concurrency": settings_service.effective_concurrency(),
        "free_ram_mb": get_free_ram_mb(),
        "worker_config": worker_config,
        "last_errors": get_recent_errors()[-5:],
    }
