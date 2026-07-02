from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import QueryLog


async def add_query_log(
    session: AsyncSession,
    *,
    telegram_chat_id: int,
    question: str,
    model: str | None,
) -> None:
    session.add(
        QueryLog(
            telegram_chat_id=telegram_chat_id,
            question=question,
            model=model,
        )
    )
    await session.commit()


async def count_query_logs(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(QueryLog))
    return int(result.scalar_one())
