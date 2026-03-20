from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select
from utils.crypto import decrypt_token
from db.models import Server, EstadoRifa, PlataformaOrigen
import os

from db.database import get_session
from core.rifa_service import get_rifa, crear_ticket, asignar_link_pago

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

router = APIRouter()

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")


@router.get("/rifa/{rifa_id}", response_class=HTMLResponse)
async def pagina_rifa(request: Request, rifa_id: int):
    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)

    if not rifa or rifa.estado != EstadoRifa.abierta:
        return HTMLResponse("<h1>Rifa no encontrada o cerrada.</h1>", status_code=404)

    return templates.TemplateResponse(
        request, "rifa.html", {"rifa": rifa}
    )


@router.post("/rifa/{rifa_id}/participar")
async def participar(
    request: Request,
    rifa_id: int,
    plataforma: str = Form(...),
    handle: str = Form(...),
    cantidad: int = Form(...),
):
    handle = handle.strip().lstrip("@")

    if not handle:
        async with get_session() as session:
            rifa = await get_rifa(session, rifa_id)
        return templates.TemplateResponse(
            request, "rifa.html",
            {"rifa": rifa, "error": "Ingresá tu usuario.", "form_handle": handle},
            status_code=422,
        )

    try:
        plataforma_enum = PlataformaOrigen(plataforma)
    except ValueError:
        plataforma_enum = PlataformaOrigen.web

    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)
        if not rifa or rifa.estado != EstadoRifa.abierta:
            return HTMLResponse("<h1>Rifa no disponible.</h1>", status_code=404)

        # Obtener token MP del servidor de la rifa
        result = await session.execute(
            select(Server).where(Server.id == rifa.server_id)
        )
        server = result.scalar_one_or_none()
        mp_token = None
        if server and server.mp_access_token_encrypted:
            mp_token = decrypt_token(server.mp_access_token_encrypted)

        if not mp_token:
            return templates.TemplateResponse(
                request, "rifa.html",
                {"rifa": rifa, "error": "Esta rifa no tiene pagos configurados todavía."},
                status_code=503,
            )

        try:
            tickets = await crear_ticket(
                session=session,
                rifa_id=rifa_id,
                cantidad=cantidad,
                plataforma=plataforma_enum,
                plataforma_uid=handle,
                plataforma_handle=handle,
            )
        except ValueError as e:
            return templates.TemplateResponse(
                request, "rifa.html",
                {"rifa": rifa, "error": str(e), "form_handle": handle},
                status_code=422,
            )

        init_point = await asignar_link_pago(
            session=session,
            tickets=tickets,
            mp_token=mp_token,
            webhook_base_url=WEBHOOK_BASE_URL,
            rifa=rifa,
        )

    return RedirectResponse(init_point, status_code=303)


@router.get("/pago/exito", response_class=HTMLResponse)
async def pago_exito(request: Request, external_reference: str = ""):
    """MP redirige aquí cuando el pago fue aprobado."""
    rifa_id = None
    codigos = []

    if external_reference:
        parts = external_reference.split(":")
        if len(parts) == 2:
            try:
                rifa_id = int(parts[0])
                ticket_ids = [int(i) for i in parts[1].split(",") if i.isdigit()]
            except ValueError:
                ticket_ids = []

            if rifa_id and ticket_ids:
                from sqlalchemy import select
                from db.models import Ticket
                async with get_session() as session:
                    result = await session.execute(
                        select(Ticket).where(Ticket.id.in_(ticket_ids))
                    )
                    tickets = result.scalars().all()
                    rifa = await get_rifa(session, rifa_id)
                    codigos = [t.codigo for t in tickets]
                    rifa_nombre = rifa.nombre if rifa else ""

    return templates.TemplateResponse(
        request, "exito.html",
        {"rifa_id": rifa_id, "rifa_nombre": rifa_nombre, "codigos": codigos},
    )


@router.get("/pago/pendiente", response_class=HTMLResponse)
async def pago_pendiente(request: Request):
    """MP redirige aquí cuando el pago está pendiente o fue rechazado."""
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:80px;background:#030712;color:#9ca3af">
    <div style="font-size:3rem;margin-bottom:1rem">⏳</div>
    <h1 style="color:white">Pago pendiente</h1>
    <p>Tu pago está siendo procesado. Cuando se acredite, tu ticket quedará confirmado.</p>
    </body></html>
    """)


@router.get("/privacidad", response_class=HTMLResponse)
async def privacidad(request: Request):
    return templates.TemplateResponse(request, "privacidad.html", {})


@router.get("/terminos", response_class=HTMLResponse)
async def terminos(request: Request):
    return templates.TemplateResponse(request, "terminos.html", {})
