"""Conversation memory — last-N messages per user, Postgres-backed (§13).

A thin domain layer over :class:`Database`: ``load_recent`` returns the recent
turns in chronological order to prepend to the Claude call; ``append`` persists a
new turn and returns its row id (used as the feedback reference, §16). Trimming is
a read-window (``CONVERSATION_MEMORY_TURNS``), not a delete — full history is kept
for analytics.
"""

from __future__ import annotations

from bot.models import ConversationTurn
from bot.services.supabase_client import Database


class ConversationMemory:
    """Read/append a user's conversation turns from the ``messages`` table."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def load_recent(self, user_id: int, turns: int) -> list[ConversationTurn]:
        """Return the last ``turns`` messages for ``user_id`` in chronological order."""
        return await self._db.load_recent_messages(user_id, turns)

    async def append(self, user_id: int, role: str, content: str) -> int:
        """Persist one turn; return its ``messages.id`` (the feedback reference)."""
        return await self._db.append_message(user_id, role, content)
