"""Integration test for ingest_document with Voyage + DB mocked (offline/CI-safe)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from bot.models import Chunk, Source
from bot.rag.ingest import ingest_document

_DIM = 1024


class _FakeEmbeddings:
    """Duck-types EmbeddingService: returns a fixed-dimension vector per text."""

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * _DIM for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.01] * _DIM


@pytest.fixture
def fake_db() -> AsyncMock:
    db = AsyncMock()
    db.find_active_source_by_hash.return_value = None
    db.ingest_source_with_chunks.return_value = uuid.uuid4()
    return db


async def test_ingest_txt_creates_chunks_with_embeddings(fake_db: AsyncMock) -> None:
    text = ("Вопрос: как сбросить пароль? Ответ: на странице входа нажмите «Забыли пароль». ") * 30
    result = await ingest_document(
        db=fake_db,
        embeddings=_FakeEmbeddings(),
        file_bytes=text.encode("utf-8"),
        filename="faq.txt",
        file_type="txt",
        uploaded_by=1,
        chunk_size_tokens=60,
        overlap_tokens=10,
    )

    assert not result.skipped
    assert result.chunks_added > 0
    fake_db.ingest_source_with_chunks.assert_awaited_once()

    kwargs = fake_db.ingest_source_with_chunks.await_args.kwargs
    chunks = kwargs["chunks"]
    assert kwargs["file_type"] == "txt"
    assert result.chunks_added == len(chunks)
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(len(c.embedding) == _DIM for c in chunks)  # rows carry non-null embeddings
    assert all(c.content.strip() for c in chunks)


async def test_ingest_skips_duplicate(fake_db: AsyncMock) -> None:
    fake_db.find_active_source_by_hash.return_value = Source(
        id=uuid.uuid4(),
        filename="faq.txt",
        file_type="txt",
        chunk_count=7,
        uploaded_at=datetime.now(timezone.utc),
    )
    result = await ingest_document(
        db=fake_db,
        embeddings=_FakeEmbeddings(),
        file_bytes=b"hello world",
        filename="faq.txt",
        file_type="txt",
        uploaded_by=1,
        chunk_size_tokens=60,
        overlap_tokens=10,
    )

    assert result.skipped
    assert result.chunks_added == 7
    fake_db.ingest_source_with_chunks.assert_not_awaited()
