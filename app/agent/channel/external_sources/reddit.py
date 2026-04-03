"""Reddit content source via JSON API.

Uses Reddit's public JSON API (append .json to any URL).
No authentication needed for public subreddits.
Rate limit: ~30 requests/minute without auth.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.agent.channel.http import get_http_client
from app.agent.channel.sources import ContentItem
from app.core.logging import get_logger

logger = get_logger("channel.sources.reddit")

REDDIT_BASE = "https://www.reddit.com"
DEFAULT_USER_AGENT = "supervisor-telegram/1.0 (content monitoring bot)"


async def fetch_subreddit(
    subreddit: str,
    *,
    sort: str = "hot",
    max_items: int = 10,
    min_upvotes: int = 10,
    max_age_hours: int = 48,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Fetch posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/).
        sort: Sort method (hot, new, top, rising).
        max_items: Maximum items to return.
        min_upvotes: Minimum upvotes threshold.
        max_age_hours: Maximum post age in hours.
        http_timeout: HTTP timeout in seconds.

    Returns:
        List of ContentItem objects.
    """
    subreddit = subreddit.lstrip("r/").lstrip("/")
    url = f"{REDDIT_BASE}/r/{subreddit}/{sort}.json"

    try:
        client = get_http_client(timeout=http_timeout)
        resp = await client.get(
            url,
            timeout=httpx.Timeout(http_timeout),
            headers={"User-Agent": DEFAULT_USER_AGENT},
            params={"limit": str(min(max_items * 3, 100))},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("reddit_http_error", subreddit=subreddit, status=exc.response.status_code)
        return []
    except Exception:
        logger.exception("reddit_fetch_error", subreddit=subreddit)
        return []

    items: list[ContentItem] = []
    now_ts = __import__("time").time()

    for child in data.get("data", {}).get("children", []):
        if len(items) >= max_items:
            break

        post: dict[str, Any] = child.get("data", {})

        # Skip stickied, NSFW, or removed posts
        if post.get("stickied") or post.get("over_18") or post.get("removed_by_category"):
            continue

        # Filter by upvotes
        ups = post.get("ups", 0)
        if ups < min_upvotes:
            continue

        # Filter by age
        created = post.get("created_utc", 0)
        age_hours = (now_ts - created) / 3600
        if age_hours > max_age_hours:
            continue

        title = post.get("title", "")
        selftext = post.get("selftext", "")
        permalink = post.get("permalink", "")
        ext_id = post.get("id", hashlib.sha256(title.encode()).hexdigest()[:16])

        # Extract image
        image_url = None
        if post.get("post_hint") == "image":
            image_url = post.get("url")
        elif post.get("thumbnail", "").startswith("http"):
            image_url = post.get("thumbnail")

        body = selftext[:2000] if selftext else title
        if post.get("num_comments", 0) > 10:
            body += f"\n\n[{post['num_comments']} comments, {ups} upvotes]"

        items.append(
            ContentItem(
                source_url=f"reddit:r/{subreddit}",
                external_id=f"reddit:{ext_id}",
                title=title[:200],
                body=body,
                url=f"{REDDIT_BASE}{permalink}" if permalink else None,
                image_url=image_url,
            )
        )

    logger.info("reddit_fetched", subreddit=subreddit, items=len(items))
    return items


async def search_reddit(
    query: str,
    *,
    subreddit: str | None = None,
    max_items: int = 10,
    min_upvotes: int = 5,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Search Reddit for a query.

    Args:
        query: Search query.
        subreddit: Optional subreddit to limit search to.
        max_items: Maximum items to return.
        min_upvotes: Minimum upvotes threshold.
        http_timeout: HTTP timeout in seconds.

    Returns:
        List of ContentItem objects.
    """
    if subreddit:
        subreddit = subreddit.lstrip("r/").lstrip("/")
        url = f"{REDDIT_BASE}/r/{subreddit}/search.json"
    else:
        url = f"{REDDIT_BASE}/search.json"

    try:
        client = get_http_client(timeout=http_timeout)
        resp = await client.get(
            url,
            timeout=httpx.Timeout(http_timeout),
            headers={"User-Agent": DEFAULT_USER_AGENT},
            params={
                "q": query,
                "sort": "relevance",
                "t": "week",
                "limit": str(min(max_items * 2, 50)),
                "restrict_sr": "true" if subreddit else "false",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception("reddit_search_error", query=query)
        return []

    items: list[ContentItem] = []
    for child in data.get("data", {}).get("children", []):
        if len(items) >= max_items:
            break

        post = child.get("data", {})
        if post.get("over_18") or post.get("ups", 0) < min_upvotes:
            continue

        title = post.get("title", "")
        selftext = post.get("selftext", "")
        permalink = post.get("permalink", "")
        ext_id = post.get("id", hashlib.sha256(title.encode()).hexdigest()[:16])

        items.append(
            ContentItem(
                source_url=f"reddit:search:{query}",
                external_id=f"reddit:{ext_id}",
                title=title[:200],
                body=selftext[:2000] if selftext else title,
                url=f"{REDDIT_BASE}{permalink}" if permalink else None,
            )
        )

    logger.info("reddit_search_fetched", query=query, items=len(items))
    return items


async def fetch_reddit_sources(
    subreddits: list[str],
    search_queries: list[str] | None = None,
    *,
    max_items_per_source: int = 5,
    min_upvotes: int = 10,
    http_timeout: int = 30,
) -> list[ContentItem]:
    """Fetch content from multiple subreddits and search queries.

    Args:
        subreddits: List of subreddit names.
        search_queries: Optional keyword queries.
        max_items_per_source: Max items per source.
        min_upvotes: Upvote threshold.
        http_timeout: HTTP timeout.

    Returns:
        Combined list of ContentItem objects.
    """
    import asyncio

    tasks = []

    for sub in subreddits:
        tasks.append(
            fetch_subreddit(
                sub,
                max_items=max_items_per_source,
                min_upvotes=min_upvotes,
                http_timeout=http_timeout,
            )
        )

    for query in search_queries or []:
        tasks.append(
            search_reddit(
                query,
                max_items=max_items_per_source,
                min_upvotes=min_upvotes,
                http_timeout=http_timeout,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: list[ContentItem] = []
    for result in results:
        if isinstance(result, list):
            all_items.extend(result)
        elif isinstance(result, Exception):
            logger.warning("reddit_source_error", error=str(result))

    logger.info("reddit_sources_total", items=len(all_items))
    return all_items
