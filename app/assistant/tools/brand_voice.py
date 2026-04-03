"""Assistant tools for Brand Voice, Reports, and Translation."""

from __future__ import annotations

from pydantic_ai import Agent, RunContext  # noqa: TC002 — needed at runtime for @agent.tool

from app.assistant.agent import AssistantDeps  # noqa: TC001 — needed at runtime for @agent.tool
from app.core.logging import get_logger

logger = get_logger("assistant.tools.brand_voice")


def register_brand_voice_tools(agent: Agent[AssistantDeps, str]) -> None:
    """Register Brand Voice, Reports, and Translation tools."""

    @agent.tool
    async def analyze_channel_voice(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
        preset_name: str = "default",
        post_count: int = 50,
    ) -> str:
        """Analyze a channel's posts and create a Brand Voice profile.

        This reads the last N posts and extracts: tone, addressing style,
        emoji patterns, sentence structure, signature phrases, and forbidden patterns.
        """
        from app.agent.channel.brand_voice import analyze_and_save
        from app.core.config import settings

        try:
            profile = await analyze_and_save(
                telethon_client=ctx.deps.telethon,
                session_maker=ctx.deps.session_maker,
                channel_id=channel_id,
                api_key=settings.openrouter.api_key,
                model=settings.channel.generation_model,
                preset_name=preset_name,
                limit=post_count,
            )

            if not profile.tone:
                return f"Could not analyze channel {channel_id} — no posts found or analysis failed."

            return (
                f"✅ Voice profile '{preset_name}' saved for channel {channel_id}.\n\n"
                f"**Tone:** {profile.tone}\n"
                f"**Addressing:** {profile.addressing}\n"
                f"**Avg length:** {profile.avg_length} chars\n"
                f"**Emoji:** {profile.emoji_density}\n"
                f"**Style:** {profile.sentence_style}\n"
                f"**Signature phrases:** {', '.join(profile.signature_phrases[:5])}\n"
                f"**Forbidden:** {', '.join(profile.forbidden_patterns[:5])}"
            )
        except Exception:
            logger.exception("analyze_voice_failed")
            return "Failed to analyze channel voice. Check logs."

    @agent.tool
    async def get_voice_profile(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
        preset_name: str = "default",
    ) -> str:
        """Get the current Brand Voice profile for a channel."""
        from app.agent.channel.brand_voice import load_voice_profile

        profile = await load_voice_profile(ctx.deps.session_maker, channel_id, preset_name)
        if not profile:
            return f"No voice profile '{preset_name}' found for channel {channel_id}. Use analyze_channel_voice first."

        return profile.to_prompt_block()

    @agent.tool
    async def list_voice_presets(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
    ) -> str:
        """List all voice presets for a channel."""
        from app.agent.channel.brand_voice import list_voice_presets as _list

        presets = await _list(ctx.deps.session_maker, channel_id)
        if not presets:
            return f"No voice presets for channel {channel_id}."
        return f"Voice presets for {channel_id}: {', '.join(presets)}"

    @agent.tool
    async def get_channel_report(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
        days: int = 7,
    ) -> str:
        """Generate an analytics report for a channel.

        Includes: post stats, approval rate, engagement metrics,
        LLM costs breakdown, and actionable recommendations.
        """
        from app.agent.channel.reports import generate_channel_report

        try:
            return await generate_channel_report(ctx.deps.session_maker, channel_id, days=days)
        except Exception:
            logger.exception("report_generation_failed")
            return "Failed to generate report. Check logs."

    @agent.tool
    async def send_weekly_report(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
        admin_chat_id: int | None = None,
    ) -> str:
        """Send a weekly analytics report to an admin chat.

        If admin_chat_id is not specified, sends to the channel's review_chat_id
        or falls back to the first super admin.
        """
        from app.agent.channel.reports import send_report_to_admin
        from app.core.config import settings

        if admin_chat_id is None:
            # Try channel's review_chat_id, then first super admin
            from sqlalchemy import select

            from app.infrastructure.db.models import Channel

            async with ctx.deps.session_maker() as session:
                result = await session.execute(select(Channel.review_chat_id).where(Channel.telegram_id == channel_id))
                row = result.one_or_none()
            admin_chat_id = row[0] if row and row[0] else None
            if not admin_chat_id and settings.admin.super_admins:
                admin_chat_id = settings.admin.super_admins[0]
            if not admin_chat_id:
                return "No admin_chat_id specified and no super admins configured."

        success = await send_report_to_admin(
            ctx.deps.main_bot, admin_chat_id, ctx.deps.session_maker, channel_id, days=7
        )
        return "✅ Report sent." if success else "❌ Failed to send report."

    @agent.tool
    async def get_best_posting_time(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
    ) -> str:
        """Get the recommended best time to post based on engagement analytics."""
        from app.agent.channel.best_time import recommend_posting_time
        from app.core.config import settings

        try:
            rec = await recommend_posting_time(
                ctx.deps.session_maker,
                channel_id,
                api_key=settings.openrouter.api_key,
                model=settings.channel.screening_model,
            )
            lines = [
                f"🕐 **Recommended:** {rec['recommended_time']} UTC ({rec['confidence']} confidence)",
                f"📊 Source: {rec['source']}",
            ]
            if rec.get("alternatives"):
                lines.append(f"🔄 Alternatives: {', '.join(rec['alternatives'])}")
            lines.append(f"\n{rec['reasoning']}")
            return "\n".join(lines)
        except Exception:
            logger.exception("best_time_failed")
            return "Could not determine best posting time. Need more analytics data."

    @agent.tool
    async def compare_periods(
        ctx: RunContext[AssistantDeps],
        channel_id: int,
        days: int = 7,
    ) -> str:
        """Compare current period with previous period — shows trends in posts, views, costs."""
        from app.agent.channel.reports import generate_channel_report

        try:
            # Current period report already includes week-over-week comparison
            return await generate_channel_report(ctx.deps.session_maker, channel_id, days=days)
        except Exception:
            logger.exception("compare_failed")
            return "Failed to generate comparison. Check logs."

    @agent.tool
    async def translate_post(
        ctx: RunContext[AssistantDeps],
        post_id: int,
        target_language: str,
    ) -> str:
        """Translate a channel post to another language while preserving Brand Voice.

        Supported languages: ru, en, es, vi, uk, zh, pt, de, fr, tr, ar, ko, ja.
        Uses the channel's voice profile for tone preservation.
        """
        from sqlalchemy import select

        from app.agent.channel.brand_voice import load_voice_profile
        from app.agent.channel.translate import SUPPORTED_LANGUAGES
        from app.agent.channel.translate import translate_post as _translate
        from app.core.config import settings
        from app.infrastructure.db.models import ChannelPost

        if target_language not in SUPPORTED_LANGUAGES:
            return f"Unsupported language '{target_language}'. Supported: {', '.join(SUPPORTED_LANGUAGES.keys())}"

        async with ctx.deps.session_maker() as session:
            result = await session.execute(select(ChannelPost).where(ChannelPost.id == post_id))
            post = result.scalar_one_or_none()

        if not post:
            return f"Post {post_id} not found."

        voice = await load_voice_profile(ctx.deps.session_maker, post.channel_id)
        translated = await _translate(
            post.post_text,
            target_language,
            settings.openrouter.api_key,
            settings.channel.generation_model,
            voice_profile=voice,
        )

        if translated:
            lang_name = SUPPORTED_LANGUAGES[target_language]
            return f"**Translated to {lang_name}:**\n\n{translated}"
        return "Translation failed. Check logs."

    @agent.tool
    async def translate_text(
        ctx: RunContext[AssistantDeps],
        text: str,
        target_language: str,
        channel_id: int | None = None,
    ) -> str:
        """Translate arbitrary text to another language, optionally using a channel's Brand Voice."""
        from app.agent.channel.brand_voice import load_voice_profile
        from app.agent.channel.translate import SUPPORTED_LANGUAGES
        from app.agent.channel.translate import translate_post as _translate
        from app.core.config import settings

        if target_language not in SUPPORTED_LANGUAGES:
            return f"Unsupported language. Supported: {', '.join(SUPPORTED_LANGUAGES.keys())}"

        voice = None
        if channel_id:
            voice = await load_voice_profile(ctx.deps.session_maker, channel_id)

        translated = await _translate(
            text,
            target_language,
            settings.openrouter.api_key,
            settings.channel.generation_model,
            voice_profile=voice,
        )

        if translated:
            lang_name = SUPPORTED_LANGUAGES[target_language]
            return f"**{lang_name}:**\n\n{translated}"
        return "Translation failed."
