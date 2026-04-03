"""List all Telegram channels, groups, and forums from the userbot account."""

import asyncio
import os
import sys

# Fix Windows encoding
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv

load_dotenv()


async def main():
    from telethon import TelegramClient
    from telethon.tl.types import Channel, Chat, User

    api_id = int(os.environ["TELETHON_API_ID"])
    api_hash = os.environ["TELETHON_API_HASH"]
    import shutil
    src_session = os.environ.get("TELETHON_SESSION_NAME", "moderator_userbot")
    session = "list_channels_tmp"
    # Copy session file to avoid lock
    if os.path.exists(f"{src_session}.session"):
        shutil.copy2(f"{src_session}.session", f"{session}.session")

    client = TelegramClient(session, api_id, api_hash)
    await client.start()

    print("=" * 90)
    print(f"{'#':<4} {'Type':<12} {'ID':<16} {'Username':<25} {'Title'}")
    print("=" * 90)

    channels = []
    groups = []
    forums = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        if isinstance(entity, Channel):
            is_forum = getattr(entity, 'forum', False)
            is_megagroup = getattr(entity, 'megagroup', False)
            is_broadcast = getattr(entity, 'broadcast', False)
            username = f"@{entity.username}" if entity.username else ""
            members = getattr(entity, 'participants_count', None) or ""

            entry = {
                "id": entity.id,
                "title": dialog.title or "",
                "username": username,
                "members": members,
                "is_forum": is_forum,
                "is_megagroup": is_megagroup,
                "is_broadcast": is_broadcast,
            }

            if is_forum:
                forums.append(entry)
            elif is_broadcast:
                channels.append(entry)
            elif is_megagroup:
                groups.append(entry)
            else:
                channels.append(entry)

    idx = 1

    print("\n--- КАНАЛЫ (broadcast) ---")
    for e in channels:
        print(f"{idx:<4} {'channel':<12} {e['id']:<16} {e['username']:<25} {e['title'][:40]}")
        idx += 1

    print(f"\n--- ГРУППЫ (megagroup) ---")
    for e in groups:
        print(f"{idx:<4} {'group':<12} {e['id']:<16} {e['username']:<25} {e['title'][:40]}")
        idx += 1

    print(f"\n--- ФОРУМЫ (forum) ---")
    for e in forums:
        print(f"{idx:<4} {'forum':<12} {e['id']:<16} {e['username']:<25} {e['title'][:40]}")
        idx += 1

    print("=" * 90)
    print(f"Итого: {len(channels)} каналов, {len(groups)} групп, {len(forums)} форумов")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
