from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from noteturner.config.settings import Settings
from noteturner.integrations.gdrive import GoogleDriveClient
from noteturner.integrations.hollihop import HollihopClient
from noteturner.integrations.openrouter import OpenRouterClient


class InjectDependenciesMiddleware(BaseMiddleware):
    def __init__(
        self,
        settings: Settings,
        openrouter: OpenRouterClient,
        hollihop: HollihopClient,
        gdrive: GoogleDriveClient,
    ) -> None:
        self.settings = settings
        self.openrouter = openrouter
        self.hollihop = hollihop
        self.gdrive = gdrive

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        data["openrouter"] = self.openrouter
        data["hollihop"] = self.hollihop
        data["gdrive"] = self.gdrive
        return await handler(event, data)
