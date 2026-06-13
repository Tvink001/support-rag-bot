"""``/start`` and ``/help`` — a short RU greeting describing the bot."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

start_router = Router(name="start")

_GREETING = (
    "👋 Hi! I'm the company's knowledge-base assistant.\n\n"
    "Ask a question by text or voice — I answer strictly from the uploaded "
    "documents and cite the sources. If the answer isn't in the base, I'll say so "
    "honestly and pass your question to a manager.\n\n"
    "Commands:\n"
    "• /start — this message\n"
    "• /help — quick help"
)


@start_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(_GREETING)


@start_router.message(Command("help"))
async def handle_help(message: Message) -> None:
    await message.answer(_GREETING)
