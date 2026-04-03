from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Admin
from app.infrastructure.db.repositories.base import BaseRepository


class AdminRepository(BaseRepository[Admin]):
    model = Admin

    async def save(self, admin: Admin) -> Admin:
        existing = await self.get_by_id(admin.id)
        if existing:
            existing.state = admin.state
            admin_model = existing
        else:
            admin_model = Admin(id=admin.id, state=admin.state)
            self.db.add(admin_model)
        await self.db.commit()
        await self.db.refresh(admin_model)
        return admin_model

    async def delete(self, admin_id: int) -> None:
        await self.db.execute(delete(Admin).where(Admin.id == admin_id))
        await self.db.commit()

    async def is_admin(self, user_id: int) -> bool:
        result = await self.db.execute(select(Admin).where(Admin.id == user_id).where(Admin.state))
        return result.scalars().first() is not None

    async def get_all_active(self) -> list[Admin]:
        result = await self.db.execute(select(Admin).filter(Admin.state))
        return list(result.scalars().all())

    async def get_db_admins(self) -> list[Admin]:
        result = await self.db.execute(select(Admin))
        return list(result.scalars().all())

    async def insert_admin(self, id_tg: int) -> None:
        await self.db.execute(insert(Admin).values(id=id_tg))
        await self.db.commit()

    async def delete_admin(self, id_tg: int) -> None:
        await self.delete(id_tg)


def get_admin_repository(db: AsyncSession) -> AdminRepository:
    return AdminRepository(db)
