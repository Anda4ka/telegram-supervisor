from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiogram import BaseMiddleware, Bot, types
from aiogram.types import TelegramObject

from app.core.ttl_cache import TTLSetCache
from app.presentation.telegram.logger import logger

if TYPE_CHECKING:
    from app.infrastructure.db.repositories import UserRepository

_blacklist_cache: TTLSetCache[int] = TTLSetCache(ttl=300)


def invalidate_blacklist_cache() -> None:
    _blacklist_cache.invalidate()


class BlacklistMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot = data["bot"]
        user_repo: UserRepository = data["user_repo"]

        blacklisted_ids = _blacklist_cache.get()
        if blacklisted_ids is None:
            blacklisted_users = await user_repo.get_blocked_users()
            blacklisted_ids = {user.id for user in blacklisted_users}
            _blacklist_cache.set(blacklisted_ids)

        if isinstance(event, types.Message) and event.from_user and event.from_user.id in blacklisted_ids:
            try:
                await bot.ban_chat_member(event.chat.id, event.from_user.id)
                await event.delete()
            except Exception as e:
                logger.error(f"Failed to ban or delete message for user {event.from_user.id}: {e}")
            return None

        return await handler(event, data)
