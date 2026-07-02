from unittest.mock import MagicMock

from aiogram.enums import MessageEntityType

from noteturner.bot.utils import is_bot_mentioned, strip_bot_mention


def _message_with_text(text: str, entities: list | None = None) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.caption = None
    message.entities = entities or []
    message.caption_entities = []
    return message


def test_is_bot_mentioned_true() -> None:
    entity = MagicMock()
    entity.type = MessageEntityType.MENTION
    entity.offset = 0
    entity.length = 6
    message = _message_with_text("@mybot hi", [entity])
    assert is_bot_mentioned(message, "mybot") is True


def test_is_bot_mentioned_false() -> None:
    message = _message_with_text("hello everyone", [])
    assert is_bot_mentioned(message, "mybot") is False


def test_strip_bot_mention() -> None:
    assert strip_bot_mention("@mybot как дела?", "mybot") == "как дела?"
