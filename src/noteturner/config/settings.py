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

    # Embeddings (via OpenRouter /embeddings)
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536
    # How many chunk texts to embed per request. Kept small to bound peak memory
    # during sync jobs on small instances (e.g. Render 256 MB).
    embedding_batch_size: int = 32

    # Sync execution
    # When True, sync jobs are enqueued into the database and executed by a
    # separate worker process (noteturner.worker) instead of running inline in
    # the web process. This isolates heavy CRM/Drive processing from the bot so
    # an out-of-memory sync cannot restart the webhook service.
    sync_worker_enabled: bool = False
    # Seconds between queue polls in the worker.
    sync_worker_poll_interval: float = 5.0
    # Upper bound on CRM records fetched per entity type in one sync run.
    crm_max_records_per_type: int = 5000
    # CRM API page size cap (overrides per-entity page size when smaller).
    crm_sync_page_size: int = 100

    # Hollihop CRM
    hollihop_subdomain: str = ""
    hollihop_auth_key: str = ""

    # Google Drive knowledge source (service account — fields from downloaded JSON key)
    gdrive_folder_id: str = ""
    google_project_id: str = ""
    google_service_account_email: str = ""
    google_private_key_id: str = ""
    google_private_key: str = ""
    google_client_id: str = ""
    # Optional fallback: paste the whole JSON key instead of separate fields above.
    google_service_account_json: str = ""
    # Filename keywords marking a file as financial (comma-separated, case-insensitive).
    financial_keywords: str = "финанс,оплат,payment,бюджет,budget,зарплат,выручк,revenue"

    @staticmethod
    def parse_gdrive_root_ids(raw: str) -> list[str]:
        """Parse comma-separated Drive root IDs or URLs (folders, projects, files)."""
        ids: list[str] = []
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            for marker in ("/folders/", "/project/", "/file/d/"):
                if marker in token:
                    token = token.split(marker, 1)[1]
                    break
            token = token.split("/")[0].split("?")[0].strip()
            if token and token not in ids:
                ids.append(token)
        return ids

    @field_validator("google_private_key", mode="before")
    @classmethod
    def normalize_google_private_key(cls, value: str) -> str:
        if not value:
            return value
        # Render/env often stores PEM with literal \n sequences.
        return value.replace("\\n", "\n").strip()

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

    @property
    def gdrive_is_configured(self) -> bool:
        if not self.gdrive_root_ids:
            return False
        if self.google_service_account_json.strip():
            return True
        return bool(
            self.google_project_id.strip()
            and self.google_service_account_email.strip()
            and self.google_private_key.strip()
            and self.google_private_key_id.strip()
            and self.google_client_id.strip()
        )

    @property
    def gdrive_root_ids(self) -> list[str]:
        return Settings.parse_gdrive_root_ids(self.gdrive_folder_id)

    def google_service_account_info(self) -> dict[str, str]:
        """Build a service-account dict for google-auth from env vars."""
        if self.google_service_account_json.strip():
            import json

            return json.loads(self.google_service_account_json)

        if not self.gdrive_is_configured:
            raise ValueError("Google Drive service account is not configured")

        return {
            "type": "service_account",
            "project_id": self.google_project_id.strip(),
            "private_key_id": self.google_private_key_id.strip(),
            "private_key": self.google_private_key.strip(),
            "client_email": self.google_service_account_email.strip(),
            "client_id": self.google_client_id.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "universe_domain": "googleapis.com",
        }

    @property
    def financial_keyword_list(self) -> list[str]:
        return [kw.strip().lower() for kw in self.financial_keywords.split(",") if kw.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
