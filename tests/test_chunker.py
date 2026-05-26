"""Unit tests for the pure chunker — size, overlap, boundaries, exact offsets."""

from __future__ import annotations

import pytest

from bot.rag.chunker import chunk_text, estimate_tokens

# Long enough to force many chunks; uses ". " sentence separators.
_PROSE = (
    "Supabase is an open source Firebase alternative. "
    "It provides a Postgres database, authentication, instant APIs, and storage. "
    "pgvector adds vector similarity search to Postgres. "
) * 20


def test_returns_empty_for_blank() -> None:
    assert chunk_text("", chunk_size_tokens=100, overlap_tokens=10) == []
    assert chunk_text("   \n  ", chunk_size_tokens=100, overlap_tokens=10) == []


@pytest.mark.parametrize("bad_overlap", [0, 100, 150])
def test_rejects_bad_overlap(bad_overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text(_PROSE, chunk_size_tokens=100, overlap_tokens=bad_overlap)


def test_offsets_are_exact() -> None:
    chunks = chunk_text(_PROSE, chunk_size_tokens=60, overlap_tokens=10)
    assert len(chunks) > 1
    for c in chunks:
        assert _PROSE[c.char_start : c.char_end] == c.text


def test_consecutive_chunks_overlap() -> None:
    chunks = chunk_text(_PROSE, chunk_size_tokens=60, overlap_tokens=10)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.char_start < prev.char_end  # non-zero overlap
        assert nxt.char_start > prev.char_start  # forward progress


@pytest.mark.parametrize(("size", "overlap"), [(40, 8), (80, 16), (120, 30)])
def test_chunks_respect_size(size: int, overlap: int) -> None:
    chars_per_token = 4
    chunks = chunk_text(
        _PROSE, chunk_size_tokens=size, overlap_tokens=overlap, chars_per_token=chars_per_token
    )
    assert chunks
    for c in chunks:
        assert len(c.text) <= size * chars_per_token


def test_covers_full_text() -> None:
    chunks = chunk_text(_PROSE, chunk_size_tokens=60, overlap_tokens=10)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(_PROSE)


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40, chars_per_token=4) == 10
