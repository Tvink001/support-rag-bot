"""Admin commands: /upload (ingest a document) and /sources (project_specs.md §16).

The router gates each admin command with ``AdminFilter``; non-admins fall through
to a single "нет прав" reply. ``db`` and ``embeddings`` are injected from the
dispatcher's workflow data (set in ``bot/main.py``).
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, Filter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import get_settings
from bot.rag.ingest import SUPPORTED_TYPES, ingest_document
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database

logger = logging.getLogger(__name__)

admin_router = Router(name="admin")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


class Admin(StatesGroup):
    awaiting_upload = State()


class AdminFilter(Filter):
    """Allow admins only — works for both messages and callback queries."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id in get_settings().ADMIN_TELEGRAM_IDS


class DeleteSourceCB(CallbackData, prefix="del"):
    """``del:<action>:<sources.id>`` — action is confirm | cancel."""

    action: str
    source_id: str


def _delete_confirm_keyboard(source_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Удалить", callback_data=DeleteSourceCB(action="confirm", source_id=source_id)
    )
    builder.button(
        text="Отмена", callback_data=DeleteSourceCB(action="cancel", source_id=source_id)
    )
    builder.adjust(2)
    return builder.as_markup()


@admin_router.message(Command("upload"), AdminFilter())
async def cmd_upload(message: Message, state: FSMContext) -> None:
    await state.set_state(Admin.awaiting_upload)
    await message.answer(
        "📎 Пришлите документ <b>PDF</b>, <b>DOCX</b> или <b>TXT</b> "
        "(до 20 МБ) одним файлом. Для отмены — /cancel."
    )


@admin_router.message(Command("cancel"), AdminFilter(), Admin.awaiting_upload)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.")


@admin_router.message(Command("sources"), AdminFilter())
async def cmd_sources(message: Message, db: Database) -> None:
    sources = await db.list_active_sources()
    if not sources:
        await message.answer("База знаний пуста. Загрузите документ через /upload.")
        return
    lines = ["<b>Активные источники:</b>"]
    for s in sources:
        when = s.uploaded_at.strftime("%Y-%m-%d %H:%M")
        lines.append(f"• <code>{s.id}</code>\n  {s.filename} — {s.chunk_count} чанков — {when}")
    await message.answer("\n".join(lines))


@admin_router.message(Admin.awaiting_upload, F.document)
async def handle_upload(
    message: Message,
    bot: Bot,
    state: FSMContext,
    db: Database,
    embeddings: EmbeddingService,
) -> None:
    document = message.document
    if document is None:  # guaranteed by F.document, but keeps the type checker happy
        return
    file_type = Path(document.file_name or "").suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        await message.answer(
            f"Формат «{file_type or '?'}» не поддерживается. Нужен PDF, DOCX или TXT."
        )
        return
    if document.file_size is not None and document.file_size > MAX_UPLOAD_BYTES:
        await message.answer("Файл слишком большой (максимум 20 МБ).")
        return

    await state.clear()
    await message.answer("⏳ Обрабатываю документ…")
    settings = get_settings()
    try:
        buffer = BytesIO()
        await bot.download(document, destination=buffer)
        result = await ingest_document(
            db=db,
            embeddings=embeddings,
            file_bytes=buffer.getvalue(),
            filename=document.file_name or "document",
            file_type=file_type,
            uploaded_by=message.from_user.id if message.from_user else 0,
            chunk_size_tokens=settings.CHUNK_SIZE_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
        )
    except Exception:
        logger.exception("Ingestion failed for an uploaded document")
        await message.answer("⚠️ Не удалось обработать документ. Попробуйте ещё раз позже.")
        return

    if result.skipped:
        await message.answer(
            f"ℹ️ «{result.filename}» уже в базе ({result.chunks_added} чанков). Пропускаю."
        )
    elif result.chunks_added == 0:
        await message.answer("⚠️ Не удалось извлечь текст из документа.")
    else:
        await message.answer(
            f"✅ «{result.filename}»: добавлено <b>{result.chunks_added}</b> чанков "
            f"за {result.elapsed_seconds:.1f} с."
        )


@admin_router.message(Command("delete"), AdminFilter())
async def cmd_delete(message: Message, command: CommandObject, db: Database) -> None:
    raw = (command.args or "").strip()
    try:
        source_id = UUID(raw)
    except ValueError:
        await message.answer("Использование: <code>/delete &lt;id&gt;</code> (id из /sources).")
        return
    source = await db.get_source(source_id)
    if source is None or source.status != "active":
        await message.answer("Источник не найден или уже удалён.")
        return
    await message.answer(
        f"Удалить «{source.filename}» ({source.chunk_count} чанков)? Действие необратимо.",
        reply_markup=_delete_confirm_keyboard(str(source_id)),
    )


@admin_router.callback_query(DeleteSourceCB.filter(F.action == "cancel"), AdminFilter())
async def on_delete_cancel(query: CallbackQuery) -> None:
    await query.answer("Отменено")
    await _finish_delete(query, "Удаление отменено.")


@admin_router.callback_query(DeleteSourceCB.filter(F.action == "confirm"), AdminFilter())
async def on_delete_confirm(
    query: CallbackQuery, callback_data: DeleteSourceCB, db: Database
) -> None:
    removed = await db.soft_delete_source(UUID(callback_data.source_id))
    if removed is None:
        await query.answer("Уже удалён.")
        await _finish_delete(query, "Источник уже был удалён.")
        return
    await query.answer("Удалено ✅")
    await _finish_delete(query, f"🗑 Источник удалён — {removed} чанков убрано из базы.")


async def _finish_delete(query: CallbackQuery, text: str) -> None:
    """Replace the confirm prompt with the outcome and drop its buttons (idempotent)."""
    edit = getattr(query.message, "edit_text", None)
    if edit is None:
        return
    try:
        await edit(text, reply_markup=None)
    except TelegramBadRequest:
        logger.debug("delete prompt already updated (double-tap)")


@admin_router.message(Command("upload", "sources", "delete"))
async def admin_denied(message: Message) -> None:
    await message.answer("⛔ Нет прав.")
