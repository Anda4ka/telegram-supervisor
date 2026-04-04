from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.telegram_helpers import TelegramObjectFactory, create_normal_user, create_test_chat


@pytest.fixture(autouse=True)
def _clear_report_rate_limit_state():
    from app.presentation.telegram.handlers import agent_handler

    agent_handler._report_cooldowns.clear()
    agent_handler._report_dedup.clear()
    yield
    agent_handler._report_cooldowns.clear()
    agent_handler._report_dedup.clear()


@pytest.mark.handlers
class TestAgentReportRateLimits:
    async def test_duplicate_report_same_message_is_suppressed(self) -> None:
        from app.presentation.telegram.handlers.agent_handler import handle_report

        factory = TelegramObjectFactory()
        reporter = create_normal_user(id=111111, username="reporter")
        target_user = create_normal_user(id=222222, username="target")
        chat = create_test_chat()
        target = factory.create_message(message_id=555, user=target_user, chat=chat, text="spam message")
        command_message = factory.create_command_message(
            command="report",
            user=reporter,
            chat=chat,
            reply_to_message=target,
        )
        bot = AsyncMock()

        fake_settings = SimpleNamespace(admin=SimpleNamespace(default_report_chat_id=999999))

        with (
            patch("app.presentation.telegram.handlers.agent_handler.settings", fake_settings),
            patch("app.presentation.telegram.handlers.agent_handler.sleep_and_delete") as mock_sleep,
        ):
            await handle_report(command_message, bot)
            await handle_report(command_message, bot)

        bot.send_message.assert_awaited_once()
        assert command_message.answer.await_count == 2
        first_text = command_message.answer.await_args_list[0].args[0]
        second_text = command_message.answer.await_args_list[1].args[0]
        assert "Жалоба отправлена" in first_text
        assert "уже отправляли жалобу" in second_text
        assert mock_sleep.call_count == 2
        assert command_message.delete.await_count == 2

    async def test_report_cooldown_blocks_fast_second_report_for_other_message(self) -> None:
        from app.presentation.telegram.handlers.agent_handler import handle_report

        factory = TelegramObjectFactory()
        reporter = create_normal_user(id=111111, username="reporter")
        target_user = create_normal_user(id=222222, username="target")
        chat = create_test_chat()

        first_target = factory.create_message(message_id=101, user=target_user, chat=chat, text="first spam")
        second_target = factory.create_message(message_id=102, user=target_user, chat=chat, text="second spam")

        first_report = factory.create_command_message(
            command="report",
            user=reporter,
            chat=chat,
            reply_to_message=first_target,
        )
        second_report = factory.create_command_message(
            command="spam",
            user=reporter,
            chat=chat,
            reply_to_message=second_target,
        )
        bot = AsyncMock()

        fake_settings = SimpleNamespace(admin=SimpleNamespace(default_report_chat_id=999999))

        with (
            patch("app.presentation.telegram.handlers.agent_handler.settings", fake_settings),
            patch("app.presentation.telegram.handlers.agent_handler.sleep_and_delete") as mock_sleep,
        ):
            await handle_report(first_report, bot)
            await handle_report(second_report, bot)

        bot.send_message.assert_awaited_once()
        assert first_report.answer.await_count == 1
        assert second_report.answer.await_count == 1
        second_text = second_report.answer.await_args_list[0].args[0]
        assert "Слишком часто" in second_text
        assert mock_sleep.call_count == 2
        assert first_report.delete.await_count == 1
        assert second_report.delete.await_count == 1
