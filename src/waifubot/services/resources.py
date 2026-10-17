"""Resource management: soft MemoryGuard and serialized ChallengeGate (R28, R35)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import ctypes
import os
import platform
import sys
from typing import Callable


class ChallengeTimeoutError(Exception):
    """Raised when a job waits longer than challenge_wait_seconds for the challenge window."""


def get_system_free_ram_mb() -> float:
    """Returns available free physical RAM in megabytes."""
    system = platform.system()
    if system == "Windows":
        try:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullAvailPhys / (1024.0 * 1024.0)
        except Exception:
            return 2048.0  # Safe fallback if ctypes query fails
    elif system == "Linux" and os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        parts = line.split()
                        return float(parts[1]) / 1024.0
        except Exception:
            return 2048.0

    return 2048.0


class MemoryGuard:
    """Soft guard preventing new slot acquisition when free RAM is below threshold (R35)."""

    def __init__(
        self,
        min_free_ram_mb: float = 512.0,
        memory_reader: Callable[[], float] | None = None,
        on_low_memory_alert: Callable[[float, float], None] | None = None,
    ) -> None:
        self.min_free_ram_mb = min_free_ram_mb
        self.memory_reader = memory_reader or get_system_free_ram_mb
        self.on_low_memory_alert = on_low_memory_alert
        self._alerted = False

    def can_acquire(self) -> bool:
        """Returns True if free memory exceeds the minimum threshold."""
        free_mb = self.memory_reader()
        if free_mb < self.min_free_ram_mb:
            if not self._alerted:
                self._alerted = True
                if self.on_low_memory_alert:
                    self.on_low_memory_alert(free_mb, self.min_free_ram_mb)
            return False

        # Memory recovered
        self._alerted = False
        return True


class ChallengeGate:
    """Serializes challenge window interactions to guarantee only one window at a time (R28)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def is_locked(self) -> bool:
        return self._lock.locked()

    @asynccontextmanager
    async def acquire_challenge(self, wait_timeout_seconds: float = 180.0) -> AsyncIterator[None]:
        """Acquires exclusive access to the challenge window or raises ChallengeTimeoutError."""
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=wait_timeout_seconds)
        except asyncio.TimeoutError as err:
            raise ChallengeTimeoutError(
                f"Timed out waiting {wait_timeout_seconds}s for challenge window"
            ) from err

        try:
            yield
        finally:
            self._lock.release()
