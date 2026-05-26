"""Manager escalation: honest hand-off + Take/Suggest + per-user cooldown (§14).

Triggered by the chat handler when retrieval is below threshold OR Claude emits the
``needs_human`` sentinel. Flow: open an ``escalations`` row → tell the user honestly
→ post the question to ``MANAGER_CHAT_ID`` with **Взять** / **Предложить ответ**
buttons. *Взять* sets a per-user ``cooldown_until`` (the bot then stays silent for
that user, checked at the top of the chat handler). *Предложить ответ* captures the
manager's next message as ``resolution_text`` and relays it to the user (this also
sets up WOW 2's "save as FAQ?" offer, added in Prompt 10).

Idempotency (learnings #idempotency #telegram): status flips only after the side
effect succeeds; ``take_escalation`` only transitions an ``open`` row, so a
double-tap is a no-op; "message is not modified" on a re-edit is swallowed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.text_decorations import html_decoration as html

from bot.config import get_settings
from bot.services.supabase_client import Database

logger = logging.getLogger(__name__)

escalation_router = Router(name="escalation")

ESCALATED_TO_USER = "Не нашёл ответ в базе знаний — передаю ваш вопрос менеджеру. Ответят скоро 🙏"


class EscalateCB(CallbackData, prefix="esc"):
    """``esc:<action>:<escalations.id>`` — action is take | suggest."""

    action: str
    escalation_id: str


class ManagerFlow(StatesGroup):
    awaiting_suggestion = State()


# --- pure helpers (unit-tested; no aiogram/DB deps) --------------------------
def is_below_threshold(best_similarity: float, threshold: float, has_chunks: bool) -> bool:
    """The retrieval-gate escalation trigger (§11/§14)."""
    return (not has_chunks) or best_similarity < threshold


def is_in_cooldown(status: str, cooldown_until: datetime | None, now: datetime) -> bool:
    """True while a taken escalation's cooldown is still in effect (bot stays silent)."""
    return status == "taken" and cooldown_until is not None and cooldown_until > now


def compute_cooldown_until(now: datetime, hours: int) -> datetime:
    """When the per-user mute expires after a manager takes the escalation."""
    return now + timedelta(hours=hours)


def _manager_post(user: User, question: str) -> str:
    name = html.quote(user.full_name) if user.full_name else "пользователь"
    handle = f" (@{user.username})" if user.username else ""
    return (
        "🆘 <b>Новый вопрос — нужна помощь менеджера</b>\n\n"
        f"👤 {name}{handle} · id <code>{user.id}</code>\n\n"
        f"❓ {html.quote(question)}"
    )


def build_escalation_keyboard(escalation_id: UUID) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    ref = str(escalation_id)
    builder.button(text="✅ Взять", callback_data=EscalateCB(action="take", escalation_id=ref))
    builder.button(
        text="✍️ Предложить ответ", callback_data=EscalateCB(action="suggest", escalation_id=ref)
    )
    builder.adjust(2)
    return builder.as_markup()


async def escalate(
    message: Message, *, db: Database, bot: Bot, question: str, user: User | None
) -> None:
    """Open an escalation, tell the user, and post it to the managers' chat (§14)."""
    if user is None:  # can't track an escalation without a user id
        await message.answer(ESCALATED_TO_USER)
        return

    settings = get_settings()
    try:
        escalation_id = await db.create_escalation(user.id, question)
    except Exception:
        logger.exception("Failed to open escalation for user %s", user.id)
        await message.answer(ESCALATED_TO_USER)  # still honest to the user
        return

    await message.answer(ESCALATED_TO_USER)

    # write-after-success: the row is open before we post; save msg_id only after.
    try:
        sent = await bot.send_message(
            settings.MANAGER_CHAT_ID,
            _manager_post(user, question),
            reply_markup=build_escalation_keyboard(escalation_id),
        )
        await db.set_escalation_manager_msg(escalation_id, sent.message_id)
    except Exception:
        logger.exception("Failed to post escalation %s to managers chat", escalation_id)


async def _strike_buttons(message: object, note: str) -> None:
    """Append a status note to the managers' post and drop its buttons (idempotent)."""
    edit = getattr(message, "edit_text", None)
    if edit is None:
        return
    base = getattr(message, "html_text", None) or getattr(message, "text", None) or ""
    try:
        await edit(f"{base}\n\n{note}", reply_markup=None)
    except TelegramBadRequest:
        logger.debug("managers' post already updated (double-tap)")


@escalation_router.callback_query(EscalateCB.filter(F.action == "take"))
async def on_take(query: CallbackQuery, callback_data: EscalateCB, db: Database) -> None:
    manager = query.from_user
    settings = get_settings()
    cooldown_until = compute_cooldown_until(
        datetime.now(timezone.utc), settings.ESCALATION_COOLDOWN_HOURS
    )
    took = await db.take_escalation(UUID(callback_data.escalation_id), manager.id, cooldown_until)
    if not took:
        await query.answer("Уже в работе.")
        return
    await query.answer("Взято в работу ✅")
    await _strike_buttons(query.message, f"✅ В работе у {html.quote(manager.full_name)}")


@escalation_router.callback_query(EscalateCB.filter(F.action == "suggest"))
async def on_suggest(query: CallbackQuery, callback_data: EscalateCB, state: FSMContext) -> None:
    await state.set_state(ManagerFlow.awaiting_suggestion)
    await state.update_data(escalation_id=callback_data.escalation_id)
    await query.answer()
    reply = getattr(query.message, "answer", None)
    if reply is not None:
        await reply("✍️ Напишите ответ одним сообщением — я передам его пользователю.")


@escalation_router.message(ManagerFlow.awaiting_suggestion, F.text)
async def on_manager_suggestion(
    message: Message, state: FSMContext, bot: Bot, db: Database
) -> None:
    data = await state.get_data()
    await state.clear()
    raw_id = data.get("escalation_id")
    if not raw_id or not message.text:
        return

    escalation = await db.resolve_escalation(UUID(str(raw_id)), message.text)
    if escalation is None:
        await message.answer("Не нашёл это обращение (возможно, уже закрыто).")
        return

    try:
        await bot.send_message(
            escalation.user_id,
            f"💬 <b>Ответ от менеджера:</b>\n\n{html.quote(message.text)}",
        )
    except Exception:
        logger.exception("Failed to deliver manager reply to user %s", escalation.user_id)
        await message.answer("⚠️ Не удалось доставить ответ пользователю.")
        return

    await message.answer("✅ Ответ отправлен пользователю.")
    # Prompt 10 (WOW 2) adds the "Сохранить как FAQ?" offer here.
