from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import and_

from app.infrastructure.db.models import Message
from app.infrastructure.db.repositories.base import BaseRepository


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def save(self, message: Message) -> Message:
        result = await self.db.execute(
            select(Message).where(
                and_(
                    Message.chat_id == message.chat_id,
                    Message.user_id == message.user_id,
                    Message.message_id == message.message_id,
                )
            )
        )
        existing = result.scalars().first()
        if existing:
            existing.message = message.message
            existing.message_info = message.message_info or {}
            existing.spam = message.spam
        else:
            new_message = Message(
                chat_id=message.chat_id,
                user_id=message.user_id,
                message_id=message.message_id,
                message=message.message,
                message_info=message.message_info or {},
                spam=message.spam,
            )
            self.db.add(new_message)
        await self.db.commit()
        return message

    async def add_message(
        self,
        chat_id: int,
        user_id: int,
        message_id: int,
        message: str | None,
        message_info: dict[str, Any],
    ) -> None:
        await self.db.execute(
            insert(Message).values(
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
                message=message,
                message_info=message_info,
            )
        )
        await self.db.commit()

    async def label_spam(self, chat_id: int, message_id: int) -> None:
        await self.db.execute(
            update(Message).where(and_(Message.chat_id == chat_id, Message.message_id == message_id)).values(spam=True)
        )
        await self.db.commit()

    async def get_user_messages(self, user_id: int, chat_id: int | None = None) -> list[Message]:
        query = select(Message).where(Message.user_id == user_id)
        if chat_id is not None:
            query = query.where(Message.chat_id == chat_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_spam_messages(self, limit: int | None = None) -> list[Message]:
        query = select(Message).where(Message.spam)
        if limit:
            query = query.limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def delete_user_messages(self, user_id: int, chat_id: int | None = None) -> int:
        query = delete(Message).where(Message.user_id == user_id)
        if chat_id is not None:
            query = query.where(Message.chat_id == chat_id)
        cursor = await self.db.execute(query)
        await self.db.commit()
        return cursor.rowcount or 0  # type: ignore[attr-defined]

    async def count_user_chats(self, user_id: int) -> int:
        result = await self.db.execute(
            select(func.count(func.distinct(Message.chat_id))).where(Message.user_id == user_id)
        )
        return result.scalar() or 0

    async def count_user_messages(self, user_id: int) -> int:
        result = await self.db.execute(select(func.count()).where(Message.user_id == user_id))
        return result.scalar() or 0

    async def has_previous_messages(self, chat_id: int, user_id: int) -> bool:
        result = await self.db.execute(
            select(func.count()).where(Message.user_id == user_id, Message.chat_id == chat_id)
        )
        count = result.scalar()
        return count is not None and count > 0

    async def is_first_message(self, chat_id: int, user_id: int) -> bool:
        return not await self.has_previous_messages(chat_id, user_id)

    async def is_similar_spam_message(self, message: str) -> bool:
        result = await self.db.execute(select(func.count()).where(Message.message == message, Message.spam))
        count = result.scalar()
        return count is not None and count > 0


def get_message_repository(db: AsyncSession) -> MessageRepository:
    return MessageRepository(db)
