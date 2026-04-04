"""Channel agent configuration."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LANGUAGE_NAMES: dict[str, str] = {
    "ru": "Russian",
    "cs": "Czech",
    "en": "English",
}


def language_name(code: str) -> str:
    """Get full language name from code, defaulting to the code itself."""
    return LANGUAGE_NAMES.get(code, code)


DEFAULT_DISCOVERY_QUERY = (
    "free AI tools hacks, crypto airdrops farming, GitHub Copilot free access, NFT mint opportunities this week"
)
DEFAULT_SOURCE_DISCOVERY_QUERY = "RSS feeds about crypto airdrops, AI tools free access, NFT minting, DeFi farming"
DEFAULT_BRAVE_DISCOVERY_QUERY = (
    "free AI API credits hack, crypto airdrop farming guide, free Claude GPT Copilot access 2026"
)


class ChannelAgentSettings(BaseSettings):
    """Channel content agent configuration."""

    enabled: bool = Field(default=False, description="Enable channel content agent")
    fetch_interval_minutes: int = Field(default=60, description="How often to fetch new content")

    # Discovery settings
    discovery_enabled: bool = Field(default=True, description="Enable Perplexity Sonar content discovery")
    discovery_model: str = Field(default="perplexity/sonar", description="Model for content discovery")

    # Source discovery — agent finds RSS feeds automatically
    source_discovery_enabled: bool = Field(default=True, description="Agent auto-discovers RSS feeds")
    source_discovery_interval_hours: int = Field(default=24, description="How often to search for new feeds")

    # LLM settings
    screening_model: str = Field(
        default="google/gemini-3.1-flash-lite-preview", description="Cheap model for screening"
    )
    generation_model: str = Field(
        default="google/gemini-3.1-flash-lite-preview", description="Model for post generation"
    )
    reasoning_model: str | None = Field(
        default=None, description="Model for reasoning step (defaults to generation_model)"
    )
    reasoning_enabled: bool = Field(default=True, description="Enable chain-of-thought reasoning step")
    max_items_per_source: int = Field(default=5, description="Max items to fetch per source")
    http_timeout: int = Field(default=30, description="HTTP client timeout in seconds")
    screening_threshold: int = Field(default=5, description="Minimum relevance score (0-10) to pass screening")
    temperature: float = Field(default=0.3, description="LLM temperature for content generation")

    # Brave Search — complementary to Perplexity for URL-based factual search
    brave_discovery_enabled: bool = Field(
        default=False, description="Enable Brave Web Search as additional discovery source"
    )

    # Embedding settings for semantic dedup
    # NOTE: embedding dimension (768) is a schema constant in embeddings.py — changing it requires a DB migration
    embedding_model: str = Field(
        default="openai/text-embedding-3-small", description="Embedding model for semantic dedup"
    )
    semantic_dedup_threshold: float = Field(
        default=0.85, description="Cosine similarity threshold to consider items as duplicates (0-1)"
    )

    # Analytics
    analytics_enabled: bool = Field(default=False, description="Enable post analytics collection via Telethon")
    analytics_interval_minutes: int = Field(default=120, description="How often to collect analytics")
    analytics_lookback_days: int = Field(default=30, description="How far back to collect metrics")
    analytics_public_id: int = Field(default=0, description="Additional public channel ID to track")

    model_config = SettingsConfigDict(
        env_prefix="CHANNEL_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
