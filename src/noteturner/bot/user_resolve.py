import re

from aiogram import Bot
from aiogram.enums import ChatType, MessageEntityType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, User

USERNAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def parse_telegram_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _user_from_forward(message: Message) -> User | None:
    if message.forward_from:
        return message.forward_from
    origin = message.forward_origin
    if origin is not None and getattr(origin, "sender_user", None):
        return origin.sender_user
    return None


def _user_from_text_mention(message: Message) -> User | None:
    text = message.text or ""
    for entity in message.entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            return entity.user
        if entity.type == MessageEntityType.MENTION:
            mention = text[entity.offset : entity.offset + entity.length].lstrip("@")
            if USERNAME_PATTERN.match(mention):
                # Resolved later via get_chat if no embedded user id.
                return None
    return None


async def resolve_telegram_user(bot: Bot, raw: str) -> tuple[int | None, str | None, str | None]:
    """Resolve a numeric id or @username to a Telegram user id.

    Returns ``(telegram_id, username, error)``. Only one of id/error is set.
    """
    text = (raw or "").strip()
    if not text:
        return None, None, "Пустой ввод."

    numeric = parse_telegram_id(text)
    if numeric is not None:
        return numeric, None, None

    username = text.lstrip("@").strip()
    if not USERNAME_PATTERN.match(username):
        return None, None, "Укажите <code>telegram_id</code> или <code>@username</code>."

    try:
        chat = await bot.get_chat(f"@{username}")
    except TelegramBadRequest:
        return None, username, (
            f"Не удалось найти @{username}. "
            "Пользователь должен хотя бы раз написать боту /start, "
            "или перешлите его сообщение сюда."
        )

    if chat.type != ChatType.PRIVATE:
        return None, username, f"@{username} — не личный аккаунт пользователя."

    return chat.id, chat.username or username, None


async def resolve_admin_target(message: Message) -> tuple[int | None, str | None, str | None]:
    """Resolve admin target from a message: forward, text mention, id or @username."""
    forwarded = _user_from_forward(message)
    if forwarded is not None:
        label = f"@{forwarded.username}" if forwarded.username else str(forwarded.id)
        return forwarded.id, label, None

    mentioned = _user_from_text_mention(message)
    if mentioned is not None:
        label = f"@{mentioned.username}" if mentioned.username else str(mentioned.id)
        return mentioned.id, label, None

    return await resolve_telegram_user(message.bot, message.text or "")


def format_user_label(*, telegram_id: int, username: str | None = None) -> str:
    if username:
        return f"@{username.lstrip('@')} (<code>{telegram_id}</code>)"
    return f"<code>{telegram_id}</code>"
