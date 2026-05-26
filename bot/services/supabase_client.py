"""Async Postgres access for Supabase (pgvector + state tables).

Per §9.4 / OQ-4, raw SQL goes through ``asyncpg`` over ``DATABASE_URL`` (the
session pooler, port 5432). Embeddings are written as the canonical pgvector text
literal (``'[...]'::extensions.vector``) — robust across drivers, no codec/numpy
coupling. Connects retry transient ``OSError`` (DNS/network blips); SSL via
``ssl="require"``. The pool is created once and reused for the bot's lifetime.
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

import asyncpg

from bot.models import Chunk, ConversationTurn, FeedbackContext, RetrievedChunk, Source

logger = logging.getLogger(__name__)

_CONNECT_RETRIES = 5
_CONNECT_TIMEOUT_SECONDS = 10.0
_CONNECT_BACKOFF_SECONDS = 1.0


def _vector_literal(embedding: list[float]) -> str:
    """Render an embedding as the pgvector text input, e.g. ``[0.1,0.2,...]``."""
    return "[" + ",".join(repr(x) for x in embedding) + "]"


async def _set_search_path(conn: asyncpg.Connection) -> None:
    """Make unqualified ``vector`` / ``<=>`` resolve regardless of pgvector's schema."""
    await conn.execute("set search_path = public, extensions")


