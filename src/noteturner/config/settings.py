from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    admin_telegram_id: int = 0
    webhook_base_url: str = ""
    bot_mode: str = "webhook"  # webhook | polling

    # Database
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/noteturner"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_test_model: str = "google/gemma-2-9b-it:free"

    # Hollihop CRM
    hollihop_subdomain: str = ""
    hollihop_auth_key: str = ""

    @field_validator("hollihop_subdomain", mode="before")
    @classmethod
    def normalize_hollihop_subdomain(cls, value: str) -> str:
        if not value:
            return value
        cleaned = value.strip().rstrip("/")
        for prefix in ("https://", "http://"):
            if cleaned.lower().startswith(prefix):
                cleaned = cleaned[len(prefix) :]
        if cleaned.lower().endswith(".t8s.ru"):
            cleaned = cleaned[: -len(".t8s.ru")]
        return cleaned.strip("/")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://") and "+asyncpg" not in value:
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @property
    def webhook_url(self) -> str:
        base = self.webhook_base_url.rstrip("/")
        return f"{base}/webhook/{self.telegram_webhook_secret}"

    @property
    def hollihop_base_url(self) -> str:
        subdomain = self.hollihop_subdomain.strip()
        return f"https://{subdomain}.t8s.ru/Api/V2"


@lru_cache
def get_settings() -> Settings:
    return Settings()
