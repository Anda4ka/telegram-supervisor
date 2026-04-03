"""Best-time posting recommender.

Analyzes historical analytics data to recommend optimal posting times.
Falls back to general best practices when insufficient data is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.core.time import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("channel.best_time")

# General best practices for Telegram (UTC)
# Weekdays: morning and evening peaks
# Weekends: slightly later morning
DEFAULT_SLOTS = {
    0: ["09:00", "18:00"],  # Monday
    1: ["09:00", "18:00"],  # Tuesday
    2: ["09:00", "18:00"],  # Wednesday
    3: ["09:00", "18:00"],  # Thursday
    4: ["09:00", "17:00"],  # Friday
    5: ["10:00", "16:00"],  # Saturday
    6: ["10:00", "17:00"],  # Sunday
}

# Minimum posts per hour-slot to trust the data
MIN_POSTS_FOR_CONFIDENCE = 3


async def recommend_posting_time(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    *,
    target_date: datetime | None = None,
    api_key: str = "",
    model: str = "",
) -> dict:
    """Recommend the best time to post based on historical data.

    Args:
        session_maker: DB session maker.
        channel_id: Telegram channel ID.
        target_date: Date to recommend for (defaults to today).
        api_key: OpenRouter API key (for LLM reasoning, optional).
        model: LLM model for reasoning (optional).

    Returns:
        Dict with recommended_time (HH:MM), confidence, reasoning, source.
    """
    if target_date is None:
        target_date = utc_now()

    day_of_week = target_date.weekday()

    # Try data-driven recommendation
    hourly_data = await _get_hourly_stats(session_maker, channel_id)
    data_slots = _rank_slots(hourly_data, day_of_week)

    if data_slots:
        best = data_slots[0]

        reasoning = (
            f"На основе {best['post_count']} постов: "
            f"час {best['hour']:02d}:00 показывает "
            f"средний engagement {best['avg_engagement_rate']:.1f}% "
            f"и {best['avg_views']:.0f} просмотров. "
            f"Это лучший слот из проанализированных."
        )

        # If LLM is available, get richer reasoning
        if api_key and model:
            reasoning = await _llm_reasoning(api_key, model, hourly_data, day_of_week, best)

        return {
            "recommended_time": f"{best['hour']:02d}:00",
            "confidence": "high" if best["post_count"] >= 5 else "medium",
            "reasoning": reasoning,
            "source": "data",
            "alternatives": [f"{s['hour']:02d}:00" for s in data_slots[1:3]],
        }

    # Fallback to defaults
    default_times = DEFAULT_SLOTS.get(day_of_week, ["09:00", "18:00"])
    return {
        "recommended_time": default_times[0],
        "confidence": "low",
        "reasoning": (
            "Недостаточно данных для рекомендации на основе аналитики. "
            "Используем общие best practices для Telegram. "
            "Продолжай постить — через 2-3 недели будет достаточно данных."
        ),
        "source": "default",
        "alternatives": default_times[1:],
    }


async def _get_hourly_stats(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
) -> list[dict]:
    """Get hourly performance stats from the analytics table."""
    from app.agent.channel.analytics import get_hourly_performance

    try:
        return await get_hourly_performance(session_maker, channel_id, days=30)
    except Exception:
        logger.exception("best_time_hourly_stats_error", channel_id=channel_id)
        return []


def _rank_slots(hourly_data: list[dict], day_of_week: int) -> list[dict]:  # noqa: ARG001
    """Rank time slots by engagement rate, filtering low-confidence ones."""
    if not hourly_data:
        return []

    # Filter out slots with too few posts
    confident = [h for h in hourly_data if h["post_count"] >= MIN_POSTS_FOR_CONFIDENCE]

    if not confident:
        return []

    # Sort by engagement rate descending, then by views as tiebreaker
    confident.sort(
        key=lambda h: (h["avg_engagement_rate"], h["avg_views"]),
        reverse=True,
    )

    return confident


async def _llm_reasoning(
    api_key: str,
    model: str,
    hourly_data: list[dict],
    day_of_week: int,
    best_slot: dict,
) -> str:
    """Use LLM to generate a human-readable reasoning for the recommendation."""
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    day_names = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

    data_summary = "\n".join(
        f"  {h['hour']:02d}:00 — {h['post_count']} постов, "
        f"avg views: {h['avg_views']:.0f}, engagement: {h['avg_engagement_rate']:.1f}%"
        for h in sorted(hourly_data, key=lambda x: x["hour"])
    )

    prompt = f"""Ты — аналитик Telegram-канала. Вот данные по часам публикации за последние 30 дней:

{data_summary}

Сегодня: {day_names[day_of_week]}
Лучший слот по данным: {best_slot["hour"]:02d}:00

Объясни в 2-3 предложениях, почему этот слот лучший и дай рекомендацию.
Будь конкретен, используй цифры. Отвечай на русском."""

    provider = OpenAIProvider(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    llm = OpenAIChatModel(model, provider=provider)
    agent: Agent[None, str] = Agent(model=llm, output_type=str)

    try:
        result = await agent.run(prompt)
        return result.output
    except Exception:
        logger.exception("best_time_llm_reasoning_error")
        return (
            f"Рекомендуемое время: {best_slot['hour']:02d}:00 "
            f"(engagement {best_slot['avg_engagement_rate']:.1f}%, "
            f"{best_slot['avg_views']:.0f} avg views, "
            f"{best_slot['post_count']} постов в выборке)"
        )
