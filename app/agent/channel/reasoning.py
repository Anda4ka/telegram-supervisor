"""Chain-of-thought reasoning step for the content pipeline.

Sits between screen_content and generate_post in the Burr workflow.
Evaluates content items with structured reasoning in a single batched
LLM call before passing to generation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.agent.channel.llm_client import openrouter_chat_completion
from app.agent.channel.sanitize import sanitize_external_text
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.agent.channel.sources import ContentItem

logger = get_logger("channel.reasoning")


class ReasoningResult(BaseModel):
    """Structured reasoning output for a content item."""

    relevance_score: int = Field(ge=1, le=10, description="Relevance to channel audience (1-10)")
    novelty_score: int = Field(ge=1, le=10, description="How novel/fresh is this content (1-10)")
    tone_fit: bool = Field(description="Can this be adapted to the channel's tone of voice?")
    urgency: str = Field(description="urgent / can_wait / evergreen")
    decision: str = Field(description="post / skip / delay")
    reasoning: str = Field(description="2-3 sentence explanation of the decision")
    suggested_angle: str | None = Field(default=None, description="Suggested angle/hook for the post")


BATCH_REASONING_SYSTEM = """\
Ты — AI-редактор дегенского Telegram-канала «{channel_name}» про абузы AI, крипто и инструменты.
Отвечай на русском. Отсеивай новости.

Оцени КАЖДЫЙ контент-айтем и верни JSON-объект, где ключ — индекс айтема, а значение — объект с полями:
- relevance_score (1-10): Есть ли actionable инфо?
- novelty_score (1-10): Свежий угол?
- tone_fit (true/false): Можно подать в дегенском стиле?
- urgency: "urgent" / "can_wait" / "evergreen"
- decision: "post" / "skip" / "delay"
- reasoning: 2-3 предложения обоснования
- suggested_angle: КОНКРЕТНЫЙ хук для поста — первое предложение, с которого автор начнёт пост. \
Пиши от первого лица, дегенским стилем. Примеры: "нашёл массовый абуз на...", "короче, тут можно бесплатно...", \
"пацаны, кто ещё не фармит X — вы спите". НЕ пиши заголовки вида "Бесплатный доступ к X". (null если skip)

Верни ТОЛЬКО JSON. Пример:
{{"0": {{"relevance_score": 8, "novelty_score": 7, "tone_fit": true, "urgency": "urgent", "decision": "post", "reasoning": "Свежий абуз Claude...", "suggested_angle": "Лутаем бесплатный Claude"}}, "1": {{"relevance_score": 2, "novelty_score": 3, "tone_fit": false, "urgency": "can_wait", "decision": "skip", "reasoning": "Обычная новость...", "suggested_angle": null}}}}"""

BATCH_REASONING_USER = """\
СТИЛЬ КАНАЛА: {tone_description}
ФОКУС КАНАЛА: {channel_context}

ПОСЛЕДНИЕ 10 ПОСТОВ КАНАЛА:
{recent_posts}

ОБРАТНАЯ СВЯЗЬ ОТ АДМИНА:
{feedback_context}

АНАЛИТИКА:
{analytics_summary}

АВТОМАТИЧЕСКИЙ SKIP:
- Новости без actionable контента ("X объявила о Y", "рынок вырос")
- Пресс-релизы и корпоративный контент
- Аналитика рынка без конкретных плейсов
- Общие обзоры технологий без практической пользы

Будь строгим. Канал про абузы и actionable контент, а не новостная лента.

───────────────────
КОНТЕНТ ДЛЯ ОЦЕНКИ:
{items_block}
───────────────────

