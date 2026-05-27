"""Async Voyage AI embeddings wrapper (project_specs.md §9.3).

Voyage's Python SDK is synchronous, so calls are offloaded with
``asyncio.to_thread`` (constraint #5). ``input_type`` is ``"document"`` at ingest
and ``"query"`` at retrieval; ``output_dimension`` is pinned to
``VOYAGE_EMBED_DIM`` so it always matches the pgvector column.
"""

from __future__ import annotations

import asyncio
import logging

import voyageai

from bot.config import Settings

logger = logging.getLogger(__name__)

_BATCH_SIZE = 128  # Voyage's recommended per-call batch (§9.3)


class EmbeddingService:
    """Thin async wrapper over the Voyage embeddings client."""

    def __init__(self, settings: Settings) -> None:
        self._client = voyageai.Client(api_key=settings.VOYAGE_API_KEY.get_secret_value())
        self._model = settings.VOYAGE_MODEL
        self._dim = settings.VOYAGE_EMBED_DIM

    def _embed_sync(self, texts: list[str], input_type: str) -> list[list[float]]:
        result = self._client.embed(
            texts, model=self._model, input_type=input_type, output_dimension=self._dim
        )
        embeddings: list[list[float]] = result.embeddings
        return embeddings

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed chunks for storage (batched, ``input_type='document'``)."""
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            out.extend(await asyncio.to_thread(self._embed_sync, batch, "document"))
        return out

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string (``input_type='query'``)."""
        result = await asyncio.to_thread(self._embed_sync, [text], "query")
        return result[0]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Embed several queries in batches (``input_type='query'``).

        Used by the golden-set eval to embed all queries in as few calls as
        possible (Voyage free tier is 3 RPM — batching avoids the limit).
        """
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            out.extend(
                await asyncio.to_thread(self._embed_sync, texts[i : i + _BATCH_SIZE], "query")
            )
        return out
