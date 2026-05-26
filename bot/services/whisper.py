"""Groq Whisper transcription — voice → text (§9.5, §15).

``AsyncGroq`` is natively async (no ``asyncio.to_thread`` needed, unlike Voyage).
``whisper-large-v3-turbo`` is on Groq's permanent free tier and handles RU/UK well
(P2 carryover, learnings #whisper). Telegram voice is OGG/OPUS, which Groq accepts
natively. ``response_format="text"`` may return a plain string or a ``Transcription``
object depending on SDK version — both are handled.
"""

from __future__ import annotations

import logging

from groq import AsyncGroq

from bot.config import Settings

logger = logging.getLogger(__name__)

_MODEL = "whisper-large-v3-turbo"
_MEDIA_TYPE = "audio/ogg"  # Telegram voice messages are OGG/OPUS


class WhisperService:
    """Thin async wrapper over Groq audio transcription."""

    def __init__(self, settings: Settings) -> None:
        self._client = AsyncGroq(api_key=settings.GROQ_API_KEY.get_secret_value())

    async def transcribe(
        self, file_bytes: bytes, filename: str, language: str | None = None
    ) -> str:
        """Transcribe OGG/OPUS bytes to text; ``language`` is an optional ru/uk hint."""
        file = (filename, file_bytes, _MEDIA_TYPE)
        if language:
            raw = await self._client.audio.transcriptions.create(
                model=_MODEL, file=file, response_format="text", language=language
            )
        else:
            raw = await self._client.audio.transcriptions.create(
                model=_MODEL, file=file, response_format="text"
            )
        text = raw if isinstance(raw, str) else getattr(raw, "text", "")
        return str(text).strip()
