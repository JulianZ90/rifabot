from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import random
import string
import os

from db.models import Server, Rifa, Ticket, Sorteo, EstadoRifa, EstadoTicket, PlataformaOrigen, AdminUser
from utils.crypto import encrypt_token, decrypt_token
from utils.mp import crear_preferencia_pago
from utils.randomorg import sortear_indice


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def generar_codigo_ticket() -> str:
    """Genera un código único tipo TK-4F2A."""
    chars = string.ascii_uppercase + string.digits
    sufijo = "".join(random.choices(chars, k=4))
    return f"TK-{sufijo}"


async def codigo_disponible(session: AsyncSession, rifa_id: int, codigo: str) -> bool:
    result = await session.execute(
        select(Ticket).where(
            and_(Ticket.rifa_id == rifa_id, Ticket.codigo == codigo)
        )
    )
    return result.scalar_one_or_none() is None


async def generar_codigo_unico(session: AsyncSession, rifa_id: int) -> str:
    """Genera un código que no exista ya en la rifa."""
    for _ in range(10):
        codigo = generar_codigo_ticket()
        if await codigo_disponible(session, rifa_id, codigo):
            return codigo
    raise RuntimeError("No se pudo generar un código único. Intentá de nuevo.")


# ─────────────────────────────────────────────
# ADMIN USERS
# ─────────────────────────────────────────────

async def get_admin_by_email(session: AsyncSession, email: str) -> AdminUser | None:
    result = await session.execute(
        select(AdminUser).where(AdminUser.email == email, AdminUser.activo == True)
    )
    return result.scalar_one_or_none()


def get_web_server_id(email: str) -> str:
    return f"web:{email}"


async def get_rifas_admin(session: AsyncSession, email: str) -> list[Rifa]:
    """Todas las rifas del admin (todas las abierta y las no-abiertas)."""
    server_id = get_web_server_id(email)
    result = await session.execute(
        select(Rifa)
        .join(Server)
        .where(Server.discord_server_id == server_id)
        .options(selectinload(Rifa.tickets), selectinload(Rifa.sorteo))
        .order_by(Rifa.created_at.desc())
    )
    return result.scalars().all()


# ─────────────────────────────────────────────
# SERVERS
# ─────────────────────────────────────────────

async def get_or_create_server(session: AsyncSession, discord_server_id: str) -> Server:
    result = await session.execute(
        select(Server).where(Server.discord_server_id == discord_server_id)
    )
    server = result.scalar_one_or_none()
    if not server:
        server = Server(discord_server_id=discord_server_id)
        session.add(server)
        await session.flush()
    return server


async def configurar_mp_token(session: AsyncSession, discord_server_id: str, access_token: str) -> Server:
    server = await get_or_create_server(session, discord_server_id)
    server.mp_access_token_encrypted = encrypt_token(access_token)
    return server


async def get_mp_token(session: AsyncSession, discord_server_id: str) -> str | None:
    result = await session.execute(
        select(Server).where(Server.discord_server_id == discord_server_id)
    )
    server = result.scalar_one_or_none()
    if not server or not server.mp_access_token_encrypted:
        return None
    return decrypt_token(server.mp_access_token_encrypted)


# ─────────────────────────────────────────────
# RIFAS
# ─────────────────────────────────────────────

async def crear_rifa(
    session: AsyncSession,
    discord_server_id: str,
    nombre: str,
    descripcion: str,
    precio: Decimal,
    max_tickets_por_persona: int = 10,
    canal_id: str = None,
    fecha_cierre: datetime = None,
    es_numerada: bool = False,
    numero_desde: int = None,
    numero_hasta: int = None,
) -> Rifa:
    if es_numerada:
        if numero_desde is None or numero_hasta is None:
            raise ValueError("Una rifa numerada requiere numero_desde y numero_hasta.")
        if numero_desde > numero_hasta:
            raise ValueError("numero_desde debe ser menor o igual a numero_hasta.")
        total_numeros = numero_hasta - numero_desde + 1
        if max_tickets_por_persona > total_numeros:
            max_tickets_por_persona = total_numeros

    server = await get_or_create_server(session, discord_server_id)
    rifa = Rifa(
        server_id=server.id,
        nombre=nombre,
        descripcion=descripcion,
        precio_ticket=precio,
        max_tickets_por_persona=max_tickets_por_persona,
        canal_discord_id=canal_id,
        fecha_cierre=fecha_cierre,
        estado=EstadoRifa.abierta,
        es_numerada=es_numerada,
        numero_desde=numero_desde,
        numero_hasta=numero_hasta,
    )
    session.add(rifa)
    await session.flush()
    return rifa


async def get_rifa(session: AsyncSession, rifa_id: int) -> Rifa | None:
    result = await session.execute(
        select(Rifa)
        .options(selectinload(Rifa.tickets))
        .where(Rifa.id == rifa_id)
    )
    return result.scalar_one_or_none()


