from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UserNotFoundException
from app.infrastructure.db.models import User
from app.infrastructure.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def save(self, user: User) -> User:
        existing = await self.get_by_id(user.id)
        if existing:
            existing.username = user.username
            existing.first_name = user.first_name
            existing.last_name = user.last_name
            existing.verify = user.verify
            existing.blocked = user.blocked
            user_model = existing
        else:
            user_model = User(
                id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                verify=user.verify,
                blocked=user.blocked,
            )
            self.db.add(user_model)
        await self.db.commit()
        await self.db.refresh(user_model)
        return user_model

    async def get_blocked_users(self) -> list[User]:
        result = await self.db.execute(select(User).filter(User.blocked))
        return list(result.scalars().all())

    async def find_blocked_user(self, identifier: str) -> User | None:
        if identifier.startswith("@"):
            identifier = identifier[1:]
        if identifier.isdigit():
            result = await self.db.execute(select(User).filter(User.id == int(identifier), User.blocked))
        else:
            result = await self.db.execute(select(User).filter(User.username == identifier, User.blocked))
        return result.scalars().first()

    async def get_user(self, id_tg: int) -> User | None:
        return await self.get_by_id(id_tg)

    async def add_to_blacklist(self, id_tg: int) -> None:
        user = await self.get_by_id(id_tg)
        if user:
            await self.db.execute(update(User).where(User.id == id_tg).values(blocked=True))
        else:
            await self.db.execute(insert(User).values(id=id_tg, blocked=True))
        await self.db.commit()

    async def remove_from_blacklist(self, id_tg: int) -> None:
        user = await self.get_by_id(id_tg)
        if user:
            await self.db.execute(update(User).where(User.id == id_tg).values(blocked=False))
            await self.db.commit()
        else:
            raise UserNotFoundException(id_tg)


def get_user_repository(db: AsyncSession) -> "UserRepository":
    return UserRepository(db)
