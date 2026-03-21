from decimal import Decimal

import pytest

from core.rifa_service import (
    get_admin_by_email,
    get_rifas_admin,
    get_web_server_id,
    crear_rifa,
)
from db.models import AdminUser, EstadoRifa


ADMIN_EMAIL = "admin@test.com"
OTHER_EMAIL = "otro@test.com"


# ─────────────────────────────────────────────
# get_web_server_id
# ─────────────────────────────────────────────

def test_get_web_server_id():
    assert get_web_server_id("yo@gmail.com") == "web:yo@gmail.com"


# ─────────────────────────────────────────────
# get_admin_by_email
# ─────────────────────────────────────────────

async def test_get_admin_by_email_encontrado(session):
    session.add(AdminUser(email=ADMIN_EMAIL, activo=True))
    await session.flush()

    admin = await get_admin_by_email(session, ADMIN_EMAIL)
    assert admin is not None
    assert admin.email == ADMIN_EMAIL


async def test_get_admin_by_email_no_existe(session):
    admin = await get_admin_by_email(session, "noexiste@test.com")
    assert admin is None


async def test_get_admin_by_email_inactivo(session):
    session.add(AdminUser(email=ADMIN_EMAIL, activo=False))
    await session.flush()

    admin = await get_admin_by_email(session, ADMIN_EMAIL)
    assert admin is None


# ─────────────────────────────────────────────
# get_rifas_admin
# ─────────────────────────────────────────────

async def test_get_rifas_admin_vacio(session):
    session.add(AdminUser(email=ADMIN_EMAIL, activo=True))
    await session.flush()

    rifas = await get_rifas_admin(session, ADMIN_EMAIL)
    assert rifas == []


async def test_get_rifas_admin_retorna_propias(session):
    server_id = get_web_server_id(ADMIN_EMAIL)
    await crear_rifa(session, server_id, "Rifa A", "", Decimal("100"))
    await crear_rifa(session, server_id, "Rifa B", "", Decimal("50"))

    rifas = await get_rifas_admin(session, ADMIN_EMAIL)
    assert len(rifas) == 2
    nombres = {r.nombre for r in rifas}
    assert nombres == {"Rifa A", "Rifa B"}


async def test_get_rifas_admin_no_incluye_otros(session):
    await crear_rifa(session, get_web_server_id(ADMIN_EMAIL), "Mía", "", Decimal("100"))
    await crear_rifa(session, get_web_server_id(OTHER_EMAIL), "Ajena", "", Decimal("100"))

    rifas = await get_rifas_admin(session, ADMIN_EMAIL)
    assert len(rifas) == 1
    assert rifas[0].nombre == "Mía"


async def test_get_rifas_admin_incluye_todos_estados(session):
    server_id = get_web_server_id(ADMIN_EMAIL)
    rifa_abierta = await crear_rifa(session, server_id, "Abierta", "", Decimal("100"))
    rifa_cerrada = await crear_rifa(session, server_id, "Cerrada", "", Decimal("100"))
    rifa_cerrada.estado = EstadoRifa.cerrada

    rifas = await get_rifas_admin(session, ADMIN_EMAIL)
    estados = {r.estado for r in rifas}
    assert EstadoRifa.abierta in estados
    assert EstadoRifa.cerrada in estados
