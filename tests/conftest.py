import os
from cryptography.fernet import Fernet

# Debe estar antes de cualquier import del proyecto
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("RANDOMORG_API_KEY", "")
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-key")
os.environ.setdefault("WEBHOOK_BASE_URL", "http://test")

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from db.models import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as s:
        async with s.begin():
            yield s

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
