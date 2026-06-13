"""Voice input: Telegram voice → Groq Whisper → the same RAG pipeline (§15).

Caps the clip at ~1 MB (a question, not a podcast), downloads it, transcribes via
``WhisperService``, then hands the transcript to the shared ``answer_question``
pipeline (retrieve → answer / escalate). A transcription failure is a user-flow
event, not a bug: friendly fallback, stay in flow, never raise to the global
handler (learnings #error-handling).
"""

from __future__ import annotations

import logging
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.handlers.chat import answer_question
from bot.llm.claude_client import ClaudeClient
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database
from bot.services.whisper import WhisperService

logger = logging.getLogger(__name__)

voice_router = Router(name="voice")

MAX_VOICE_BYTES = 1024 * 1024  # ~1 MB cap at the handler (§9.1)
_TOO_LARGE = "Voice message too long. Ask a shorter question or type it out 🙏"
_TRANSCRIBE_FAILED = "Couldn't recognize the voice. Please type your question 🙏"


@voice_router.message(F.voice & (F.chat.type == "private"))
async def handle_voice(
    message: Message,
    bot: Bot,
    db: Database,
    embeddings: EmbeddingService,
    claude: ClaudeClient,
    whisper: WhisperService,
) -> None:
    voice = message.voice
    if voice is None:  # guaranteed by F.voice, but keeps the type checker happy
        return
    if voice.file_size is not None and voice.file_size > MAX_VOICE_BYTES:
        await message.answer(_TOO_LARGE)
        return

    try:
        buffer = BytesIO()
        await bot.download(voice, destination=buffer)
        transcript = await whisper.transcribe(
            buffer.getvalue(), filename=f"{voice.file_unique_id}.ogg"
        )
    except Exception:
        logger.exception("Voice transcription failed")
        await message.answer(_TRANSCRIBE_FAILED)
        return

    if not transcript:
        await message.answer(_TRANSCRIBE_FAILED)
        return

    logger.info("voice transcribed (%d chars) -> RAG pipeline", len(transcript))
    await answer_question(
        message, question=transcript, bot=bot, db=db, embeddings=embeddings, claude=claude
    )
