"""Direct /command handlers for the assistant bot.

These commands execute immediately without going through the AI agent,
saving tokens and providing instant responses with inline keyboards.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import Message as TgMessage

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("assistant.commands")

commands_router = Router(name="assistant_commands")


_PUBLIC_CHANNEL_ID: int = 0
_BOT_CHANNEL_ID: int = 0
_PUBLIC_CHANNEL_NAME: str = ""
_channels_resolved: bool = False


async def _resolve_channel_ids() -> None:
    """Lazy-resolve channel IDs from DB on first use (not import time)."""
    global _PUBLIC_CHANNEL_ID, _BOT_CHANNEL_ID, _PUBLIC_CHANNEL_NAME, _channels_resolved  # noqa: PLW0603
    if _channels_resolved:
        return
    try:
        from app.core.container import container

        sm = container.get_session_maker()
        from sqlalchemy import select

        from app.infrastructure.db.models import Channel

        async with sm() as session:
            result = await session.execute(select(Channel).where(Channel.enabled.is_(True)).limit(1))
            ch = result.scalar_one_or_none()
            if ch:
                _BOT_CHANNEL_ID = ch.telegram_id
                _PUBLIC_CHANNEL_NAME = f"@{ch.username}" if ch.username else ch.name
                _PUBLIC_CHANNEL_ID = settings.channel.analytics_public_id or ch.telegram_id
    except Exception:
        logger.warning("channel_ids_resolve_failed", exc_info=True)
    _channels_resolved = True


# ──────────────────────────────────────────────
#  /stats — Channel analytics
# ──────────────────────────────────────────────


@commands_router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show analytics overview with inline navigation."""
    await _resolve_channel_ids()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Обзор", callback_data="stats:overview"),
                InlineKeyboardButton(text="🏆 Топ посты", callback_data="stats:top"),
            ],
            [
                InlineKeyboardButton(text="🕐 По часам", callback_data="stats:hourly"),
                InlineKeyboardButton(text="📈 За неделю", callback_data="stats:week"),
            ],
        ]
    )
    await message.answer(
        f"📊 <b>Аналитика {_PUBLIC_CHANNEL_NAME}</b>\n\nВыбери раздел:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@commands_router.callback_query(F.data == "stats:overview")
async def cb_stats_overview(callback: CallbackQuery) -> None:
    """30-day engagement overview."""
    from app.agent.channel.analytics import get_engagement_rate
    from app.core.container import container

    sm = container.get_session_maker()
    metrics = await get_engagement_rate(sm, _PUBLIC_CHANNEL_ID, days=30)

    text = (
        f"📊 <b>Аналитика {_PUBLIC_CHANNEL_NAME}</b> (30 дней)\n\n"
        f"📝 Постов: <b>{metrics['total_posts']}</b>\n"
        f"👁 Avg views: <b>{metrics['avg_views']:.0f}</b>\n"
        f"❤️ Avg reactions: <b>{metrics['avg_reactions']:.1f}</b>\n"
        f"🔄 Avg forwards: <b>{metrics['avg_forwards']:.1f}</b>\n"
        f"💬 Avg comments: <b>{metrics['avg_comments']:.1f}</b>\n"
        f"📈 Engagement rate: <b>{metrics['avg_engagement_rate']:.2f}%</b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="stats:back")],
        ]
    )
    await _edit_or_answer(callback, text, kb)


