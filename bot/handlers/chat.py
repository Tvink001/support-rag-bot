"""Main RAG dialogue: memory → retrieve → similarity gate → grounded answer (§11–§13).

Conversation memory (last ``CONVERSATION_MEMORY_TURNS`` messages) is prepended to
the Claude call, and the new user+assistant pair is persisted afterward (§13).
Every grounded answer ships 👍/👎 feedback buttons (§16). Below
``SIMILARITY_THRESHOLD`` the bot refuses honestly without calling the LLM — real
manager escalation is wired in Prompt 6.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.handlers.feedback import SOURCE_FOOTER_PREFIX, build_feedback_keyboard
from bot.llm.claude_client import ClaudeClient
from bot.memory.conversation import ConversationMemory
from bot.models import ConversationTurn
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

    user = message.from_user
    memory = ConversationMemory(db)

    history: list[ConversationTurn] = []
    if user is not None:
        try:
            history = await memory.load_recent(user.id, settings.CONVERSATION_MEMORY_TURNS)
        except Exception:
            logger.exception("Loading conversation memory failed; answering without it")

    try:
        answer = await claude.answer(question, result.chunks, history=history)
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
        text += "\n\n" + SOURCE_FOOTER_PREFIX + ", ".join(answer.sources)

    # Persist the turn pair and hang feedback buttons off the assistant message (§13/§16).
    reply_markup: InlineKeyboardMarkup | None = None
    if user is not None:
        try:
            await memory.append(user.id, "user", question)
            assistant_msg_id = await memory.append(user.id, "assistant", answer.text)
            reply_markup = build_feedback_keyboard(assistant_msg_id)
        except Exception:
            logger.exception("Persisting memory/feedback failed; sending answer without buttons")

    # parse_mode=None: Claude's answer is arbitrary text, not HTML — don't parse it.
    await message.answer(text, parse_mode=None, reply_markup=reply_markup)
