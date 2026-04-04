"""Smart notification system — alerts for viral posts, cost spikes, and anomalies.

Integrates with AnalyticsCollector (post-collection hooks) and
can be called from any module that detects noteworthy events.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now

if TYPE_CHECKING:
    from aiogram import Bot
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.notifications")

# Cooldown tracking to avoid spam (event_key → last_sent_utc)
_cooldowns: dict[str, float] = {}
_COOLDOWN_HOURS = 6


def _can_send(event_key: str) -> bool:
    """Check if enough time passed since last notification of this type."""
    now = utc_now().timestamp()
    last = _cooldowns.get(event_key, 0)
    if now - last < _COOLDOWN_HOURS * 3600:
        return False
    _cooldowns[event_key] = now
    return True


async def _notify_admin(bot: Bot, text: str) -> None:
    """Send notification to the admin report chat."""
    try:
        chat_id = settings.admin.default_report_chat_id
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("notification_send_failed")


# ── Viral Post Detection ──


async def check_viral_posts(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    *,
    multiplier: float = 2.0,
) -> None:
    """Alert if any post got significantly more views than the channel average.

    Called after analytics collection. Compares latest metrics against
    the 30-day average for the channel.
    """
    from sqlalchemy import text

    key = f"viral:{channel_id}"
    if not _can_send(key):
        return

    try:
        async with session_maker() as session:
            result = await session.execute(
                text("""
                    WITH avg_stats AS (
                        SELECT COALESCE(AVG(views), 0) as avg_views
                        FROM (
                            SELECT DISTINCT ON (message_id) views
                            FROM post_analytics
                            WHERE channel_id = :cid
                              AND measured_at > NOW() - interval '30 days'
                            ORDER BY message_id, measured_at DESC
                        ) sub
                    ),
                    recent AS (
                        SELECT DISTINCT ON (message_id)
                            message_id, views
                        FROM post_analytics
                        WHERE channel_id = :cid
                          AND measured_at > NOW() - interval '24 hours'
                        ORDER BY message_id, measured_at DESC
                    )
                    SELECT r.message_id, r.views, a.avg_views
                    FROM recent r, avg_stats a
                    WHERE a.avg_views > 0 AND r.views > a.avg_views * :mult
                    ORDER BY r.views DESC
                    LIMIT 3
                """),
                {"cid": channel_id, "mult": multiplier},
            )
            rows = result.fetchall()

        if not rows:
            return

        lines = ["🔥 <b>Вирусный пост!</b>\n"]
        for msg_id, views, avg_views in rows:
            ratio = views / avg_views if avg_views else 0
            lines.append(f"  📊 Пост #{msg_id}: <b>{views:,}</b> views ({ratio:.1f}x от среднего {avg_views:,.0f})")

        await _notify_admin(bot, "\n".join(lines))
        logger.info("viral_post_notification_sent", channel_id=channel_id, posts=len(rows))

    except Exception:
        logger.exception("viral_post_check_failed", channel_id=channel_id)


# ── Cost Alert ──


async def check_cost_alert(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    weekly_threshold_usd: float = 1.0,
) -> None:
    """Alert if LLM costs for the past 7 days exceed the threshold."""
    key = "cost_alert"
    if not _can_send(key):
        return

    try:
        from sqlalchemy import func, select

        from app.infrastructure.db.models import LLMUsageLog

        cutoff = utc_now() - timedelta(days=7)

        async with session_maker() as session:
            result = await session.execute(
                select(func.sum(LLMUsageLog.estimated_cost_usd)).where(LLMUsageLog.created_at >= cutoff)
            )
            total_cost = result.scalar() or 0

        if total_cost <= weekly_threshold_usd:
            return

        await _notify_admin(
            bot,
            f"💰 <b>LLM Cost Alert</b>\n\n"
            f"Расходы за 7 дней: <b>${total_cost:.4f}</b>\n"
            f"Порог: ${weekly_threshold_usd:.2f}",
        )
        logger.info("cost_alert_sent", total_cost=total_cost, threshold=weekly_threshold_usd)

    except Exception:
        logger.exception("cost_alert_check_failed")


# ── Post-Collection Hook ──


async def run_post_collection_checks(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
) -> None:
    """Run all notification checks after analytics collection.

    Called from AnalyticsCollector after each channel's metrics are collected.
    """
    await check_viral_posts(bot, session_maker, channel_id)
    await check_cost_alert(bot, session_maker)
