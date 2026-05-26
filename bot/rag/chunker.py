"""Pure, deterministic text chunking for ingestion (project_specs.md §10).

Boundary-aware sliding window: walk the text in ~``chunk_size_tokens`` windows,
snapping each window end back to the nearest natural separator (paragraph > line
> sentence > clause > space) so chunks never cut mid-word, with a fixed overlap
between consecutive chunks (**never zero**). Token counts are ESTIMATED from
characters (``chars_per_token``) to keep this function pure and offline — exact
Voyage token counts aren't needed, since chunks sit far below the model's 32k
context limit. Char offsets are exact: ``text[c.char_start:c.char_end] == c.text``.
"""

from __future__ import annotations

from dataclasses import dataclass

# Strongest -> weakest. A window end snaps to the latest separator in range.
_SEPARATORS = ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ")


@dataclass(frozen=True)
class TextChunk:
    """A chunk of text with exact offsets into the source string."""

    text: str
    char_start: int
    char_end: int
    token_estimate: int


def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    """Rough token count from characters (pure/offline; see module docstring)."""
    return round(len(text) / chars_per_token) if text else 0


def chunk_text(
    text: str,
    *,
    chunk_size_tokens: int,
    overlap_tokens: int,
    chars_per_token: int = 4,
) -> list[TextChunk]:
    """Split ``text`` into overlapping, boundary-aligned chunks.

    Raises ``ValueError`` if the size/overlap parameters are inconsistent.
    """
    if chunk_size_tokens <= 0:
        raise ValueError("chunk_size_tokens must be positive")
    if not 0 < overlap_tokens < chunk_size_tokens:
        raise ValueError("overlap_tokens must satisfy 0 < overlap_tokens < chunk_size_tokens")
    if not text or not text.strip():
        return []

    window = chunk_size_tokens * chars_per_token
    overlap = overlap_tokens * chars_per_token
    min_end = max(1, window - overlap)  # earliest a window end may snap back to

    n = len(text)
    chunks: list[TextChunk] = []
    start = 0
    while start < n:
        hard_end = min(start + window, n)
        end = hard_end if hard_end >= n else _snap_end(text, start, hard_end, min_end)
        piece = text[start:end]
        if piece.strip():
            chunks.append(TextChunk(piece, start, end, estimate_tokens(piece, chars_per_token)))
        if end >= n:
            break
        next_start = end - overlap
        if next_start <= start:  # guarantee forward progress (and thus termination)
            next_start = start + 1
        start = next_start
    return chunks


def _snap_end(text: str, start: int, hard_end: int, min_end: int) -> int:
    """End index <= ``hard_end`` snapped to the strongest separator in range."""
    lo = start + min_end  # never snap back before here (avoids tiny chunks)
    for sep in _SEPARATORS:
        idx = text.rfind(sep, lo, hard_end)
        if idx != -1:
            return idx + len(sep)
    return hard_end  # no separator nearby -> hard split
