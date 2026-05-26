"""Tests for the chat path: citations, escalation triggers, memory + feedback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bot.config import Settings
from bot.handlers.chat import handle_question
from bot.llm.claude_client import ClaudeClient
from bot.models import Escalation, RetrievedChunk

_DIM = 1024


class _FakeEmbeddings:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * _DIM for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * _DIM


def _chunk(content: str, similarity: float, filename: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid4(),
        source_id=uuid4(),
        chunk_index=0,
        content=content,
        similarity=similarity,
        filename=filename,
    )


async def test_claude_answer_parses_citations() -> None:
    chunk = _chunk("Доставка курьером в день заказа при оформлении до 14:00.", 0.81, "faq.docx")
    fake_response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Доставка по Киеву — ", citations=[]),
            SimpleNamespace(
                type="text",
                text="в день заказа",
                citations=[
                    SimpleNamespace(
                        type="char_location",
                        document_title="faq.docx",
                        document_index=0,
                        cited_text="Доставка курьером в день заказа",
                    )
                ],
            ),
        ],
        usage=SimpleNamespace(
            input_tokens=1200,
            output_tokens=42,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=1100,
        ),
    )

    claude = ClaudeClient(Settings())
    claude._client = SimpleNamespace(  # type: ignore[assignment]
        messages=SimpleNamespace(create=AsyncMock(return_value=fake_response))
    )

    result = await claude.answer("Сколько идёт доставка?", [chunk])

    assert "в день заказа" in result.text
    assert result.sources == ["faq.docx"]  # citation document_title parsed
    assert result.input_tokens == 1200
    assert result.output_tokens == 42
    assert result.cache_creation_tokens == 1100


async def test_below_threshold_escalates_without_calling_claude() -> None:
    db = AsyncMock()
    db.get_active_escalation.return_value = None
    db.match_chunks.return_value = [_chunk("нерелевантный текст", 0.2, "x.docx")]
    db.create_escalation.return_value = uuid4()
    claude = AsyncMock()
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=1)
    message = AsyncMock()
    message.text = "вопрос, которого нет в базе"
    message.from_user = SimpleNamespace(id=555, full_name="U", username=None)

    await handle_question(message, bot=bot, db=db, embeddings=_FakeEmbeddings(), claude=claude)

    claude.answer.assert_not_awaited()  # no LLM call below the similarity threshold
    db.create_escalation.assert_awaited_once()  # escalation opened
    bot.send_message.assert_awaited_once()  # posted to the managers' chat
    message.answer.assert_awaited()  # user told honestly
    assert "менеджер" in message.answer.await_args.args[0]


async def test_needs_human_escalates_after_answer() -> None:
    db = AsyncMock()
    db.get_active_escalation.return_value = None
    db.match_chunks.return_value = [_chunk("какой-то контекст", 0.9, "faq.docx")]
    db.load_recent_messages.return_value = []
    db.create_escalation.return_value = uuid4()
    claude = AsyncMock()
    claude.answer.return_value = SimpleNamespace(
        text="",
        sources=[],
        needs_human=True,
        input_tokens=10,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=2)
    message = AsyncMock()
    message.text = "вопрос есть, но не в этих чанках"
    message.from_user = SimpleNamespace(id=9, full_name="U", username=None)

    await handle_question(message, bot=bot, db=db, embeddings=_FakeEmbeddings(), claude=claude)

    claude.answer.assert_awaited_once()
    db.create_escalation.assert_awaited_once()  # escalated despite an above-threshold hit
    db.append_message.assert_not_awaited()  # a non-answer is not persisted to memory


async def test_cooldown_keeps_bot_silent() -> None:
    db = AsyncMock()
    db.get_active_escalation.return_value = Escalation(
        id=uuid4(),
        user_id=7,
        question="q",
        status="taken",
        cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    message = AsyncMock()
    message.text = "ещё вопрос"
    message.from_user = SimpleNamespace(id=7)

    await handle_question(
        message, bot=AsyncMock(), db=db, embeddings=_FakeEmbeddings(), claude=AsyncMock()
    )

    message.answer.assert_not_awaited()  # silent during cooldown
    db.match_chunks.assert_not_awaited()  # didn't even retrieve


async def test_open_escalation_reassures_without_re_escalating() -> None:
    db = AsyncMock()
    db.get_active_escalation.return_value = Escalation(
        id=uuid4(), user_id=7, question="q", status="open"
    )
    message = AsyncMock()
    message.text = "ещё"
    message.from_user = SimpleNamespace(id=7)

    await handle_question(
        message, bot=AsyncMock(), db=db, embeddings=_FakeEmbeddings(), claude=AsyncMock()
    )

    message.answer.assert_awaited_once()
    assert "уже" in message.answer.await_args.args[0].lower()
    db.create_escalation.assert_not_awaited()  # no duplicate escalation


async def test_answer_path_loads_memory_persists_and_attaches_feedback() -> None:
    db = AsyncMock()
    db.get_active_escalation.return_value = None
    db.match_chunks.return_value = [_chunk("Доставка курьером в день заказа.", 0.81, "faq.docx")]
    db.load_recent_messages.return_value = []  # no prior turns
    db.append_message.return_value = 77  # stand-in assistant messages.id

    claude = AsyncMock()
    claude.answer.return_value = SimpleNamespace(
        text="Доставка в день заказа.",
        sources=["faq.docx"],
        needs_human=False,
        input_tokens=1000,
        output_tokens=20,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )

    message = AsyncMock()
    message.text = "Сколько идёт доставка?"
    message.from_user = SimpleNamespace(id=555)

    await handle_question(
        message, bot=AsyncMock(), db=db, embeddings=_FakeEmbeddings(), claude=claude
    )

    claude.answer.assert_awaited_once()
    assert claude.answer.await_args.kwargs["history"] == []  # memory loaded + passed
    assert db.append_message.await_count == 2  # user + assistant turns persisted
    message.answer.assert_awaited_once()
    sent_text = message.answer.await_args.args[0]
    assert "Источник: faq.docx" in sent_text
    assert message.answer.await_args.kwargs["reply_markup"] is not None  # feedback buttons
