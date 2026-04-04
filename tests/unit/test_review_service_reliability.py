from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from app.agent.channel.channel_repo import release_reserved_daily_slot, try_reserve_daily_slot
from app.agent.channel.review.service import approve_post, reject_post
from app.core.enums import PostStatus
from app.infrastructure.db.models import Channel, ChannelPost
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def _create_channel(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    telegram_id: int,
    daily_posts_count: int = 0,
    daily_count_date: str | None = None,
    max_posts_per_day: int = 3,
    username: str | None = "testchannel",
) -> Channel:
    async with session_maker() as session:
        channel = Channel(
            telegram_id=telegram_id,
            name="Test Channel",
            username=username,
            max_posts_per_day=max_posts_per_day,
            daily_posts_count=daily_posts_count,
            daily_count_date=daily_count_date,
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        return channel


async def _create_post(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    channel_id: int,
    status: str = PostStatus.DRAFT,
    scheduled_telegram_id: int | None = None,
) -> ChannelPost:
    async with session_maker() as session:
        post = ChannelPost(
            channel_id=channel_id,
            external_id=f"ext-{channel_id}-{status}",
            title="T",
            post_text="hello world",
            status=status,
            scheduled_telegram_id=scheduled_telegram_id,
            scheduled_at=datetime.now(UTC) if scheduled_telegram_id else None,
        )
        session.add(post)
        await session.commit()
        await session.refresh(post)
        return post


@pytest.mark.unit
class TestDailySlotReservation:
    async def test_try_reserve_daily_slot_resets_stale_day(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        telegram_id = -1001234567001
        await _create_channel(
            session_maker,
            telegram_id=telegram_id,
            daily_posts_count=3,
            daily_count_date="2000-01-01",
            max_posts_per_day=3,
        )

        reserved = await try_reserve_daily_slot(session_maker, telegram_id)

        assert reserved is True
        async with session_maker() as session:
            result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
            channel = result.scalar_one()
            assert channel.daily_posts_count == 1
            assert channel.daily_count_date == _today()

    async def test_release_reserved_daily_slot_decrements_counter(
        self, session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        telegram_id = -1001234567002
        await _create_channel(
            session_maker,
            telegram_id=telegram_id,
            daily_posts_count=2,
            daily_count_date=_today(),
            max_posts_per_day=3,
        )

        await release_reserved_daily_slot(session_maker, telegram_id)

        async with session_maker() as session:
            result = await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))
            channel = result.scalar_one()
            assert channel.daily_posts_count == 1


@pytest.mark.unit
class TestApprovePostReliability:
    async def test_approve_post_releases_slot_when_publish_returns_none(
        self, session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        telegram_id = -1001234567003
        await _create_channel(
            session_maker,
            telegram_id=telegram_id,
            daily_posts_count=0,
            daily_count_date=_today(),
            max_posts_per_day=3,
        )
        post = await _create_post(session_maker, channel_id=telegram_id)
        publish_fn = AsyncMock(return_value=None)

        status, msg_id = await approve_post(post.id, telegram_id, publish_fn, session_maker)

        assert status == "Failed to publish."
        assert msg_id is None
        async with session_maker() as session:
            channel = (await session.execute(select(Channel).where(Channel.telegram_id == telegram_id))).scalar_one()
            saved_post = (await session.execute(select(ChannelPost).where(ChannelPost.id == post.id))).scalar_one()
            assert channel.daily_posts_count == 0
            assert saved_post.status == PostStatus.DRAFT


@pytest.mark.unit
class TestRejectPostReliability:
    async def test_reject_scheduled_post_does_not_change_db_when_cancel_fails(
        self, session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        telegram_id = -1001234567004
        await _create_channel(session_maker, telegram_id=telegram_id)
        post = await _create_post(
            session_maker,
            channel_id=telegram_id,
            status=PostStatus.SCHEDULED,
            scheduled_telegram_id=42,
        )

        with patch("app.core.container.container.get_telethon_client", return_value=None):
            result = await reject_post(post.id, session_maker, reason="nope")

        assert result == "Failed to cancel scheduled post. Telethon is unavailable."
        async with session_maker() as session:
            saved_post = (await session.execute(select(ChannelPost).where(ChannelPost.id == post.id))).scalar_one()
            assert saved_post.status == PostStatus.SCHEDULED
            assert saved_post.scheduled_telegram_id == 42
            assert saved_post.scheduled_at is not None

    async def test_reject_scheduled_post_clears_schedule_after_successful_cancel(
        self, session_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        telegram_id = -1001234567005
        await _create_channel(session_maker, telegram_id=telegram_id, username="reviewchannel")
        post = await _create_post(
            session_maker,
            channel_id=telegram_id,
            status=PostStatus.SCHEDULED,
            scheduled_telegram_id=77,
        )
        telethon_client = SimpleNamespace(delete_scheduled_messages=AsyncMock(return_value=True))

        with patch("app.core.container.container.get_telethon_client", return_value=telethon_client):
            result = await reject_post(post.id, session_maker, reason="bad fit")

        assert result == "Post rejected."
        telethon_client.delete_scheduled_messages.assert_awaited_once()
        async with session_maker() as session:
            saved_post = (await session.execute(select(ChannelPost).where(ChannelPost.id == post.id))).scalar_one()
            assert saved_post.status == PostStatus.REJECTED
            assert saved_post.scheduled_telegram_id is None
            assert saved_post.scheduled_at is None
            assert saved_post.admin_feedback == "bad fit"
