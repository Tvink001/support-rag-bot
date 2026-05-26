"""Anthropic Claude client — grounded answers with native citations (§9.2, §12).

Per OQ-2 (Prompt 1), citations and structured outputs are mutually exclusive, so
the answer call uses **citations only**. The system prompt is cached with
``cache_control: ephemeral``; retrieved chunks are passed as ``document`` blocks
with citations enabled (never cached — they change per query). Retries are the
SDK's built-in ``max_retries`` (exponential backoff honoring ``retry-after`` on
429/529/5xx) — see the note in §12; no extra tenacity layer is needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, TextBlockParam

from bot.config import Settings
from bot.llm.prompts import SYSTEM_PROMPT
from bot.models import ConversationTurn, RetrievedChunk

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_TIMEOUT_SECONDS = 30.0


@dataclass
class AnswerResult:
    text: str
    sources: list[str]  # cited source filenames, de-duplicated, in first-seen order
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class ClaudeClient:
    """Thin async wrapper over Anthropic Messages for grounded, cited answers."""

    def __init__(self, settings: Settings) -> None:
        self._client = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
            max_retries=_MAX_RETRIES,
        )
        self._model = settings.ANTHROPIC_MODEL
        self._max_tokens = settings.ANTHROPIC_MAX_TOKENS

    async def answer(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[ConversationTurn] | None = None,
    ) -> AnswerResult:
        system = cast(
            "list[TextBlockParam]",
            [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        )
        documents = [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": c.content},
                "title": c.filename,
                "citations": {"enabled": True},
            }
            for c in chunks
        ]
        # Prior turns are plain text (§13); document blocks + citations live ONLY in
        # the current question turn (Anthropic Messages API, Context7-verified 2026-05-27).
        prior = [{"role": turn.role, "content": turn.content} for turn in (history or [])]
        messages = cast(
            "list[MessageParam]",
            [*prior, {"role": "user", "content": [*documents, {"type": "text", "text": question}]}],
        )

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,
            timeout=_TIMEOUT_SECONDS,
        )

        parts: list[str] = []
        sources: list[str] = []
        for block in response.content:
            if block.type != "text":
                continue
            parts.append(block.text)
            for citation in block.citations or []:
                title = getattr(citation, "document_title", None)
                index = getattr(citation, "document_index", None)
                cited = getattr(citation, "cited_text", "")
                # Post-verify the cited span actually exists in the referenced chunk.
                if isinstance(index, int) and 0 <= index < len(chunks) and cited:
                    if cited not in chunks[index].content:
                        logger.warning("Cited text not found in chunk index %d", index)
                if isinstance(title, str) and title and title not in sources:
                    sources.append(title)

        usage = response.usage
        return AnswerResult(
            text="".join(parts).strip(),
            sources=sources,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_tokens=usage.cache_creation_input_tokens or 0,
        )
