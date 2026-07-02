from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from noteturner.db.models import Admin


async def exists(session: AsyncSession, telegram_id: int) -> bool:
    result = await session.execute(
        select(Admin.id).where(Admin.telegram_id == telegram_id)
    )
    return result.first() is not None


async def add_admin(
    session: AsyncSession,
    *,
    telegram_id: int,
    added_by: int | None,
) -> bool:
    """Insert an admin. Returns False if it already existed."""
    if await exists(session, telegram_id):
        return False
    session.add(Admin(telegram_id=telegram_id, added_by=added_by))
    await session.commit()
    return True


async def remove_admin(session: AsyncSession, *, telegram_id: int) -> bool:
    """Delete an admin. Returns False if it was not present."""
    if not await exists(session, telegram_id):
        return False
    await session.execute(delete(Admin).where(Admin.telegram_id == telegram_id))
    await session.commit()
    return True


async def list_admins(session: AsyncSession) -> list[Admin]:
    result = await session.execute(select(Admin).order_by(Admin.created_at))
    return list(result.scalars().all())
