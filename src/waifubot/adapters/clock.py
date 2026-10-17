"""Clock adapters implementing core.ports.Clock."""

import time
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> float: ...


class SystemClock:
    """System wall-clock time provider."""

    def now_utc(self) -> float:
        return time.time()


class MockClock:
    """Deterministic, manually advanceable clock for testing."""

    def __init__(self, initial_time: float = 1700000000.0) -> None:
        self._current_time: float = initial_time

    def now_utc(self) -> float:
        return self._current_time

    def advance(self, seconds: float) -> float:
        self._current_time += seconds
        return self._current_time

    def set_time(self, timestamp: float) -> None:
        self._current_time = timestamp
