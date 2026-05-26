"""``/start`` and ``/help`` — a short RU greeting describing the bot."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

start_router = Router(name="start")

_GREETING = (
    "👋 Привет! Я — ассистент базы знаний компании.\n\n"
    "Задайте вопрос текстом или голосом — я отвечу строго по загруженным "
    "документам и укажу источники. Если ответа в базе нет, я честно скажу об "
    "этом и передам вопрос менеджеру.\n\n"
    "Команды:\n"
    "• /start — это сообщение\n"
    "• /help — краткая справка"
)


@start_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(_GREETING)


@start_router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(_GREETING)