@commands_router.callback_query(F.data == "stats:top")
async def cb_stats_top(callback: CallbackQuery) -> None:
    """Top 5 posts by views."""
    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        result = await session.execute(
            sa_text("""
                WITH latest AS (
                    SELECT DISTINCT ON (message_id)
                        message_id, views, reactions_count, forwards, published_at
                    FROM post_analytics
                    WHERE channel_id = :cid
                    ORDER BY message_id, measured_at DESC
                )
                SELECT message_id, views, reactions_count, forwards, published_at::text
                FROM latest
                ORDER BY views DESC
                LIMIT 5
            """),
            {"cid": _PUBLIC_CHANNEL_ID},
        )
        rows = result.fetchall()

    if not rows:
        await _edit_or_answer(callback, "Нет данных. Подожди пока соберётся аналитика.")
        return

    lines = [f"🏆 <b>Топ-5 постов {_PUBLIC_CHANNEL_NAME}</b>\n"]
    for i, (msg_id, views, reactions, forwards, pub_date) in enumerate(rows, 1):
        date_str = pub_date[:10] if pub_date else "?"
        lines.append(
            f"{i}. 👁 <b>{views}</b> | ❤️ {reactions} | 🔄 {forwards}\n"
            f"   📅 {date_str} | <a href='https://t.me/grassfoundationn/{msg_id}'>Пост #{msg_id}</a>"
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="stats:back")],
        ]
    )
    await _edit_or_answer(callback, "\n".join(lines), kb)


@commands_router.callback_query(F.data == "stats:hourly")
async def cb_stats_hourly(callback: CallbackQuery) -> None:
    """Engagement by hour of day."""
    from app.agent.channel.analytics import get_hourly_performance
    from app.core.container import container

    sm = container.get_session_maker()
    hourly = await get_hourly_performance(sm, _PUBLIC_CHANNEL_ID, days=30)

    if not hourly:
        await _edit_or_answer(callback, "Нет данных по часам. Подожди пока соберётся аналитика.")
        return

    lines = [f"🕐 <b>Активность по часам {_PUBLIC_CHANNEL_NAME}</b>\n"]

    # Find best hour
    best = max(hourly, key=lambda h: h["avg_views"])

    for h in hourly:
        bar_len = int(h["avg_views"] / max(best["avg_views"], 1) * 8)
        bar = "█" * bar_len + "░" * (8 - bar_len)
        star = " ⭐" if h["hour"] == best["hour"] else ""
        lines.append(
            f"<code>{h['hour']:02d}:00</code> {bar} {h['avg_views']:.0f} views ({h['post_count']} постов){star}"
        )

    lines.append(f"\n🎯 Лучшее время: <b>{best['hour']:02d}:00</b> ({best['avg_views']:.0f} avg views)")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="stats:back")],
        ]
    )
    await _edit_or_answer(callback, "\n".join(lines), kb)


@commands_router.callback_query(F.data == "stats:week")
async def cb_stats_week(callback: CallbackQuery) -> None:
    """Last 7 days stats."""
    from app.agent.channel.analytics import get_engagement_rate
    from app.core.container import container

    sm = container.get_session_maker()
    week = await get_engagement_rate(sm, _PUBLIC_CHANNEL_ID, days=7)
    month = await get_engagement_rate(sm, _PUBLIC_CHANNEL_ID, days=30)

    def _trend(week_val: float, month_val: float) -> str:
        if month_val == 0:
            return ""
        diff = ((week_val - month_val) / month_val) * 100
        if diff > 5:
            return f" 📈 +{diff:.0f}%"
        if diff < -5:
            return f" 📉 {diff:.0f}%"
        return " ➡️"

    text = (
        f"📈 <b>За неделю vs месяц {_PUBLIC_CHANNEL_NAME}</b>\n\n"
        f"📝 Постов: <b>{week['total_posts']}</b> (мес: {month['total_posts']})\n"
        f"👁 Avg views: <b>{week['avg_views']:.0f}</b>{_trend(week['avg_views'], month['avg_views'])}\n"
        f"❤️ Avg reactions: <b>{week['avg_reactions']:.1f}</b>{_trend(week['avg_reactions'], month['avg_reactions'])}\n"
        f"🔄 Avg forwards: <b>{week['avg_forwards']:.1f}</b>{_trend(week['avg_forwards'], month['avg_forwards'])}\n"
        f"📈 Engagement: <b>{week['avg_engagement_rate']:.2f}%</b>"
        f"{_trend(week['avg_engagement_rate'], month['avg_engagement_rate'])}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="stats:back")],
        ]
    )
    await _edit_or_answer(callback, text, kb)


