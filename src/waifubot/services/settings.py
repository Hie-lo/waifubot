"""Settings registry and live-configuration cache (SPEC §3.3, R25)."""

from collections.abc import Callable
from typing import Any

# Default values and ceilings per SPEC §3.3
SETTINGS_REGISTRY: dict[str, dict[str, Any]] = {
    "worker_count": {"default": 2, "ceiling": 8, "type": int, "min": 1},
    "max_concurrent_jobs": {"default": 2, "ceiling": 8, "type": int, "min": 1},
    "max_browser_contexts": {"default": 2, "ceiling": 4, "type": int, "min": 1},
    "provider_concurrency": {"default": 2, "ceiling": 4, "type": int, "min": 1},
    "fair_claim": {"default": True, "ceiling": True, "type": bool},
    "min_free_ram_mb": {"default": 512, "ceiling": 4096, "type": int, "min": 0},
    "challenge_wait_seconds": {"default": 180, "ceiling": 600, "type": int, "min": 10},
    "heartbeat_seconds": {"default": 20, "ceiling": 60, "type": int, "min": 5},
    "lease_ttl_seconds": {"default": 60, "ceiling": 300, "type": int, "min": 15},
    "shutdown_grace_seconds": {"default": 30, "ceiling": 120, "type": int, "min": 5},
    "app_timezone": {"default": "Asia/Tehran", "ceiling": "Asia/Tehran", "type": str},
    "missed_run_policy": {"default": "catch_up_one", "ceiling": "skip", "type": str},
    "invite_ttl_hours": {"default": 48, "ceiling": 168, "type": int, "min": 1},
    "phash_distance": {"default": 6, "ceiling": 20, "type": int, "min": 0},
    "claim_enabled": {"default": True, "ceiling": True, "type": bool},
    "channel_recheck_hours": {"default": 12, "ceiling": 168, "type": int, "min": 1},
}


class SettingsService:
    """Thread-safe and async-safe in-memory settings cache with ceiling enforcement."""

    def __init__(self, initial_values: dict[str, Any] | None = None) -> None:
        self._listeners: list[Callable[[str, Any], None]] = []
        self._values: dict[str, Any] = {}
        for key, spec in SETTINGS_REGISTRY.items():
            self._values[key] = spec["default"]

        if initial_values:
            for k, v in initial_values.items():
                self.set(k, v)

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the setting value or fallback."""
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Sets a setting, clamped by its ceiling and minimum bounds if registered."""
        if key in SETTINGS_REGISTRY:
            spec = SETTINGS_REGISTRY[key]
            val_type = spec["type"]
            casted = val_type(value)

            if val_type is int:
                min_val = spec.get("min", 0)
                ceiling = spec.get("ceiling", casted)
                casted = max(min_val, min(casted, ceiling))

            self._values[key] = casted
        else:
            self._values[key] = value

        for listener in self._listeners:
            listener(key, self._values[key])

    def add_listener(self, listener: Callable[[str, Any], None]) -> None:
        self._listeners.append(listener)

    def effective_concurrency(self) -> int:
        """Calculates effective concurrency per R34.

        effective = min(worker_count, max_concurrent_jobs, max_browser_contexts, provider_concurrency)
        """
        return min(
            int(self.get("worker_count", 2)),
            int(self.get("max_concurrent_jobs", 2)),
            int(self.get("max_browser_contexts", 2)),
            int(self.get("provider_concurrency", 2)),
        )
