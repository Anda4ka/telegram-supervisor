"""Source fetcher implementations for the content pipeline.

Each fetcher handles one source_type (rss, telegram, twitter, etc.)
and encapsulates its own error handling and health tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.agent.channel.config import ChannelAgentSettings
    from app.agent.channel.sources import ContentItem
    from app.infrastructure.db.models import Channel, ChannelSource

logger = get_logger("channel.source_fetchers")


@dataclass
class FetchContext:
    """Shared dependencies for source fetching."""

    session_maker: async_sessionmaker[AsyncSession]
    config: ChannelAgentSettings
    api_key: str
    brave_api_key: str
    channel: Channel


class SourceFetcher(Protocol):
    """Protocol for source fetchers."""

    source_type: str

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]: ...


async def _record_success(session_maker: async_sessionmaker[AsyncSession], url: str) -> None:
    from app.agent.channel.source_manager import record_fetch_success

    await record_fetch_success(session_maker, url)


async def _record_error(session_maker: async_sessionmaker[AsyncSession], url: str, error: str) -> None:
    from app.agent.channel.source_manager import record_fetch_error

    await record_fetch_error(session_maker, url, error)


class RSSFetcher:
    """Fetch content from RSS feeds."""

    source_type = "rss"

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]:
        from app.agent.channel.sources import fetch_all_sources

        urls = [s.url for s in sources]
        if not urls:
            return []

        result = await fetch_all_sources(urls, http_timeout=ctx.config.http_timeout)

        for url in result.successful_urls:
            await _record_success(ctx.session_maker, url)
        for url in result.errored_urls:
            await _record_error(ctx.session_maker, url, "fetch_error")

        return list(result.items)


class TelegramChannelFetcher:
    """Fetch content from Telegram channels via Telethon."""

    source_type = "telegram"

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]:
        if not sources:
            return []

        try:
            from app.agent.channel.external_sources.telegram_channels import fetch_channel_posts
            from app.core.container import container
        except ImportError:
            logger.warning("telethon_client_not_available")
            return []

        telethon_wrapper = container.get_telethon_client()
        if not telethon_wrapper or not telethon_wrapper.is_available:
            logger.warning("telethon_not_connected_skipping_tg_channels")
            return []

        raw_client = telethon_wrapper.client
        if not raw_client:
            logger.warning("telethon_not_connected_skipping_tg_channels")
            return []

        all_items: list[ContentItem] = []
        for src in sources:
            try:
                raw_id = int(src.url.split(":")[-1])
                tg_id = int(f"-100{raw_id}")
                items = await fetch_channel_posts(
                    raw_client,
                    tg_id,
                    max_items=ctx.config.max_items_per_source,
                    hours_lookback=24,
                )
                all_items.extend(items)
                await _record_success(ctx.session_maker, src.url)
            except Exception:
                logger.warning("tg_channel_source_error", url=src.url, exc_info=True)
                await _record_error(ctx.session_maker, src.url, "tg_fetch_error")

        return all_items


class TelegramForumFetcher:
    """Fetch content from Telegram forums via Telethon."""

    source_type = "telegram_forum"

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]:
        if not sources:
            return []

        try:
            from app.agent.channel.external_sources.telegram_channels import fetch_forum_topics
            from app.core.container import container
        except ImportError:
            logger.warning("telethon_client_not_available")
            return []

        telethon_wrapper = container.get_telethon_client()
        if not telethon_wrapper or not telethon_wrapper.is_available:
            logger.warning("telethon_not_connected_skipping_tg_forums")
            return []

        raw_client = telethon_wrapper.client
        if not raw_client:
            logger.warning("telethon_not_connected_skipping_tg_forums")
            return []

        all_items: list[ContentItem] = []
        for src in sources:
            try:
                raw_id = int(src.url.split(":")[-1])
                tg_id = int(f"-100{raw_id}")
                items = await fetch_forum_topics(
                    raw_client,
                    tg_id,
                    max_items_per_topic=3,
                    hours_lookback=24,
                )
                all_items.extend(items)
                await _record_success(ctx.session_maker, src.url)
            except Exception:
                logger.warning("tg_forum_source_error", url=src.url, exc_info=True)
                await _record_error(ctx.session_maker, src.url, "tg_fetch_error")

        return all_items


class TwitterFetcher:
    """Fetch content from Twitter/X via Nitter RSS."""

    source_type = "twitter"

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]:
        if not sources:
            return []

        from app.agent.channel.external_sources import fetch_twitter_user
        from app.agent.channel.rate_limiter import twitter_limiter

        all_items: list[ContentItem] = []
        for src in sources:
            try:
                await twitter_limiter.acquire()
                username = src.url.removeprefix("twitter:").lstrip("@")
                items = await fetch_twitter_user(
                    username,
                    max_items=ctx.config.max_items_per_source,
                    http_timeout=ctx.config.http_timeout,
                )
                all_items.extend(items)
                await _record_success(ctx.session_maker, src.url)
            except Exception:
                logger.warning("twitter_source_error", url=src.url, exc_info=True)
                await _record_error(ctx.session_maker, src.url, "twitter_fetch_error")

        logger.info("twitter_sources_fetched", accounts=len(sources), items_added=len(all_items))
        return all_items


class RedditFetcher:
    """Fetch content from Reddit via JSON API."""

    source_type = "reddit"

    async def fetch(self, sources: list[ChannelSource], ctx: FetchContext) -> list[ContentItem]:
        if not sources:
            return []

        from app.agent.channel.external_sources import fetch_subreddit
        from app.agent.channel.rate_limiter import reddit_limiter

        all_items: list[ContentItem] = []
        for src in sources:
            try:
                await reddit_limiter.acquire()
                subreddit = src.url.removeprefix("reddit:").lstrip("r/").lstrip("/")
                items = await fetch_subreddit(
                    subreddit,
                    max_items=ctx.config.max_items_per_source,
                    min_upvotes=10,
                    max_age_hours=24,
                    http_timeout=ctx.config.http_timeout,
                )
                all_items.extend(items)
                await _record_success(ctx.session_maker, src.url)
            except Exception:
                logger.warning("reddit_source_error", url=src.url, exc_info=True)
                await _record_error(ctx.session_maker, src.url, "reddit_fetch_error")

        logger.info("reddit_sources_fetched", subreddits=len(sources), items_added=len(all_items))
        return all_items


# Registry of all DB-source fetchers (keyed by source_type)
DB_SOURCE_FETCHERS: list[
    RSSFetcher | TelegramChannelFetcher | TelegramForumFetcher | TwitterFetcher | RedditFetcher
] = [
    RSSFetcher(),
    TelegramChannelFetcher(),
    TelegramForumFetcher(),
    TwitterFetcher(),
    RedditFetcher(),
]


async def fetch_db_sources(
    db_sources: list[ChannelSource],
    ctx: FetchContext,
) -> list[ContentItem]:
    """Run all registered fetchers against their matching source types."""
    all_items: list[Any] = []
    for fetcher in DB_SOURCE_FETCHERS:
        matching = [s for s in db_sources if s.source_type == fetcher.source_type]
        if matching:
            items = await fetcher.fetch(matching, ctx)
            all_items.extend(items)
    return all_items


async def fetch_discovery_sources(ctx: FetchContext, brave_api_key: str) -> list[ContentItem]:
    """Fetch from discovery sources (Perplexity, Brave) that don't use DB sources."""
    all_items: list[Any] = []

    if ctx.config.discovery_enabled:
        from app.agent.channel.config import DEFAULT_DISCOVERY_QUERY
        from app.agent.channel.discovery import discover_content

        query = ctx.channel.discovery_query or DEFAULT_DISCOVERY_QUERY
        discovered = await discover_content(
            api_key=ctx.api_key,
            query=query,
            model=ctx.config.discovery_model,
            channel_name=ctx.channel.name,
            discovery_query=ctx.channel.discovery_query,
            http_timeout=ctx.config.http_timeout,
            temperature=ctx.config.temperature,
        )
        all_items.extend(discovered)

    if ctx.config.brave_discovery_enabled and brave_api_key:
        try:
            from app.agent.channel.brave_search import discover_content_brave
            from app.agent.channel.config import DEFAULT_BRAVE_DISCOVERY_QUERY
            from app.agent.channel.rate_limiter import brave_limiter

            await brave_limiter.acquire()

            brave_query = ctx.channel.discovery_query or DEFAULT_BRAVE_DISCOVERY_QUERY
            brave_items = await discover_content_brave(
                api_key=brave_api_key,
                query=brave_query,
                count=5,
                freshness="pw",
                timeout=ctx.config.http_timeout,
            )
            all_items.extend(brave_items)
        except Exception:
            logger.exception("brave_discovery_error", channel_id=ctx.channel.id)

    return all_items
