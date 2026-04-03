from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import ChatLink
from app.infrastructure.db.repositories.base import BaseRepository


class ChatLinkRepository(BaseRepository[ChatLink]):
    model = ChatLink

    async def get_all(self) -> list[ChatLink]:
        result = await self.db.execute(select(ChatLink).order_by(ChatLink.priority.desc()))
        return list(result.scalars().all())

    async def save(self, chat_link: ChatLink) -> ChatLink:
        if chat_link.id:
            existing = await self.db.get(ChatLink, chat_link.id)
            if existing:
                existing.text = chat_link.text
                existing.link = chat_link.link
                existing.priority = chat_link.priority
            else:
                raise ValueError(f"ChatLink with id {chat_link.id} not found")
        else:
            existing = None
            chat_link_model = ChatLink(text=chat_link.text, link=chat_link.link, priority=chat_link.priority)
            self.db.add(chat_link_model)
        await self.db.commit()
        if chat_link.id and existing:
            await self.db.refresh(existing)
            return existing
        await self.db.refresh(chat_link_model)
        return chat_link_model

    async def delete(self, link_id: int) -> None:
        await self.db.execute(delete(ChatLink).where(ChatLink.id == link_id))
        await self.db.commit()

    async def get_chat_links(self) -> Sequence[ChatLink]:
        return await self.get_all()


def get_chat_link_repository(db: AsyncSession) -> ChatLinkRepository:
    return ChatLinkRepository(db)
