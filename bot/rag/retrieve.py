"""Hybrid retrieval: vector (pgvector cosine) + keyword (Postgres FTS) fused via RRF (§17).

WOW 1. Each arm returns its top-k; we fuse the two rankings with Reciprocal Rank
Fusion (``bot/rag/rrf.py``, pure) into the final top-k. ``best_similarity`` is the
strongest cosine among the fused chunks (every chunk carries one — the keyword arm
computes it too), and ``keyword_hit`` flags that the FTS arm matched at all. The
chat handler's gate stays meaningful: it escalates only when the vector arm is weak
AND there is no keyword hit — so a rare term / SKU that vector misses but keyword
nails (low cosine) is still answered instead of escalated.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.models import RetrievedChunk
from bot.rag.rrf import reciprocal_rank_fusion
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    best_similarity: float
    keyword_hit: bool


async def retrieve(
    db: Database, embeddings: EmbeddingService, question: str, top_k: int
) -> RetrievalResult:
    """Embed the query, run both arms, fuse via RRF, return the top-k chunks."""
    query_embedding = await embeddings.embed_query(question)
    vector_hits = await db.match_chunks(query_embedding, match_count=top_k, min_similarity=0.0)
    keyword_hits = await db.keyword_search(query_embedding, question, match_count=top_k)

    by_id = {chunk.id: chunk for chunk in (*vector_hits, *keyword_hits)}
    fused_ids = reciprocal_rank_fusion([[c.id for c in vector_hits], [c.id for c in keyword_hits]])
    chunks = [by_id[cid] for cid in fused_ids[:top_k]]

    best_similarity = max((c.similarity for c in chunks), default=0.0)
    return RetrievalResult(
        chunks=chunks, best_similarity=best_similarity, keyword_hit=bool(keyword_hits)
    )
