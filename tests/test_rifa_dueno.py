"""Tests for admin ownership restriction on raffle landing page."""
import json
from base64 import b64encode
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from itsdangerous import TimestampSigner
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from db.models import Base, AdminUser
from core.rifa_service import crear_rifa, get_web_server_id

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
TEST_SECRET = "test-session-secret-key"
ADMIN_EMAIL = "admin@test.com"
OTRO_EMAIL = "otro@test.com"


def make_session_cookie(data: dict) -> str:
    signer = TimestampSigner(TEST_SECRET)
    inner = b64encode(json.dumps(data).encode("utf-8"))
    return signer.sign(inner).decode("utf-8")


def mock_get_session_for(session):
    @asynccontextmanager
    async def _get_session():
        yield session
    return _get_session


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        async with s.begin():
            yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from webhooks.mp_webhook import app
    mock = mock_get_session_for(db_session)
    with patch("web.admin_routes.get_session", mock), \
         patch("web.routes.get_session", mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest_asyncio.fixture
async def admin_in_db(db_session):
    db_session.add(AdminUser(email=ADMIN_EMAIL, nombre="Admin", activo=True))
    await db_session.flush()


def admin_cookie():
    return make_session_cookie({
        "oauth_user": {"email": ADMIN_EMAIL, "name": "Admin", "provider": "google"},
        "is_admin": True,
    })


def user_cookie(email=OTRO_EMAIL):
    return make_session_cookie({
        "oauth_user": {"email": email, "name": "Otro", "provider": "google"},
    })


# ─────────────────────────────────────────────
# GET /rifa/{id} — vista según rol
# ─────────────────────────────────────────────

async def test_dueno_ve_banner_sin_formulario(client, admin_in_db, db_session):
    rifa = await crear_rifa(
        db_session, get_web_server_id(ADMIN_EMAIL), "Mi Rifa", "", Decimal("100")
    )
    r = await client.get(f"/rifa/{rifa.id}", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Esta es tu rifa" in r.text
    assert "participar" not in r.text.lower() or "Ir al panel" in r.text


async def test_usuario_normal_ve_formulario(client, admin_in_db, db_session):
    rifa = await crear_rifa(
        db_session, get_web_server_id(ADMIN_EMAIL), "Mi Rifa", "", Decimal("100")
    )
    r = await client.get(f"/rifa/{rifa.id}", cookies={"rifabot_session": user_cookie()})
    assert r.status_code == 200
    assert "Esta es tu rifa" not in r.text


async def test_admin_rifa_ajena_ve_formulario(client, db_session):
    """An admin visiting someone else's raffle can still participate."""
    rifa = await crear_rifa(
        db_session, get_web_server_id(OTRO_EMAIL), "Rifa Ajena", "", Decimal("100")
    )
    r = await client.get(f"/rifa/{rifa.id}", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Esta es tu rifa" not in r.text


# ─────────────────────────────────────────────
# POST /rifa/{id}/participar — bloqueo en backend
# ─────────────────────────────────────────────

async def test_dueno_no_puede_comprar_via_post(client, admin_in_db, db_session):
    rifa = await crear_rifa(
        db_session, get_web_server_id(ADMIN_EMAIL), "Mi Rifa", "", Decimal("0")
    )
    r = await client.post(
        f"/rifa/{rifa.id}/participar",
        data={"cantidad": "1"},
        cookies={"rifabot_session": admin_cookie()},
        follow_redirects=False,
    )
    # Should redirect back to the rifa page, not to /pago/exito
    assert r.status_code == 303
    assert f"/rifa/{rifa.id}" in r.headers["location"]
