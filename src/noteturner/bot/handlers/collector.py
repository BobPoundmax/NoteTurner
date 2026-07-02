import logging

from aiogram import F, Router
from aiogram.types import Message

from noteturner.bot.filters import ChatRoleFilter
from noteturner.db.repositories.chats import get_chat_by_telegram_id
from noteturner.db.repositories.collector import add_collector_message
from noteturner.db.session import session_scope

logger = logging.getLogger(__name__)

router = Router()


def _author_name(message: Message) -> str | None:
    user = message.from_user
    if user is None:
        return None
    parts = [user.first_name, user.last_name]
    name = " ".join(p for p in parts if p)
    return name or user.username


@router.message(F.text, ChatRoleFilter("collector"))
async def handle_collector_message(message: Message) -> None:
    try:
        async with session_scope() as session:
            chat = await get_chat_by_telegram_id(session, message.chat.id)
            if chat is None:
                return
            await add_collector_message(
                session,
                chat_id=chat.id,
                author_id=message.from_user.id if message.from_user else None,
                author_name=_author_name(message),
                text=message.text or "",
            )
    except RuntimeError:
        logger.warning("Collector message dropped: database not configured")
    except Exception:
        logger.exception("Failed to store collector message")
