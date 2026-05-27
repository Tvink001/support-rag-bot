"""WOW 2: auto-learn FAQ — one high-priority source, idempotent on double-tap (§18)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from bot.handlers.escalation import SaveFaqCB, on_save_faq
from bot.models import Chunk, Escalation, Source
from bot.rag.ingest import ingest_faq

_DIM = 1024


class _FakeEmbeddings:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * _DIM for _ in texts]


class _FakeDB:
    def __init__(self) -> None:
        self.sources: list[tuple[Source, list[Chunk], int]] = []
        self._by_hash: dict[str, Source] = {}

    async def find_active_source_by_hash(self, sha256: str) -> Source | None:
        return self._by_hash.get(sha256)

    async def ingest_source_with_chunks(
        self,
        *,
        filename: str,
        file_type: str,
        uploaded_by: int,
        sha256: str,
        priority: int,
        chunks: list[Chunk],
    ) -> object:
        source = Source(
            id=uuid4(),
            filename=filename,
            file_type=file_type,
            chunk_count=len(chunks),
            uploaded_at=datetime.now(timezone.utc),
            status="active",
        )
        self.sources.append((source, chunks, priority))
        self._by_hash[sha256] = source
        return source.id


async def test_ingest_faq_creates_one_high_priority_source_idempotently() -> None:
    db, emb = _FakeDB(), _FakeEmbeddings()
    kwargs = dict(db=db, embeddings=emb, created_by=1, chunk_size_tokens=500, overlap_tokens=50)

    first = await ingest_faq(question="Как вернуть товар?", answer="В течение 14 дней.", **kwargs)  # type: ignore[arg-type]
    assert first.skipped is False
    assert len(db.sources) == 1
    source, chunks, priority = db.sources[0]
    assert source.file_type == "faq"
    assert priority == 100
    assert len(chunks) >= 1
    assert all(c.priority == 100 for c in chunks)  # elevated priority on every chunk

    # double-tap with the same Q/A -> dedup, no second source
    second = await ingest_faq(question="Как вернуть товар?", answer="В течение 14 дней.", **kwargs)  # type: ignore[arg-type]
    assert second.skipped is True
    assert len(db.sources) == 1


def _faq_query() -> AsyncMock:
    query = AsyncMock()
    query.from_user = SimpleNamespace(id=9)
    query.message = SimpleNamespace(html_text="offer", text="offer", edit_text=AsyncMock())
    return query


async def test_on_save_faq_saves_then_double_tap_is_noop() -> None:
    db, emb = _FakeDB(), _FakeEmbeddings()
    escalation = Escalation(
        id=uuid4(), user_id=5, question="Q?", status="resolved", resolution_text="Ответ менеджера."
    )
    db.get_escalation = AsyncMock(return_value=escalation)  # type: ignore[attr-defined]
    cb = SaveFaqCB(action="save", escalation_id=str(escalation.id))

    await on_save_faq(_faq_query(), callback_data=cb, db=db, embeddings=emb)  # type: ignore[arg-type]
    assert len(db.sources) == 1

    await on_save_faq(_faq_query(), callback_data=cb, db=db, embeddings=emb)  # type: ignore[arg-type]
    assert len(db.sources) == 1  # second tap deduped — no duplicate FAQ
