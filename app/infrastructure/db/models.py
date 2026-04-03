import datetime
from typing import Any

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EscalationStatus, PostStatus
from app.core.time import utc_now
from app.infrastructure.db.base import Base


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    state: Mapped[bool] = mapped_column(Boolean, default=True)

    def activate(self) -> None:
        self.state = True

    def deactivate(self) -> None:
        self.state = False

    @property
    def is_active(self) -> bool:
        return self.state


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    is_forum: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_message: Mapped[str | None] = mapped_column(String, nullable=True)
    time_delete: Mapped[int] = mapped_column(Integer, default=60)
    is_welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
    modified_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    def enable_welcome(self, message: str | None = None) -> None:
        self.is_welcome_enabled = True
        if message:
            self.welcome_message = message

    def disable_welcome(self) -> None:
        self.is_welcome_enabled = False

    def set_welcome_message(self, message: str) -> None:
        self.welcome_message = message

    def set_welcome_delete_time(self, seconds: int) -> None:
        if seconds > 0:
            self.time_delete = seconds
        else:
            raise ValueError("Delete time must be positive")

    def enable_captcha(self) -> None:
        self.is_captcha_enabled = True

    def disable_captcha(self) -> None:
        self.is_captcha_enabled = False


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    verify: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
    modified_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    def block(self) -> None:
        self.blocked = True

    def unblock(self) -> None:
        self.blocked = False

    def verify_user(self) -> None:
        self.verify = True

    def unverify_user(self) -> None:
        self.verify = False

    @property
    def is_blocked(self) -> bool:
        return self.blocked

    @property
    def is_verified(self) -> bool:
        return self.verify

    @property
    def display_name(self) -> str:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        if self.first_name:
            return self.first_name
        if self.username:
            return f"@{self.username}"
        return f"User {self.id}"

    def update_profile(
        self, username: str | None = None, first_name: str | None = None, last_name: str | None = None
    ) -> None:
        if username is not None:
            self.username = username
        if first_name is not None:
            self.first_name = first_name
        if last_name is not None:
            self.last_name = last_name


class ChatLink(Base):
    __tablename__ = "chat_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(String, unique=True)
    link: Mapped[str] = mapped_column(String, unique=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    message_info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
    spam: Mapped[bool] = mapped_column(Boolean, default=False)

    def mark_as_spam(self) -> None:
        self.spam = True

    def unmark_as_spam(self) -> None:
        self.spam = False


class Channel(Base):
    """A managed Telegram channel with its content pipeline configuration."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    language: Mapped[str] = mapped_column(String(8), default="ru")
    review_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_posts_per_day: Mapped[int] = mapped_column(Integer, default=3)
    posting_schedule: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    publish_schedule: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    discovery_query: Mapped[str] = mapped_column(String, default="")
    source_discovery_query: Mapped[str] = mapped_column(String, default="")
    daily_posts_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_count_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_source_discovery_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    footer_template: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
    modified_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    _DEFAULT_FOOTER = "——\n🔗 **{name}** | @{username}"

    @property
    def footer(self) -> str:
        """Resolved footer text. Uses template if set, otherwise builds from name/username."""
        if self.footer_template:
            return self.footer_template
        if self.username:
            username = self.username.lstrip("@")
            return self._DEFAULT_FOOTER.format(name=self.name, username=username)
        return f"——\n🔗 **{self.name}**"

    def reset_daily_count(self, today: str) -> None:
        if self.daily_count_date != today:
            self.daily_posts_count = 0
            self.daily_count_date = today

    def increment_daily_count(self) -> int:
        self.daily_posts_count += 1
        return self.daily_posts_count

    @property
    def can_post_today(self) -> bool:
        return self.daily_posts_count < self.max_posts_per_day


class ChannelSource(Base):
    __tablename__ = "channel_sources"
    __table_args__ = (sa.UniqueConstraint("channel_id", "url", name="uq_channel_source_channel_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    url: Mapped[str] = mapped_column(String, index=True)
    source_type: Mapped[str] = mapped_column(String(16), default="rss")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=1.0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_fetched_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    added_by: Mapped[str] = mapped_column(String(16), default="agent")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)

    def record_success(self) -> None:
        self.error_count = 0
        self.last_error = None
        self.last_fetched_at = utc_now()

    def record_error(self, error: str) -> None:
        self.error_count += 1
        self.last_error = error
        if self.error_count >= 5:
            self.enabled = False

    def boost_relevance(self) -> None:
        self.relevance_score = min(self.relevance_score + 0.1, 2.0)

    def penalize_relevance(self) -> None:
        self.relevance_score = max(self.relevance_score - 0.2, 0.0)
        if self.relevance_score < 0.3:
            self.enabled = False

    def disable(self) -> None:
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True
        self.error_count = 0


class ChannelPost(Base):
    __tablename__ = "channel_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    external_id: Mapped[str] = mapped_column(String, index=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    post_text: Mapped[str] = mapped_column(String)
    source_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    review_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    review_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    image_urls: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=PostStatus.DRAFT, index=True)
    admin_feedback: Mapped[str | None] = mapped_column(String, nullable=True)
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)

    def approve(self, message_id: int) -> None:
        self.status = PostStatus.APPROVED
        self.telegram_message_id = message_id
        self.published_at = utc_now()

    def schedule(self, scheduled_at: datetime.datetime, telegram_scheduled_id: int) -> None:
        self.status = PostStatus.SCHEDULED
        self.scheduled_at = scheduled_at
        self.scheduled_telegram_id = telegram_scheduled_id

    def confirm_published(self, message_id: int) -> None:
        self.status = PostStatus.APPROVED
        self.telegram_message_id = message_id
        self.published_at = utc_now()

    def reschedule(self, new_time: datetime.datetime, new_telegram_id: int) -> None:
        self.scheduled_at = new_time
        self.scheduled_telegram_id = new_telegram_id

    def unschedule(self) -> None:
        self.status = PostStatus.DRAFT
        self.scheduled_at = None
        self.scheduled_telegram_id = None

    def reject(self, feedback: str | None = None) -> None:
        self.status = PostStatus.REJECTED
        if feedback:
            self.admin_feedback = feedback

    def skip(self) -> None:
        self.status = PostStatus.SKIPPED

    def update_text(self, new_text: str) -> None:
        self.post_text = new_text
        if self.status != PostStatus.SCHEDULED:
            self.status = PostStatus.DRAFT


class LLMUsageLog(Base):
    """Persisted LLM usage record for cost tracking across restarts."""

    __tablename__ = "llm_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(32), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_savings_usd: Mapped[float] = mapped_column(Float, default=0.0)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ChannelVoiceProfile(Base):
    """Stored brand voice profile for a channel — extracted style characteristics."""

    __tablename__ = "channel_voice_profiles"
    __table_args__ = (sa.UniqueConstraint("channel_id", "preset_name", name="uq_voice_channel_preset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    preset_name: Mapped[str] = mapped_column(String(32), default="default")
    profile_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analyzed_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32))
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    reporter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_text: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(default=None)
    admin_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)


class AgentEscalation(Base):
    __tablename__ = "agent_escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    target_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_text: Mapped[str | None] = mapped_column(String, nullable=True)
    suggested_action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String)
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    admin_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=EscalationStatus.PENDING)
    resolved_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    timeout_at: Mapped[datetime.datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utc_now)
