"""Shared test fixtures.

An autouse fixture injects a complete set of required env vars so ``Settings``
constructs without a real ``.env``. Monkeypatched env vars take precedence over
the ``.env`` file in pydantic-settings, so tests are isolated from the operator's
real secrets even when a local ``.env`` is present.
"""

import pytest

_TEST_ENV: dict[str, str] = {
    "TELEGRAM_BOT_TOKEN": "123456:test-bot-token",
    "MANAGER_CHAT_ID": "-1001234567890",
    "ADMIN_TELEGRAM_IDS": "111,222,333",
    "WEBHOOK_SECRET": "test-webhook-secret",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "VOYAGE_API_KEY": "pa-test",
    "GROQ_API_KEY": "gsk-test",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_KEY": "sb_secret_test",
    "DATABASE_URL": "postgresql://postgres:pw@db.test.supabase.co:5432/postgres",
}

# Optional vars that must NOT leak in from the host environment during tests.
_CLEARED_ENV = ("REDIS_URL", "SENTRY_DSN", "WEBHOOK_BASE_URL", "MODE")


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    for key in _CLEARED_ENV:
        monkeypatch.delenv(key, raising=False)
