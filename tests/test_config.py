"""Settings load correctly from the environment."""

from bot.config import Settings


def test_settings_loads_from_env() -> None:
    settings = Settings()
    assert settings.MODE == "polling"
    assert settings.ANTHROPIC_MODEL == "claude-haiku-4-5"
    assert settings.ANTHROPIC_MAX_TOKENS == 1024
    assert settings.VOYAGE_MODEL == "voyage-3.5"
    assert settings.VOYAGE_EMBED_DIM == 1024
    assert settings.TELEGRAM_BOT_TOKEN.get_secret_value() == "123456:test-bot-token"
    assert settings.REDIS_URL is None


def test_admin_ids_parsed_as_int_list() -> None:
    settings = Settings()
    assert settings.ADMIN_TELEGRAM_IDS == [111, 222, 333]
    assert all(isinstance(x, int) for x in settings.ADMIN_TELEGRAM_IDS)