@commands_router.callback_query(F.data == "stats:back")
async def cb_stats_back(callback: CallbackQuery) -> None:
    """Back to stats menu."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Обзор", callback_data="stats:overview"),
                InlineKeyboardButton(text="🏆 Топ посты", callback_data="stats:top"),
            ],
            [
                InlineKeyboardButton(text="🕐 По часам", callback_data="stats:hourly"),
                InlineKeyboardButton(text="📈 За неделю", callback_data="stats:week"),
            ],
        ]
    )
    await _edit_or_answer(callback, f"📊 <b>Аналитика {_PUBLIC_CHANNEL_NAME}</b>\n\nВыбери раздел:", kb)


# ──────────────────────────────────────────────
#  /sources — Source management
# ──────────────────────────────────────────────

_SOURCE_TYPE_EMOJI = {
    "rss": "📡",
    "twitter": "🐦",
    "telegram": "📢",
    "telegram_forum": "💬",
    "reddit": "🔴",
}


@commands_router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    """Show sources overview with type filter buttons."""
    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        result = await session.execute(
            sa_text("""
                SELECT source_type, COUNT(*), SUM(CASE WHEN enabled THEN 1 ELSE 0 END),
                       SUM(error_count)
                FROM channel_sources
                GROUP BY source_type ORDER BY source_type
            """)
        )
        rows = result.fetchall()

    if not rows:
        await message.answer("Нет источников.")
        return

    lines = ["🔌 <b>Источники контента</b>\n"]
    total = 0
    total_active = 0
    total_errors = 0

    buttons = []
    for stype, count, active, errors in rows:
        emoji = _SOURCE_TYPE_EMOJI.get(stype, "📄")
        lines.append(f"{emoji} <b>{stype}</b>: {active}/{count} активных" + (f" ⚠️ {errors} ошибок" if errors else ""))
        total += count
        total_active += active
        total_errors += errors or 0
        buttons.append(InlineKeyboardButton(text=f"{emoji} {stype} ({count})", callback_data=f"src:list:{stype}"))

    lines.append(
        f"\n📊 Всего: <b>{total_active}/{total}</b> активных" + (f", {total_errors} ошибок" if total_errors else "")
    )

    # Arrange buttons in rows of 2
    kb_rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@commands_router.callback_query(F.data.startswith("src:list:"))
async def cb_sources_list(callback: CallbackQuery) -> None:
    """List sources of a specific type."""
    source_type = callback.data.split(":", 2)[2]  # type: ignore[union-attr]

    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        result = await session.execute(
            sa_text("""
                SELECT id, url, title, enabled, error_count, last_fetched_at::text
                FROM channel_sources
                WHERE source_type = :stype
                ORDER BY enabled DESC, url
            """),
            {"stype": source_type},
        )
        rows = result.fetchall()

    emoji = _SOURCE_TYPE_EMOJI.get(source_type, "📄")
    lines = [f"{emoji} <b>Источники: {source_type}</b>\n"]

    buttons = []
    for source_id, url, title, enabled, errors, last_fetch in rows:
        status = "✅" if enabled else "❌"
        name = title or url
        if len(name) > 45:
            name = name[:42] + "..."
        error_str = f" ⚠️{errors}" if errors else ""
        fetch_str = f" | {last_fetch[:16]}" if last_fetch else ""
        lines.append(f"{status} <code>{name}</code>{error_str}{fetch_str}")

        # Toggle button
        action = "off" if enabled else "on"
        btn_text = f"{'❌' if enabled else '✅'} {name[:20]}"
        buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"src:toggle:{source_id}:{action}"))

    # Show first 10 toggle buttons max (Telegram limit)
    kb_rows = [[b] for b in buttons[:10]]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="src:back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await _edit_or_answer(callback, "\n".join(lines), kb)


@commands_router.callback_query(F.data.startswith("src:toggle:"))
async def cb_source_toggle(callback: CallbackQuery) -> None:
    """Toggle a source on/off by primary key."""
    parts = callback.data.split(":", 3)  # type: ignore[union-attr]
    source_id = int(parts[2])
    action = parts[3]  # "on" or "off"
    new_enabled = action == "on"

    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        result = await session.execute(
            sa_text("UPDATE channel_sources SET enabled = :enabled WHERE id = :source_id"),
            {"enabled": new_enabled, "source_id": source_id},
        )
        await session.commit()

    rowcount = getattr(result, "rowcount", None)
    if rowcount != 1:
        await callback.answer("Источник не найден", show_alert=True)
        return

    status = "✅ включён" if new_enabled else "❌ выключен"
    await callback.answer(f"Источник {status}", show_alert=True)


@commands_router.callback_query(F.data == "src:back")
async def cb_sources_back(callback: CallbackQuery) -> None:
    """Back to sources overview — re-run the sources command."""
    # Simulate /sources command by editing the message
    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        result = await session.execute(
            sa_text("""
                SELECT source_type, COUNT(*), SUM(CASE WHEN enabled THEN 1 ELSE 0 END),
                       SUM(error_count)
                FROM channel_sources
                GROUP BY source_type ORDER BY source_type
            """)
        )
        rows = result.fetchall()

    lines = ["🔌 <b>Источники контента</b>\n"]
    buttons = []
    total, total_active = 0, 0
    for stype, count, active, errors in rows:
        emoji = _SOURCE_TYPE_EMOJI.get(stype, "📄")
        lines.append(f"{emoji} <b>{stype}</b>: {active}/{count} активных" + (f" ⚠️ {errors} ошибок" if errors else ""))
        total += count
        total_active += active
        buttons.append(InlineKeyboardButton(text=f"{emoji} {stype} ({count})", callback_data=f"src:list:{stype}"))

    lines.append(f"\n📊 Всего: <b>{total_active}/{total}</b> активных")
    kb_rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await _edit_or_answer(callback, "\n".join(lines), kb)


# ──────────────────────────────────────────────
#  /setup — Quick channel setup wizard
# ──────────────────────────────────────────────


@commands_router.message(Command("setup"))
async def cmd_setup(message: Message) -> None:
    """Quick setup wizard — guides admin through adding a channel."""
    text = (
        "🧙 <b>Setup Wizard</b>\n\n"
        "Привет! Давай настроим канал. Что нужно:\n\n"
        "1️⃣ Добавь бота в канал как администратора\n"
        "2️⃣ Перешли мне любое сообщение из канала\n"
        "   (или отправь @username канала)\n"
        "3️⃣ Я автоматически добавлю канал\n\n"
        "Или используй кнопки для быстрых действий:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Показать мои каналы", callback_data="setup:list")],
            [InlineKeyboardButton(text="🔍 Проверить здоровье", callback_data="setup:health")],
            [InlineKeyboardButton(text="📖 Гайд по настройке", callback_data="setup:guide")],
        ]
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@commands_router.callback_query(F.data == "setup:list")
async def cb_setup_list(callback: CallbackQuery) -> None:
    """Show existing channels."""
    from app.core.container import container

    sm = container.get_session_maker()
    from sqlalchemy import select

    from app.infrastructure.db.models import Channel

    async with sm() as session:
        result = await session.execute(select(Channel))
        channels = list(result.scalars().all())

    if not channels:
        await callback.answer("Каналов пока нет. Перешли сообщение из канала!", show_alert=True)
        return

    lines = ["<b>Твои каналы:</b>\n"]
    for ch in channels:
        status = "✅" if ch.enabled else "⏸"
        name = ch.name or str(ch.telegram_id)
        username = f" (@{ch.username})" if ch.username else ""
        schedule = ", ".join(ch.posting_schedule) if ch.posting_schedule else "не задано"
        lines.append(f"{status} <b>{name}</b>{username}")
        lines.append(f"   ID: <code>{ch.telegram_id}</code> | Расписание: {schedule}")
        lines.append(f"   Постов/день: {ch.daily_posts_count}/{ch.max_posts_per_day}")
    await _edit_or_answer(callback, "\n".join(lines))


@commands_router.callback_query(F.data == "setup:health")
async def cb_setup_health(callback: CallbackQuery) -> None:
    """Run quick health check."""
    from app.core.container import container
    from app.core.healthcheck import run_healthcheck

    report = await run_healthcheck(container.get_session_maker())
    if callback.message:
        await callback.message.edit_text(report.format_telegram(), parse_mode="HTML")
    else:
        await callback.answer("OK" if report.all_ok else "Issues found", show_alert=True)


@commands_router.callback_query(F.data == "setup:guide")
async def cb_setup_guide(callback: CallbackQuery) -> None:
    """Show setup guide."""
    text = (
        "📖 <b>Полный гайд по настройке</b>\n\n"
        "<b>1. Добавить канал:</b>\n"
        "  Скажи мне: <i>«Добавь канал @username»</i>\n"
        "  Или используй команду в AI-чате\n\n"
        "<b>2. Добавить источники контента:</b>\n"
        "  <i>«Добавь RSS https://example.com/feed»</i>\n"
        "  <i>«Добавь Twitter @username»</i>\n"
        "  <i>«Добавь Reddit r/LocalLLaMA»</i>\n\n"
        "<b>3. Настроить расписание:</b>\n"
        "  <i>«Поставь расписание 09:00, 15:00, 21:00»</i>\n\n"
        "<b>4. Настроить review чат:</b>\n"
        "  <i>«Поставь review чат на этот»</i>\n\n"
        "<b>5. Мониторинг конкурентов:</b>\n"
        "  <i>«Добавь конкурента @channel»</i>\n\n"
        "<b>Полезные команды:</b>\n"
        "  /stats — аналитика\n"
        "  /sources — источники\n"
        "  /calendar — календарь постов\n"
        "  /settings — настройки\n"
        "  /healthcheck — диагностика"
    )
    await _edit_or_answer(callback, text)


# ──────────────────────────────────────────────
#  /calendar — Content calendar (7 days)
# ──────────────────────────────────────────────

_DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@commands_router.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    """Show 7-day content calendar with scheduled posts and empty slots."""
    await _resolve_channel_ids()
    from datetime import timedelta

    from app.core.container import container
    from app.core.time import utc_now

    sm = container.get_session_maker()
    channel_id = _BOT_CHANNEL_ID
    if not channel_id:
        await message.answer("Канал не настроен.")
        return

    # Fetch scheduled posts for 7 days
    from sqlalchemy import select

    from app.core.enums import PostStatus
    from app.infrastructure.db.models import ChannelPost

    now = utc_now()
    week_end = now + timedelta(days=7)

    async with sm() as session:
        result = await session.execute(
            select(ChannelPost.id, ChannelPost.title, ChannelPost.scheduled_at).where(
                ChannelPost.channel_id == channel_id,
                ChannelPost.status == PostStatus.SCHEDULED,
                ChannelPost.scheduled_at.is_not(None),
                ChannelPost.scheduled_at >= now,
                ChannelPost.scheduled_at < week_end,
            ).order_by(ChannelPost.scheduled_at)
        )
        posts = result.all()

    # Get best time recommendation
    best_hour = None
    try:
        from app.agent.channel.best_time import recommend_posting_time

        rec = await recommend_posting_time(sm, channel_id)
        if rec:
            best_hour = rec.get("recommended_time", "")
    except Exception:
        logger.debug("best_time_unavailable_for_calendar", exc_info=True)

    # Group posts by day
    from collections import defaultdict

    by_day: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for post_id, title, sched_at in posts:
        day_key = sched_at.strftime("%Y-%m-%d")
        time_str = sched_at.strftime("%H:%M")
        label = (title or "Без названия")[:40]
        by_day[day_key].append((time_str, f"#{post_id} {label}"))

    # Build calendar
    lines = ["📅 <b>Контент-календарь</b> (7 дней)\n"]
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    total_scheduled = 0

    for i in range(7):
        day = today + timedelta(days=i)
        day_key = day.strftime("%Y-%m-%d")
        day_name = _DAY_NAMES[day.weekday()]
        date_label = day.strftime("%d.%m")
        day_label = "Сегодня" if i == 0 else ("Завтра" if i == 1 else f"{day_name} {date_label}")

        day_posts = by_day.get(day_key, [])
        total_scheduled += len(day_posts)

        if day_posts:
            lines.append(f"<b>{day_label}</b>")
            for time_str, label in day_posts:
                lines.append(f"  ⏰ {time_str} — {label}")
        else:
            lines.append(f"<b>{day_label}</b> — <i>свободно</i>")

    lines.append("")
    lines.append(f"Всего запланировано: {total_scheduled}")
    if best_hour:
        lines.append(f"💡 Лучшее время: {best_hour} UTC")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ──────────────────────────────────────────────
#  /settings — Bot configuration
# ──────────────────────────────────────────────


@commands_router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Show current bot settings with edit buttons."""
    text, kb = await _build_settings_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _build_settings_view() -> tuple[str, InlineKeyboardMarkup]:
    """Build settings message text and keyboard."""
    await _resolve_channel_ids()
    from app.core.container import container

    sm = container.get_session_maker()
    channel_config = settings.channel

    # Read current values from DB + config
    from sqlalchemy import text as sa_text

    async with sm() as session:
        result = await session.execute(
            sa_text("SELECT max_posts_per_day, name FROM channels WHERE telegram_id = :tid"),
            {"tid": _BOT_CHANNEL_ID},
        )
        row = result.fetchone()

    max_posts = row[0] if row else "?"
    interval = str(channel_config.fetch_interval_minutes)
    threshold = str(channel_config.screening_threshold)
    gen_model = channel_config.generation_model
    screen_model = channel_config.screening_model
    reason_model = channel_config.reasoning_model or channel_config.generation_model

    text = (
        "⚙️ <b>Настройки бота</b>\n\n"
        f"⏱ Интервал цикла: <b>{interval} мин</b>\n"
        f"📝 Лимит постов/день: <b>{max_posts}</b>\n"
        f"🎯 Screening threshold: <b>{threshold}</b>\n\n"
        f"🤖 <b>Модели:</b>\n"
        f"  Screening: <code>{screen_model}</code>\n"
        f"  Reasoning: <code>{reason_model}</code>\n"
        f"  Generation: <code>{gen_model}</code>\n"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ Интервал", callback_data="set:interval"),
                InlineKeyboardButton(text="📝 Лимит", callback_data="set:limit"),
            ],
            [
                InlineKeyboardButton(text="🎯 Threshold", callback_data="set:threshold"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="set:refresh"),
            ],
        ]
    )

    return text, kb


