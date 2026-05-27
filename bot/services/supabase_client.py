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
from datetime import datetime
from uuid import UUID

import asyncpg

from bot.models import (
    Chunk,
    ConversationTurn,
    Escalation,
    FeedbackContext,
    RetrievedChunk,
    Source,
)

logger = logging.getLogger(__name__)

_CONNECT_RETRIES = 5
_CONNECT_TIMEOUT_SECONDS = 10.0
_CONNECT_BACKOFF_SECONDS = 1.0

# Full column list for the escalations table (maps 1:1 to the Escalation model).
_ESCALATION_COLS = (
    "id, user_id, question, status, manager_id, manager_msg_id, "
    "taken_at, resolved_at, resolution_text, cooldown_until, created_at"
)


def _vector_literal(embedding: list[float]) -> str:
    """Render an embedding as the pgvector text input, e.g. ``[0.1,0.2,...]``."""
    return "[" + ",".join(repr(x) for x in embedding) + "]"


async def _set_search_path(conn: asyncpg.Connection) -> None:
    """Make unqualified ``vector`` / ``<=>`` resolve regardless of pgvector's schema."""
    await conn.execute("set search_path = public, extensions")


def _row_to_chunk(row: asyncpg.Record) -> RetrievedChunk:
    """Map a retrieval row (match_chunks / keyword_search) to a ``RetrievedChunk``."""
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta)
    return RetrievedChunk(
        id=row["id"],
        source_id=row["source_id"],
        chunk_index=row["chunk_index"],
        content=row["content"],
        similarity=row["similarity"],
        filename=row["filename"],
        metadata=meta or {},
    )


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

    async def get_source(self, source_id: UUID) -> Source | None:
        row = await self.pool.fetchrow(
            "select id, filename, file_type, chunk_count, uploaded_at, status "
            "from public.sources where id = $1",
            source_id,
        )
        return Source.model_validate(dict(row)) if row is not None else None

    async def soft_delete_source(self, source_id: UUID) -> int | None:
        """Soft-delete an active source and hard-delete its chunks (atomic).

        Returns the number of chunks removed, or ``None`` if the source was not
        found / already deleted (so a repeat ``/delete`` is a clean no-op). The
        source row is kept (status='deleted') for audit; only the vectors go.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "update public.sources set status = 'deleted' "
                    "where id = $1 and status = 'active' returning chunk_count",
                    source_id,
                )
                if row is None:
                    return None
                await conn.execute("delete from public.chunks where source_id = $1", source_id)
        chunk_count: int = row["chunk_count"]
        return chunk_count

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
        return [_row_to_chunk(r) for r in rows]

    async def keyword_search(
        self, query_embedding: list[float], query_text: str, match_count: int
    ) -> list[RetrievedChunk]:
        """Full-text (BM25-style) arm of hybrid search, ranked by ts_rank_cd (§17).

        Returns only chunks whose ``fts`` matches the query, ordered by keyword rank;
        each row also carries its cosine ``similarity`` (for the hybrid gate).
        """
        rows = await self.pool.fetch(
            "select id, source_id, chunk_index, content, similarity, metadata, filename "
            "from public.keyword_search($1::vector, $2, $3)",
            _vector_literal(query_embedding),
            query_text,
            match_count,
        )
        return [_row_to_chunk(r) for r in rows]

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

    # --- escalations (§14) ----------------------------------------------------
    async def get_active_escalation(self, user_id: int) -> Escalation | None:
        """The user's still-active escalation: open, or taken within its cooldown."""
        row = await self.pool.fetchrow(
            f"select {_ESCALATION_COLS} from public.escalations "
            "where user_id = $1 "
            "  and (status = 'open' or (status = 'taken' and cooldown_until > now())) "
            "order by created_at desc limit 1",
            user_id,
        )
        return Escalation.model_validate(dict(row)) if row is not None else None

    async def get_escalation(self, escalation_id: UUID) -> Escalation | None:
        row = await self.pool.fetchrow(
            f"select {_ESCALATION_COLS} from public.escalations where id = $1",
            escalation_id,
        )
        return Escalation.model_validate(dict(row)) if row is not None else None

    async def create_escalation(self, user_id: int, question: str) -> UUID:
        """Open a new escalation; return its id."""
        escalation_id: UUID = await self.pool.fetchval(
            "insert into public.escalations (user_id, question, status) "
            "values ($1, $2, 'open') returning id",
            user_id,
            question,
        )
        return escalation_id

    async def set_escalation_manager_msg(self, escalation_id: UUID, manager_msg_id: int) -> None:
        """Record the managers'-chat message id (so the post can be edited later)."""
        await self.pool.execute(
            "update public.escalations set manager_msg_id = $2 where id = $1",
            escalation_id,
            manager_msg_id,
        )

    async def take_escalation(
        self, escalation_id: UUID, manager_id: int, cooldown_until: datetime
    ) -> bool:
        """Transition open → taken (idempotent). True if this call did the transition.

        Only an ``open`` escalation flips, so a double-tap of *Взять* is a no-op
        (returns False) — write-after-success: the cooldown is set only here.
        """
        updated = await self.pool.fetchval(
            "update public.escalations "
            "set status = 'taken', manager_id = $2, taken_at = now(), cooldown_until = $3 "
            "where id = $1 and status = 'open' returning id",
            escalation_id,
            manager_id,
            cooldown_until,
        )
        return updated is not None

    async def resolve_escalation(
        self, escalation_id: UUID, resolution_text: str
    ) -> Escalation | None:
        """Mark an open/taken escalation resolved with the manager's reply; return the row."""
        row = await self.pool.fetchrow(
            "update public.escalations "
            "set status = 'resolved', resolution_text = $2, resolved_at = now() "
            f"where id = $1 and status in ('open', 'taken') returning {_ESCALATION_COLS}",
            escalation_id,
            resolution_text,
        )
        return Escalation.model_validate(dict(row)) if row is not None else None
