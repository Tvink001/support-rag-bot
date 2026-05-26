"""Retrieval: embed the question and run vector-only match_chunks (§11).

v1 baseline = vector-only cosine search. WOW 1 (§17) upgrades this to hybrid.
The similarity gate itself lives in the chat handler, which compares
``best_similarity`` against ``SIMILARITY_THRESHOLD``.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.models import RetrievedChunk
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    best_similarity: float


async def retrieve(
    db: Database, embeddings: EmbeddingService, question: str, top_k: int
) -> RetrievalResult:
    """Embed the question (input_type='query') and return the top-k chunks ranked."""
    query_embedding = await embeddings.embed_query(question)
    chunks = await db.match_chunks(query_embedding, match_count=top_k, min_similarity=0.0)
    best_similarity = chunks[0].similarity if chunks else 0.0
    return RetrievalResult(chunks=chunks, best_similarity=best_similarity)
