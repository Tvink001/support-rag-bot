"""Entry point: bot, dispatcher, FSM storage, DB pool, and the polling/webhook lifecycle."""

from __future__ import annotations

import asyncio
import logging

import truststore
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.config import Settings, get_settings
from bot.handlers import admin, chat, escalation, feedback, start, voice
from bot.handlers.errors import init_sentry, register_error_handler
from bot.llm.claude_client import ClaudeClient
from bot.middlewares import ThrottleMiddleware
from bot.services.embeddings import EmbeddingService
from bot.services.supabase_client import Database
from bot.services.whisper import WhisperService

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _build_storage(settings: Settings) -> BaseStorage:
    """Persistent FSM storage.

    ``RedisStorage`` when ``REDIS_URL`` is set; ``MemoryStorage`` is a DEV-ONLY
    fallback and is rejected in webhook (production) mode (CLAUDE.md constraint #10).
    """
    if settings.REDIS_URL is not None:
        logger.info("FSM storage: RedisStorage")
        return RedisStorage.from_url(settings.REDIS_URL.get_secret_value())
    if settings.MODE == "webhook":
        raise RuntimeError(
            "REDIS_URL is required in webhook (production) mode; MemoryStorage "
            "must never be used in production (CLAUDE.md constraint #10)."
        )
    logger.warning(
        "REDIS_URL not set -> using in-memory FSM storage (DEV ONLY; state will "
        "not survive a restart). Set REDIS_URL for production."
    )
    return MemoryStorage()


def _build_dispatcher(
    settings: Settings,
    db: Database,
    embeddings: EmbeddingService,
    claude: ClaudeClient,
    whisper: WhisperService,
) -> Dispatcher:
    dp = Dispatcher(storage=_build_storage(settings))
    dp["db"] = db  # injected into handlers declaring `db: Database`
    dp["embeddings"] = embeddings  # injected into handlers declaring `embeddings: EmbeddingService`
    dp["claude"] = claude  # injected into handlers declaring `claude: ClaudeClient`
    dp["whisper"] = whisper  # injected into the voice handler
    dp.message.middleware(
        ThrottleMiddleware(
            min_interval_seconds=settings.RATE_LIMIT_INTERVAL_SECONDS,
            llm_per_minute=settings.RATE_LIMIT_LLM_PER_MINUTE,
        )
    )
    dp.include_router(start.start_router)
    dp.include_router(admin.admin_router)
    dp.include_router(feedback.feedback_router)  # callback_query handlers (👍/👎)
    dp.include_router(escalation.escalation_router)  # Take/Suggest + manager reply capture
    dp.include_router(voice.voice_router)  # voice -> transcribe -> RAG pipeline
    dp.include_router(chat.chat_router)  # last: commands match first, then free-text Q&A
    return dp


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _open_db(db: Database) -> None:
    await db.connect()
    await db.ping()
    logger.info("Supabase/Postgres connectivity OK (SELECT 1)")


async def _set_commands(bot: Bot) -> None:
    """Register the tappable command menu so commands autocomplete in Telegram."""
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="About the bot and what it can do"),
            BotCommand(command="help", description="Quick help"),
            BotCommand(command="upload", description="Upload a document (admin)"),
            BotCommand(command="sources", description="List sources (admin)"),
        ]
    )


async def _run_polling(bot: Bot, dp: Dispatcher, settings: Settings, db: Database) -> None:
    async def _on_shutdown() -> None:
        await db.close()
        await dp.storage.close()

    dp.shutdown.register(_on_shutdown)
    await bot.delete_webhook(drop_pending_updates=True)
    await _open_db(db)
    await _set_commands(bot)
    await dp.start_polling(bot)


def _run_webhook(bot: Bot, dp: Dispatcher, settings: Settings, db: Database) -> None:
    if not settings.WEBHOOK_BASE_URL:
        raise RuntimeError("WEBHOOK_BASE_URL is required in webhook mode.")
    secret = settings.WEBHOOK_SECRET.get_secret_value()

    async def _on_startup() -> None:
        await _open_db(db)
        await _set_commands(bot)
        url = f"{settings.WEBHOOK_BASE_URL.rstrip('/')}{WEBHOOK_PATH}"
        await bot.set_webhook(url, secret_token=secret, drop_pending_updates=True)
        logger.info("Webhook set: %s", url)

    async def _on_shutdown() -> None:
        await db.close()
        await dp.storage.close()
        await bot.session.close()

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    app = web.Application()
    app.router.add_get("/health", _health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(
        app, path=WEBHOOK_PATH
    )
    setup_application(app, dp, bot=bot)
    web.run_app(app, host=settings.WEB_HOST, port=settings.WEB_PORT)


def main() -> None:
    # Use the OS trust store so corporate-proxy / TLS-interception CAs are honored
    # (Voyage/Anthropic/Supabase HTTPS via certifi otherwise fail; harmless on Railway).
    truststore.inject_into_ssl()
    settings = get_settings()
    _configure_logging(settings)
    sentry_enabled = init_sentry(settings)
    bot = _build_bot(settings)
    db = Database(settings.DATABASE_URL.get_secret_value())
    embeddings = EmbeddingService(settings)
    claude = ClaudeClient(settings)
    whisper = WhisperService(settings)
    dp = _build_dispatcher(settings, db, embeddings, claude, whisper)
    register_error_handler(dp, bot, settings, sentry_enabled=sentry_enabled)
    if settings.MODE == "webhook":
        _run_webhook(bot, dp, settings, db)
    else:
        asyncio.run(_run_polling(bot, dp, settings, db))


if __name__ == "__main__":
    main()
