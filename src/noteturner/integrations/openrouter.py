import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from noteturner.config.settings import Settings

logger = logging.getLogger(__name__)


class OpenRouterError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openrouter_api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._settings.webhook_base_url or "https://noteturner.local",
            "X-Title": "Note Turner",
        }

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 512,
    ) -> str:
        if not self.is_configured:
            raise OpenRouterError("OpenRouter is not configured (OPENROUTER_API_KEY)")

        payload = {
            "model": model or self._settings.openrouter_test_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        url = f"{self._settings.openrouter_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=self._headers())

        if response.status_code >= 400:
            raise OpenRouterError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError(f"Unexpected response format: {data}") from exc

    async def health_check(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        if not self.is_configured:
            return {"status": "skipped", "error": "OPENROUTER_API_KEY not set"}

        try:
            reply = await self.chat_completion(
                [{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=16,
            )
            return {
                "status": "ok",
                "reply": reply[:50],
                "model": self._settings.openrouter_test_model,
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except OpenRouterError as exc:
            return {"status": "error", "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"Network error: {exc}"}
