from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import CollectorMessage


async def add_collector_message(
    session: AsyncSession,
    *,
    chat_id: int,
    author_id: int | None,
    author_name: str | None,
    text: str,
) -> None:
    session.add(
        CollectorMessage(
            chat_id=chat_id,
            author_id=author_id,
            author_name=author_name,
            text=text,
        )
    )
    await session.commit()


async def count_collector_messages(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(CollectorMessage))
    return int(result.scalar_one())
