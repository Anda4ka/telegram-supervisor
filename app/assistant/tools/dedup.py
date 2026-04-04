"""Dedup, search & analytics tools."""

from pydantic_ai import Agent, RunContext

from app.assistant.agent import AssistantDeps, _validate_channel_id
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("assistant.tools.dedup")


def register_dedup_tools(agent: Agent[AssistantDeps, str]) -> None:
    """Register dedup, search & analytics tools on the agent."""

    @agent.tool
    async def check_duplicate(ctx: RunContext[AssistantDeps], channel_id: int, text: str) -> str:
        """Check if a text is semantically similar to recent posts. Returns similarity score."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        try:
            from app.agent.channel.semantic_dedup import find_nearest_posts

            results = await find_nearest_posts(
                text,
                channel_id=channel_id,
                api_key=settings.openrouter.api_key,
                session_maker=ctx.deps.session_maker,
            )

            if not results:
                return "No recent posts with embeddings to compare against."

            lines = ["Similarity to recent posts:\n"]
            for title, similarity in results:
                flag = " DUPLICATE" if similarity >= 0.85 else ""
                lines.append(f"- {similarity:.2%} — {title[:60]}{flag}")
            return "\n".join(lines)
        except Exception:
            logger.exception("check_duplicate_failed", channel_id=channel_id)
            return "Не удалось проверить дубликаты. Проверьте логи."

    @agent.tool
    async def list_recent_topics(ctx: RunContext[AssistantDeps], channel_id: int, days: int = 7) -> str:
        """List recent post topics for a channel to avoid repetition. Shows titles and dates."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        days = min(max(1, days), 30)

        try:
            from sqlalchemy import text as sql_text

            query = sql_text("""
                SELECT title, status, created_at::date as day
                FROM channel_posts
                WHERE channel_id = :channel_id
                  AND created_at > NOW() - make_interval(days => :days)
                ORDER BY created_at DESC
            """)

            async with ctx.deps.session_maker() as session:
                result = await session.execute(query, {"channel_id": channel_id, "days": days})
                rows = result.fetchall()

            if not rows:
                return f"No posts in last {days} days for {channel_id}."

            lines = [f"Posts in last {days} days for {channel_id} ({len(rows)} total):\n"]
            for title, status, day in rows:
                lines.append(f"- [{status}] {day}: {title[:70]}")
            return "\n".join(lines)
        except Exception:
            logger.exception("list_recent_topics_failed", channel_id=channel_id)
            return "Не удалось получить список тем. Проверьте логи."

    @agent.tool
    async def backfill_embeddings(ctx: RunContext[AssistantDeps], channel_id: int, limit: int = 50) -> str:
        """Generate embeddings for posts that don't have them yet. Useful after enabling semantic dedup."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        limit = min(max(1, limit), 200)

        try:
            from sqlalchemy import select

            from app.agent.channel.embeddings import EMBEDDING_MODEL, get_embeddings
            from app.infrastructure.db.models import ChannelPost

            async with ctx.deps.session_maker() as session:
                result = await session.execute(
                    select(ChannelPost)
                    .where(
                        ChannelPost.channel_id == channel_id,
                        ChannelPost.embedding.is_(None),
                    )
                    .order_by(ChannelPost.id.desc())
                    .limit(limit)
                )
                posts = list(result.scalars().all())

            if not posts:
                return f"All posts in {channel_id} already have embeddings."

            texts = [f"{p.title} {(p.post_text or '')[:100]}" for p in posts]
            embeddings = await get_embeddings(texts, api_key=settings.openrouter.api_key, model=EMBEDDING_MODEL)

            post_ids = [p.id for p in posts]
            async with ctx.deps.session_maker() as session:
                result = await session.execute(select(ChannelPost).where(ChannelPost.id.in_(post_ids)))
                db_posts = {p.id: p for p in result.scalars().all()}
                for post, emb in zip(posts, embeddings, strict=True):
                    db_posts[post.id].embedding = emb
                    db_posts[post.id].embedding_model = EMBEDDING_MODEL
                await session.commit()
            updated = len(db_posts)

            return f"Backfilled embeddings for {updated} posts in {channel_id}."
        except Exception:
            logger.exception("backfill_embeddings_failed", channel_id=channel_id)
            return "Не удалось создать эмбеддинги. Проверьте логи."

    @agent.tool
    async def get_analytics(ctx: RunContext[AssistantDeps], channel_id: int, days: int = 30) -> str:
        """Get channel engagement analytics: views, reactions, forwards, best posting hours."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        days = min(max(1, days), 90)

        try:
            from app.agent.channel.analytics import get_engagement_rate, get_hourly_performance

            metrics = await get_engagement_rate(ctx.deps.session_maker, channel_id, days=days)
            hourly = await get_hourly_performance(ctx.deps.session_maker, channel_id, days=days)

            if metrics["total_posts"] == 0:
                return f"Нет данных аналитики за {days} дней для {channel_id}. Коллектор уже запущен — данные появятся через 2 часа."

            lines = [
                f"📊 Аналитика за {days} дней (канал {channel_id}):\n",
                f"Постов: {metrics['total_posts']}",
                f"Avg views: {metrics['avg_views']}",
                f"Avg reactions: {metrics['avg_reactions']}",
                f"Avg forwards: {metrics['avg_forwards']}",
                f"Avg comments: {metrics['avg_comments']}",
                f"Engagement rate: {metrics['avg_engagement_rate']}%",
            ]

            if hourly:
                lines.append("\n⏰ Лучшие часы (по engagement):")
                top = sorted(hourly, key=lambda h: h["avg_engagement_rate"], reverse=True)[:5]
                for h in top:
                    lines.append(
                        f"  {h['hour']:02d}:00 — {h['avg_engagement_rate']:.1f}% eng, "
                        f"{h['avg_views']:.0f} views ({h['post_count']} постов)"
                    )

            return "\n".join(lines)
        except Exception:
            logger.exception("get_analytics_failed", channel_id=channel_id)
            return "Не удалось получить аналитику. Проверьте логи."

    @agent.tool
    async def best_time(ctx: RunContext[AssistantDeps], channel_id: int) -> str:
        """Recommend the best time to post based on analytics data. Returns time, confidence, and reasoning."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        try:
            from app.agent.channel.best_time import recommend_posting_time

            result = await recommend_posting_time(
                ctx.deps.session_maker,
                channel_id,
                api_key=settings.openrouter.api_key,
                model=settings.channel.screening_model,
            )

            lines = [
                f"⏰ Рекомендация: **{result['recommended_time']}**",
                f"Уверенность: {result['confidence']}",
                f"Источник: {result['source']}",
                f"\n{result['reasoning']}",
            ]
            if result.get("alternatives"):
                lines.append(f"\nАльтернативы: {', '.join(result['alternatives'])}")
            return "\n".join(lines)
        except Exception:
            logger.exception("best_time_failed", channel_id=channel_id)
            return "Не удалось рассчитать лучшее время. Проверьте логи."

    @agent.tool
    async def search_news(ctx: RunContext[AssistantDeps], query: str, count: int = 5, freshness: str = "pw") -> str:  # noqa: ARG001
        """Search the web for current news and information. Use this to find fresh content before generating posts. freshness: pd=past day, pw=past week, pm=past month."""
        brave_key = settings.brave.api_key
        if not brave_key:
            return "Brave API key not configured. Set BRAVE_API_KEY in .env."

        if freshness not in {"pd", "pw", "pm", "py"}:
            freshness = "pw"
        count = min(max(1, count), 10)

        try:
            from app.agent.channel.brave_search import brave_search_for_assistant

            return await brave_search_for_assistant(brave_key, query, count=count, freshness=freshness)
        except Exception:
            logger.exception("search_news_failed", query=query)
            return "Не удалось выполнить поиск. Проверьте логи."

    @agent.tool
    async def predict_engagement(ctx: RunContext[AssistantDeps], channel_id: int, post_text: str) -> str:
        """Predict engagement potential for a post based on historical data. Returns 🟢/🟡/🔴 score with reasoning."""
        error = await _validate_channel_id(ctx, channel_id)
        if error:
            return error

        try:
            from app.agent.channel.analytics import get_engagement_rate, get_hourly_performance

            metrics = await get_engagement_rate(ctx.deps.session_maker, channel_id)
            hourly = await get_hourly_performance(ctx.deps.session_maker, channel_id)

            if metrics["total_posts"] < 5:
                return "Недостаточно данных для прогноза (нужно минимум 5 постов с аналитикой)."

            # Build context for LLM prediction
            top_hours = sorted(hourly, key=lambda h: h["avg_views"], reverse=True)[:3]
            hours_str = ", ".join(f"{h['hour']}:00 ({h['avg_views']:.0f} views)" for h in top_hours)

            from app.agent.channel.llm_client import openrouter_chat_completion

            prompt = (
                f"Оцени потенциал вовлечённости поста для Telegram-канала.\n\n"
                f"СТАТИСТИКА КАНАЛА (30 дней):\n"
                f"- Постов: {metrics['total_posts']}\n"
                f"- Avg views: {metrics['avg_views']}\n"
                f"- Avg reactions: {metrics['avg_reactions']}\n"
                f"- Avg engagement rate: {metrics['avg_engagement_rate']}%\n"
                f"- Лучшие часы: {hours_str}\n\n"
                f"ТЕКСТ ПОСТА:\n{post_text[:1500]}\n\n"
                f"Оцени по шкале:\n"
                f"🟢 Высокий — выше среднего, viral potential\n"
                f"🟡 Средний — на уровне среднего\n"
                f"🔴 Низкий — скорее всего ниже среднего\n\n"
                f"Ответь ОДНОЙ строкой: emoji + прогноз views + короткое объяснение (1 предложение)."
            )

            response = await openrouter_chat_completion(
                api_key=settings.openrouter.api_key,
                model=settings.channel.screening_model,
                messages=[{"role": "user", "content": prompt}],
                operation="engagement_prediction",
                channel_id=str(channel_id),
                temperature=0.3,
            )
            return response or "Не удалось получить прогноз."

        except Exception:
            logger.exception("predict_engagement_failed", channel_id=channel_id)
            return "Ошибка при прогнозировании. Проверьте логи."
