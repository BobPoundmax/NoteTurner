import httpx
import pytest

from noteturner.config.settings import Settings
from noteturner.integrations.hollihop import HollihopClient, HollihopError
from noteturner.integrations.openrouter import OpenRouterClient, OpenRouterError


class _TimeoutHttpClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, *args, **kwargs):
        raise httpx.ReadTimeout("")

    async def post(self, *args, **kwargs):
        raise httpx.ReadTimeout("")


@pytest.mark.asyncio
async def test_hollihop_call_wraps_httpx_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "noteturner.integrations.hollihop.httpx.AsyncClient",
        lambda *args, **kwargs: _TimeoutHttpClient(),
    )
    client = HollihopClient(Settings(hollihop_subdomain="school", hollihop_auth_key="secret"))

    with pytest.raises(HollihopError, match="Network error: ReadTimeout"):
        await client.call("GetLeads", take=1, skip=0)


@pytest.mark.asyncio
async def test_openrouter_embed_wraps_httpx_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "noteturner.integrations.openrouter.httpx.AsyncClient",
        lambda *args, **kwargs: _TimeoutHttpClient(),
    )
    client = OpenRouterClient(Settings(openrouter_api_key="token"))

    with pytest.raises(OpenRouterError, match="Network error: ReadTimeout"):
        await client.embed(["hello"])
