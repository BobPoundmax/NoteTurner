from unittest.mock import MagicMock

from noteturner.bot.filters import ChatRoleFilter


async def test_chat_role_filter_matches() -> None:
    filter_ = ChatRoleFilter("assistant")
    assert await filter_(MagicMock(), chat_role="assistant") is True


async def test_chat_role_filter_rejects_other_role() -> None:
    filter_ = ChatRoleFilter("assistant")
    assert await filter_(MagicMock(), chat_role="collector") is False


async def test_chat_role_filter_rejects_missing_role() -> None:
    filter_ = ChatRoleFilter("collector")
    assert await filter_(MagicMock()) is False
