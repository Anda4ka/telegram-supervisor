"""Burr-based state machine workflow for the channel content pipeline.

Replaces manual orchestration with a persistent, checkpointable workflow.
Each pipeline run is modeled as a state machine with the following steps:

    fetch_sources -> screen_content -> generate_post -> send_for_review
        -> await_review -(approved)-> publish_post -> done
                        -(rejected)-> handle_rejection -> done
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from burr.core import ApplicationBuilder, GraphBuilder, Result, State, action, default
from burr.core.action import Condition

from app.agent.channel.exceptions import (
    ChannelPipelineError,
    GenerationError,
    PipelineStageError,
    PublishError,
    ScreeningError,
    SourceFetchError,
)
from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from aiogram import Bot
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.agent.channel.config import ChannelAgentSettings
    from app.infrastructure.db.models import Channel

logger = get_logger("channel.workflow")

_OWN_POSTS_LOOKBACK = 30


def _stage_error(stage: str, exc: Exception, *, recoverable: bool = True) -> str:
    """Create a serialized PipelineStageError from an exception."""
    return PipelineStageError(
        stage=stage,
        error_type=type(exc).__name__,
        message=str(exc),
        recoverable=recoverable,
    ).model_dump_json()


def _filter_already_posted(items: list[Any], own_posts: list[str]) -> list[Any]:
    """Remove items whose key phrases already appear in the channel's own posts.

    Uses simple substring matching on URLs and significant phrases.
    This catches cases where the admin manually posted the same content.
    """
    # Extract URLs and key phrases from own channel posts
    import re

    own_urls: set[str] = set()
    own_phrases: set[str] = set()
    for text in own_posts:
        # Extract URLs
        for url in re.findall(r"https?://[^\s)\]]+", text):
            # Normalize: strip trailing punctuation, lowercase
            own_urls.add(url.rstrip(".,;:!?)").lower())
        # Extract significant phrases (first line, bold text)
        for phrase in re.findall(r"\*\*(.+?)\*\*", text):
            if len(phrase) > 10:
                own_phrases.add(phrase.lower())

    filtered: list[Any] = []
    for item in items:
        item_url = (item.url or "").lower().rstrip(".,;:!?)")
        item_title = (item.title or "").lower()
        item_body = (item.body or "")[:500].lower()

        # Check if any URL from the item is already in channel posts
        skip = False
        if item_url and any(item_url in own_url or own_url in item_url for own_url in own_urls):
            skip = True

        # Check if significant phrases overlap
        if not skip:
            for phrase in own_phrases:
                if phrase in item_title or phrase in item_body:
                    skip = True
                    break

        if not skip:
            filtered.append(item)

    return filtered


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@action(
    reads=["channel_id", "config", "channel", "api_key", "brave_api_key", "session_maker"],
    writes=["content_items", "error"],
)
async def fetch_sources(state: State) -> State:
    """Fetch RSS content and discover fresh items from all configured sources."""
    from app.agent.channel.source_fetchers import FetchContext, fetch_db_sources, fetch_discovery_sources
    from app.agent.channel.source_manager import get_active_sources

    channel_id: int = state["channel_id"]
    config: ChannelAgentSettings = state["config"]
    channel: Channel = state["channel"]
    api_key: str = state["api_key"]
    brave_api_key: str = state.get("brave_api_key", "")
    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]

    ctx = FetchContext(
        session_maker=session_maker,
        config=config,
        api_key=api_key,
        brave_api_key=brave_api_key,
        channel=channel,
    )

    try:
        all_items: list[Any] = []

        # DB-configured sources (RSS, Telegram, Twitter, Reddit)
        db_sources = await get_active_sources(session_maker, channel_id)
        if db_sources:
            items = await fetch_db_sources(db_sources, ctx)
            all_items.extend(items)

        # Discovery sources (Perplexity, Brave)
        discovery_items = await fetch_discovery_sources(ctx, brave_api_key)
        all_items.extend(discovery_items)

        # Deduplicate against DB
        if all_items:
            from sqlalchemy import select

            from app.infrastructure.db.models import ChannelPost

            ext_ids = [i.external_id for i in all_items]
            async with session_maker() as session:
                existing_result = await session.execute(
                    select(ChannelPost.external_id).where(
                        ChannelPost.channel_id == channel_id,
                        ChannelPost.external_id.in_(ext_ids),
                    )
                )
                existing_ids = set(existing_result.scalars().all())
            all_items = [i for i in all_items if i.external_id not in existing_ids]

        logger.info("workflow_fetch_done", count=len(all_items), channel_id=channel_id)
        return state.update(content_items=all_items, error=None)

    except SourceFetchError as exc:
        logger.warning("workflow_fetch_source_error", channel_id=channel_id, error=str(exc))
        return state.update(content_items=[], error=_stage_error("fetch", exc))
    except ChannelPipelineError as exc:
        logger.warning("workflow_fetch_pipeline_error", channel_id=channel_id, error=str(exc))
        return state.update(content_items=[], error=_stage_error("fetch", exc))
    except Exception as exc:
        logger.exception("workflow_fetch_error", channel_id=channel_id)
        return state.update(content_items=[], error=_stage_error("fetch", exc))


@action(
    reads=["content_items", "api_key", "brave_api_key", "config", "channel_id", "session_maker"],
    writes=["content_items", "error"],
)
async def split_and_enrich_topics(state: State) -> State:
    """Split multi-topic items into individual topics, enrich with Brave, then semantic dedup."""
    from app.agent.channel.topic_splitter import split_and_enrich

    items = state["content_items"]
    if not items:
        return state.update(content_items=[], error=None)

    api_key: str = state["api_key"]
    config: ChannelAgentSettings = state["config"]
    channel_id: int = state["channel_id"]
    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]
    brave_key: str = state.get("brave_api_key", "")

    try:
        enriched = await split_and_enrich(
            items,
            api_key=api_key,
            model=config.screening_model,
            brave_api_key=brave_key,
            temperature=config.temperature,
            timeout=config.http_timeout,
        )
        logger.info("workflow_split_enrich_done", before=len(items), after=len(enriched))
    except Exception:
        logger.exception("workflow_split_enrich_error")
        # Keep original items so pipeline can continue with unsplit content
        enriched = items

    # Semantic dedup after split — catches per-topic duplicates against recent posts
    if enriched:
        try:
            from app.agent.channel.semantic_dedup import filter_semantic_duplicates

            enriched = await filter_semantic_duplicates(
                enriched,
                channel_id=channel_id,
                api_key=api_key,
                session_maker=session_maker,
                model=config.embedding_model,
                threshold=config.semantic_dedup_threshold,
            )
        except Exception:
            logger.exception("semantic_dedup_error_skipping", channel_id=channel_id)

    return state.update(content_items=enriched, error=None)


@action(reads=["content_items", "api_key", "config", "channel"], writes=["relevant_items", "error"])
async def screen_content(state: State) -> State:
    """Screen fetched items for relevance.

    When reasoning is enabled, skips the LLM screening call — reasoning
    will handle both screening and evaluation in a single LLM call.
    Own-channel dedup (Telethon) always runs regardless.
    """
    from app.agent.channel.generator import screen_items

    items = state["content_items"]
    if not items:
        return state.update(relevant_items=[], error=None)

    api_key: str = state["api_key"]
    config: ChannelAgentSettings = state["config"]
    channel: Channel = state["channel"]

    # Pre-screen: filter out items that overlap with already-published channel posts
    try:
        from app.core.container import container

        telethon_wrapper = container.get_telethon_client()
        raw_client = telethon_wrapper.client if telethon_wrapper else None
        if raw_client and telethon_wrapper.is_available:
            from app.agent.channel.external_sources.telegram_channels import (
                fetch_own_channel_posts,
            )

            own_posts = await fetch_own_channel_posts(
                raw_client,
                channel.telegram_id,
                limit=_OWN_POSTS_LOOKBACK,
            )
            if own_posts:
                before = len(items)
                items = _filter_already_posted(items, own_posts)
                skipped = before - len(items)
                if skipped:
                    logger.info("pre_screen_own_channel_dedup", skipped=skipped, remaining=len(items))
    except Exception:
        logger.warning("pre_screen_own_channel_error", exc_info=True)

    if not items:
        return state.update(relevant_items=[], error=None)

    # When reasoning is enabled, skip LLM screening — reasoning handles both
    if config.reasoning_enabled:
        logger.info("screening_skipped_reasoning_enabled", items=len(items))
        return state.update(relevant_items=items, error=None)

    try:
        relevant = await screen_items(
            items,
            api_key=api_key,
            model=config.screening_model,
            threshold=config.screening_threshold,
            channel_name=channel.name,
            discovery_query=channel.discovery_query,
        )
        logger.info("workflow_screen_done", relevant=len(relevant), total=len(items))
        return state.update(relevant_items=relevant, error=None)
    except ScreeningError as exc:
        logger.warning("workflow_screen_error", error=str(exc))
        return state.update(relevant_items=[], error=_stage_error("screen", exc))
    except Exception as exc:
        logger.exception("workflow_screen_error")
        return state.update(relevant_items=[], error=_stage_error("screen", exc))


@action(
    reads=["relevant_items", "api_key", "config", "channel", "channel_id", "session_maker"],
    writes=["relevant_items", "reasoning_results", "feedback_context", "error"],
)
async def reason_content(state: State) -> State:
    """Chain-of-thought reasoning on screened items — decides what to post and why.

    Sits between screen_content and generate_post.
    Filters items through structured reasoning (relevance, novelty, tone fit).
    """
    items = state["relevant_items"]
    if not items:
        return state.update(relevant_items=[], reasoning_results=[], feedback_context=None, error=None)

    api_key: str = state["api_key"]
    config: ChannelAgentSettings = state["config"]
    channel: Channel = state["channel"]
    channel_id: int = state["channel_id"]
    session_maker = state["session_maker"]

    reasoning_model = config.reasoning_model or config.generation_model
    reasoning_enabled = config.reasoning_enabled

    if not reasoning_enabled:
        logger.info("reasoning_disabled_passing_through", items=len(items))
        return state.update(relevant_items=items, reasoning_results=[], error=None)

    try:
        from app.agent.channel.feedback import get_feedback_summary
        from app.agent.channel.reasoning import evaluate_batch

        # Get feedback context for reasoning
        feedback_context = ""
        try:
            feedback_context = (
                await get_feedback_summary(
                    session_maker=session_maker,
                    channel_id=channel_id,
                    api_key=api_key,
                    model=config.screening_model,
                    http_timeout=config.http_timeout,
                    temperature=config.temperature,
                )
                or ""
            )
        except Exception:
            logger.warning("reasoning_feedback_fetch_failed", exc_info=True)

        # Get analytics summary for reasoning context
        analytics_summary = ""
        try:
            from app.agent.channel.analytics import get_engagement_rate

            metrics = await get_engagement_rate(session_maker, channel_id)
            if metrics["total_posts"] > 0:
                analytics_summary = (
                    f"Постов за 30 дней: {metrics['total_posts']}, "
                    f"avg views: {metrics['avg_views']}, "
                    f"avg engagement: {metrics['avg_engagement_rate']}%"
                )
        except Exception:
            logger.warning("reasoning_analytics_fetch_failed", exc_info=True)

        channel_context = ""
        if channel.discovery_query:
            channel_context = f"Фокус канала: {channel.discovery_query}"

        approved = await evaluate_batch(
            items,
            api_key=api_key,
            model=reasoning_model,
            session_maker=session_maker,
            channel_id=channel_id,
            channel_name=channel.name,
            channel_context=channel_context,
            feedback_context=feedback_context,
            analytics_summary=analytics_summary,
            temperature=config.temperature,
            screening_threshold=config.screening_threshold,
        )

        approved_items = [item for item, _ in approved]
        reasoning_results = [r.model_dump() for _, r in approved]

        logger.info(
            "workflow_reasoning_done",
            input=len(items),
            approved=len(approved_items),
            channel_id=channel_id,
        )
        return state.update(
            relevant_items=approved_items,
            reasoning_results=reasoning_results,
            feedback_context=feedback_context,
            error=None,
        )

    except Exception:
        logger.exception("workflow_reasoning_error", channel_id=channel_id)
        # On reasoning failure, pass items through unfiltered
        return state.update(relevant_items=items, reasoning_results=[], feedback_context=None, error=None)


@action(
    reads=[
        "relevant_items",
        "reasoning_results",
        "feedback_context",
        "api_key",
        "config",
        "channel",
        "channel_id",
        "session_maker",
    ],
    writes=["generated_post", "error"],
)
async def generate_post(state: State) -> State:
    """Generate a Telegram post from relevant items."""
    from app.agent.channel.feedback import get_feedback_summary
    from app.agent.channel.generator import generate_post as _generate

    relevant = state["relevant_items"]
    if not relevant:
        return state.update(generated_post=None, error="no_relevant_items")

    api_key: str = state["api_key"]
    config: ChannelAgentSettings = state["config"]
    channel: Channel = state["channel"]
    channel_id: int = state["channel_id"]
    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]

    from app.agent.channel.config import language_name

    language = language_name(channel.language)
    footer = channel.footer

    # Pre-generation dedup: single batched embedding call for all items
    try:
        from app.agent.channel.semantic_dedup import find_nearest_posts_batch

        texts = [f"{item.title} {item.body[:100]}" for item in relevant]
        batch_results = await find_nearest_posts_batch(
            texts,
            channel_id=channel_id,
            api_key=api_key,
            session_maker=session_maker,
            model=config.embedding_model,
            lookback_days=7,
        )

        deduplicated: list[Any] = []
        for item, nearest in zip(relevant, batch_results, strict=False):
            if nearest and nearest[0][1] >= config.semantic_dedup_threshold:
                logger.info(
                    "pre_generation_dedup_skip",
                    title=item.title[:60],
                    similar_to=nearest[0][0][:60],
                    similarity=f"{nearest[0][1]:.3f}",
                )
                continue
            deduplicated.append(item)
        if deduplicated:
            relevant = deduplicated
        else:
            logger.info("all_relevant_items_are_duplicates", channel_id=channel_id)
            return state.update(generated_post=None, error="all_items_are_duplicates")
    except Exception:
        logger.warning("pre_generation_dedup_failed_continuing", exc_info=True)

    # Use cached feedback from reasoning step, or fetch if not available
    feedback_context: str | None = state.get("feedback_context")
    if feedback_context is None:
        try:
            feedback_context = await get_feedback_summary(
                session_maker=session_maker,
                channel_id=channel_id,
                api_key=api_key,
                model=config.screening_model,
                http_timeout=config.http_timeout,
                temperature=config.temperature,
            )
        except Exception:
            logger.exception("workflow_feedback_error")

    # Build channel context for generation prompt
    channel_context = ""
    if channel.discovery_query:
        channel_context = f"Channel focus: {channel.discovery_query}"

    # Load Brand Voice profile (if available) for style-consistent generation
    voice_prompt: str | None = None
    try:
        from app.agent.channel.brand_voice import load_voice_profile

        voice = await load_voice_profile(session_maker, channel_id)
        if voice:
            voice_prompt = voice.to_prompt_block()
            logger.info("voice_profile_loaded", channel_id=channel_id, preset=voice.preset_name)
    except Exception:
        logger.debug("voice_profile_load_skipped", exc_info=True)

    # Extract suggested_angle from reasoning results (if available)
    suggested_angle: str | None = None
    reasoning_results = state.get("reasoning_results", [])
    if reasoning_results:
        first_reasoning = reasoning_results[0]
        if isinstance(first_reasoning, dict):
            suggested_angle = first_reasoning.get("suggested_angle")

    try:
        post = await _generate(
            relevant[:1],  # 1 news = 1 post
            api_key=api_key,
            model=config.generation_model,
            language=language,
            feedback_context=feedback_context,
            footer=footer,
            channel_name=channel.name,
            channel_context=channel_context,
            suggested_angle=suggested_angle,
            voice_prompt=voice_prompt,
        )
        if post is None:
            return state.update(generated_post=None, error="generation_failed")

        post_dict = post.model_dump()
        logger.info("workflow_generate_done", length=len(post.text), images=len(post.image_urls or []))
        return state.update(generated_post=post_dict, error=None)

    except GenerationError as exc:
        logger.warning("workflow_generate_error", error=str(exc))
        return state.update(generated_post=None, error=_stage_error("generate", exc))
    except Exception as exc:
        logger.exception("workflow_generate_error")
        return state.update(generated_post=None, error=_stage_error("generate", exc))


@action(
    reads=[
        "generated_post",
        "relevant_items",
        "channel_id",
        "channel",
        "config",
        "publish_bot",
        "review_bot",
        "session_maker",
    ],
    writes=["post_id", "result_message", "error"],
)
async def send_for_review(state: State) -> State:
    """Send the generated post for admin review (or publish directly if no review channel)."""
    from app.agent.channel.generator import GeneratedPost
    from app.agent.channel.review import send_for_review as _send_review

    post_dict = state["generated_post"]
    if not post_dict:
        return state.update(post_id=None, result_message="no_post", error="no_generated_post")

    review_bot: Bot = state["review_bot"]
    publish_bot: Bot = state["publish_bot"]
    channel_id: int = state["channel_id"]
    channel: Channel = state["channel"]
    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]
    relevant = state["relevant_items"]

    review_chat_id = channel.review_chat_id
    post = GeneratedPost.model_validate(post_dict)

    if review_chat_id:
        try:
            api_key: str = state["api_key"]
            config: ChannelAgentSettings = state["config"]
            post_id = await _send_review(
                bot=review_bot,
                review_chat_id=review_chat_id,
                channel_id=channel_id,
                post=post,
                source_items=relevant[:3],
                session_maker=session_maker,
                api_key=api_key,
                embedding_model=config.embedding_model,
                channel_name=channel.name,
                channel_username=channel.username,
            )
            if post_id:
                logger.info("workflow_review_sent", post_id=post_id)
                return state.update(post_id=post_id, result_message="sent_for_review", error=None)
            return state.update(post_id=None, result_message="review_send_failed", error="review_send_failed")
        except Exception as exc:
            logger.exception("workflow_review_error")
            return state.update(post_id=None, result_message="review_error", error=_stage_error("review", exc))
    else:
        # Direct publish (no review channel) — also create a ChannelPost record for audit trail
        from app.agent.channel.publisher import publish_post as _publish

        try:
            msg_id = await _publish(publish_bot, channel.telegram_id, post)
            if msg_id:
                # Create ChannelPost record for dedup, feedback, and audit
                try:
                    from hashlib import sha256

                    from app.core.enums import PostStatus
                    from app.infrastructure.db.models import ChannelPost

                    ext_id = sha256(post.text[:200].encode()).hexdigest()[:16]
                    async with session_maker() as session:
                        db_post = ChannelPost(
                            channel_id=channel_id,
                            external_id=f"direct:{ext_id}",
                            title=relevant[0].title[:200] if relevant else "Direct publish",
                            post_text=post.text,
                            image_url=post.image_url,
                            image_urls=post.image_urls or None,
                            status=PostStatus.APPROVED,
                            telegram_message_id=msg_id,
                        )
                        session.add(db_post)
                        await session.commit()
                        post_db_id = db_post.id

                    # Store embedding for future dedup (best-effort)
                    try:
                        from app.agent.channel.semantic_dedup import store_post_embedding

                        await store_post_embedding(
                            post_id=post_db_id,
                            text_for_embedding=post.text[:300],
                            api_key=api_key,
                            session_maker=session_maker,
                        )
                    except Exception:
                        logger.debug("direct_publish_embedding_failed", exc_info=True)
                except Exception:
                    logger.warning("direct_publish_record_failed", msg_id=msg_id, exc_info=True)

                return state.update(post_id=None, result_message=f"published_directly:{msg_id}", error=None)
            return state.update(post_id=None, result_message="publish_failed", error="direct_publish_failed")
        except Exception as exc:
            logger.exception("workflow_direct_publish_error")
            return state.update(post_id=None, result_message="publish_error", error=_stage_error("publish", exc))


@action(reads=["post_id"], writes=["review_decision"])
async def await_review(state: State) -> State:
    """Halt point — waits for admin review decision.

    This action simply passes through. The workflow halts *after* this action,
    and is resumed later with an updated ``review_decision`` via inputs.
    """
    # On first entry, review_decision is None. When the workflow is resumed,
    # the caller injects the decision via ApplicationBuilder state update.
    return state.update(review_decision=state.get("review_decision"))


@action(reads=["post_id", "channel_id", "channel", "publish_bot", "session_maker"], writes=["result_message", "error"])
async def publish_post(state: State) -> State:
    """Publish an approved post to the channel."""
    from app.agent.channel.review import handle_approve

    post_id: int | None = state["post_id"]
    if not post_id:
        return state.update(result_message="no_post_id", error="missing_post_id")

    bot: Bot = state["publish_bot"]
    channel: Channel = state["channel"]
    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]

    try:
        result = await handle_approve(
            bot=bot, post_id=post_id, channel_id=channel.telegram_id, session_maker=session_maker
        )
        logger.info("workflow_published", post_id=post_id, result=result)
        return state.update(result_message=result, error=None)
    except PublishError as exc:
        logger.warning("workflow_publish_error", post_id=post_id, error=str(exc))
        return state.update(result_message="publish_failed", error=_stage_error("publish", exc, recoverable=False))
    except Exception as exc:
        logger.exception("workflow_publish_error", post_id=post_id)
        return state.update(result_message="publish_failed", error=_stage_error("publish", exc))


@action(reads=["post_id", "session_maker"], writes=["result_message", "error"])
async def handle_rejection(state: State) -> State:
    """Handle a rejected post."""
    from app.agent.channel.review import handle_reject

    post_id: int | None = state["post_id"]
    if not post_id:
        return state.update(result_message="no_post_id", error="missing_post_id")

    session_maker: async_sessionmaker[AsyncSession] = state["session_maker"]

    try:
        result = await handle_reject(post_id=post_id, session_maker=session_maker)
        logger.info("workflow_rejected", post_id=post_id, result=result)
        return state.update(result_message=result, error=None)
    except Exception as exc:
        logger.exception("workflow_rejection_error", post_id=post_id)
        return state.update(result_message="rejection_failed", error=_stage_error("rejection", exc))


# ---------------------------------------------------------------------------
# Graph & App factory
# ---------------------------------------------------------------------------


def _has_content(state: State) -> bool:
    """Transition guard: content_items is non-empty."""
    items = state.get("content_items")
    return bool(items)


def _has_relevant(state: State) -> bool:
    """Transition guard: relevant_items is non-empty."""
    items = state.get("relevant_items")
    return bool(items)


def _has_post(state: State) -> bool:
    """Transition guard: generated_post is present."""
    return state.get("generated_post") is not None


def _has_review_channel(state: State) -> bool:
    """Transition guard: a review channel is configured (HITL path)."""
    channel: Channel | None = state.get("channel")
    return bool(channel and channel.review_chat_id)


def _is_approved(state: State) -> bool:
    from app.core.enums import ReviewDecision

    return state.get("review_decision") == ReviewDecision.APPROVED


def _is_rejected(state: State) -> bool:
    from app.core.enums import ReviewDecision

    return state.get("review_decision") == ReviewDecision.REJECTED


def build_content_pipeline_graph() -> Any:
    """Build the reusable Burr graph for the content pipeline.

    Returns the compiled Graph object.
    """
    # Wrap guard functions as Burr Condition objects
    has_content = Condition.lmda(_has_content, ["content_items"])
    has_relevant = Condition.lmda(_has_relevant, ["relevant_items"])
    has_post = Condition.lmda(_has_post, ["generated_post"])
    has_review_ch = Condition.lmda(_has_review_channel, ["channel"])
    is_approved = Condition.lmda(_is_approved, ["review_decision"])
    is_rejected = Condition.lmda(_is_rejected, ["review_decision"])

    return (
        GraphBuilder()
        .with_actions(
            fetch_sources=fetch_sources,
            split_and_enrich_topics=split_and_enrich_topics,
            screen_content=screen_content,
            reason_content=reason_content,
            generate_post=generate_post,
            send_for_review=send_for_review,
            await_review=await_review,
            publish_post=publish_post,
            handle_rejection=handle_rejection,
            done=Result("result_message", "error"),
        )
        .with_transitions(
            # fetch -> split_and_enrich (if content found) or done
            ("fetch_sources", "split_and_enrich_topics", has_content),
            ("fetch_sources", "done", default),
            # split_and_enrich -> screen (if content remains) or done
            ("split_and_enrich_topics", "screen_content", has_content),
            ("split_and_enrich_topics", "done", default),
            # screen -> reason (if relevant) or done
            ("screen_content", "reason_content", has_relevant),
            ("screen_content", "done", default),
            # reason -> generate (if items survived reasoning) or done
            ("reason_content", "generate_post", has_relevant),
            ("reason_content", "done", default),
            # generate -> send_for_review (if post generated) or done
            ("generate_post", "send_for_review", has_post),
            ("generate_post", "done", default),
            # send_for_review -> await_review (HITL) or done (direct publish)
            ("send_for_review", "await_review", has_review_ch),
            ("send_for_review", "done", default),
            # await_review -> publish or reject based on decision
            ("await_review", "publish_post", is_approved),
            ("await_review", "handle_rejection", is_rejected),
            # terminal transitions
            ("publish_post", "done", default),
            ("handle_rejection", "done", default),
        )
        .build()
    )


# Module-level singleton for reuse
_pipeline_graph: Any | None = None


def get_pipeline_graph() -> Any:
    """Get or build the singleton pipeline graph."""
    global _pipeline_graph  # noqa: PLW0603
    if _pipeline_graph is None:
        _pipeline_graph = build_content_pipeline_graph()
    return _pipeline_graph


def create_pipeline_app(
    channel_id: int,
    session_maker: async_sessionmaker[AsyncSession],
    publish_bot: Bot,
    api_key: str,
    config: ChannelAgentSettings,
    channel: Channel,
    *,
    review_bot: Bot | None = None,
    brave_api_key: str = "",
    app_id: str | None = None,
    resume_state: dict[str, Any] | None = None,
    entrypoint: str = "fetch_sources",
) -> Any:
    """Create a Burr Application for a single pipeline run."""
    graph = get_pipeline_graph()

    resolved_brave_key = brave_api_key or settings.brave.api_key

    initial_state = {
        "channel_id": channel_id,
        "session_maker": session_maker,
        "publish_bot": publish_bot,
        "review_bot": review_bot or publish_bot,
        "api_key": api_key,
        "brave_api_key": resolved_brave_key,
        "config": config,
        "channel": channel,
        "content_items": [],
        "relevant_items": [],
        "reasoning_results": [],
        "feedback_context": None,
        "generated_post": None,
        "post_id": None,
        "review_decision": None,
        "result_message": "",
        "error": None,
    }

    if resume_state:
        initial_state.update(resume_state)

    builder: Any = ApplicationBuilder().with_graph(graph).with_state(**initial_state).with_entrypoint(entrypoint)

    if app_id:
        builder = builder.with_identifiers(app_id=app_id)

    return builder.build()
