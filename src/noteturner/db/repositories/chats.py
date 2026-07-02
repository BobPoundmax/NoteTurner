from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import Chat


async def get_chat_by_telegram_id(session: AsyncSession, telegram_chat_id: int) -> Chat | None:
    result = await session.execute(
        select(Chat).where(Chat.telegram_chat_id == telegram_chat_id)
    )
    return result.scalar_one_or_none()


async def upsert_chat(
    session: AsyncSession,
    *,
    telegram_chat_id: int,
    role: str,
    title: str | None = None,
) -> Chat:
    chat = await get_chat_by_telegram_id(session, telegram_chat_id)
    if chat is None:
        chat = Chat(telegram_chat_id=telegram_chat_id, role=role, title=title)
        session.add(chat)
    else:
        chat.role = role
        if title is not None:
            chat.title = title
    await session.commit()
    await session.refresh(chat)
    return chat


async def list_chats(session: AsyncSession) -> list[Chat]:
    result = await session.execute(select(Chat).order_by(Chat.created_at))
    return list(result.scalars().all())


async def count_chats_by_role(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(Chat.role, func.count()).group_by(Chat.role))
    return {role: count for role, count in result.all()}
