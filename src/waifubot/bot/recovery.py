"""Startup recovery (SPEC §10): after a restart, any job left `running` becomes
`failed` with `error_code=interrupted` (never retried automatically). Startup
also cleans what that job left behind: `.part` files in storage, and image rows
pointing at missing files are marked `missing`."""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from waifubot.db.models import Image, Job

log = logging.getLogger(__name__)


async def startup_recover(
    sessions: async_sessionmaker[AsyncSession], storage_root: Path
) -> dict[str, int]:
    stats = {
        "interrupted_jobs": 0,
        "part_files_removed": 0,
        "missing_images": 0,
        "interrupted_ids": [],
    }

    # 1) stale running jobs -> failed/interrupted (not retried automatically)
    async with sessions() as s:
        ids = list((await s.execute(
            select(Job.id).where(Job.status == "running")
        )).scalars().all())
        result = await s.execute(
            update(Job)
            .where(Job.status == "running")
            .values(status="failed", error_code="interrupted",
                    error_summary="interrupted by restart")
        )
        stats["interrupted_jobs"] = result.rowcount or 0
        stats["interrupted_ids"] = [int(i) for i in ids]
        await s.commit()

    # 2) leftover .part files from atomic writes
    for folder in (storage_root / "images", storage_root / "temp"):
        if folder.is_dir():
            for part in folder.glob("*.part"):
                try:
                    part.unlink()
                    stats["part_files_removed"] += 1
                except OSError:
                    log.warning("could not remove part file %s", part.name)

    # 3) image rows whose files are gone -> status=missing (never delete rows)
    async with sessions() as s:
        rows = (await s.execute(select(Image))).scalars().all()
        for img in rows:
            if not (storage_root / img.file_path).is_file() and img.status != "missing":
                img.status = "missing"
                stats["missing_images"] += 1
        await s.commit()

    if any(stats.values()):
        log.info("startup recovery: %s", stats)
    return stats
