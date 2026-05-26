"""Tests for the answer path: citation parsing + the below-threshold gate."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bot.config import Settings
from bot.handlers.chat import handle_question
from bot.llm.claude_client import ClaudeClient
from bot.models import RetrievedChunk

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


async def test_below_threshold_returns_honest_without_calling_claude() -> None:
    db = AsyncMock()
    db.match_chunks.return_value = [
        _chunk("нерелевантный текст", similarity=0.2, filename="x.docx")
    ]
    claude = AsyncMock()
    message = AsyncMock()
    message.text = "вопрос, которого нет в базе"

    await handle_question(message, db=db, embeddings=_FakeEmbeddings(), claude=claude)

    message.answer.assert_awaited_once()
    assert "менеджеру" in message.answer.await_args.args[0]
    claude.answer.assert_not_awaited()  # no LLM call below the similarity threshold
