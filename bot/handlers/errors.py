"""Global error handling + observability glue (§21, §22).

Per-message handlers already swallow user-flow failures (constraint #8), so anything
reaching the global handler is genuinely unexpected: we log it **sanitized** (never
the Update payload / message body, never secrets), alert the managers, and forward it
to Sentry if configured. ``sanitize`` redacts secret-shaped substrings + long base64
and truncates. Sentry is lazy-imported so the package is only needed when
``SENTRY_DSN`` is set.
"""

from __future__ import annotations

import logging
import re

from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent

from bot.config import Settings

logger = logging.getLogger(__name__)

# Matches a key-shaped NAME (incl. underscore-joined ones like ANTHROPIC_API_KEY)
# followed by = or : and its value; redacts the value, keeps the name for context.
_SECRET_RE = re.compile(
    r"(?i)([\w-]*(?:token|api[_-]?key|key|secret|password|credential)[\w-]*\s*[=:]\s*)(\S+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_LONG_B64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_MAX_LEN = 1500


def sanitize(text: str, max_len: int = _MAX_LEN) -> str:
    """Redact secret-shaped substrings + long base64 and truncate (for logs/alerts)."""
    text = _SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _LONG_B64_RE.sub("[REDACTED_B64]", text)
    if len(text) > max_len:
        text = text[:max_len] + "…[truncated]"
    return text


def init_sentry(settings: Settings) -> bool:
    """Initialise Sentry if ``SENTRY_DSN`` is set; return whether it was enabled."""
    dsn = settings.SENTRY_DSN.get_secret_value() if settings.SENTRY_DSN else ""
    if not dsn:
        logger.info("SENTRY_DSN not set -> Sentry disabled")
        return False
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.scrubber import EventScrubber

    sentry_sdk.init(
        dsn=dsn,
        integrations=[AsyncioIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,  # never ship user content / PII
        event_scrubber=EventScrubber(),  # default denylist (token/secret/password/…)
    )
    logger.info("Sentry initialised")
    return True


def register_error_handler(
    dp: Dispatcher, bot: Bot, settings: Settings, *, sentry_enabled: bool
) -> None:
    """Register the catch-all handler for exceptions that bubble past local guards."""

    async def _on_error(event: ErrorEvent) -> None:
        exc = event.exception
        detail = sanitize(f"{type(exc).__name__}: {exc}")
        # exc_info logs the traceback; we never log event.update (it holds the body).
        logger.error("Unhandled update error: %s", detail, exc_info=exc)

        if sentry_enabled:
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:
                logger.exception("Sentry capture failed")

        try:
            # parse_mode=None: the sanitized detail is plain text, not HTML.
            await bot.send_message(
                settings.MANAGER_CHAT_ID,
                f"⚠️ Unexpected bot error:\n{detail}",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Failed to deliver error alert to the managers' chat")

    dp.errors.register(_on_error)
