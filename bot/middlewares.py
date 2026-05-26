"""aiogram middlewares — per-user throttling (§3.3).

``ThrottleMiddleware`` enforces a minimum interval between any two messages from a
user (anti-flood) plus a tighter per-minute cap on the expensive LLM path (free-text
questions + voice). State is in-memory (per process) — fine for the single-instance
v1; a multi-instance deploy would move this to Redis. The clock is injectable so the
windows are unit-testable without sleeping.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

logger = logging.getLogger(__name__)

_BUSY = "Слишком много запросов подряд. Подождите минуту 🙏"


class ThrottleMiddleware(BaseMiddleware):
    """Drop flood messages; cap LLM-bearing messages per user per minute."""

    def __init__(
        self,
        *,
        min_interval_seconds: float,
        llm_per_minute: int,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._llm_per_minute = llm_per_minute
        self._now = time_func
        self._last_seen: dict[int, float] = {}
        self._llm_hits: dict[int, deque[float]] = defaultdict(deque)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        uid = user.id
        now = self._now()

        last = self._last_seen.get(uid)
        if last is not None and now - last < self._min_interval:
            logger.debug("throttle: dropping flood message from %s", uid)
            return None
        self._last_seen[uid] = now

        if self._is_llm_message(event):
            hits = self._llm_hits[uid]
            cutoff = now - 60.0
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self._llm_per_minute:
                logger.info("throttle: per-minute LLM cap reached for %s", uid)
                await self._tell_busy(event)
                return None
            hits.append(now)

        return await handler(event, data)

    @staticmethod
    def _is_llm_message(event: TelegramObject) -> bool:
        """Free-text questions and voice hit the LLM; commands don't."""
        if getattr(event, "voice", None) is not None:
            return True
        text = getattr(event, "text", None)
        if not isinstance(text, str):
            return False
        return not text.startswith("/")

    @staticmethod
    async def _tell_busy(event: TelegramObject) -> None:
        answer = getattr(event, "answer", None)
        if answer is None:
            return
        try:
            await answer(_BUSY)
        except Exception:
            logger.debug("throttle: failed to send the busy notice")
