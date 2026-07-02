import logging

from noteturner.config.settings import Settings
from noteturner.db.repositories import admins as admins_repo
from noteturner.db.session import session_scope

logger = logging.getLogger(__name__)


def is_main_admin(user_id: int | None, settings: Settings) -> bool:
    return (
        user_id is not None
        and settings.admin_telegram_id > 0
        and user_id == settings.admin_telegram_id
    )


async def is_admin(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False
    if is_main_admin(user_id, settings):
        return True
    try:
        async with session_scope() as session:
            return await admins_repo.exists(session, user_id)
    except RuntimeError:
        return False
    except Exception:
        logger.exception("Failed to check admin status for %s", user_id)
        return False
