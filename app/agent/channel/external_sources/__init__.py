"""External content source fetchers — Twitter/X, Telegram channels, Reddit.

Each module provides functions that return ``ContentItem`` objects
compatible with the existing pipeline.
"""

from app.agent.channel.external_sources.reddit import fetch_reddit_sources, fetch_subreddit
from app.agent.channel.external_sources.telegram_channels import fetch_channel_posts, fetch_multiple_sources
from app.agent.channel.external_sources.twitter import fetch_twitter_sources, fetch_twitter_user

__all__ = [
    "fetch_twitter_sources",
    "fetch_twitter_user",
    "fetch_channel_posts",
    "fetch_multiple_sources",
    "fetch_reddit_sources",
    "fetch_subreddit",
]
