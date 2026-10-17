# ADR-0004 — Typed callbacks and per-admin panel lock

**Status:** accepted (v4.1)

**Context.** Callback strings were hand-built and audited manually; `PanelMemory` held one message per admin, so double-clicks and multi-chat use could race or edit the wrong message. `bot/commands.py` had grown into one large object.

**Decision.** Every callback is a typed `CallbackData` class with a compact versioned prefix (≤ 64 bytes) registered in one registry; a test enforces routing and size. `PanelEngine` takes a per-admin lock, answers callbacks immediately, versions each panel (stale/duplicate presses are ignored), and recreates the panel when the message is gone. Panel state is keyed by `(admin_id, chat_id)`. Handlers are split per feature.

**Consequences.** Races removed, audit automatic, smaller modules. Framework stays python-telegram-bot; moving to aiogram remains possible because delivery is behind `BotGateway`.
