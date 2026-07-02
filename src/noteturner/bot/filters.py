from aiogram.filters import Filter
from aiogram.types import Message


class ChatRoleFilter(Filter):
    """Match messages whose chat role (set by ChatAccessMiddleware) equals ``role``."""

    def __init__(self, role: str) -> None:
        self.role = role

    async def __call__(self, message: Message, chat_role: str | None = None) -> bool:
        return chat_role == self.role
