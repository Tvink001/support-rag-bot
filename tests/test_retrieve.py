"""Hybrid retrieve: fuses both arms, flags keyword_hit, reports best cosine (§17)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

from bot.models import RetrievedChunk
from bot.rag.retrieve import retrieve

_DIM = 1024


class _FakeEmbeddings:
    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * _DIM


def _chunk(content: str, similarity: float) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid4(),
        source_id=uuid4(),
        chunk_index=0,
        content=content,
        similarity=similarity,
        filename="f.docx",
    )


async def test_retrieve_fuses_arms_and_flags_keyword_hit() -> None:
    a, b, z = _chunk("a", 0.7), _chunk("b", 0.5), _chunk("z", 0.42)
    db = AsyncMock()
    db.match_chunks.return_value = [a, b]  # vector arm
    db.keyword_search.return_value = [z, a]  # keyword arm (z is FTS-only)

    result = await retrieve(db, _FakeEmbeddings(), "q", top_k=5)
    ids = [c.id for c in result.chunks]

    assert ids[0] == a.id  # ranked by both arms -> top
    assert z.id in ids  # keyword-only chunk still surfaces
    assert result.keyword_hit is True
    assert result.best_similarity == 0.7  # max cosine among fused chunks


async def test_retrieve_without_keyword_hit() -> None:
    a = _chunk("a", 0.8)
    db = AsyncMock()
    db.match_chunks.return_value = [a]
    db.keyword_search.return_value = []

    result = await retrieve(db, _FakeEmbeddings(), "q", top_k=5)

    assert result.keyword_hit is False
    assert result.best_similarity == 0.8
    assert [c.id for c in result.chunks] == [a.id]
