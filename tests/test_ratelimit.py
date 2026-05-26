"""ThrottleMiddleware: anti-flood interval + per-minute LLM cap (§3.3)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.middlewares import ThrottleMiddleware


def _msg(text: str | None = None, voice: object | None = None, uid: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid), text=text, voice=voice, answer=AsyncMock()
    )


async def test_min_interval_drops_flood() -> None:
    clock = {"t": 100.0}
    mw = ThrottleMiddleware(
        min_interval_seconds=1.0, llm_per_minute=100, time_func=lambda: clock["t"]
    )
    handler = AsyncMock(return_value="ok")
    msg = _msg(text="привет")

    assert await mw(handler, msg, {}) == "ok"  # first passes
    clock["t"] = 100.3
    assert await mw(handler, msg, {}) is None  # 0.3 s later -> dropped
    assert handler.await_count == 1
    clock["t"] = 101.5
    await mw(handler, msg, {})  # >1 s later -> passes again
    assert handler.await_count == 2


async def test_llm_per_minute_cap_fires() -> None:
    clock = {"t": 0.0}
    mw = ThrottleMiddleware(
        min_interval_seconds=0.0, llm_per_minute=2, time_func=lambda: clock["t"]
    )
    handler = AsyncMock(return_value="ok")
    msg = _msg(text="вопрос")

    await mw(handler, msg, {})
    clock["t"] += 1
    await mw(handler, msg, {})
    clock["t"] += 1
    result = await mw(handler, msg, {})  # 3rd within the minute -> capped

    assert result is None
    assert handler.await_count == 2
    msg.answer.assert_awaited()  # told it's busy


async def test_commands_bypass_the_llm_cap() -> None:
    clock = {"t": 0.0}
    mw = ThrottleMiddleware(
        min_interval_seconds=0.0, llm_per_minute=1, time_func=lambda: clock["t"]
    )
    handler = AsyncMock(return_value="ok")
    cmd = _msg(text="/sources")

    for _ in range(3):
        await mw(handler, cmd, {})
        clock["t"] += 1

    assert handler.await_count == 3  # slash-commands are not LLM-capped


async def test_event_without_user_passes_through() -> None:
    mw = ThrottleMiddleware(min_interval_seconds=999, llm_per_minute=1)
    handler = AsyncMock(return_value="ok")
    assert await mw(handler, SimpleNamespace(from_user=None), {}) == "ok"