@commands_router.callback_query(F.data == "set:refresh")
async def cb_settings_refresh(callback: CallbackQuery) -> None:
    """Refresh settings display."""
    text, kb = await _build_settings_view()
    await _edit_or_answer(callback, text, kb)


@commands_router.callback_query(F.data == "set:interval")
async def cb_set_interval(callback: CallbackQuery) -> None:
    """Change fetch interval."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10 мин", callback_data="set:interval:10"),
                InlineKeyboardButton(text="20 мин", callback_data="set:interval:20"),
                InlineKeyboardButton(text="30 мин", callback_data="set:interval:30"),
            ],
            [
                InlineKeyboardButton(text="45 мин", callback_data="set:interval:45"),
                InlineKeyboardButton(text="60 мин", callback_data="set:interval:60"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="set:refresh")],
        ]
    )
    await _edit_or_answer(
        callback, "⏱ <b>Выбери интервал цикла:</b>\n\nЧаще = больше токенов, быстрее находит контент.", kb
    )


@commands_router.callback_query(F.data.startswith("set:interval:"))
async def cb_set_interval_value(callback: CallbackQuery) -> None:
    """Apply interval change."""
    value = int(callback.data.split(":")[2])  # type: ignore[union-attr]
    # Mutate frozen Pydantic settings in-place so running orchestrator picks up the change
    object.__setattr__(settings.channel, "fetch_interval_minutes", value)
    await callback.answer(f"✅ Интервал: {value} мин (применится при следующем цикле)", show_alert=True)
    text, kb = await _build_settings_view()
    await _edit_or_answer(callback, text, kb)


@commands_router.callback_query(F.data == "set:limit")
async def cb_set_limit(callback: CallbackQuery) -> None:
    """Change daily post limit."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="3", callback_data="set:limit:3"),
                InlineKeyboardButton(text="5", callback_data="set:limit:5"),
                InlineKeyboardButton(text="10", callback_data="set:limit:10"),
            ],
            [
                InlineKeyboardButton(text="20", callback_data="set:limit:20"),
                InlineKeyboardButton(text="∞ Безлимит", callback_data="set:limit:9999"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="set:refresh")],
        ]
    )
    await _edit_or_answer(callback, "📝 <b>Лимит постов в день:</b>", kb)


