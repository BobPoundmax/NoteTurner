from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import Message


def is_private_chat(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def is_group_chat(message: Message) -> bool:
    return message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)


def is_bot_mentioned(message: Message, bot_username: str | None) -> bool:
    """Return True if the bot is @mentioned in a group message."""
    if not bot_username:
        return False

    entities = message.entities or message.caption_entities or []
    text = message.text or message.caption or ""
    normalized_username = bot_username.lstrip("@").lower()

    for entity in entities:
        if entity.type == MessageEntityType.MENTION:
            mention = text[entity.offset : entity.offset + entity.length]
            if mention.lstrip("@").lower() == normalized_username:
                return True
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            if entity.user.username and entity.user.username.lower() == normalized_username:
                return True

    return False


def strip_bot_mention(text: str, bot_username: str | None) -> str:
    if not bot_username or not text:
        return text.strip()

    mention = f"@{bot_username.lstrip('@')}"
    cleaned = text.replace(mention, "").replace(mention.lower(), "")
    return cleaned.strip()
