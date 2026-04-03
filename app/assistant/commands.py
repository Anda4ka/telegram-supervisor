"""Direct /command handlers for the assistant bot.

These commands execute immediately without going through the AI agent,
saving tokens and providing instant responses with inline keyboards.
"""

from __future__ import annotations

import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.core.logging import get_logger

logger = get_logger("assistant.commands")

commands_router = Router(name="assistant_commands")

# Public channel for analytics (the real channel, not the private test channel)
_PUBLIC_CHANNEL_ID = int(os.environ.get("CHANNEL_ANALYTICS_PUBLIC_ID", "-1001952807891"))
_PUBLIC_CHANNEL_NAME = "@grassfoundationn"

# Private channel where bot publishes
_BOT_CHANNEL_ID = int(os.environ.get("CHANNEL_TELEGRAM_ID", "-1002086726135"))


# ──────────────────────────────────────────────
#  /stats — Channel analytics
# ──────────────────────────────────────────────


@commands_router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show analytics overview with inline navigation."""
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
                SELECT url, title, enabled, error_count, last_fetched_at::text
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
    for url, title, enabled, errors, last_fetch in rows:
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
        buttons.append(InlineKeyboardButton(text=btn_text, callback_data=f"src:toggle:{url[:50]}:{action}"))

    # Show first 10 toggle buttons max (Telegram limit)
    kb_rows = [[b] for b in buttons[:10]]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="src:back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await _edit_or_answer(callback, "\n".join(lines), kb)


@commands_router.callback_query(F.data.startswith("src:toggle:"))
async def cb_source_toggle(callback: CallbackQuery) -> None:
    """Toggle a source on/off."""
    parts = callback.data.split(":", 3)  # type: ignore[union-attr]
    url_prefix = parts[2]
    action = parts[3]  # "on" or "off"
    new_enabled = action == "on"

    from sqlalchemy import text as sa_text

    from app.core.container import container

    sm = container.get_session_maker()
    async with sm() as session:
        await session.execute(
            sa_text("UPDATE channel_sources SET enabled = :enabled WHERE url LIKE :url"),
            {"enabled": new_enabled, "url": f"{url_prefix}%"},
        )
        await session.commit()

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
#  /settings — Bot configuration
# ──────────────────────────────────────────────


@commands_router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Show current bot settings with edit buttons."""
    text, kb = await _build_settings_view()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _build_settings_view() -> tuple[str, InlineKeyboardMarkup]:
    """Build settings message text and keyboard."""
    from app.core.container import container

    sm = container.get_session_maker()

    # Read current values from DB + env
    from sqlalchemy import text as sa_text

    async with sm() as session:
        result = await session.execute(
            sa_text("SELECT max_posts_per_day, name FROM channels WHERE telegram_id = :tid"),
            {"tid": _BOT_CHANNEL_ID},
        )
        row = result.fetchone()

    max_posts = row[0] if row else "?"
    interval = os.environ.get("CHANNEL_FETCH_INTERVAL_MINUTES", "20")
    threshold = os.environ.get("CHANNEL_SCREENING_THRESHOLD", "7")
    gen_model = os.environ.get("CHANNEL_GENERATION_MODEL", "?")
    screen_model = os.environ.get("CHANNEL_SCREENING_MODEL", "?")
    reason_model = os.environ.get("CHANNEL_REASONING_MODEL", "?")

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
    value = callback.data.split(":")[2]  # type: ignore[union-attr]
    os.environ["CHANNEL_FETCH_INTERVAL_MINUTES"] = value
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
    value = callback.data.split(":")[2]  # type: ignore[union-attr]
    os.environ["CHANNEL_SCREENING_THRESHOLD"] = value
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
        if callback.message:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.answer(text[:200], show_alert=True)
    except Exception:
        # If edit fails (message not modified), just answer the callback
        await callback.answer()
