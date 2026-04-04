"""Startup health checks — validate external dependencies before bot starts.

Checks DB connectivity, Telegram Bot API token, OpenRouter API key,
and Telethon configuration. Returns a structured report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger("healthcheck")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks if c.name in ("database", "main_bot"))

    def format_log(self) -> str:
        lines: list[str] = []
        for c in self.checks:
            icon = "✅" if c.ok else ("⚠️" if c.name not in ("database", "main_bot") else "❌")
            lines.append(f"  {icon} {c.name}: {c.detail}")
        return "\n".join(lines)

    def format_telegram(self) -> str:
        lines: list[str] = ["<b>🏥 Health Check</b>\n"]
        for c in self.checks:
            icon = "✅" if c.ok else "⚠️"
            lines.append(f"{icon} <b>{c.name}</b>: {c.detail}")
        return "\n".join(lines)


async def check_database(session_maker: async_sessionmaker[AsyncSession]) -> CheckResult:
    """Verify database connectivity."""
    try:
        from sqlalchemy import text

        async with session_maker() as session:
            result = await session.execute(text("SELECT 1"))
            result.scalar()
        return CheckResult("database", True, f"connected ({settings.database.host}:{settings.database.port})")
    except Exception as e:
        return CheckResult("database", False, f"connection failed: {e}")


async def check_main_bot() -> CheckResult:
    """Verify moderator bot token via getMe API call."""
    from aiogram import Bot

    try:
        bot = Bot(token=settings.telegram.token)
        me = await bot.get_me()
        await bot.session.close()
        return CheckResult("main_bot", True, f"@{me.username} (ID: {me.id})")
    except Exception as e:
        return CheckResult("main_bot", False, f"invalid token: {e}")


async def check_assistant_bot() -> CheckResult:
    """Verify assistant bot token if enabled."""
    if not settings.assistant.enabled or not settings.assistant.token:
        return CheckResult("assistant_bot", True, "disabled")

    from aiogram import Bot

    try:
        bot = Bot(token=settings.assistant.token)
        me = await bot.get_me()
        await bot.session.close()
        return CheckResult("assistant_bot", True, f"@{me.username} (ID: {me.id})")
    except Exception as e:
        return CheckResult("assistant_bot", False, f"invalid token: {e}")


async def check_openrouter() -> CheckResult:
    """Verify OpenRouter API key with a minimal request."""
    if not settings.openrouter.api_key:
        ai_features = []
        if settings.channel.enabled:
            ai_features.append("channel")
        if settings.moderation.enabled:
            ai_features.append("moderation")
        if settings.assistant.enabled:
            ai_features.append("assistant")
        if ai_features:
            return CheckResult("openrouter", False, f"API key missing (needed for: {', '.join(ai_features)})")
        return CheckResult("openrouter", True, "not configured (AI features disabled)")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.openrouter.base_url}/models",
                headers={"Authorization": f"Bearer {settings.openrouter.api_key}"},
            )
            resp.raise_for_status()
        return CheckResult("openrouter", True, "API key valid")
    except Exception as e:
        return CheckResult("openrouter", False, f"API check failed: {e}")


def check_telethon() -> CheckResult:
    """Check Telethon configuration (does not connect)."""
    if not settings.telethon.enabled:
        return CheckResult("telethon", True, "disabled")
    if not settings.telethon.api_id or not settings.telethon.api_hash:
        return CheckResult("telethon", False, "enabled but TELETHON_API_ID or TELETHON_API_HASH missing")
    return CheckResult("telethon", True, f"configured (session: {settings.telethon.session_name})")


async def run_healthcheck(session_maker: async_sessionmaker[AsyncSession]) -> HealthReport:
    """Run all health checks and return a report."""
    report = HealthReport()

    report.checks.append(await check_database(session_maker))
    report.checks.append(await check_main_bot())
    report.checks.append(await check_assistant_bot())
    report.checks.append(await check_openrouter())
    report.checks.append(check_telethon())

    log_text = report.format_log()
    if report.all_ok:
        logger.info("healthcheck_passed\n%s", log_text)
    else:
        logger.error("healthcheck_failed\n%s", log_text)

    return report
