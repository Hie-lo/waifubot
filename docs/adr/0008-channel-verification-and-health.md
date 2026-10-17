# ADR-0008 — Channel verification at add time, re-check at use time, continuous health

**Status:** accepted (v4.1)

**Context.** v4.0 stored raw channel ids in scheduler state. Anyone able to name a channel id could try to publish to it, and lost bot rights surfaced only as failed posts.

**Decision.**
- Channels are registered entities (`channels`). Adding verifies, in order: the chat is visible, the bot has the needed admin rights, and the **adder is creator/admin of the chat** (`get_chat_member`); the superadmin skips only the adder check (R53), never the technical bot-rights check.
- Using a channel one does not own repeats the adder check at use time; stale grants never carry over.
- Health: `my_chat_member` updates plus a periodic re-verify (`channel_recheck_hours`). Lost rights → `lost_access`, schedules paused (not deleted), owner and admins notified; resume after re-verify and confirmation.
- Telegram access is behind a `ChannelProbe` port so services stay Telegram-free and the whole matrix is testable with a fake.

**Consequences.** Clear, explainable failure reasons (R7), no silent dead schedules, and a safe path to user-owned channels (plan-gated). Cost: extra API calls on add/use and a background loop.