@commands_router.callback_query(F.data.startswith("set:limit:"))
async def cb_set_limit_value(callback: CallbackQuery) -> None:
    """Apply limit change to DB."""
    value = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        await session.execute(
            sa_text("UPDATE channels SET max_posts_per_day = :val WHERE telegram_id = :tid"),
            {"val": value, "tid": _BOT_CHANNEL_ID},
        )
        await session.commit()

    label = "∞" if value >= 9999 else str(value)
    await callback.answer(f"✅ Лимит: {label} постов/день", show_alert=True)
    text, kb = await _build_settings_view()
    await _edit_or_answer(callback, text, kb)


@commands_router.callback_query(F.data == "set:threshold")
async def cb_set_threshold(callback: CallbackQuery) -> None:
    """Change screening threshold."""
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="5 (мягкий)", callback_data="set:threshold:5"),
                InlineKeyboardButton(text="6", callback_data="set:threshold:6"),
            ],
            [
                InlineKeyboardButton(text="7 (стандарт)", callback_data="set:threshold:7"),
                InlineKeyboardButton(text="8 (строгий)", callback_data="set:threshold:8"),
            ],
            [
                InlineKeyboardButton(text="9 (очень строгий)", callback_data="set:threshold:9"),
            ],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="set:refresh")],
        ]
    )
    await _edit_or_answer(
        callback,
        "🎯 <b>Screening threshold:</b>\n\n"
        "Чем выше — тем строже фильтр контента.\n"
        "7 = стандарт, 8+ = только самое релевантное.",
        kb,
    )


