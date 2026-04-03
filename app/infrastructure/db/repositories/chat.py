from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Chat
from app.infrastructure.db.repositories.base import BaseRepository


class ChatRepository(BaseRepository[Chat]):
    model = Chat

    async def get_all(self) -> list[Chat]:
        result = await self.db.execute(select(Chat))
        return list(result.scalars().all())

    async def save(self, chat: Chat) -> Chat:
        existing = await self.get_by_id(chat.id)
        if existing:
            existing.title = chat.title
            existing.is_forum = chat.is_forum
            existing.welcome_message = chat.welcome_message
            existing.time_delete = chat.time_delete
            existing.is_welcome_enabled = chat.is_welcome_enabled
            existing.is_captcha_enabled = chat.is_captcha_enabled
            chat_model = existing
        else:
            chat_model = Chat(
                id=chat.id,
                title=chat.title,
                is_forum=chat.is_forum,
                welcome_message=chat.welcome_message,
                time_delete=chat.time_delete,
                is_welcome_enabled=chat.is_welcome_enabled,
                is_captcha_enabled=chat.is_captcha_enabled,
            )
            self.db.add(chat_model)
        await self.db.commit()
        await self.db.refresh(chat_model)
        return chat_model

    async def get_chat(self, id_tg_chat: int) -> Chat | None:
        return await self.get_by_id(id_tg_chat)

    async def get_chats(self) -> list[Chat]:
        return await self.get_all()

    async def merge_chat(self, id_tg_chat: int, title: str | None = None, is_forum: bool | None = None) -> None:
        existing = await self.get_by_id(id_tg_chat)
        if existing:
            if title is not None:
                existing.title = title
            if is_forum is not None:
                existing.is_forum = is_forum
        else:
            chat_model = Chat(id=id_tg_chat, title=title, is_forum=is_forum or False)
            self.db.add(chat_model)
        await self.db.commit()

    async def update_welcome_message(self, id_tg_chat: int, message: str) -> None:
        await self.db.execute(update(Chat).filter(Chat.id == id_tg_chat).values(welcome_message=message))
        await self.db.commit()


def get_chat_repository(db: AsyncSession) -> ChatRepository:
    return ChatRepository(db)
