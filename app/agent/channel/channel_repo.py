"""Channel repository — DB access for the channels table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.time import utc_now
from app.infrastructure.db.models import Channel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.repo")


async def get_active_channels(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[Channel]:
    """Return all enabled channels."""
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.enabled.is_(True)).order_by(Channel.id))
        return list(result.scalars().all())


async def get_channel_by_telegram_id(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> Channel | None:
    """Find a channel by its numeric Telegram chat ID."""
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
        return result.scalar_one_or_none()


async def create_channel(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
    name: str,
    *,
    description: str = "",
    language: str = "ru",
    review_chat_id: int | None = None,
    max_posts_per_day: int = 3,
    posting_schedule: list[str] | None = None,
    discovery_query: str = "",
    source_discovery_query: str = "",
    username: str | None = None,
) -> Channel:
    """Create a new channel. Raises on duplicate telegram_id."""
    async with session_maker() as session:
        channel = Channel(
            telegram_id=telegram_id,
            name=name,
            description=description,
            language=language,
            review_chat_id=review_chat_id,
            max_posts_per_day=max_posts_per_day,
            posting_schedule=posting_schedule,
            discovery_query=discovery_query,
            source_discovery_query=source_discovery_query,
            username=username,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        logger.info("channel_created", telegram_id=telegram_id, name=name)
        return channel


async def update_channel(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
    **fields: object,
) -> Channel | None:
    """Update channel fields. Returns None if not found."""
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return None
        for key, value in fields.items():
            if hasattr(channel, key):
                setattr(channel, key, value)
        await session.commit()
        await session.refresh(channel)
        logger.info("channel_updated", telegram_id=telegram_id, fields=list(fields.keys()))
        return channel


async def delete_channel(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> bool:
    """Delete a channel. Returns False if not found."""
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return False
        await session.delete(channel)
        await session.commit()
        logger.info("channel_deleted", telegram_id=telegram_id)
        return True


async def reset_daily_count_if_needed(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> None:
    """Reset daily post counter if the date has changed."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
        channel = result.scalar_one_or_none()
        if channel:
            channel.reset_daily_count(today)
            await session.commit()


async def try_reserve_daily_slot(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> bool:
    """Atomically reserve one daily publishing slot.

    Resets stale day counters as part of the same SQL statement so manual approve
    flows cannot be blocked by yesterday's count.
    Returns ``True`` when a slot was reserved or when no channel row exists.
    Returns ``False`` only when the configured daily limit has been reached.
    """
    from sqlalchemy import text

    today = datetime.now(UTC).strftime("%Y-%m-%d")

    async with session_maker() as session:
        result = await session.execute(
            text(
                "UPDATE channels "
                "SET daily_posts_count = CASE "
                "    WHEN daily_count_date IS NULL OR daily_count_date != :today THEN 1 "
                "    ELSE daily_posts_count + 1 "
                "END, "
                "daily_count_date = :today "
                "WHERE telegram_id = :tid "
                "AND CASE "
                "    WHEN daily_count_date IS NULL OR daily_count_date != :today THEN 0 "
                "    ELSE daily_posts_count "
                "END < max_posts_per_day "
                "RETURNING daily_posts_count"
            ),
            {"tid": telegram_id, "today": today},
        )
        row = result.fetchone()
        if row is not None:
            await session.commit()
            return True

        exists = await session.execute(
            text("SELECT 1 FROM channels WHERE telegram_id = :tid"),
            {"tid": telegram_id},
        )
        await session.commit()
        return exists.fetchone() is None


async def release_reserved_daily_slot(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> None:
    """Release one previously reserved daily slot after a failed publish attempt."""
    from sqlalchemy import text

    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE channels "
                "SET daily_posts_count = CASE "
                "    WHEN daily_posts_count > 0 THEN daily_posts_count - 1 "
                "    ELSE 0 "
                "END "
                "WHERE telegram_id = :tid"
            ),
            {"tid": telegram_id},
        )
        await session.commit()


async def update_source_discovery_time(
    session_maker: async_sessionmaker[AsyncSession],
    telegram_id: int,
) -> None:
    """Record that source discovery was run for this channel."""
    async with session_maker() as session:
        result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
        channel = result.scalar_one_or_none()
        if channel:
            channel.last_source_discovery_at = utc_now()
            await session.commit()
