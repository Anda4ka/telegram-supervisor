"""Telegram channel & forum monitoring source via Telethon.

Monitors specified Telegram channels and forum topics for new posts,
filters short messages, and converts them to ContentItem objects for the pipeline.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.agent.channel.sources import ContentItem
from app.core.logging import get_logger
from app.core.time import utc_now

if TYPE_CHECKING:
    from telethon import TelegramClient

logger = get_logger("channel.sources.telegram_channels")

# Minimum text length to consider a post worth analyzing
MIN_TEXT_LENGTH = 100


async def fetch_channel_posts(
    client: TelegramClient,
    channel_id: int | str,
    *,
    max_items: int = 10,
    min_views: int = 0,
    min_text_length: int = MIN_TEXT_LENGTH,
    hours_lookback: int = 24,
) -> list[ContentItem]:
    """Fetch recent posts from a Telegram channel.

    Args:
        client: Authenticated Telethon client.
        channel_id: Channel ID (int) or username (str like '@channel').
        max_items: Maximum items to return.
        min_views: Minimum views threshold.
        min_text_length: Skip posts shorter than this (default 100 chars).
        hours_lookback: How far back to look (hours).

    Returns:
        List of ContentItem objects.
    """
    from datetime import timedelta

    now = utc_now()
    cutoff = now - timedelta(hours=hours_lookback)

    try:
        entity = await client.get_entity(channel_id)
    except Exception:
        logger.exception("tg_channel_entity_error", channel_id=channel_id)
        return []

    channel_name = getattr(entity, "title", str(channel_id))
    source_label = f"telegram:{channel_name}"

    items: list[ContentItem] = []
    skipped_short = 0

    try:
        async for msg in client.iter_messages(entity, limit=max_items * 5):
            if len(items) >= max_items:
                break

            # Skip old messages
            msg_date = msg.date.replace(tzinfo=None) if msg.date else now
            if msg_date < cutoff:
                break

            # Skip non-content messages (service messages, etc.)
            if not msg.text and not msg.media:
                continue

            text = msg.text or ""

            # Filter short posts — not worth AI analysis
            if len(text.strip()) < min_text_length:
                skipped_short += 1
                continue

            # Filter by views
            views = msg.views or 0
            if min_views > 0 and views < min_views:
                continue

            title = text[:100].split("\n")[0] if text else f"Media post from {channel_name}"

            # Extract image URL from media
            image_url = None
            if msg.photo:
                image_url = f"tg://photo/{channel_id}/{msg.id}"

            ext_id = hashlib.sha256(f"{channel_id}:{msg.id}".encode()).hexdigest()[:16]

            items.append(
                ContentItem(
                    source_url=source_label,
                    external_id=f"tg:{ext_id}",
                    title=title[:200],
                    body=text,
                    url=f"https://t.me/c/{str(channel_id).removeprefix('-100')}/{msg.id}",
                    image_url=image_url,
                )
            )

        logger.info(
            "tg_channel_fetched",
            channel=channel_name,
            items=len(items),
            skipped_short=skipped_short,
        )

    except Exception:
        logger.exception("tg_channel_fetch_error", channel_id=channel_id)

    return items


async def fetch_forum_topics(
    client: TelegramClient,
    forum_id: int | str,
    *,
    topic_ids: list[int] | None = None,
    max_items_per_topic: int = 5,
    max_topics: int = 10,
    min_text_length: int = MIN_TEXT_LENGTH,
    hours_lookback: int = 24,
) -> list[ContentItem]:
    """Fetch recent posts from a Telegram forum (supergroup with topics).

    Args:
        client: Authenticated Telethon client.
        forum_id: Forum supergroup ID (int) or username (str).
        topic_ids: Specific topic IDs to monitor (None = all recent topics).
        max_items_per_topic: Max items per topic.
        max_topics: Max number of topics to scan if topic_ids is None.
        min_text_length: Skip posts shorter than this.
        hours_lookback: How far back to look (hours).

    Returns:
        List of ContentItem objects.
    """
    from datetime import timedelta

    from telethon.tl.functions.messages import GetForumTopicsRequest

    now = utc_now()
    cutoff = now - timedelta(hours=hours_lookback)

    try:
        entity = await client.get_entity(forum_id)
    except Exception:
        logger.exception("tg_forum_entity_error", forum_id=forum_id)
        return []

    forum_name = getattr(entity, "title", str(forum_id))
    all_items: list[ContentItem] = []

    # Get topics
    topics_to_scan: list[int] = []

    if topic_ids:
        topics_to_scan = topic_ids
    else:
        # Fetch recent active topics
        try:
            result = await client(
                GetForumTopicsRequest(
                    channel=entity,
                    offset_date=0,
                    offset_id=0,
                    offset_topic=0,
                    limit=max_topics,
                )
            )
            for topic in result.topics:
                topics_to_scan.append(topic.id)
            logger.info("tg_forum_topics_found", forum=forum_name, topics=len(topics_to_scan))
        except Exception:
            logger.exception("tg_forum_topics_error", forum_id=forum_id)
            # Fallback: scan general messages
            topics_to_scan = []

    if topics_to_scan:
        # Fetch messages from each topic (reply_to thread)
        for topic_id in topics_to_scan:
            skipped_short = 0
            items: list[ContentItem] = []

            try:
                async for msg in client.iter_messages(
                    entity,
                    reply_to=topic_id,
                    limit=max_items_per_topic * 5,
                ):
                    if len(items) >= max_items_per_topic:
                        break

                    msg_date = msg.date.replace(tzinfo=None) if msg.date else now
                    if msg_date < cutoff:
                        break

                    if not msg.text:
                        continue

                    text = msg.text.strip()
                    if len(text) < min_text_length:
                        skipped_short += 1
                        continue

                    title = text[:100].split("\n")[0]
                    ext_id = hashlib.sha256(f"{forum_id}:{topic_id}:{msg.id}".encode()).hexdigest()[:16]

                    items.append(
                        ContentItem(
                            source_url=f"telegram:forum:{forum_name}:topic:{topic_id}",
                            external_id=f"tg:forum:{ext_id}",
                            title=title[:200],
                            body=text,
                            url=f"https://t.me/c/{str(forum_id).removeprefix('-100')}/{msg.id}",
                        )
                    )

                if items:
                    logger.info(
                        "tg_forum_topic_fetched",
                        forum=forum_name,
                        topic_id=topic_id,
                        items=len(items),
                        skipped_short=skipped_short,
                    )
                all_items.extend(items)

            except Exception:
                logger.warning("tg_forum_topic_error", forum_id=forum_id, topic_id=topic_id, exc_info=True)
    else:
        # No topics available — scan general messages
        general_items = await fetch_channel_posts(
            client,
            forum_id,
            max_items=max_items_per_topic * 3,
            min_text_length=min_text_length,
            hours_lookback=hours_lookback,
        )
        all_items.extend(general_items)

    logger.info(
        "tg_forum_fetched",
        forum=forum_name,
        total_items=len(all_items),
    )

    return all_items


async def fetch_multiple_sources(
    client: TelegramClient,
    channels: list[int | str] | None = None,
    forums: list[int | str] | None = None,
    *,
    max_items_per_source: int = 5,
    min_views: int = 0,
    min_text_length: int = MIN_TEXT_LENGTH,
    hours_lookback: int = 24,
) -> list[ContentItem]:
    """Fetch content from multiple Telegram channels and forums.

    Args:
        client: Authenticated Telethon client.
        channels: List of channel IDs or usernames.
        forums: List of forum supergroup IDs or usernames.
        max_items_per_source: Max items per source.
        min_views: Minimum views filter (channels only).
        min_text_length: Minimum text length filter.
        hours_lookback: Lookback window in hours.

    Returns:
        Combined list of ContentItem objects.
    """
    all_items: list[ContentItem] = []

    for cid in channels or []:
        try:
            items = await fetch_channel_posts(
                client,
                cid,
                max_items=max_items_per_source,
                min_views=min_views,
                min_text_length=min_text_length,
                hours_lookback=hours_lookback,
            )
            all_items.extend(items)
        except Exception:
            logger.exception("tg_multi_channel_error", channel_id=cid)

    for fid in forums or []:
        try:
            items = await fetch_forum_topics(
                client,
                fid,
                max_items_per_topic=max_items_per_source,
                min_text_length=min_text_length,
                hours_lookback=hours_lookback,
            )
            all_items.extend(items)
        except Exception:
            logger.exception("tg_multi_forum_error", forum_id=fid)

    logger.info(
        "tg_sources_total",
        channels=len(channels or []),
        forums=len(forums or []),
        items=len(all_items),
    )

    return all_items


async def fetch_own_channel_posts(
    client: TelegramClient,
    channel_id: int | str,
    *,
    limit: int = 30,
    min_text_length: int = 20,
) -> list[str]:
    """Fetch recent post texts from the OWN channel for dedup/style reference.

    Returns a list of post text strings (not ContentItem).
    Used to prevent the bot from re-posting content already on the channel.
    """
    try:
        entity = await client.get_entity(channel_id)
    except Exception:
        logger.exception("own_channel_entity_error", channel_id=channel_id)
        return []

    texts: list[str] = []
    try:
        async for msg in client.iter_messages(entity, limit=limit):
            if not msg.text or len(msg.text.strip()) < min_text_length:
                continue
            texts.append(msg.text.strip())
    except Exception:
        logger.exception("own_channel_fetch_error", channel_id=channel_id)

    logger.info("own_channel_posts_fetched", channel_id=channel_id, count=len(texts))
    return texts
