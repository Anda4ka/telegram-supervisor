"""Channel analytics collector — views, reactions, forwards via Telethon.

Collects post-level metrics from Telegram channels using the Client API
(GetMessagesViewsRequest works for any channel, no 500+ subscriber limit).

Stores measurements in ``post_analytics`` table with time-series data
so we can compute engagement rates and view-growth curves.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.core.time import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from telethon import TelegramClient

logger = get_logger("channel.analytics")


async def collect_post_metrics(
    client: TelegramClient,
    channel_id: int,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    lookback_days: int = 30,
    batch_size: int = 50,
) -> int:
    """Collect views, reactions, forwards for recent posts.

    Args:
        client: Authenticated Telethon client.
        channel_id: Telegram channel ID (negative number).
        session_maker: SQLAlchemy async session maker.
        lookback_days: How far back to collect metrics.
        batch_size: Number of messages to fetch per batch.

    Returns:
        Number of metric records saved.
    """
    from datetime import timedelta

    from telethon.tl.functions.messages import GetMessagesViewsRequest

    now = utc_now()
    cutoff = now - timedelta(days=lookback_days)
    saved = 0

    try:
        entity = await client.get_entity(channel_id)
    except Exception:
        logger.exception("analytics_get_entity_failed", channel_id=channel_id)
        return 0

    try:
        messages = []
        async for msg in client.iter_messages(entity, limit=batch_size * 3):
            if msg.date and msg.date.replace(tzinfo=None) < cutoff:
                break
            if msg.text or msg.media:
                messages.append(msg)

        if not messages:
            logger.info("analytics_no_messages", channel_id=channel_id)
            return 0

        # Batch request views (up to 100 per call)
        for i in range(0, len(messages), 100):
            batch = messages[i : i + 100]
            msg_ids = [m.id for m in batch]

            try:
                views_result = await client(
                    GetMessagesViewsRequest(
                        peer=entity,
                        id=msg_ids,
                        increment=False,
                    )
                )
            except Exception:
                logger.exception("analytics_views_request_failed", channel_id=channel_id)
                views_result = None

            for idx, msg in enumerate(batch):
                views = 0
                if views_result and idx < len(views_result.views):
                    view_info = views_result.views[idx]
                    views = getattr(view_info, "views", 0) or 0

                forwards = msg.forwards or 0

                # Extract reactions
                reactions_count = 0
                reactions_breakdown: dict[str, int] = {}
                if msg.reactions:
                    for r in msg.reactions.results:
                        emoji = getattr(r.reaction, "emoticon", None) or str(r.reaction)
                        reactions_breakdown[emoji] = r.count
                        reactions_count += r.count

                # Comments (replies)
                comments_count = 0
                if msg.replies:
                    comments_count = msg.replies.replies or 0

                msg_date = msg.date.replace(tzinfo=None) if msg.date else now
                hours_since = (now - msg_date).total_seconds() / 3600

                await _save_metric(
                    session_maker=session_maker,
                    channel_id=channel_id,
                    message_id=msg.id,
                    views=views,
                    forwards=forwards,
                    reactions_count=reactions_count,
                    reactions_breakdown=reactions_breakdown,
                    comments_count=comments_count,
                    published_at=msg_date,
                    hours_since_publish=hours_since,
                )
                saved += 1

        logger.info("analytics_collected", channel_id=channel_id, records=saved)

    except Exception:
        logger.exception("analytics_collection_error", channel_id=channel_id)

    return saved


async def _save_metric(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    message_id: int,
    views: int,
    forwards: int,
    reactions_count: int,
    reactions_breakdown: dict[str, int],
    comments_count: int,
    published_at: datetime,
    hours_since_publish: float,
) -> None:
    """Save a single metric record to the database."""
    from sqlalchemy import text

    now = utc_now()
    async with session_maker() as session:
        await session.execute(
            text("""
                INSERT INTO post_analytics
                    (channel_id, message_id, views, forwards,
                     reactions_count, reactions_breakdown, comments_count,
                     published_at, hours_since_publish, measured_at)
                VALUES
                    (:channel_id, :message_id, :views, :forwards,
                     :reactions_count, cast(:reactions_breakdown as jsonb), :comments_count,
                     :published_at, :hours_since_publish, :measured_at)
            """),
            {
                "channel_id": channel_id,
                "message_id": message_id,
                "views": views,
                "forwards": forwards,
                "reactions_count": reactions_count,
                "reactions_breakdown": json.dumps(reactions_breakdown),
                "comments_count": comments_count,
                "published_at": published_at,
                "hours_since_publish": round(hours_since_publish, 1),
                "measured_at": now,
            },
        )
        await session.commit()


async def get_engagement_rate(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    *,
    days: int = 30,
) -> dict[str, float]:
    """Calculate engagement metrics for the channel.

    Returns dict with avg_engagement_rate, avg_views, total_posts, etc.
    """
    from sqlalchemy import text

    async with session_maker() as session:
        result = await session.execute(
            text("""
                WITH latest_metrics AS (
                    SELECT DISTINCT ON (message_id)
                        message_id, views, forwards, reactions_count,
                        comments_count, published_at, hours_since_publish
                    FROM post_analytics
                    WHERE channel_id = :channel_id
                      AND measured_at > NOW() - make_interval(days => :days)
                    ORDER BY message_id, measured_at DESC
                )
                SELECT
                    COUNT(*) as total_posts,
                    COALESCE(AVG(views), 0) as avg_views,
                    COALESCE(AVG(forwards), 0) as avg_forwards,
                    COALESCE(AVG(reactions_count), 0) as avg_reactions,
                    COALESCE(AVG(comments_count), 0) as avg_comments,
                    CASE
                        WHEN AVG(views) > 0
                        THEN AVG((reactions_count + forwards + comments_count)::float / NULLIF(views, 0)) * 100
                        ELSE 0
                    END as avg_engagement_rate
                FROM latest_metrics
            """),
            {"channel_id": channel_id, "days": days},
        )
        row = result.fetchone()

    if not row:
        return {
            "total_posts": 0,
            "avg_views": 0.0,
            "avg_forwards": 0.0,
            "avg_reactions": 0.0,
            "avg_comments": 0.0,
            "avg_engagement_rate": 0.0,
        }

    return {
        "total_posts": row[0],
        "avg_views": round(float(row[1]), 1),
        "avg_forwards": round(float(row[2]), 1),
        "avg_reactions": round(float(row[3]), 1),
        "avg_comments": round(float(row[4]), 1),
        "avg_engagement_rate": round(float(row[5]), 2),
    }


async def get_hourly_performance(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    *,
    days: int = 30,
) -> list[dict]:
    """Get average engagement by hour of day (0-23).

    Returns list of {hour, avg_views, avg_engagement_rate, post_count}.
    """
    from sqlalchemy import text

    async with session_maker() as session:
        result = await session.execute(
            text("""
                WITH latest_metrics AS (
                    SELECT DISTINCT ON (message_id)
                        message_id, views, forwards, reactions_count,
                        comments_count, published_at
                    FROM post_analytics
                    WHERE channel_id = :channel_id
                      AND measured_at > NOW() - make_interval(days => :days)
                    ORDER BY message_id, measured_at DESC
                )
                SELECT
                    EXTRACT(HOUR FROM published_at) as hour,
                    COUNT(*) as post_count,
                    AVG(views) as avg_views,
                    CASE
                        WHEN AVG(views) > 0
                        THEN AVG((reactions_count + forwards + comments_count)::float / NULLIF(views, 0)) * 100
                        ELSE 0
                    END as avg_engagement_rate
                FROM latest_metrics
                GROUP BY EXTRACT(HOUR FROM published_at)
                ORDER BY hour
            """),
            {"channel_id": channel_id, "days": days},
        )
        rows = result.fetchall()

    return [
        {
            "hour": int(row[0]),
            "post_count": row[1],
            "avg_views": round(float(row[2]), 1),
            "avg_engagement_rate": round(float(row[3]), 2),
        }
        for row in rows
    ]


class AnalyticsCollector:
    """Background collector that periodically gathers channel metrics."""

    def __init__(
        self,
        client: TelegramClient,
        session_maker: async_sessionmaker[AsyncSession],
        channel_ids: list[int],
        *,
        interval_minutes: int = 120,
        lookback_days: int = 30,
    ) -> None:
        self.client = client
        self.session_maker = session_maker
        self.channel_ids = channel_ids
        self.interval_minutes = interval_minutes
        self.lookback_days = lookback_days
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())
        logger.info("analytics_collector_started", channels=self.channel_ids)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("analytics_collector_stopped")

    async def _run_loop(self) -> None:
        await asyncio.sleep(30)  # Initial delay

        while True:
            for channel_id in self.channel_ids:
                try:
                    await collect_post_metrics(
                        self.client,
                        channel_id,
                        self.session_maker,
                        lookback_days=self.lookback_days,
                    )
                    # Post-collection notification checks (viral posts, cost alerts)
                    await self._run_notifications(channel_id)
                except Exception:
                    logger.exception("analytics_loop_error", channel_id=channel_id)
            await asyncio.sleep(self.interval_minutes * 60)

    async def _run_notifications(self, channel_id: int) -> None:
        """Run notification checks after collecting metrics for a channel."""
        try:
            from app.core.container import container

            bot = container.try_get_bot()
            if not bot:
                return
            from app.agent.channel.notifications import run_post_collection_checks

            await run_post_collection_checks(bot, self.session_maker, channel_id)
        except Exception:
            logger.debug("notification_checks_skipped", channel_id=channel_id, exc_info=True)
