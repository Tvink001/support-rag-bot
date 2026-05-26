"""Application configuration (pydantic-settings).

The authoritative list of variables lives in project_specs.md §3.1 and
``.env.example``. Every secret is a ``SecretStr`` so it never leaks into logs or
tracebacks. ``ADMIN_TELEGRAM_IDS`` is a comma-separated env value decoded
manually (pydantic-settings would otherwise try to JSON-parse a list field).
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, loaded from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: SecretStr
    MANAGER_CHAT_ID: int
    ADMIN_TELEGRAM_IDS: Annotated[list[int], NoDecode]
    WEBHOOK_SECRET: SecretStr
    WEBHOOK_BASE_URL: str = ""

    # --- Anthropic (answer generation) ---
    ANTHROPIC_API_KEY: SecretStr
    ANTHROPIC_MODEL: str = "claude-haiku-4-5"
    ANTHROPIC_MAX_TOKENS: int = Field(default=1024, ge=1, le=1024)

    # --- Voyage (embeddings) ---
    VOYAGE_API_KEY: SecretStr
    VOYAGE_MODEL: str = "voyage-3.5"
    VOYAGE_EMBED_DIM: int = 1024

    # --- Groq (voice input) ---
    GROQ_API_KEY: SecretStr

    # --- Supabase (pgvector + state tables) ---
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: SecretStr
    DATABASE_URL: SecretStr

    # --- RAG tuning ---
    CHUNK_SIZE_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50
    RETRIEVAL_TOP_K: int = 5
    SIMILARITY_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)
    CONVERSATION_MEMORY_TURNS: int = 20
    ESCALATION_COOLDOWN_HOURS: int = 24

    # --- Mode / web server ---
    MODE: Literal["polling", "webhook"] = "polling"
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080

    # --- FSM storage (persistent in production; see §8 / §9.1) ---
    REDIS_URL: SecretStr | None = None

    # --- Observability ---
    SENTRY_DSN: SecretStr | None = None
    LOG_LEVEL: str = "INFO"

    @field_validator("ADMIN_TELEGRAM_IDS", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        """Decode a comma-separated string of ids into ``list[int]``."""
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        return value

    @field_validator("MODE", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("REDIS_URL", "SENTRY_DSN", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Treat an empty/blank env value as unset (None)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
