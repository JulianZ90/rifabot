from decimal import Decimal
from datetime import datetime, timezone, timedelta

import pytest

from core.rifa_service import (
    crear_rifa,
    get_rifa,
    get_rifas_abiertas,
    get_rifas_para_sortear,
    crear_ticket,
    confirmar_tickets_gratis,
    realizar_sorteo,
    cancelar_rifa,
    get_or_create_server,
)
from db.models import EstadoRifa, EstadoTicket, PlataformaOrigen

SERVER_ID = "111222333"
SERVER_ID_2 = "444555666"


# ─────────────────────────────────────────────
# Rifas
# ─────────────────────────────────────────────

async def test_crear_rifa(session):
    rifa = await crear_rifa(session, SERVER_ID, "Premio Test", "Descripción", Decimal("100.00"))
    assert rifa.id is not None
    assert rifa.nombre == "Premio Test"
    assert rifa.estado == EstadoRifa.abierta
    assert rifa.precio_ticket == Decimal("100.00")


async def test_get_rifa(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    found = await get_rifa(session, rifa.id)
    assert found.id == rifa.id
    assert found.nombre == "Test"


async def test_get_rifa_inexistente(session):
    found = await get_rifa(session, 9999)
    assert found is None


async def test_get_rifas_abiertas(session):
    await crear_rifa(session, SERVER_ID, "Rifa A", "", Decimal("50"))
    await crear_rifa(session, SERVER_ID, "Rifa B", "", Decimal("100"))
    # Rifa de otro servidor — no debe aparecer
    await crear_rifa(session, SERVER_ID_2, "Rifa Otro", "", Decimal("50"))

    rifas = await get_rifas_abiertas(session, SERVER_ID)
    assert len(rifas) == 2
    nombres = {r.nombre for r in rifas}
    assert nombres == {"Rifa A", "Rifa B"}


async def test_get_rifas_abiertas_excluye_cerradas(session):
    rifa = await crear_rifa(session, SERVER_ID, "Abierta", "", Decimal("50"))
    cerrada = await crear_rifa(session, SERVER_ID, "Cerrada", "", Decimal("50"))
    cerrada.estado = EstadoRifa.cerrada

    rifas = await get_rifas_abiertas(session, SERVER_ID)
    assert len(rifas) == 1
    assert rifas[0].id == rifa.id


# ─────────────────────────────────────────────
# Tickets
# ─────────────────────────────────────────────

async def test_crear_ticket(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(
        session, rifa.id, 1,
        plataforma=PlataformaOrigen.discord,
        plataforma_uid="user1",
    )
    assert len(tickets) == 1
    assert tickets[0].estado == EstadoTicket.pendiente
    assert tickets[0].codigo.startswith("TK-")
    assert tickets[0].rifa_id == rifa.id


async def test_crear_ticket_multiples(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 3, plataforma_uid="user1")
    assert len(tickets) == 3
    codigos = {t.codigo for t in tickets}
    assert len(codigos) == 3  # todos únicos


async def test_crear_ticket_limite_por_persona(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"), max_tickets_por_persona=2)
    await crear_ticket(session, rifa.id, 2, plataforma_uid="user1")

    with pytest.raises(ValueError, match="Límite"):
        await crear_ticket(session, rifa.id, 1, plataforma_uid="user1")


async def test_crear_ticket_rifa_no_existe(session):
    with pytest.raises(ValueError, match="no existe"):
        await crear_ticket(session, 9999, 1)


async def test_crear_ticket_rifa_cerrada(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    rifa.estado = EstadoRifa.cerrada

    with pytest.raises(ValueError, match="no está abierta"):
        await crear_ticket(session, rifa.id, 1)


async def test_confirmar_tickets_gratis(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 2, plataforma_uid="user1")
    await confirmar_tickets_gratis(session, tickets)

    for t in tickets:
        assert t.estado == EstadoTicket.confirmado
        assert t.confirmado_at is not None


# ─────────────────────────────────────────────
# Sorteo
# ─────────────────────────────────────────────

async def test_realizar_sorteo_sin_tickets_confirmados(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    # Solo tickets pendientes
    await crear_ticket(session, rifa.id, 1, plataforma_uid="user1")

    sorteo = await realizar_sorteo(session, rifa.id)
    assert sorteo is None


async def test_realizar_sorteo_con_ganador(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 3, plataforma_uid="user1")
    await confirmar_tickets_gratis(session, tickets)

    sorteo = await realizar_sorteo(session, rifa.id)

    assert sorteo is not None
    assert sorteo.ticket_ganador_id in {t.id for t in tickets}
    # ticket_ganador debe ser accesible (bug del scheduler)
    assert sorteo.ticket_ganador is not None
    assert sorteo.ticket_ganador.codigo.startswith("TK-")

    rifa_actualizada = await get_rifa(session, rifa.id)
    assert rifa_actualizada.estado == EstadoRifa.sorteada


async def test_realizar_sorteo_solo_elige_confirmados(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    # Pendiente — no elegible
    await crear_ticket(session, rifa.id, 1, plataforma_uid="user1")
    # Confirmado — el único elegible
    tickets_conf = await crear_ticket(session, rifa.id, 1, plataforma_uid="user2")
    await confirmar_tickets_gratis(session, tickets_conf)

    sorteo = await realizar_sorteo(session, rifa.id)

    assert sorteo is not None
    assert sorteo.ticket_ganador_id == tickets_conf[0].id


async def test_realizar_sorteo_guarda_hash(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 1, plataforma_uid="user1")
    await confirmar_tickets_gratis(session, tickets)

    sorteo = await realizar_sorteo(session, rifa.id)

    assert sorteo.hash_resultado is not None
    assert sorteo.seed is not None


# ─────────────────────────────────────────────
# Scheduler: rifas para sortear
# ─────────────────────────────────────────────

async def test_get_rifas_para_sortear_vencida(session):
    rifa_vencida = await crear_rifa(
        session, SERVER_ID, "Vencida", "", Decimal("50"),
        fecha_cierre=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    # Sin fecha — no debe aparecer
    await crear_rifa(session, SERVER_ID, "Sin fecha", "", Decimal("50"))
    # Fecha futura — no debe aparecer
    await crear_rifa(
        session, SERVER_ID, "Futura", "", Decimal("50"),
        fecha_cierre=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    rifas = await get_rifas_para_sortear(session)
    assert len(rifas) == 1
    assert rifas[0].id == rifa_vencida.id


async def test_get_rifas_para_sortear_excluye_sorteadas(session):
    rifa = await crear_rifa(
        session, SERVER_ID, "Vencida", "", Decimal("50"),
        fecha_cierre=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    rifa.estado = EstadoRifa.sorteada

    rifas = await get_rifas_para_sortear(session)
    assert len(rifas) == 0


# ─────────────────────────────────────────────
# Cancelar rifa
# ─────────────────────────────────────────────

async def test_cancelar_rifa(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 2, plataforma_uid="user1")

    ok = await cancelar_rifa(session, rifa.id, SERVER_ID)
    assert ok is True

    rifa_actualizada = await get_rifa(session, rifa.id)
    assert rifa_actualizada.estado == EstadoRifa.cancelada
    for t in tickets:
        assert t.estado == EstadoTicket.rechazado


async def test_cancelar_rifa_servidor_incorrecto(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))

    ok = await cancelar_rifa(session, rifa.id, SERVER_ID_2)
    assert ok is False

    rifa_actualizada = await get_rifa(session, rifa.id)
    assert rifa_actualizada.estado == EstadoRifa.abierta


async def test_cancelar_rifa_inexistente(session):
    ok = await cancelar_rifa(session, 9999, SERVER_ID)
    assert ok is False


async def test_cancelar_rifa_ya_sorteada(session):
    rifa = await crear_rifa(session, SERVER_ID, "Test", "", Decimal("50"))
    tickets = await crear_ticket(session, rifa.id, 1, plataforma_uid="user1")
    await confirmar_tickets_gratis(session, tickets)
    await realizar_sorteo(session, rifa.id)

    with pytest.raises(ValueError, match="ya fue sorteada"):
        await cancelar_rifa(session, rifa.id, SERVER_ID)
