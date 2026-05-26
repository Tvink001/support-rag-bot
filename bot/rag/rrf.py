"""Reciprocal Rank Fusion — pure, unit-tested (WOW 1, §17).

Fuses several ranked lists (here: the vector arm + the keyword/FTS arm) into one
ordering by ``score(d) = Σ 1/(k + rank_i(d))`` with ``k=60`` (the standard RRF
constant). A document ranked highly by either retriever rises; appearing in both
compounds. Rank is 1-based. Keys are anything hashable (we fuse chunk ids).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Sequence
from typing import TypeVar

RRF_K = 60

K = TypeVar("K", bound=Hashable)


def reciprocal_rank_fusion(ranked_lists: Sequence[Sequence[K]], *, k: int = RRF_K) -> list[K]:
    """Return keys ordered by descending fused RRF score.

    Ties keep first-seen order (stable). ``k`` damps the weight of low ranks.
    """
    scores: dict[K, float] = defaultdict(float)
    order: list[K] = []  # first-seen order, for stable tie-breaking
    for ranked in ranked_lists:
        for rank, key in enumerate(ranked, start=1):
            if key not in scores:
                order.append(key)
            scores[key] += 1.0 / (k + rank)
    return sorted(order, key=lambda key: scores[key], reverse=True)
