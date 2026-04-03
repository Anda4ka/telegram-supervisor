import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware, types
from aiogram.types import TelegramObject

from app.core.config import settings
from app.core.ttl_cache import TTLSetCache

if TYPE_CHECKING:
    from app.infrastructure.db.repositories import AdminRepository

_admin_cache: TTLSetCache[int] = TTLSetCache(ttl=300)


def invalidate_admin_cache() -> None:
    _admin_cache.invalidate()


async def you_are_not_admin(event: TelegramObject, text: str = "🚫 You are not an Admin.") -> None:
    if isinstance(event, types.Message):
        answer = await event.answer(text)
        await event.delete()
        await asyncio.sleep(5)
        await answer.delete()


class SuperAdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, (types.Message, types.CallbackQuery))
            and event.from_user
            and event.from_user.id in settings.admin.super_admins
        ):
            return await handler(event, data)
        await you_are_not_admin(event, "You are not a Super Admin.")
        return None


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        admin_repo: AdminRepository = data["admin_repo"]

        admin_ids = _admin_cache.get()
        if admin_ids is None:
            db_admins = await admin_repo.get_db_admins()
            admin_ids = {admin.id for admin in db_admins}
            _admin_cache.set(admin_ids)

        all_admins_id = admin_ids | set(settings.admin.super_admins)
        if (
            isinstance(event, (types.Message, types.CallbackQuery))
            and event.from_user
            and event.from_user.id in all_admins_id
        ):
            return await handler(event, data)
        await you_are_not_admin(event)
        return None
