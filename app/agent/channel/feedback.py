"""Feedback memory — summarizes admin preferences to improve future generations.

Uses an in-memory cache with 4-hour TTL to avoid redundant LLM summarization
calls. The cache is invalidated when the underlying post count changes
(approve/reject), so the summary stays fresh without per-cycle LLM cost.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.sql import func

from app.agent.channel.llm_client import openrouter_chat_completion
from app.core.enums import PostStatus
from app.core.logging import get_logger
from app.infrastructure.db.models import ChannelPost, ChannelSource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.feedback")

# In-memory cache: channel_id → (summary, timestamp, post_count_hash)
_feedback_cache: dict[int, tuple[str | None, float, int]] = {}

# Cache TTL: 12 hours (feedback changes rarely, saves LLM calls)
_CACHE_TTL_SECONDS = 12 * 60 * 60


def invalidate_feedback_cache(channel_id: int) -> None:
    """Invalidate the feedback cache for a channel.

    Call this after admin approves/rejects a post to force re-summarization.
    """
    _feedback_cache.pop(channel_id, None)


async def get_feedback_summary(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    api_key: str,
    model: str,
    *,
    http_timeout: int = 30,
    temperature: float = 0.2,
) -> str | None:
    """Summarize admin feedback patterns for a channel.

    Uses a 4-hour in-memory cache. The cache is also invalidated when the
    number of approved+rejected posts changes (detecting new admin actions).

    Analyzes approved/rejected posts and source health to produce
    a summary the generation agent can use as context.
    """
    # Quick count check to detect changes without loading all posts
    async with session_maker() as session:
        count_result = await session.execute(
            select(func.count(ChannelPost.id)).where(
                ChannelPost.channel_id == channel_id,
                ChannelPost.status.in_([PostStatus.APPROVED, PostStatus.REJECTED]),
            )
        )
        current_count = count_result.scalar() or 0

    # Check cache
    cached = _feedback_cache.get(channel_id)
    if cached is not None:
        summary, cached_at, cached_count = cached
        age = time.monotonic() - cached_at
        if age < _CACHE_TTL_SECONDS and cached_count == current_count:
            logger.debug("feedback_cache_hit", channel_id=channel_id, age_s=int(age))
            return summary

    # Cache miss or stale — fetch and summarize
    async with session_maker() as session:
        posts_result = await session.execute(
            select(ChannelPost)
            .where(
                ChannelPost.channel_id == channel_id, ChannelPost.status.in_([PostStatus.APPROVED, PostStatus.REJECTED])
            )
            .order_by(ChannelPost.created_at.desc())
            .limit(20)
        )
        posts = list(posts_result.scalars().all())

        sources_result = await session.execute(select(ChannelSource).where(ChannelSource.channel_id == channel_id))
        sources = list(sources_result.scalars().all())

    if not posts:
        _feedback_cache[channel_id] = (None, time.monotonic(), current_count)
        return None

    approved = [p for p in posts if p.status == PostStatus.APPROVED]
    rejected = [p for p in posts if p.status == PostStatus.REJECTED]

    context_parts = [
        f"Channel: {channel_id}",
        f"Total recent posts: {len(posts)} ({len(approved)} approved, {len(rejected)} rejected)",
    ]

    if approved:
        context_parts.append("\nApproved post titles:")
        for p in approved[:10]:
            context_parts.append(f"  - {p.title[:80]}")

    if rejected:
        context_parts.append("\nRejected post titles:")
        for p in rejected[:10]:
            feedback = f" (feedback: {p.admin_feedback})" if p.admin_feedback else ""
            context_parts.append(f"  - {p.title[:80]}{feedback}")

    if sources:
        active = [s for s in sources if s.enabled]
        disabled = [s for s in sources if not s.enabled]
        context_parts.append(f"\nSources: {len(active)} active, {len(disabled)} disabled")
        for s in disabled:
            context_parts.append(f"  Disabled: {s.url} (errors: {s.error_count}, last: {s.last_error})")

    context = "\n".join(context_parts)

    try:
        summary = await openrouter_chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the admin's content preferences in 3-5 bullet points. "
                        "What topics do they approve? What do they reject? "
                        "What patterns do you see? Keep it concise."
                    ),
                },
                {"role": "user", "content": context},
            ],
            operation="feedback",
            channel_id=str(channel_id),
            temperature=temperature,
            timeout=http_timeout,
            strip_code_fences=False,
        )
        result = str(summary) if summary else None
        if result:
            logger.info("feedback_summarized", channel_id=channel_id, length=len(result))

        # Store in cache
        _feedback_cache[channel_id] = (result, time.monotonic(), current_count)
        return result

    except Exception:
        logger.exception("feedback_summary_error")
        return None
