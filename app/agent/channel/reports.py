"""Analytics reports — weekly/monthly summaries for channel admins.

Aggregates post performance, LLM costs, top topics, best times,
and week-over-week trends. Supports scheduled auto-delivery.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text

from app.core.enums import PostStatus
from app.core.logging import get_logger
from app.core.time import utc_now
from app.infrastructure.db.models import ChannelPost, LLMUsageLog

if TYPE_CHECKING:
    from aiogram import Bot
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.reports")


# ── Main report generator ──


async def generate_channel_report(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    days: int = 7,
) -> str:
    """Generate a comprehensive text report for a channel.

    Returns formatted Markdown text ready for Telegram.
    """
    cutoff = utc_now() - timedelta(days=days)
    prev_cutoff = cutoff - timedelta(days=days)  # previous period for comparison
    period_label = f"{days}d" if days <= 7 else f"{days // 7}w"

    async with session_maker() as session:
        # ── Post stats (current period) ──
        posts = await _fetch_posts(session, channel_id, cutoff)
        approved = [p for p in posts if p.status == PostStatus.APPROVED]
        rejected = [p for p in posts if p.status == PostStatus.REJECTED]
        scheduled = [p for p in posts if p.status == PostStatus.SCHEDULED]

        # ── Previous period (for comparison) ──
        prev_posts = await _fetch_posts(session, channel_id, prev_cutoff, cutoff)
        prev_approved = [p for p in prev_posts if p.status == PostStatus.APPROVED]

        # ── Engagement ──
        engagement = await _get_engagement_stats(session, channel_id, cutoff)
        prev_engagement = await _get_engagement_stats(session, channel_id, prev_cutoff, cutoff)

        # ── Top posts by views ──
        top_posts = await _get_top_posts(session, channel_id, cutoff, limit=3)

        # ── LLM costs ──
        costs = await _get_cost_stats(session, cutoff)
        prev_costs = await _get_cost_stats(session, prev_cutoff, cutoff)

        # ── Cost per operation ──
        ops = await _get_cost_by_operation(session, cutoff)

    # ── Best time recommendation ──
    best_time = await _get_best_time(session_maker, channel_id)

    # ── Top topics ──
    topic_analysis = _analyze_topics(approved)

    # ── Format report ──
    lines = [f"📊 **Channel Report** ({period_label})", ""]

    # Posts section with trend
    lines.append("**Posts:**")
    lines.append(f"  Generated: {len(posts)}{_trend(len(posts), len(prev_posts))}")
    lines.append(f"  ✅ Approved: {len(approved)}{_trend(len(approved), len(prev_approved))}")
    lines.append(f"  ❌ Rejected: {len(rejected)}")
    if scheduled:
        lines.append(f"  ⏰ Scheduled: {len(scheduled)}")
    if approved or rejected:
        rate = len(approved) / max(len(approved) + len(rejected), 1) * 100
        lines.append(f"  Approval rate: {rate:.0f}%")

    # Engagement section with trends
    if engagement:
        lines.extend(["", "**Engagement:**"])
        avg_v = engagement.get("avg_views", 0)
        prev_v = prev_engagement.get("avg_views", 0)
        lines.append(f"  Avg views: {avg_v:,.0f}{_trend(avg_v, prev_v)}")

        avg_r = engagement.get("avg_reactions", 0)
        prev_r = prev_engagement.get("avg_reactions", 0)
        lines.append(f"  Avg reactions: {avg_r:.1f}{_trend(avg_r, prev_r)}")

        if engagement.get("avg_forwards", 0) > 0:
            lines.append(f"  Avg forwards: {engagement['avg_forwards']:.1f}")
        if engagement.get("avg_engagement_rate", 0) > 0:
            lines.append(f"  Engagement rate: {engagement['avg_engagement_rate']:.2f}%")

    # Top posts
    if top_posts:
        lines.extend(["", "**Top Posts:**"])
        for i, tp in enumerate(top_posts, 1):
            views = tp.get("views", 0)
            reactions = tp.get("reactions", 0)
            title = tp.get("title", "—")[:45]
            lines.append(f"  {i}. {title} — {views:,} views, {reactions} reactions")

    # Top topics
    if topic_analysis:
        lines.extend(["", "**Top Topics:**"])
        for topic, count in topic_analysis[:5]:
            lines.append(f"  • {topic} ({count} posts)")

    # Best posting time
    if best_time:
        lines.extend(["", "**Best Time to Post:**"])
        lines.append(f"  🕐 {best_time['recommended_time']} UTC ({best_time['confidence']} confidence)")
        if best_time.get("alternatives"):
            alts = ", ".join(best_time["alternatives"])
            lines.append(f"  Alternatives: {alts}")

    # LLM costs section with trend
    if costs.get("total_cost"):
        total = costs["total_cost"]
        prev_total = prev_costs.get("total_cost", 0)
        savings = costs.get("total_savings", 0)
        tokens = costs.get("total_tokens", 0)
        cost_per_post = total / max(len(approved), 1)

        lines.extend(["", "**LLM Costs:**"])
        lines.append(f"  Total: ${total:.4f}{_trend(total, prev_total, lower_is_better=True)}")
        if savings > 0:
            lines.append(f"  Cache savings: ${savings:.4f}")
        lines.append(f"  Tokens: {tokens:,}")
        lines.append(f"  Cost/approved post: ${cost_per_post:.4f}")
        lines.append(f"  API calls: {costs.get('total_calls', 0)}")

        if ops:
            lines.append("  By operation:")
            for op_name, op_cost, op_calls in ops[:5]:
                lines.append(f"    {op_name}: ${op_cost:.4f} ({op_calls} calls)")

    # Recommendations
    recs = _build_recommendations(posts, approved, rejected, engagement, prev_engagement, best_time)
    if recs:
        lines.extend(["", "**Recommendations:**"])
        for rec in recs:
            lines.append(f"  💡 {rec}")

    return "\n".join(lines)


# ── Data fetchers ──


async def _fetch_posts(session: AsyncSession, channel_id: int, after: Any, before: Any | None = None) -> list[Any]:
    query = select(ChannelPost).where(
        ChannelPost.channel_id == channel_id,
        ChannelPost.created_at >= after,
    )
    if before is not None:
        query = query.where(ChannelPost.created_at < before)
    result = await session.execute(query)
    return list(result.scalars().all())


async def _get_engagement_stats(
    session: AsyncSession, channel_id: int, after: Any, before: Any | None = None
) -> dict[str, Any]:
    """Get engagement stats from post_analytics table if it exists."""
    try:
        base_sql = """
            WITH latest AS (
                SELECT DISTINCT ON (pa.message_id)
                    pa.views, pa.forwards, pa.reactions_count, pa.comments_count, cp.title
                FROM post_analytics pa
                JOIN channel_posts cp ON pa.message_id = cp.telegram_message_id AND cp.channel_id = pa.channel_id
                WHERE pa.channel_id = :channel_id
                AND pa.collected_at >= :cutoff {before_filter}
                ORDER BY pa.message_id, pa.measured_at DESC
            )
            SELECT
                COALESCE(AVG(views), 0) as avg_views,
                COALESCE(AVG(reactions_count), 0) as avg_reactions,
                COALESCE(AVG(forwards), 0) as avg_forwards,
                CASE WHEN AVG(views) > 0
                    THEN AVG((reactions_count + forwards + comments_count)::float / NULLIF(views, 0)) * 100
                    ELSE 0 END as avg_engagement_rate,
                MAX(views) as max_views
            FROM latest
        """
        params: dict[str, Any] = {"channel_id": channel_id, "cutoff": after}
        if before:
            params["before"] = before
            sql = base_sql.format(before_filter="AND pa.collected_at < :before")  # noqa: S608
        else:
            sql = base_sql.format(before_filter="")

        result = await session.execute(text(sql), params)
        row = result.one_or_none()
        if row and row.avg_views and float(row.avg_views) > 0:
            return {
                "avg_views": float(row.avg_views),
                "avg_reactions": float(row.avg_reactions),
                "avg_forwards": float(row.avg_forwards),
                "avg_engagement_rate": float(row.avg_engagement_rate),
                "max_views": int(row.max_views or 0),
            }
    except Exception:
        logger.debug("engagement_stats_not_available")
    return {}


async def _get_top_posts(session: AsyncSession, channel_id: int, after: Any, limit: int = 3) -> list[dict[str, Any]]:
    """Get top posts by views."""
    try:
        result = await session.execute(
            text("""
                SELECT DISTINCT ON (pa.message_id)
                    cp.title, pa.views, pa.reactions_count, pa.forwards
                FROM post_analytics pa
                JOIN channel_posts cp ON pa.message_id = cp.telegram_message_id AND cp.channel_id = pa.channel_id
                WHERE pa.channel_id = :channel_id AND pa.collected_at >= :cutoff
                ORDER BY pa.message_id, pa.measured_at DESC
            """),
            {"channel_id": channel_id, "cutoff": after},
        )
        rows = result.fetchall()
        ranked = sorted(rows, key=lambda r: r.views or 0, reverse=True)
        return [
            {"title": r.title, "views": r.views or 0, "reactions": r.reactions_count or 0, "forwards": r.forwards or 0}
            for r in ranked[:limit]
        ]
    except Exception:
        logger.debug("top_posts_not_available")
        return []


async def _get_cost_stats(session: AsyncSession, after: Any, before: Any | None = None) -> dict[str, Any]:
    query = select(
        func.sum(LLMUsageLog.estimated_cost_usd).label("total_cost"),
        func.sum(LLMUsageLog.cache_savings_usd).label("total_savings"),
        func.sum(LLMUsageLog.total_tokens).label("total_tokens"),
        func.count().label("total_calls"),
    ).where(LLMUsageLog.created_at >= after)
    if before is not None:
        query = query.where(LLMUsageLog.created_at < before)
    result = await session.execute(query)
    row = result.one_or_none()
    if row and row.total_cost is not None:
        return {
            "total_cost": float(row.total_cost),
            "total_savings": float(row.total_savings or 0),
            "total_tokens": int(row.total_tokens or 0),
            "total_calls": int(row.total_calls or 0),
        }
    return {}


async def _get_cost_by_operation(session: AsyncSession, after: Any) -> list[tuple[str, float, int]]:
    result = await session.execute(
        select(
            LLMUsageLog.operation,
            func.sum(LLMUsageLog.estimated_cost_usd).label("cost"),
            func.count().label("calls"),
        )
        .where(LLMUsageLog.created_at >= after)
        .group_by(LLMUsageLog.operation)
        .order_by(func.sum(LLMUsageLog.estimated_cost_usd).desc())
    )
    return [(r.operation, float(r.cost or 0), int(r.calls or 0)) for r in result.all()]


async def _get_best_time(session_maker: Any, channel_id: int) -> dict[str, Any] | None:
    try:
        from app.agent.channel.best_time import recommend_posting_time

        return await recommend_posting_time(session_maker, channel_id)
    except Exception:
        logger.debug("best_time_not_available")
        return None


# ── Analysis helpers ──


def _analyze_topics(approved_posts: list[Any]) -> list[tuple[str, int]]:
    """Extract top topic keywords from approved post titles."""
    if not approved_posts:
        return []

    # Simple keyword extraction from titles
    import re
    from collections import Counter

    stop_words = {
        "в",
        "на",
        "и",
        "с",
        "для",
        "по",
        "от",
        "к",
        "из",
        "что",
        "как",
        "это",
        "the",
        "a",
        "an",
        "in",
        "on",
        "for",
        "to",
        "of",
        "and",
        "is",
        "are",
        "—",
        "–",
        "-",
        "|",
        ":",
        "не",
        "но",
        "или",
        "уже",
    }

    words: list[str] = []
    for post in approved_posts:
        title = post.title if hasattr(post, "title") else ""
        tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]{3,}", title.lower())
        words.extend(t for t in tokens if t not in stop_words)

    return Counter(words).most_common(10)


def _trend(current: float, previous: float, *, lower_is_better: bool = False) -> str:
    """Format a trend indicator: ↑12% / ↓5% / →."""
    if not previous or not current:
        return ""
    change = ((current - previous) / previous) * 100
    if abs(change) < 2:
        return " →"
    if lower_is_better:
        arrow = "↓" if change < 0 else "↑"
        color = "" if change < 0 else " ⚠️"
    else:
        arrow = "↑" if change > 0 else "↓"
        color = "" if change > 0 else " ⚠️"
    return f" {arrow}{abs(change):.0f}%{color}"


def _build_recommendations(
    all_posts: list[Any],
    approved: list[Any],
    rejected: list[Any],
    engagement: dict[str, Any],
    prev_engagement: dict[str, Any],
    best_time: dict[str, Any] | None,
) -> list[str]:
    """Build actionable recommendations based on data."""
    recs: list[str] = []

    if not all_posts:
        recs.append("No posts generated — check source health and pipeline config")
        return recs

    # Approval rate
    approval_rate = len(approved) / max(len(approved) + len(rejected), 1)
    if approval_rate < 0.5 and len(rejected) >= 3:
        recs.append(f"Low approval rate ({approval_rate:.0%}) — adjust screening threshold or Brand Voice")

    # Engagement decline
    curr_views = engagement.get("avg_views", 0)
    prev_views = prev_engagement.get("avg_views", 0)
    if prev_views > 0 and curr_views < prev_views * 0.8:
        drop = (1 - curr_views / prev_views) * 100
        recs.append(f"Views dropped {drop:.0f}% vs previous period — try different topics or posting times")

    # Low reactions despite views
    if curr_views > 100 and engagement.get("avg_reactions", 0) < 1:
        recs.append("Good views but low reactions — try more opinionated/controversial openings")

    # Best time suggestion
    if best_time and best_time.get("source") == "data" and best_time.get("confidence") in ("high", "medium"):
        recs.append(f"Data shows best posting time: {best_time['recommended_time']} UTC")

    # Few approved posts
    if len(approved) < 2 and len(all_posts) >= 5:
        recs.append("Most generated posts rejected — consider re-analyzing Brand Voice profile")

    return recs


# ── Send to admin ──


async def send_report_to_admin(
    bot: Bot,
    admin_chat_id: int,
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    days: int = 7,
) -> bool:
    """Generate and send a report to the admin."""
    try:
        from app.core.markdown import md_to_entities

        report = await generate_channel_report(session_maker, channel_id, days=days)
        plain, entities = md_to_entities(report)
        await bot.send_message(
            chat_id=admin_chat_id,
            text=plain,
            entities=entities,
            parse_mode=None,
        )
        logger.info("report_sent", admin_chat_id=admin_chat_id, channel_id=channel_id, days=days)
        return True
    except Exception:
        logger.exception("report_send_failed", channel_id=channel_id)
        return False


# ── Scheduled report delivery ──


class ReportScheduler:
    """Background scheduler that sends weekly reports automatically.

    Started by the orchestrator on startup. Sends reports every Monday at 09:00 UTC
    to the channel's review_chat_id (or first super admin).
    """

    def __init__(
        self,
        bot: Bot,
        session_maker: async_sessionmaker[AsyncSession],
        channel_ids: list[int],
        *,
        report_day: int = 0,  # Monday
        report_hour: int = 9,  # 09:00 UTC
    ) -> None:
        self.bot = bot
        self.session_maker = session_maker
        self.channel_ids = channel_ids
        self.report_day = report_day
        self.report_hour = report_hour
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())
        logger.info("report_scheduler_started", channels=len(self.channel_ids))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("report_scheduler_stopped")

    async def _run_loop(self) -> None:
        while True:
            now = utc_now()
            if now.weekday() == self.report_day and now.hour == self.report_hour:
                await self._send_all_reports()
                # Sleep past the current hour to avoid double-send
                await asyncio.sleep(3600)
            else:
                # Check every 30 minutes
                await asyncio.sleep(1800)

    async def _send_all_reports(self) -> None:
        """Send weekly reports for all configured channels."""
        from sqlalchemy import select

        from app.core.config import settings
        from app.infrastructure.db.models import Channel

        for channel_id in self.channel_ids:
            try:
                # Get review_chat_id from channel config
                async with self.session_maker() as session:
                    result = await session.execute(
                        select(Channel.review_chat_id).where(Channel.telegram_id == channel_id)
                    )
                    row = result.one_or_none()

                admin_chat_id = row[0] if row and row[0] else None
                if not admin_chat_id:
                    if settings.admin.super_admins:
                        admin_chat_id = settings.admin.super_admins[0]
                    else:
                        logger.warning("no_admin_for_report", channel_id=channel_id)
                        continue

                await send_report_to_admin(self.bot, admin_chat_id, self.session_maker, channel_id, days=7)
            except Exception:
                logger.exception("scheduled_report_failed", channel_id=channel_id)
