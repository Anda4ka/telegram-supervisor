"""Dump recent posts from the target channel for style analysis."""
import asyncio
import os
import sys
import shutil

sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()


async def main():
    from telethon import TelegramClient

    api_id = int(os.environ["TELETHON_API_ID"])
    api_hash = os.environ["TELETHON_API_HASH"]

    session = "dump_tmp"
    src = os.environ.get("TELETHON_SESSION_NAME", "moderator_userbot")
    if os.path.exists(f"{src}.session"):
        shutil.copy2(f"{src}.session", f"{session}.session")

    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    entity = await client.get_entity(-1001952807891)
    print(f"Channel: {entity.title}\n")

    count = 0
    async for msg in client.iter_messages(entity, limit=30):
        if not msg.text or len(msg.text.strip()) < 20:
            continue
        count += 1
        date = msg.date.strftime("%Y-%m-%d %H:%M") if msg.date else "?"
        views = msg.views or 0
        reactions = 0
        if msg.reactions:
            for r in msg.reactions.results:
                reactions += r.count

        print(f"{'='*80}")
        print(f"#{count} | {date} | views: {views} | reactions: {reactions}")
        print(f"{'='*80}")
        print(msg.text[:2000])
        print()

    await client.disconnect()
    os.remove(f"{session}.session")


if __name__ == "__main__":
    asyncio.run(main())
