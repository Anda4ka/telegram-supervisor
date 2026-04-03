"""Brand Voice Engine — auto-analyzes channel style and generates voice profiles.

Analyzes recent posts from a Telegram channel to extract a structured style profile:
tone, formatting patterns, emoji density, sentence style, forbidden phrases, etc.
The profile is stored in DB and injected into generation prompts for consistent voice.

Supports multiple voice presets per channel (e.g., "degen", "serious", "meme").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.channel.llm_client import openrouter_chat_completion
from app.core.logging import get_logger
from app.core.time import utc_now

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.brand_voice")

# ── Voice Profile dataclass ──


@dataclass
class VoiceProfile:
    """Extracted style profile for a channel."""

    tone: str = ""  # e.g., "casual degen", "professional", "meme"
    addressing: str = ""  # e.g., "ты/пацаны", "вы", "friends"
    avg_length: int = 0  # average post length in chars
    emoji_density: str = ""  # "none", "low", "medium", "high"
    sentence_style: str = ""  # e.g., "short punchy", "long detailed"
    formatting_rules: list[str] = field(default_factory=list)  # bold, links style, etc.
    signature_phrases: list[str] = field(default_factory=list)  # recurring endings/phrases
    forbidden_patterns: list[str] = field(default_factory=list)  # things to avoid
    content_types: list[str] = field(default_factory=list)  # what topics dominate
    example_openings: list[str] = field(default_factory=list)  # how posts typically start
    language: str = "ru"
    preset_name: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tone": self.tone,
            "addressing": self.addressing,
            "avg_length": self.avg_length,
            "emoji_density": self.emoji_density,
            "sentence_style": self.sentence_style,
            "formatting_rules": self.formatting_rules,
            "signature_phrases": self.signature_phrases,
            "forbidden_patterns": self.forbidden_patterns,
            "content_types": self.content_types,
            "example_openings": self.example_openings,
            "language": self.language,
            "preset_name": self.preset_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VoiceProfile:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_prompt_block(self) -> str:
        """Format as a prompt block for injection into generation prompts."""
        lines = [f"BRAND VOICE PROFILE ({self.preset_name}):"]
        if self.tone:
            lines.append(f"Tone: {self.tone}")
        if self.addressing:
            lines.append(f"Addressing: {self.addressing}")
        if self.avg_length:
            lines.append(f"Target length: {self.avg_length} chars")
        if self.emoji_density:
            lines.append(f"Emoji usage: {self.emoji_density}")
        if self.sentence_style:
            lines.append(f"Sentence style: {self.sentence_style}")
        if self.formatting_rules:
            lines.append(f"Formatting: {'; '.join(self.formatting_rules)}")
        if self.signature_phrases:
            lines.append(f"Signature phrases to use: {', '.join(self.signature_phrases[:5])}")
        if self.forbidden_patterns:
            lines.append(f"NEVER use: {', '.join(self.forbidden_patterns[:5])}")
        if self.example_openings:
            lines.append(f"Example openings: {' | '.join(self.example_openings[:3])}")
        return "\n".join(lines)


# ── Analysis functions ──

_ANALYZE_PROMPT = """\
You are a writing style analyst. Analyze these Telegram channel posts and extract a precise style profile.

Return a JSON object with exactly these fields:
{
  "tone": "overall tone description (1-2 sentences)",
  "addressing": "how the author addresses readers (ты/вы/пацаны/friends/etc)",
  "avg_length": <average post length in characters as integer>,
  "emoji_density": "none|low|medium|high",
  "sentence_style": "description of typical sentence structure",
  "formatting_rules": ["rule1", "rule2", ...],
  "signature_phrases": ["phrase1", "phrase2", ...],
  "forbidden_patterns": ["pattern1", "pattern2", ...],
  "content_types": ["type1", "type2", ...],
  "example_openings": ["opening1", "opening2", ...]
}