async def get_rifas_para_sortear(session: AsyncSession) -> list[Rifa]:
    """Rifas abiertas cuya fecha_cierre ya pasó — listas para sorteo automático."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Rifa)
        .options(selectinload(Rifa.tickets))
        .where(
            and_(
                Rifa.estado == EstadoRifa.abierta,
                Rifa.fecha_cierre.isnot(None),
                Rifa.fecha_cierre <= now,
            )
        )
    )
    return result.scalars().all()


async def get_rifas_abiertas(session: AsyncSession, discord_server_id: str) -> list[Rifa]:
    result = await session.execute(
        select(Rifa)
        .join(Server)
        .where(
            and_(
                Server.discord_server_id == discord_server_id,
                Rifa.estado == EstadoRifa.abierta,
            )
        )
        .options(selectinload(Rifa.tickets))
    )
    return result.scalars().all()


async def cerrar_rifa(session: AsyncSession, rifa_id: int) -> Rifa | None:
    rifa = await get_rifa(session, rifa_id)
    if not rifa:
        return None
    rifa.estado = EstadoRifa.cerrada
    rifa.cerrada_at = datetime.now(timezone.utc)
    return rifa


async def cancelar_rifa(session: AsyncSession, rifa_id: int, discord_server_id: str) -> bool:
    """Cancela una rifa lógicamente. Solo si pertenece al servidor y no fue sorteada."""
    rifa = await get_rifa(session, rifa_id)
    if not rifa:
        return False
    result = await session.execute(
        select(Server).where(Server.id == rifa.server_id)
    )
    server = result.scalar_one_or_none()
    if not server or server.discord_server_id != discord_server_id:
        return False
    if rifa.estado == EstadoRifa.sorteada:
        raise ValueError("No se puede cancelar una rifa que ya fue sorteada.")
    rifa.estado = EstadoRifa.cancelada
    rifa.cerrada_at = datetime.now(timezone.utc)
    # Marcar tickets pendientes como rechazados — consulta directa para evitar
    # dependencia en la colección cacheada rifa.tickets
    tickets_result = await session.execute(
        select(Ticket).where(
            and_(Ticket.rifa_id == rifa_id, Ticket.estado == EstadoTicket.pendiente)
        )
    )
    for ticket in tickets_result.scalars().all():
        ticket.estado = EstadoTicket.rechazado
    return True


# ─────────────────────────────────────────────
# TICKETS
# ─────────────────────────────────────────────

async def get_numeros_ocupados(session: AsyncSession, rifa_id: int) -> list[int]:
    """Returns list of ticket numbers already reserved (pendiente or confirmado)."""
    result = await session.execute(
        select(Ticket.numero_ticket).where(
            and_(
                Ticket.rifa_id == rifa_id,
                Ticket.numero_ticket.isnot(None),
                Ticket.estado.in_([EstadoTicket.pendiente, EstadoTicket.confirmado]),
            )
        )
    )
    return [row[0] for row in result.all()]


async def numero_disponible(session: AsyncSession, rifa_id: int, numero: int) -> bool:
    """Returns True if the given number is not yet taken in the rifa."""
    result = await session.execute(
        select(Ticket.id).where(
            and_(
                Ticket.rifa_id == rifa_id,
                Ticket.numero_ticket == numero,
                Ticket.estado.in_([EstadoTicket.pendiente, EstadoTicket.confirmado]),
            )
        )
    )
    return result.scalar_one_or_none() is None


async def contar_tickets_usuario(session: AsyncSession, rifa_id: int, plataforma_uid: str) -> int:
    result = await session.execute(
        select(func.count(Ticket.id)).where(
            and_(
                Ticket.rifa_id == rifa_id,
                Ticket.plataforma_uid == plataforma_uid,
                Ticket.estado.in_([EstadoTicket.pendiente, EstadoTicket.confirmado]),
            )
        )
    )
    return result.scalar() or 0


async def crear_ticket(
    session: AsyncSession,
    rifa_id: int,
    cantidad: int,
    plataforma: PlataformaOrigen = None,
    plataforma_uid: str = None,
    plataforma_handle: str = None,
    nombre_participante: str = None,
    email_participante: str = None,
    telefono_participante: str = None,
    numeros: list[int] = None,
) -> list[Ticket]:
    rifa = await get_rifa(session, rifa_id)
    if not rifa or rifa.estado != EstadoRifa.abierta:
        raise ValueError("La rifa no existe o no está abierta.")

    if rifa.es_numerada:
        if not numeros:
            raise ValueError("Esta rifa es numerada. Debés especificar los números.")
        cantidad = len(numeros)
        for n in numeros:
            if n < rifa.numero_desde or n > rifa.numero_hasta:
                raise ValueError(
                    f"El número {n} está fuera del rango "
                    f"({rifa.numero_desde}–{rifa.numero_hasta})."
                )
            if not await numero_disponible(session, rifa_id, n):
                raise ValueError(f"El número {n} ya está ocupado.")

    if plataforma_uid:
        tickets_actuales = await contar_tickets_usuario(session, rifa_id, plataforma_uid)
        if tickets_actuales + cantidad > rifa.max_tickets_por_persona:
            disponibles = rifa.max_tickets_por_persona - tickets_actuales
            raise ValueError(
                f"Límite de {rifa.max_tickets_por_persona} tickets por persona. "
                f"Ya tenés {tickets_actuales}, podés comprar {disponibles} más."
            )

    tickets = []
    for i in range(cantidad):
        codigo = await generar_codigo_unico(session, rifa_id)
        ticket = Ticket(
            rifa_id=rifa_id,
            codigo=codigo,
            plataforma=plataforma,
            plataforma_uid=plataforma_uid,
            plataforma_handle=plataforma_handle,
            nombre_participante=nombre_participante,
            email_participante=email_participante,
            telefono_participante=telefono_participante,
            estado=EstadoTicket.pendiente,
            numero_ticket=numeros[i] if numeros else None,
        )
        session.add(ticket)
        tickets.append(ticket)

    await session.flush()
    return tickets


async def asignar_link_pago(
    session: AsyncSession,
    tickets: list[Ticket],
    mp_token: str,
    webhook_base_url: str,
    rifa: Rifa,
) -> str:
    cantidad = len(tickets)
    ids_tickets = ",".join(str(t.id) for t in tickets)

    preference_id, init_point = await crear_preferencia_pago(
        access_token=mp_token,
        titulo=f"Rifa {rifa.nombre} — {cantidad} ticket{'s' if cantidad > 1 else ''}",
        precio=float(rifa.precio_ticket) * cantidad,
        external_reference=f"{rifa.id}:{ids_tickets}",
        notification_url=f"{webhook_base_url}/webhook/mp",
    )

    for ticket in tickets:
        ticket.mp_preference_id = preference_id

    return init_point


async def confirmar_tickets_gratis(session: AsyncSession, tickets: list[Ticket]) -> list[Ticket]:
    for ticket in tickets:
        ticket.estado = EstadoTicket.confirmado
        ticket.confirmado_at = datetime.now(timezone.utc)
    return tickets


async def confirmar_tickets_por_pago(
    session: AsyncSession,
    mp_payment_id: str,
    external_reference: str,
    payer_email: str | None = None,
) -> list[Ticket] | None:
    parts = external_reference.split(":")
    if len(parts) != 2:
        return None

    ids_str = parts[1].split(",")
    ticket_ids = [int(i) for i in ids_str if i.isdigit()]

    result = await session.execute(
        select(Ticket).where(
            and_(
                Ticket.id.in_(ticket_ids),
                Ticket.estado == EstadoTicket.pendiente,
            )
        )
    )
    tickets = result.scalars().all()

    for ticket in tickets:
        ticket.mp_payment_id = mp_payment_id
        ticket.mp_payer_email = payer_email
        ticket.estado = EstadoTicket.confirmado
        ticket.confirmado_at = datetime.now(timezone.utc)

    return list(tickets)


# ─────────────────────────────────────────────
# SORTEO
# ─────────────────────────────────────────────

async def realizar_sorteo(session: AsyncSession, rifa_id: int) -> Sorteo | None:
    result = await session.execute(
        select(Ticket).where(
            and_(
                Ticket.rifa_id == rifa_id,
                Ticket.estado == EstadoTicket.confirmado,
            )
        )
    )
    tickets_confirmados = result.scalars().all()

    if not tickets_confirmados:
        return None

    tickets_ordenados = sorted(tickets_confirmados, key=lambda t: t.id)
    total = len(tickets_ordenados)

    indice, serial, signature = await sortear_indice(total)
    ganador = tickets_ordenados[indice]

    if serial:
        seed = f"randomorg:serial={serial}:indice={indice}:total={total}"
        hash_resultado = signature
    else:
        seed = f"local:{datetime.now(timezone.utc).isoformat()}:indice={indice}:total={total}"
        hash_data = f"{seed}:{ganador.codigo}"
        hash_resultado = hashlib.sha256(hash_data.encode()).hexdigest()

    rifa = await get_rifa(session, rifa_id)
    rifa.estado = EstadoRifa.sorteada
    rifa.cerrada_at = datetime.now(timezone.utc)

    sorteo = Sorteo(
        rifa_id=rifa_id,
        ticket_ganador_id=ganador.id,
        seed=seed,
        hash_resultado=hash_resultado,
    )
    sorteo.ticket_ganador = ganador
    session.add(sorteo)
    await session.flush()
    return sorteo
