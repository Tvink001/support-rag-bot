"""👍 / 👎 feedback buttons under every grounded answer (§16).

Each answer ships an inline keyboard whose ``callback_data`` carries the rating
and the assistant ``messages.id`` (``msg_ref``). On a tap we recover the
question/answer from ``messages`` (so it survives a restart), log a feedback row
(idempotent — a second tap updates the rating in place), toast "спасибо", and
remove the buttons so the same answer can't be double-rated from the UI.

``callback_data`` is built only from the ``CallbackData`` factory (never raw
f-strings) and carries only a small integer id — well inside the 64-byte budget
(learnings #aiogram).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.supabase_client import Database

logger = logging.getLogger(__name__)

feedback_router = Router(name="feedback")

# Shared with the chat handler so the footer is written and parsed identically.
SOURCE_FOOTER_PREFIX = "Source: "


class FeedbackCB(CallbackData, prefix="fb"):
    """``fb:<rating>:<assistant_messages.id>`` — rating is +1 / -1."""

    rating: int
    msg_ref: str


def build_feedback_keyboard(assistant_msg_id: int) -> InlineKeyboardMarkup:
    """A two-button 👍 / 👎 row referencing the answer's ``messages.id``."""
    builder = InlineKeyboardBuilder()
    ref = str(assistant_msg_id)
    builder.button(text="👍", callback_data=FeedbackCB(rating=1, msg_ref=ref))
    builder.button(text="👎", callback_data=FeedbackCB(rating=-1, msg_ref=ref))
    builder.adjust(2)
    return builder.as_markup()


def _parse_cited_sources(text: str | None) -> list[str]:
    """Pull the cited source filenames out of an answer's "Source: a, b" footer."""
    if not text:
        return []
    idx = text.rfind(SOURCE_FOOTER_PREFIX)
    if idx == -1:
        return []
    tail = text[idx + len(SOURCE_FOOTER_PREFIX) :].splitlines()
    if not tail:
        return []
    return [part.strip() for part in tail[0].split(",") if part.strip()]


@feedback_router.callback_query(FeedbackCB.filter())
async def handle_feedback(query: CallbackQuery, callback_data: FeedbackCB, db: Database) -> None:
    try:
        assistant_msg_id = int(callback_data.msg_ref)
    except ValueError:
        await query.answer()
        return

    context = await db.get_feedback_context(assistant_msg_id)
    if context is None:
        await query.answer("Couldn't save your feedback 🤔")
        return

    message = query.message
    cited = _parse_cited_sources(getattr(message, "text", None))
    await db.record_feedback(
        user_id=context.user_id,
        question=context.question,
        answer=context.answer,
        rating=callback_data.rating,
        cited_source_ids=cited,
    )
    await query.answer("Thanks for your feedback! 🙏")

    # Remove the buttons so the answer can't be re-rated from the UI (idempotent).
    edit_markup = getattr(message, "edit_reply_markup", None)
    if edit_markup is not None:
        try:
            await edit_markup(reply_markup=None)
        except TelegramBadRequest:
            # "message is not modified" on a double-tap — desired state already holds.
            logger.debug("feedback keyboard already cleared (double-tap)")
