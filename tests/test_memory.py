"""Conversation memory: read-window passthrough + history wiring into Claude (§13)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from bot.config import Settings
from bot.llm.claude_client import ClaudeClient
from bot.memory.conversation import ConversationMemory
from bot.models import ConversationTurn, RetrievedChunk

_DIM = 1024


class _FakeDB:
    """Records calls so we can assert the memory layer delegates correctly."""

    def __init__(self, recent: list[ConversationTurn]) -> None:
        self._recent = recent
        self.appended: list[tuple[int, str, str]] = []
        self.load_args: tuple[int, int] | None = None

    async def load_recent_messages(self, user_id: int, limit: int) -> list[ConversationTurn]:
        self.load_args = (user_id, limit)
        return self._recent

    async def append_message(self, user_id: int, role: str, content: str) -> int:
        self.appended.append((user_id, role, content))
        return len(self.appended)  # stand-in messages.id


async def test_memory_load_recent_and_append_delegate_to_db() -> None:
    recent = [
        ConversationTurn(role="user", content="Q1"),
        ConversationTurn(role="assistant", content="A1"),
    ]
    db = _FakeDB(recent)
    memory = ConversationMemory(db)  # type: ignore[arg-type]

    loaded = await memory.load_recent(user_id=42, turns=20)
    assert loaded == recent
    assert db.load_args == (42, 20)

    new_id = await memory.append(42, "user", "Q2")
    assert new_id == 1
    assert db.appended == [(42, "user", "Q2")]


def _chunk(content: str, similarity: float, filename: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid4(),
        source_id=uuid4(),
        chunk_index=0,
        content=content,
        similarity=similarity,
        filename=filename,
    )


async def test_history_is_prepended_before_the_document_turn() -> None:
    """Prior turns are plain text; document blocks live ONLY in the final user turn."""
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok", citations=[])],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    claude = ClaudeClient(Settings())
    claude._client = SimpleNamespace(  # type: ignore[assignment]
        messages=SimpleNamespace(create=fake_create)
    )

    history = [
        ConversationTurn(role="user", content="Q1"),
        ConversationTurn(role="assistant", content="A1"),
    ]
    await claude.answer("Q2", [_chunk("doc text", 0.8, "faq.docx")], history=history)

    messages = captured["messages"]
    assert messages[0] == {"role": "user", "content": "Q1"}
    assert messages[1] == {"role": "assistant", "content": "A1"}
    # Final turn = the document blocks followed by the current question.
    final = messages[2]
    assert final["role"] == "user"
    assert final["content"][-1] == {"type": "text", "text": "Q2"}
    assert any(block.get("type") == "document" for block in final["content"][:-1])