Be specific and concrete. Use the actual language of the posts (don't translate).
Focus on what makes this channel's voice UNIQUE."""


def _compute_basic_stats(posts: list[str]) -> dict[str, Any]:
    """Compute basic statistics from post texts."""
    if not posts:
        return {}

    lengths = [len(p) for p in posts]
    emoji_pattern = re.compile(
        "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff"
        "\U0001f1e0-\U0001f1ff\U00002702-\U000027b0\U0001f900-\U0001f9ff"
        "\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002600-\U000026ff]+",
        flags=re.UNICODE,
    )
    emoji_counts = [len(emoji_pattern.findall(p)) for p in posts]
    avg_emoji = sum(emoji_counts) / len(posts)

    return {
        "post_count": len(posts),
        "avg_length": int(sum(lengths) / len(lengths)),
        "min_length": min(lengths),
        "max_length": max(lengths),
        "avg_emoji_per_post": round(avg_emoji, 1),
    }


async def analyze_posts(
    posts: list[str],
    api_key: str,
    model: str,
    *,
    language: str = "ru",
    preset_name: str = "default",
    temperature: float = 0.3,
    http_timeout: int = 60,
) -> VoiceProfile:
    """Analyze a list of post texts and extract a VoiceProfile.

    Args:
        posts: List of post text strings to analyze.
        api_key: OpenRouter API key.
        model: Model to use for analysis.
        language: Primary language of the channel.
        preset_name: Name for this voice preset.

    Returns:
        VoiceProfile with extracted style characteristics.
    """
    if not posts:
        return VoiceProfile(language=language, preset_name=preset_name)

    stats = _compute_basic_stats(posts)

    # Prepare posts sample — take up to 30, cap each at 1500 chars
    sample = [p[:1500] for p in posts[:30]]
    posts_text = "\n\n---POST---\n\n".join(sample)

    user_msg = f"Channel statistics: {json.dumps(stats)}\n\nPosts ({len(sample)} samples):\n\n{posts_text}"

    try:
        response = await openrouter_chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": _ANALYZE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            operation="brand_voice_analysis",
            temperature=temperature,
            timeout=http_timeout,
        )

        if not response:
            logger.warning("brand_voice_empty_response")
            return VoiceProfile(language=language, preset_name=preset_name, avg_length=stats.get("avg_length", 0))

        data = json.loads(response) if isinstance(response, str) else response
        profile = VoiceProfile.from_dict(data)
        profile.language = language
        profile.preset_name = preset_name

        # Override avg_length with actual computed value (more accurate than LLM guess)
        if stats.get("avg_length"):
            profile.avg_length = stats["avg_length"]

        logger.info(
            "brand_voice_analyzed",
            preset=preset_name,
            tone=profile.tone[:60],
            posts=len(sample),
            avg_length=profile.avg_length,
        )
        return profile

    except Exception:
        logger.exception("brand_voice_analysis_failed")
        return VoiceProfile(language=language, preset_name=preset_name, avg_length=stats.get("avg_length", 0))


# ── Telethon-based post fetcher ──


async def fetch_channel_posts_for_analysis(
    telethon_client: Any,
    channel_id: int,
    limit: int = 50,
) -> list[str]:
    """Fetch recent text posts from a channel via Telethon for voice analysis."""
    if not telethon_client or not telethon_client.is_available:
        return []

    try:
        messages = await telethon_client.get_chat_history(channel_id, limit=limit * 2)
        texts = []
        for msg in messages:
            if msg.text and len(msg.text.strip()) >= 50:
                texts.append(msg.text.strip())
            if len(texts) >= limit:
                break
        logger.info("fetched_posts_for_voice", channel_id=channel_id, count=len(texts))
        return texts
    except Exception:
        logger.exception("fetch_posts_for_voice_failed", channel_id=channel_id)
        return []


# ── DB persistence ──


async def save_voice_profile(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    profile: VoiceProfile,
) -> None:
    """Save a voice profile to the channel_voice_profiles table."""
    from sqlalchemy import select

    from app.infrastructure.db.models import ChannelVoiceProfile

    async with session_maker() as session:
        result = await session.execute(
            select(ChannelVoiceProfile).where(
                ChannelVoiceProfile.channel_id == channel_id,
                ChannelVoiceProfile.preset_name == profile.preset_name,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.profile_data = profile.to_dict()
            existing.analyzed_at = utc_now()
        else:
            record = ChannelVoiceProfile(
                channel_id=channel_id,
                preset_name=profile.preset_name,
                profile_data=profile.to_dict(),
            )
            session.add(record)

        await session.commit()
        logger.info("voice_profile_saved", channel_id=channel_id, preset=profile.preset_name)


async def load_voice_profile(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    preset_name: str = "default",
) -> VoiceProfile | None:
    """Load a voice profile from DB. Returns None if not found."""
    from sqlalchemy import select

    from app.infrastructure.db.models import ChannelVoiceProfile

    async with session_maker() as session:
        result = await session.execute(
            select(ChannelVoiceProfile).where(
                ChannelVoiceProfile.channel_id == channel_id,
                ChannelVoiceProfile.preset_name == preset_name,
            )
        )
        record = result.scalar_one_or_none()
        if record and record.profile_data:
            return VoiceProfile.from_dict(record.profile_data)
    return None


async def list_voice_presets(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
) -> list[str]:
    """List all voice preset names for a channel."""
    from sqlalchemy import select

    from app.infrastructure.db.models import ChannelVoiceProfile

    async with session_maker() as session:
        result = await session.execute(
            select(ChannelVoiceProfile.preset_name).where(ChannelVoiceProfile.channel_id == channel_id)
        )
        return [row[0] for row in result.all()]


# ── Full pipeline: analyze + save ──


async def analyze_and_save(
    telethon_client: Any,
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    api_key: str,
    model: str,
    *,
    preset_name: str = "default",
    language: str = "ru",
    limit: int = 50,
) -> VoiceProfile:
    """Full pipeline: fetch posts → analyze → save profile → return it."""
    posts = await fetch_channel_posts_for_analysis(telethon_client, channel_id, limit=limit)
    if not posts:
        logger.warning("no_posts_for_voice_analysis", channel_id=channel_id)
        return VoiceProfile(language=language, preset_name=preset_name)

    profile = await analyze_posts(posts, api_key, model, language=language, preset_name=preset_name)
    await save_voice_profile(session_maker, channel_id, profile)
    return profile
