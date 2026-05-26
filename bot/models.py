"""Pydantic models for the knowledge base (project_specs.md §7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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
