"""Reciprocal Rank Fusion ordering (WOW 1, §17)."""

from __future__ import annotations

from bot.rag.rrf import reciprocal_rank_fusion


def test_doc_ranked_high_by_both_arms_wins() -> None:
    vector = ["A", "B", "C"]
    keyword = ["A", "D", "B"]
    fused = reciprocal_rank_fusion([vector, keyword])
    assert fused[0] == "A"  # rank 1 in both arms -> top
    assert fused.index("B") < fused.index("C")  # B (in both) beats C (vector only)


def test_doc_unique_to_one_arm_still_surfaces() -> None:
    vector = ["A", "B"]
    keyword = ["Z"]  # only the keyword arm found Z
    fused = reciprocal_rank_fusion([vector, keyword])
    assert set(fused) == {"A", "B", "Z"}
    assert "Z" in fused


def test_keyword_only_rank1_can_outrank_vector_rank2() -> None:
    # A is vector #1; Z is keyword #1. With one arm each, they tie on score, but a
    # doc appearing in BOTH arms must outrank either single-arm doc.
    vector = ["A", "B"]
    keyword = ["Z", "A"]
    fused = reciprocal_rank_fusion([vector, keyword])
    assert fused[0] == "A"  # A: 1/(60+1) + 1/(60+2) > any single-arm doc


def test_empty_inputs() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_k_constant_changes_weighting() -> None:
    # Smaller k sharpens the advantage of rank-1; ordering of a clear winner is stable.
    fused = reciprocal_rank_fusion([["A", "B"], ["A", "C"]], k=10)
    assert fused[0] == "A"
