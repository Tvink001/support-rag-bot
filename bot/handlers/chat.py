"""Main RAG dialogue: retrieve → similarity gate → grounded Claude answer (§11, §12).

No conversation memory or real escalation yet (Prompts 5/6). Below
``SIMILARITY_THRESHOLD`` the bot gives an honest "не знаю" placeholder instead of
guessing — real manager escalation is wired in Prompt 6.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from bot.config import get_settings
from bot.llm.claude_client import ClaudeClient
from bot.rag.retrieve import retrieve
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database

logger = logging.getLogger(__name__)

chat_router = Router(name="chat")

_HONEST_DONT_KNOW = (
    "Не нашёл ответа в базе знаний. Передам ваш вопрос менеджеру — он скоро ответит."
)
_ERROR_REPLY = "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже."


@chat_router.message(F.text & ~F.text.startswith("/"))
async def handle_question(
    message: Message,
    db: Database,
    embeddings: EmbeddingService,
    claude: ClaudeClient,
) -> None:
    question = message.text
    if not question:
        return
    settings = get_settings()

    try:
        result = await retrieve(db, embeddings, question, top_k=settings.RETRIEVAL_TOP_K)
    except Exception:
        logger.exception("Retrieval failed")
        await message.answer(_ERROR_REPLY)
        return

    # Similarity gate: if even the best chunk is too weak, refuse honestly (no LLM call).
    if not result.chunks or result.best_similarity < settings.SIMILARITY_THRESHOLD:
        logger.info("below-threshold (best=%.3f) -> honest refusal", result.best_similarity)
        await message.answer(_HONEST_DONT_KNOW)
        return

    try:
        answer = await claude.answer(question, result.chunks)
    except Exception:
        logger.exception("Claude answer failed")
        await message.answer(_ERROR_REPLY)
        return

    logger.info(
        "answer ok | best_sim=%.3f in_tok=%d out_tok=%d cache_read=%d cache_create=%d",
        result.best_similarity,
        answer.input_tokens,
        answer.output_tokens,
        answer.cache_read_tokens,
        answer.cache_creation_tokens,
    )

    text = answer.text or _HONEST_DONT_KNOW
    if answer.sources:
        text += "\n\nИсточник: " + ", ".join(answer.sources)
    # parse_mode=None: Claude's answer is arbitrary text, not HTML — don't parse it.
    await message.answer(text, parse_mode=None)
