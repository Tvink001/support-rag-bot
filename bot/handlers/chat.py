"""Main RAG dialogue: cooldown gate → memory → retrieve → answer / escalate (§11–§14).

Restricted to **private** chats so the bot never RAG-answers in the managers' group.
At the top it honours an active escalation (silent during a manager's cooldown,
reassures while still queued). Below ``SIMILARITY_THRESHOLD`` — or when Claude emits
the ``needs_human`` sentinel — it escalates instead of guessing (§14). Otherwise it
prepends conversation memory (§13), answers with citations, persists the turn pair,
and attaches 👍/👎 buttons (§16).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import InlineKeyboardMarkup, Message

from bot.config import get_settings
from bot.handlers.escalation import escalate, is_below_threshold, is_in_cooldown
from bot.handlers.feedback import SOURCE_FOOTER_PREFIX, build_feedback_keyboard
from bot.llm.claude_client import ClaudeClient
from bot.memory.conversation import ConversationMemory
from bot.models import ConversationTurn
from bot.rag.retrieve import retrieve
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database

logger = logging.getLogger(__name__)

chat_router = Router(name="chat")

_ALREADY_QUEUED = "Ваш вопрос уже передан менеджеру — ожидайте ответа 🙏"
_HONEST_DONT_KNOW = (
    "Не нашёл ответа в базе знаний. Передам ваш вопрос менеджеру — он скоро ответит."
)
_ERROR_REPLY = "⚠️ Что-то пошло не так. Попробуйте ещё раз чуть позже."


@chat_router.message(F.text & ~F.text.startswith("/") & (F.chat.type == "private"))
async def handle_question(
    message: Message,
    bot: Bot,
    db: Database,
    embeddings: EmbeddingService,
    claude: ClaudeClient,
) -> None:
    question = message.text
    if not question:
        return
    settings = get_settings()
    user = message.from_user

    # Escalation gate (§14): silent during a cooldown; reassure while still queued.
    if user is not None:
        try:
            active = await db.get_active_escalation(user.id)
        except Exception:
            logger.exception("active-escalation check failed; continuing")
            active = None
        if active is not None:
            if is_in_cooldown(active.status, active.cooldown_until, datetime.now(timezone.utc)):
                logger.info("user %s within escalation cooldown -> staying silent", user.id)
                return
            if active.status == "open":
                await message.answer(_ALREADY_QUEUED)
                return

    try:
        result = await retrieve(db, embeddings, question, top_k=settings.RETRIEVAL_TOP_K)
    except Exception:
        logger.exception("Retrieval failed")
        await message.answer(_ERROR_REPLY)
        return

    # Retrieval-gate trigger: if even the best chunk is too weak, escalate (no LLM call).
    if is_below_threshold(
        result.best_similarity, settings.SIMILARITY_THRESHOLD, bool(result.chunks)
    ):
        logger.info("below-threshold (best=%.3f) -> escalating", result.best_similarity)
        await escalate(message, db=db, bot=bot, question=question, user=user)
        return

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

    # needs_human trigger: Claude couldn't answer from the retrieved context (§14).
    if answer.needs_human:
        logger.info("Claude signalled needs_human -> escalating")
        await escalate(message, db=db, bot=bot, question=question, user=user)
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
