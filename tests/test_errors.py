"""Error sanitizer + Sentry-disabled path (§21, §22)."""

from __future__ import annotations

from bot.config import Settings
from bot.handlers.errors import init_sentry, sanitize


def test_sanitize_redacts_secret_assignments() -> None:
    out = sanitize("ANTHROPIC_API_KEY=sk-ant-abc123def")
    assert "[REDACTED]" in out
    assert "sk-ant-abc123def" not in out
    assert "[REDACTED]" in sanitize("token: 12345secretvalue")


def test_sanitize_redacts_bearer_and_base64() -> None:
    assert "Bearer [REDACTED]" in sanitize("Authorization: Bearer abcdef.ghijkl")
    blob = "A1b2C3d4" * 8  # 64 base64-ish chars
    assert "[REDACTED_B64]" in sanitize(f"payload={blob}")


def test_sanitize_passes_plain_text_and_truncates() -> None:
    assert sanitize("just a normal error message") == "just a normal error message"
    # spaced words (no 40+ char run) so it truncates rather than being redacted as base64
    out = sanitize("word " * 600)
    assert out.endswith("…[truncated]")
    assert len(out) < 3000


def test_sentry_disabled_without_dsn() -> None:
    # conftest clears SENTRY_DSN -> init returns False and never imports sentry_sdk.
    assert init_sentry(Settings()) is False