@commands_router.callback_query(F.data.startswith("set:threshold:"))
async def cb_set_threshold_value(callback: CallbackQuery) -> None:
    """Apply threshold change."""
    value = int(callback.data.split(":")[2])  # type: ignore[union-attr]
    # Mutate frozen Pydantic settings in-place so running orchestrator picks up the change
    object.__setattr__(settings.channel, "screening_threshold", value)
    await callback.answer(f"✅ Threshold: {value} (применится при следующем цикле)", show_alert=True)
    text, kb = await _build_settings_view()
    await _edit_or_answer(callback, text, kb)


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────


async def _edit_or_answer(
    callback: CallbackQuery,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the callback message or send a new one."""
    try:
        if isinstance(callback.message, TgMessage):
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.answer(text[:200], show_alert=True)
    except Exception:
        # If edit fails (message not modified), just answer the callback
        await callback.answer()


# ──────────────────────────────────────────────
#  /healthcheck — System diagnostics
# ──────────────────────────────────────────────


@commands_router.message(Command("healthcheck", "health", "status"))
async def cmd_healthcheck(message: Message) -> None:
    """Run health checks and report system status."""
    from app.core.container import container
    from app.core.healthcheck import run_healthcheck

    wait_msg = await message.answer("🔍 Проверяю...")
    report = await run_healthcheck(container.get_session_maker())
    await wait_msg.edit_text(report.format_telegram(), parse_mode="HTML")
