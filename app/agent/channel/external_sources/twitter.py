"""Twitter/X content source via Nitter RSS.

Uses Nitter RSS feeds as the primary method — free, no API key needed,
and integrates with the existing feedparser infrastructure.

Falls back through multiple Nitter instances for reliability.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from functools import partial

import feedparser
import httpx

from app.agent.channel.http import get_http_client
from app.agent.channel.sources import ContentItem
from app.core.logging import get_logger

logger = get_logger("channel.sources.twitter")

# Default Nitter instances — configurable via env
DEFAULT_NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.net",
]

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_REPLY_RE = re.compile(r"^(?:Replying\s+to\s+@|R\s+to\s+@)", re.IGNORECASE)
_RT_PREFIX_RE = re.compile(r"^RT\s+@\w+:", re.IGNORECASE)


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text)


def _get_nitter_instances() -> list[str]:
    """Get configured Nitter instances from env or defaults."""
    import os

    env_instances = os.environ.get("TWITTER_NITTER_INSTANCES", "")
    if env_instances.strip():
        return [i.strip() for i in env_instances.split(",") if i.strip()]
    return DEFAULT_NITTER_INSTANCES


def _build_nitter_rss_url(instance: str, username: str) -> str:
    """Build Nitter RSS feed URL for a Twitter/X username."""
    username = username.lstrip("@")
    return f"{instance.rstrip('/')}/{username}/rss"


def _build_nitter_search_url(instance: str, query: str) -> str:
    """Build Nitter search RSS URL."""
    from urllib.parse import quote_plus

    return f"{instance.rstrip('/')}/search/rss?f=tweets&q={quote_plus(query)}"


async def fetch_twitter_user(
    username: str,
    *,
    max_items: int = 10,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Fetch recent tweets from a Twitter/X user via Nitter RSS.

    Filters out replies and pure retweets automatically.

    Args:
        username: Twitter handle (with or without @).
        max_items: Maximum items to return.
        http_timeout: HTTP timeout in seconds.

    Returns:
        List of ContentItem objects.
    """
    username = username.lstrip("@")
    instances = _get_nitter_instances()

    for instance in instances:
        url = _build_nitter_rss_url(instance, username)
        items = await _fetch_nitter_rss(url, f"twitter:@{username}", max_items, http_timeout)
        if items:
            logger.info(
                "twitter_user_fetched",
                username=username,
                instance=instance,
                items=len(items),
            )
            return items
        logger.warning("twitter_instance_failed", instance=instance, username=username)

    logger.warning("twitter_all_instances_failed", username=username)
    return []


async def fetch_twitter_search(
    query: str,
    *,
    max_items: int = 10,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Search Twitter/X for a query via Nitter RSS.

    Args:
        query: Search query string.
        max_items: Maximum items to return.
        http_timeout: HTTP timeout in seconds.

    Returns:
        List of ContentItem objects.
    """
    instances = _get_nitter_instances()

    for instance in instances:
        url = _build_nitter_search_url(instance, query)
        items = await _fetch_nitter_rss(url, f"twitter:search:{query}", max_items, http_timeout)
        if items:
            logger.info("twitter_search_fetched", query=query, items=len(items))
            return items

    logger.warning("twitter_search_all_instances_failed", query=query)
    return []


async def fetch_twitter_sources(
    accounts: list[str],
    search_queries: list[str] | None = None,
    *,
    max_items_per_source: int = 5,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Fetch content from multiple Twitter accounts and search queries.

    Args:
        accounts: List of Twitter usernames to monitor.
        search_queries: Optional list of keyword queries.
        max_items_per_source: Max items per account/query.
        http_timeout: HTTP timeout.

    Returns:
        Combined list of ContentItem objects.
    """
    tasks = []

    for account in accounts:
        tasks.append(
            fetch_twitter_user(
                account,
                max_items=max_items_per_source,
                http_timeout=http_timeout,
            )
        )

    for query in search_queries or []:
        tasks.append(
            fetch_twitter_search(
                query,
                max_items=max_items_per_source,
                http_timeout=http_timeout,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: list[ContentItem] = []
    for result in results:
        if isinstance(result, list):
            all_items.extend(result)
        elif isinstance(result, Exception):
            logger.warning("twitter_source_error", error=str(result))

    logger.info("twitter_sources_total", items=len(all_items))
    return all_items


async def _fetch_nitter_rss(
    url: str,
    source_label: str,
    max_items: int,
    http_timeout: int,
) -> list[ContentItem]:
    """Fetch and parse a Nitter RSS feed."""
    try:
        client = get_http_client(timeout=http_timeout)
        resp = await client.get(
            url,
            timeout=httpx.Timeout(http_timeout),
            headers={"User-Agent": "Mozilla/5.0 (compatible; supervisor-telegram/1.0)"},
        )
        resp.raise_for_status()

        loop = asyncio.get_running_loop()
        feed = await loop.run_in_executor(None, partial(feedparser.parse, resp.text))

        items: list[ContentItem] = []
        for entry in feed.entries:
            if len(items) >= max_items:
                break

            raw_title = _strip_html(entry.get("title", ""))
            raw_body = _strip_html(entry.get("summary", entry.get("description", "")))

            # Skip replies and pure retweets
            if _REPLY_RE.match(raw_title) or _REPLY_RE.match(raw_body):
                continue
            if _RT_PREFIX_RE.match(raw_title) and len(raw_title) < 20:
                continue

            ext_id = entry.get("id") or entry.get("link") or hashlib.sha256(raw_title.encode()).hexdigest()

            title = raw_title
            body = raw_body

            # Extract image if present
            image_url = None
            if "media_content" in entry:
                for media in entry.media_content:
                    if media.get("medium") == "image" or media.get("type", "").startswith("image"):
                        image_url = media.get("url")
                        break

            items.append(
                ContentItem(
                    source_url=source_label,
                    external_id=f"twitter:{ext_id}",
                    title=title[:200],
                    body=body,
                    url=entry.get("link"),
                    image_url=image_url,
                )
            )

        return items

    except httpx.HTTPStatusError as exc:
        logger.warning("nitter_http_error", url=url, status=exc.response.status_code)
        return []
    except Exception:
        logger.warning("nitter_fetch_error", url=url, exc_info=True)
        return []
