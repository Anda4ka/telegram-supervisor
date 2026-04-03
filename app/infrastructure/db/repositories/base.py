"""Base repository with common CRUD patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class BaseRepository[T]:
    """Base repository providing common query patterns."""

    model: type[T]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, entity_id: Any) -> T | None:
        result = await self.db.execute(select(self.model).filter(self.model.id == entity_id))  # type: ignore[attr-defined]
        return result.scalars().first()

    async def exists(self, entity_id: Any) -> bool:
        result = await self.db.execute(select(self.model.id).filter(self.model.id == entity_id))  # type: ignore[attr-defined]
        return result.scalars().first() is not None
