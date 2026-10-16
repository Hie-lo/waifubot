"""Admin authorization (SPEC §11): authorize every command AND callback on the
server; a hidden button is not authorization. Unknown callers are rejected
silently with a safe log line (never a stack)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AdminGate:
    def __init__(self, admin_ids: tuple[int, ...]) -> None:
        self._admins = frozenset(admin_ids)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self._admins

    def allow(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            return True
        log.info("unauthorized access denied (user_id=%s)", user_id)
        return False
