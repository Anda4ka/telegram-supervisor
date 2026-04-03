"""One-time Telethon auth script. Creates session file interactively."""
import asyncio
from telethon import TelegramClient

API_ID = 31862157
API_HASH = "ad2b6ad5e7e36ff4e91cc8b7feda09b3"
SESSION_NAME = "moderator_userbot"

async def main():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"\nАвторизация успешна! Аккаунт: {me.first_name} (@{me.username})")
    print(f"Файл сессии создан: {SESSION_NAME}.session")
    await client.disconnect()

asyncio.run(main())
