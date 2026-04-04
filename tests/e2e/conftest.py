"""Shared E2E test fixtures.

db_engine and db_session_maker are inherited from root conftest.py.
"""

from collections.abc import Iterator

import pytest
import pytest_asyncio
from app.presentation.telegram.handlers import agent_handler

from tests.fake_telegram import FakeTelegramServer


@pytest.fixture(autouse=True)
def reset_agent_report_caches() -> Iterator[None]:

    agent_handler._report_cooldowns.clear()
    agent_handler._report_dedup.clear()
    yield
    agent_handler._report_cooldowns.clear()
    agent_handler._report_dedup.clear()


@pytest_asyncio.fixture()
async def fake_tg():
    """Start fake Telegram server."""
    async with FakeTelegramServer() as server:
        yield server
