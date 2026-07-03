import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

from noteturner.config.settings import Settings

logger = logging.getLogger(__name__)


def _format_httpx_error(exc: httpx.HTTPError) -> str:
    details = str(exc).strip()
    if details:
        return f"Network error: {exc.__class__.__name__}: {details}"
    return f"Network error: {exc.__class__.__name__}"


class HollihopError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class HollihopClient:
    """Client for Hollihop CRM API 2.0."""

    MAX_REQUESTS = 600
    WINDOW_SECONDS = 30

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._request_times: deque[float] = deque()

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.hollihop_subdomain and self._settings.hollihop_auth_key)

    async def _wait_for_rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        while self._request_times and now - self._request_times[0] >= self.WINDOW_SECONDS:
            self._request_times.popleft()

        if len(self._request_times) >= self.MAX_REQUESTS:
            sleep_for = self.WINDOW_SECONDS - (now - self._request_times[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._request_times.popleft()

        self._request_times.append(asyncio.get_event_loop().time())

    async def call(self, function_name: str, **params: Any) -> dict[str, Any]:
        if not self.is_configured:
            raise HollihopError("Hollihop CRM is not configured (HOLLIHOP_SUBDOMAIN, HOLLIHOP_AUTH_KEY)")

        await self._wait_for_rate_limit()

        url = f"{self._settings.hollihop_base_url}/{function_name}"
        query_params = {"authkey": self._settings.hollihop_auth_key, **params}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=query_params)
        except httpx.HTTPError as exc:
            raise HollihopError(_format_httpx_error(exc)) from exc

        if response.status_code >= 400:
            raise HollihopError(
                f"HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        data = response.json()
        if "Error" in data:
            raise HollihopError(str(data["Error"]))

        return data

    async def get_locations(self) -> dict[str, Any]:
        return await self.call("GetLocations")

    async def get_leads(self, *, take: int = 5, skip: int = 0) -> dict[str, Any]:
        return await self.call("GetLeads", take=take, skip=skip)

    async def health_check(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        try:
            data = await self.get_locations()
            locations = data.get("Locations", [])
            return {
                "status": "ok",
                "locations_count": len(locations),
                "latency_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            }
        except HollihopError as exc:
            return {"status": "error", "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"status": "error", "error": f"Network error: {exc}"}
