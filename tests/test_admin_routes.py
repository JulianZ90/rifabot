import json
import urllib.parse
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
from core.rifa_service import crear_rifa, get_rifas_admin, get_web_server_id, get_mp_token

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
TEST_SECRET = "test-session-secret-key"
ADMIN_EMAIL = "admin@test.com"
ADMIN_USER_DATA = {
    "email": ADMIN_EMAIL,
    "name": "Admin Test",
    "picture": None,
    "provider": "google",
}


def make_session_cookie(data: dict, secret: str = TEST_SECRET) -> str:
    """Crea una cookie de sesión válida para Starlette SessionMiddleware.
    Formato: signer.sign(b64encode(json_bytes)).decode() — igual a como lo escribe Starlette.
    """
    signer = TimestampSigner(secret)
    inner = b64encode(json.dumps(data).encode("utf-8"))
    signed = signer.sign(inner)
    return signed.decode("utf-8")


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
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as s:
        async with s.begin():
            yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_in_db(db_session):
    db_session.add(AdminUser(email=ADMIN_EMAIL, nombre="Admin Test", activo=True))
    await db_session.flush()


@pytest_asyncio.fixture
async def client(db_session):
    from webhooks.mp_webhook import app
    mock = mock_get_session_for(db_session)
    with patch("web.admin_routes.get_session", mock), \
         patch("web.routes.get_session", mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c


def admin_cookie() -> str:
    return make_session_cookie({"oauth_user": ADMIN_USER_DATA, "is_admin": True})


def user_cookie() -> str:
    return make_session_cookie({"oauth_user": ADMIN_USER_DATA})  # sin is_admin


# ─────────────────────────────────────────────
# Acceso sin autenticación
# ─────────────────────────────────────────────

async def test_rifas_sin_sesion_redirige_a_login(client):
    r = await client.get("/admin/rifas", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/admin/login"


async def test_nueva_rifa_sin_sesion_redirige_a_login(client):
    r = await client.get("/admin/nueva-rifa", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/admin/login"


async def test_configuracion_sin_sesion_redirige_a_login(client):
    r = await client.get("/admin/configuracion", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/admin/login"


async def test_admin_index_redirige_a_rifas(client):
    r = await client.get("/admin", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "rifas" in r.headers["location"]


# ─────────────────────────────────────────────
# Usuario sin rol admin
# ─────────────────────────────────────────────

async def test_usuario_sin_is_admin_redirige_a_login(client):
    r = await client.get("/admin/rifas", cookies={"rifabot_session": user_cookie()}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/admin/login"


# ─────────────────────────────────────────────
# Login page
# ─────────────────────────────────────────────

async def test_login_page_muestra_boton_google(client):
    r = await client.get("/admin/login")
    assert r.status_code == 200
    assert "Continuar con Google" in r.text


async def test_login_page_redirige_si_ya_autenticado(client, admin_in_db):
    r = await client.get("/admin/login", cookies={"rifabot_session": admin_cookie()}, follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert "rifas" in r.headers["location"]


# ─────────────────────────────────────────────
# Dashboard de rifas
# ─────────────────────────────────────────────

async def test_dashboard_admin_sin_rifas(client, admin_in_db):
    r = await client.get("/admin/rifas", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Mis Rifas" in r.text


async def test_dashboard_muestra_rifas_del_admin(client, admin_in_db, db_session):
    await crear_rifa(db_session, get_web_server_id(ADMIN_EMAIL), "Gran Premio", "", Decimal("500"))

    r = await client.get("/admin/rifas", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Gran Premio" in r.text


async def test_dashboard_no_muestra_rifas_de_otros(client, admin_in_db, db_session):
    await crear_rifa(db_session, get_web_server_id("otro@test.com"), "Rifa Ajena", "", Decimal("100"))

    r = await client.get("/admin/rifas", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Rifa Ajena" not in r.text


# ─────────────────────────────────────────────
# Crear rifa
# ─────────────────────────────────────────────

async def test_nueva_rifa_form_renderiza(client, admin_in_db):
    r = await client.get("/admin/nueva-rifa", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "Nueva rifa" in r.text


async def test_crear_rifa_exitoso(client, admin_in_db, db_session):
    r = await client.post(
        "/admin/nueva-rifa",
        data={"nombre": "Sorteo Mayo", "descripcion": "Premio TV", "precio": "1000", "max_tickets": "5"},
        cookies={"rifabot_session": admin_cookie()},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/rifas"

    rifas = await get_rifas_admin(db_session, ADMIN_EMAIL)
    assert len(rifas) == 1
    assert rifas[0].nombre == "Sorteo Mayo"


async def test_crear_rifa_precio_invalido(client, admin_in_db):
    r = await client.post(
        "/admin/nueva-rifa",
        data={"nombre": "Test", "precio": "no-es-un-numero", "max_tickets": "5"},
        cookies={"rifabot_session": admin_cookie()},
    )
    assert r.status_code == 422
    assert "Precio inválido" in r.text


# ─────────────────────────────────────────────
# Cancelar rifa
# ─────────────────────────────────────────────

async def test_cancelar_rifa_propia(client, admin_in_db, db_session):
    rifa = await crear_rifa(db_session, get_web_server_id(ADMIN_EMAIL), "Mi Rifa", "", Decimal("100"))

    r = await client.post(
        f"/admin/rifa/{rifa.id}/cancelar",
        cookies={"rifabot_session": admin_cookie()},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from db.models import EstadoRifa
    rifas = await get_rifas_admin(db_session, ADMIN_EMAIL)
    assert rifas[0].estado == EstadoRifa.cancelada


async def test_cancelar_rifa_de_otro_admin_falla(client, admin_in_db, db_session):
    rifa = await crear_rifa(db_session, get_web_server_id("otro@test.com"), "Rifa Ajena", "", Decimal("100"))

    r = await client.post(
        f"/admin/rifa/{rifa.id}/cancelar",
        cookies={"rifabot_session": admin_cookie()},
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────
# Configuración MercadoPago
# ─────────────────────────────────────────────

async def test_configuracion_page_renderiza(client, admin_in_db):
    r = await client.get("/admin/configuracion", cookies={"rifabot_session": admin_cookie()})
    assert r.status_code == 200
    assert "MercadoPago" in r.text


async def test_guardar_token_mp(client, admin_in_db, db_session):
    r = await client.post(
        "/admin/configuracion",
        data={"mp_token": "APP_USR-test-token-12345"},
        cookies={"rifabot_session": admin_cookie()},
    )
    assert r.status_code == 200
    assert "guardado" in r.text.lower()

    token = await get_mp_token(db_session, get_web_server_id(ADMIN_EMAIL))
    assert token == "APP_USR-test-token-12345"


async def test_guardar_token_mp_vacio_falla(client, admin_in_db):
    r = await client.post(
        "/admin/configuracion",
        data={"mp_token": "   "},
        cookies={"rifabot_session": admin_cookie()},
    )
    assert r.status_code == 422
    assert "vacío" in r.text
