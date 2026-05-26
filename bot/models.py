"""Pydantic models for the knowledge base (project_specs.md §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Source(BaseModel):
    """A row of the ``sources`` table (an uploaded document)."""

    id: UUID
    filename: str
    file_type: str
    chunk_count: int
    uploaded_at: datetime
    status: str = "active"


class Chunk(BaseModel):
    """A knowledge-base chunk ready to be inserted into ``chunks``.

    ``embedding`` is the Voyage vector (length ``VOYAGE_EMBED_DIM`` = 1024);
    ``metadata`` carries ``{page, char_start, char_end}``.
    """

    chunk_index: int
    content: str
    embedding: list[float]
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0


class RetrievedChunk(BaseModel):
    """A chunk returned by retrieval (``match_chunks``) with its similarity + source."""

    id: UUID
    source_id: UUID
    chunk_index: int
    content: str
    similarity: float
    filename: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    """One stored message in a user's conversation memory (``messages`` table, §13)."""

    role: Literal["user", "assistant"]
    content: str


class FeedbackContext(BaseModel):
    """The question/answer pair a 👍/👎 tap refers to, recovered from ``messages`` (§16)."""

    user_id: int
    question: str
    answer: str