Оцени каждый айтем. Верни ТОЛЬКО JSON."""


async def evaluate_content(
    item: ContentItem,
    *,
    api_key: str,
    model: str,
    channel_name: str = "",
    channel_context: str = "",
    tone_description: str = "дегенский, разговорный, с матом, actionable контент, абузы и хаки",
    recent_posts: str = "Нет данных",
    feedback_context: str = "Нет данных",
    analytics_summary: str = "Нет данных",
    temperature: float = 0.3,
) -> ReasoningResult:
    """Run chain-of-thought reasoning on a single content item.

    Thin wrapper around evaluate_batch for backward compatibility.
    """
    results = await _evaluate_batch_llm(
        [item],
        api_key=api_key,
        model=model,
        channel_name=channel_name,
        channel_context=channel_context,
        tone_description=tone_description,
        recent_posts=recent_posts,
        feedback_context=feedback_context,
        analytics_summary=analytics_summary,
        temperature=temperature,
    )
    if results:
        return results[0][1]
    # Fallback: return skip result
    return ReasoningResult(
        relevance_score=1,
        novelty_score=1,
        tone_fit=False,
        urgency="can_wait",
        decision="skip",
        reasoning="Evaluation failed",
        suggested_angle=None,
    )


async def _evaluate_batch_llm(
    items: list[ContentItem],
    *,
    api_key: str,
    model: str,
    channel_name: str = "",
    channel_context: str = "",
    tone_description: str = "дегенский, разговорный, с матом, actionable контент, абузы и хаки",
    recent_posts: str = "Нет данных",
    feedback_context: str = "Нет данных",
    analytics_summary: str = "Нет данных",
    temperature: float = 0.3,
) -> list[tuple[ContentItem, ReasoningResult]]:
    """Core batched reasoning: evaluates ALL items in a single LLM call.

    Returns list of (item, reasoning) for ALL items (including skips).
    """
    if not items:
        return []

    # Build numbered items block
    item_lines: list[str] = []
    for i, item in enumerate(items):
        title = sanitize_external_text(item.title)
        body = sanitize_external_text(item.body[:500])
        item_lines.append(f"[{i}] Источник: {item.source_url}\nЗаголовок: {title}\nТекст: {body}")

    items_block = "\n\n".join(item_lines)

    system_prompt = BATCH_REASONING_SYSTEM.format(channel_name=channel_name)
    user_prompt = BATCH_REASONING_USER.format(
        tone_description=tone_description,
        channel_context=channel_context or "Не указан",
        recent_posts=recent_posts,
        feedback_context=feedback_context or "Нет данных",
        analytics_summary=analytics_summary,
        items_block=items_block,
    )

    content = await openrouter_chat_completion(
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        operation="reasoning_batch",
        temperature=temperature,
        timeout=60,
    )

    if not content:
        logger.warning("reasoning_batch_empty_response")
        return []

    # Parse JSON response
    scores_raw = content if isinstance(content, dict) else json.loads(content)
    if not isinstance(scores_raw, dict):
        logger.warning("reasoning_batch_unexpected_type", type=type(scores_raw).__name__)
        return []

    results: list[tuple[ContentItem, ReasoningResult]] = []
    for i, item in enumerate(items):
        raw = scores_raw.get(str(i))
        if not raw or not isinstance(raw, dict):
            logger.warning("reasoning_batch_missing_index", index=i, title=item.title[:60])
            continue
        try:
            reasoning = ReasoningResult(**raw)
            results.append((item, reasoning))
            logger.info(
                "reasoning_complete",
                title=item.title[:60],
                decision=reasoning.decision,
                relevance=reasoning.relevance_score,
                novelty=reasoning.novelty_score,
            )
        except Exception:
            logger.warning("reasoning_batch_parse_item", index=i, title=item.title[:60], exc_info=True)

    return results


async def evaluate_batch(
    items: list[ContentItem],
    *,
    api_key: str,
    model: str,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    channel_id: int = 0,
    channel_name: str = "",
    channel_context: str = "",
    tone_description: str = "дегенский, разговорный, с матом, actionable контент, абузы и хаки",
    feedback_context: str = "",
    analytics_summary: str = "",
    temperature: float = 0.3,
    screening_threshold: int = 0,
) -> list[tuple[ContentItem, ReasoningResult]]:
    """Evaluate a batch of content items with reasoning in a single LLM call.

    Returns list of (item, reasoning) tuples, filtered to items where
    decision is 'post' or 'delay' AND relevance_score >= screening_threshold.

    When screening_threshold > 0, this function handles both screening and
    reasoning in one LLM call, eliminating the need for a separate screening step.
    """
    # Get recent posts for novelty checking
    recent_posts = "Нет данных"
    if session_maker and channel_id:
        recent_posts = await _get_recent_posts_summary(session_maker, channel_id)

    try:
        all_results = await _evaluate_batch_llm(
            items,
            api_key=api_key,
            model=model,
            channel_name=channel_name,
            channel_context=channel_context,
            tone_description=tone_description,
            recent_posts=recent_posts,
            feedback_context=feedback_context,
            analytics_summary=analytics_summary,
            temperature=temperature,
        )
    except Exception:
        logger.exception("reasoning_batch_error")
        all_results = []

    # Filter: keep items with 'post' or 'delay' decisions
    # When screening_threshold > 0, also enforce minimum relevance score
    approved = []
    for item, r in all_results:
        if r.decision not in ("post", "delay"):
            continue
        if screening_threshold > 0 and r.relevance_score < screening_threshold:
            logger.debug("reasoning_below_threshold", title=item.title[:60], score=r.relevance_score)
            continue
        approved.append((item, r))

    logger.info(
        "reasoning_batch_done",
        total=len(items),
        evaluated=len(all_results),
        approved=len(approved),
    )

    return approved


async def _get_recent_posts_summary(
    session_maker: async_sessionmaker[AsyncSession],
    channel_id: int,
    limit: int = 10,
) -> str:
    """Get a summary of the most recent published posts for novelty checking."""
    from sqlalchemy import select

    from app.core.enums import PostStatus
    from app.infrastructure.db.models import ChannelPost

    try:
        async with session_maker() as session:
            result = await session.execute(
                select(ChannelPost.title, ChannelPost.post_text)
                .where(
                    ChannelPost.channel_id == channel_id,
                    ChannelPost.status == PostStatus.APPROVED,
                )
                .order_by(ChannelPost.created_at.desc())
                .limit(limit)
            )
            rows = result.fetchall()

        if not rows:
            return "Нет опубликованных постов"

        lines = []
        for i, (title, text) in enumerate(rows, 1):
            preview = (text or "")[:100].replace("\n", " ")
            lines.append(f"{i}. {title[:80]} — {preview}")

        return "\n".join(lines)

    except Exception:
        logger.exception("recent_posts_summary_error")
        return "Ошибка получения последних постов"