class Database:
    """asyncpg connection pool + the queries ingestion/admin need."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() must be called before use")
        return self._pool

    async def connect(self) -> None:
        """Create the pool, retrying transient network/DNS failures."""
        for attempt in range(1, _CONNECT_RETRIES + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    ssl="require",
                    min_size=1,
                    max_size=5,
                    timeout=_CONNECT_TIMEOUT_SECONDS,
                    init=_set_search_path,
                )
                return
            except OSError as exc:
                logger.warning(
                    "DB pool connect attempt %d/%d failed (transient): %s",
                    attempt,
                    _CONNECT_RETRIES,
                    exc,
                )
                if attempt >= _CONNECT_RETRIES:
                    raise
                await asyncio.sleep(_CONNECT_BACKOFF_SECONDS * attempt)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> None:
        """Prove connectivity + creds with ``SELECT 1``."""
        value = await self.pool.fetchval("SELECT 1")
        if value != 1:
            raise RuntimeError(f"Unexpected SELECT 1 result: {value!r}")

    async def find_active_source_by_hash(self, sha256: str) -> Source | None:
        row = await self.pool.fetchrow(
            "select id, filename, file_type, chunk_count, uploaded_at, status "
            "from public.sources where sha256 = $1 and status = 'active'",
            sha256,
        )
        return Source.model_validate(dict(row)) if row is not None else None

    async def ingest_source_with_chunks(
        self,
        *,
        filename: str,
        file_type: str,
        uploaded_by: int,
        sha256: str,
        priority: int,
        chunks: list[Chunk],
    ) -> UUID:
        """Insert a source and all its chunks in one transaction; return its id."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                source_id: UUID = await conn.fetchval(
                    "insert into public.sources "
                    "(filename, file_type, uploaded_by, sha256, priority, status, chunk_count) "
                    "values ($1, $2, $3, $4, $5, 'active', $6) returning id",
                    filename,
                    file_type,
                    uploaded_by,
                    sha256,
                    priority,
                    len(chunks),
                )
                await conn.executemany(
                    "insert into public.chunks "
                    "(source_id, chunk_index, content, embedding, token_count, priority, metadata) "
                    "values ($1, $2, $3, $4::vector, $5, $6, $7::jsonb)",
                    [
                        (
                            source_id,
                            c.chunk_index,
                            c.content,
                            _vector_literal(c.embedding),
                            c.token_count,
                            priority,
                            json.dumps(c.metadata),
                        )
                        for c in chunks
                    ],
                )
        return source_id

    async def list_active_sources(self) -> list[Source]:
        rows = await self.pool.fetch(
            "select id, filename, file_type, chunk_count, uploaded_at, status "
            "from public.sources where status = 'active' order by uploaded_at desc"
        )
        return [Source.model_validate(dict(r)) for r in rows]

    async def match_chunks(
        self, query_embedding: list[float], match_count: int, min_similarity: float = 0.0
    ) -> list[RetrievedChunk]:
        """Vector-only cosine search via the match_chunks() SQL function (§11)."""
        rows = await self.pool.fetch(
            "select id, source_id, chunk_index, content, similarity, metadata, filename "
            "from public.match_chunks($1::vector, $2, $3)",
            _vector_literal(query_embedding),
            match_count,
            min_similarity,
        )
        chunks: list[RetrievedChunk] = []
        for r in rows:
            meta = r["metadata"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            chunks.append(
                RetrievedChunk(
                    id=r["id"],
                    source_id=r["source_id"],
                    chunk_index=r["chunk_index"],
                    content=r["content"],
                    similarity=r["similarity"],
                    filename=r["filename"],
                    metadata=meta or {},
                )
            )
        return chunks

    # --- conversation memory (§13) -------------------------------------------
    async def append_message(self, user_id: int, role: str, content: str) -> int:
        """Persist one conversation turn; return its ``messages.id``."""
        message_id: int = await self.pool.fetchval(
            "insert into public.messages (user_id, role, content) values ($1, $2, $3) returning id",
            user_id,
            role,
            content,
        )
        return message_id

    async def load_recent_messages(self, user_id: int, limit: int) -> list[ConversationTurn]:
        """Return the last ``limit`` turns for ``user_id`` in chronological order."""
        rows = await self.pool.fetch(
            "select role, content from public.messages "
            "where user_id = $1 order by id desc limit $2",
            user_id,
            limit,
        )
        # fetched newest-first for the LIMIT; reverse to chronological for the prompt.
        return [ConversationTurn(role=r["role"], content=r["content"]) for r in reversed(rows)]

    # --- feedback (§16) -------------------------------------------------------
    async def get_feedback_context(self, assistant_msg_id: int) -> FeedbackContext | None:
        """Recover the (user, question, answer) a 👍/👎 tap refers to.

        ``assistant_msg_id`` is the ``messages.id`` of the answer the buttons hang
        under; the question is the user's most recent turn before it.
        """
        answer_row = await self.pool.fetchrow(
            "select user_id, content from public.messages where id = $1 and role = 'assistant'",
            assistant_msg_id,
        )
        if answer_row is None:
            return None
        question_row = await self.pool.fetchrow(
            "select content from public.messages "
            "where user_id = $1 and role = 'user' and id < $2 order by id desc limit 1",
            answer_row["user_id"],
            assistant_msg_id,
        )
        return FeedbackContext(
            user_id=answer_row["user_id"],
            question=question_row["content"] if question_row is not None else "",
            answer=answer_row["content"],
        )

    async def record_feedback(
        self,
        *,
        user_id: int,
        question: str,
        answer: str,
        rating: int,
        cited_source_ids: list[str],
    ) -> bool:
        """Upsert a feedback row keyed by (user, question, answer); idempotent.

        A second tap on the same answer updates the rating in place rather than
        adding a row, so a double-tap never duplicates. Returns True if a new row
        was inserted, False if an existing one was updated.
        """
        payload = json.dumps(cited_source_ids)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                updated = await conn.fetchval(
                    "update public.feedback "
                    "set rating = $4, cited_source_ids = $5::jsonb, created_at = now() "
                    "where user_id = $1 and question = $2 and answer = $3 returning id",
                    user_id,
                    question,
                    answer,
                    rating,
                    payload,
                )
                if updated is not None:
                    return False
                await conn.execute(
                    "insert into public.feedback "
                    "(user_id, question, answer, rating, cited_source_ids) "
                    "values ($1, $2, $3, $4, $5::jsonb)",
                    user_id,
                    question,
                    answer,
                    rating,
                    payload,
                )
        return True
