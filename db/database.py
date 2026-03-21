from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager
import os

_raw_url = os.getenv("DATABASE_URL", "")
# Railway provee postgresql:// pero asyncpg necesita postgresql+asyncpg://
DATABASE_URL = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    from db.models import Base
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Columnas agregadas en feature/rifas-numeradas — idempotente con IF NOT EXISTS
        migrations = [
            "ALTER TABLE rifas ADD COLUMN IF NOT EXISTS es_numerada BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE rifas ADD COLUMN IF NOT EXISTS numero_desde INTEGER",
            "ALTER TABLE rifas ADD COLUMN IF NOT EXISTS numero_hasta INTEGER",
            "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS numero_ticket INTEGER",
        ]
        for sql in migrations:
            await conn.execute(text(sql))


@asynccontextmanager
async def get_session() -> AsyncSession:
    async with async_session_maker() as session:
        async with session.begin():
            yield session
