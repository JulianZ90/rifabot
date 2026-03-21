"""Tests for numbered raffle functionality."""
from decimal import Decimal

import pytest

from core.rifa_service import (
    crear_rifa,
    crear_ticket,
    get_numeros_ocupados,
    numero_disponible,
    confirmar_tickets_gratis,
)
from db.models import EstadoTicket

SERVER_ID = "111222333"


# ─────────────────────────────────────────────
# crear_rifa con numerada
# ─────────────────────────────────────────────

async def test_crear_rifa_numerada(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Numerada", "", Decimal("100"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    assert rifa.es_numerada is True
    assert rifa.numero_desde == 1
    assert rifa.numero_hasta == 60


async def test_crear_rifa_numerada_sin_rango_falla(session):
    with pytest.raises(ValueError, match="requiere"):
        await crear_rifa(
            session, SERVER_ID, "Numerada", "", Decimal("100"),
            es_numerada=True,
        )


async def test_crear_rifa_numerada_rango_invertido_falla(session):
    with pytest.raises(ValueError, match="menor o igual"):
        await crear_rifa(
            session, SERVER_ID, "Numerada", "", Decimal("100"),
            es_numerada=True, numero_desde=60, numero_hasta=1,
        )


async def test_crear_rifa_no_numerada_no_requiere_rango(session):
    rifa = await crear_rifa(session, SERVER_ID, "Normal", "", Decimal("50"))
    assert rifa.es_numerada is False
    assert rifa.numero_desde is None
    assert rifa.numero_hasta is None


# ─────────────────────────────────────────────
# crear_ticket con números
# ─────────────────────────────────────────────

async def test_crear_ticket_numerado(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    tickets = await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[7])
    assert len(tickets) == 1
    assert tickets[0].numero_ticket == 7


async def test_crear_ticket_varios_numeros(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=0, numero_hasta=99,
    )
    tickets = await crear_ticket(session, rifa.id, 3, plataforma_uid="u1", numeros=[5, 10, 15])
    numeros = {t.numero_ticket for t in tickets}
    assert numeros == {5, 10, 15}


async def test_crear_ticket_numerado_sin_numeros_falla(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    with pytest.raises(ValueError, match="numerada"):
        await crear_ticket(session, rifa.id, 1, plataforma_uid="u1")


async def test_crear_ticket_numero_fuera_de_rango_falla(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=10,
    )
    with pytest.raises(ValueError, match="fuera del rango"):
        await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[99])


async def test_crear_ticket_numero_ya_ocupado_falla(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[42])

    with pytest.raises(ValueError, match="ya está ocupado"):
        await crear_ticket(session, rifa.id, 1, plataforma_uid="u2", numeros=[42])


async def test_numero_rechazado_queda_libre(session):
    """A ticket in 'rechazado' state does not block the number."""
    from db.models import EstadoTicket
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    tickets = await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[5])
    tickets[0].estado = EstadoTicket.rechazado

    # Now another user can take the same number
    tickets2 = await crear_ticket(session, rifa.id, 1, plataforma_uid="u2", numeros=[5])
    assert tickets2[0].numero_ticket == 5


# ─────────────────────────────────────────────
# get_numeros_ocupados
# ─────────────────────────────────────────────

async def test_get_numeros_ocupados_vacio(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    ocupados = await get_numeros_ocupados(session, rifa.id)
    assert ocupados == []


async def test_get_numeros_ocupados_incluye_pendientes_y_confirmados(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    t1 = await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[1])
    t2 = await crear_ticket(session, rifa.id, 1, plataforma_uid="u2", numeros=[2])
    await confirmar_tickets_gratis(session, t2)

    ocupados = await get_numeros_ocupados(session, rifa.id)
    assert set(ocupados) == {1, 2}


async def test_get_numeros_ocupados_excluye_rechazados(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    tickets = await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[10])
    tickets[0].estado = EstadoTicket.rechazado

    ocupados = await get_numeros_ocupados(session, rifa.id)
    assert ocupados == []


# ─────────────────────────────────────────────
# numero_disponible
# ─────────────────────────────────────────────

async def test_numero_disponible_libre(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    assert await numero_disponible(session, rifa.id, 42) is True


async def test_numero_disponible_ocupado(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
    )
    await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[42])
    assert await numero_disponible(session, rifa.id, 42) is False


# ─────────────────────────────────────────────
# Límite por persona en rifas numeradas
# ─────────────────────────────────────────────

async def test_limite_por_persona_numerada(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Test", "", Decimal("50"),
        es_numerada=True, numero_desde=1, numero_hasta=60,
        max_tickets_por_persona=2,
    )
    await crear_ticket(session, rifa.id, 2, plataforma_uid="u1", numeros=[1, 2])

    with pytest.raises(ValueError, match="Límite"):
        await crear_ticket(session, rifa.id, 1, plataforma_uid="u1", numeros=[3])
