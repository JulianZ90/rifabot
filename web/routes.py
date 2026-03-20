import os
import secrets
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select

from db.database import get_session
from db.models import Server, EstadoRifa, PlataformaOrigen, Ticket
from core.rifa_service import get_rifa, crear_ticket, asignar_link_pago, confirmar_tickets_gratis, contar_tickets_usuario
from utils.crypto import decrypt_token
from web.oauth import google_auth_url, google_exchange_code

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")


# ─────────────────────────────────────────────
# RIFA
# ─────────────────────────────────────────────

@router.get("/rifa/{rifa_id}", response_class=HTMLResponse)
async def pagina_rifa(request: Request, rifa_id: int):
    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)

    if not rifa:
        return templates.TemplateResponse(
            request, "404.html",
            {"titulo": "Esta rifa no existe", "mensaje": "El link puede estar mal o la rifa fue eliminada."},
            status_code=404,
        )
    if rifa.estado != EstadoRifa.abierta:
        estado_msg = {
            "cerrada": "Esta rifa ya cerró y no acepta más participantes.",
            "sorteada": "Esta rifa ya fue sorteada.",
            "cancelada": "Esta rifa fue cancelada.",
        }.get(rifa.estado.value, "Esta rifa no está disponible.")
        return templates.TemplateResponse(
            request, "404.html",
            {"titulo": rifa.nombre, "mensaje": estado_msg},
            status_code=404,
        )

    oauth_user = request.session.get("oauth_user")
    return templates.TemplateResponse(
        request, "rifa.html", {"rifa": rifa, "oauth_user": oauth_user}
    )


@router.post("/rifa/{rifa_id}/participar")
async def participar(
    request: Request,
    rifa_id: int,
    cantidad: int = Form(...),
    email: str = Form(None),
):
    oauth_user = request.session.get("oauth_user")

    if oauth_user:
        plataforma = PlataformaOrigen(oauth_user["provider"])
        plataforma_uid = oauth_user["email"]
        plataforma_handle = oauth_user["name"]
        email_participante = oauth_user["email"]
        nombre_participante = oauth_user["name"]
    else:
        email = (email or "").strip()
        if not email or "@" not in email:
            async with get_session() as session:
                rifa = await get_rifa(session, rifa_id)
            return templates.TemplateResponse(
                request, "rifa.html",
                {"rifa": rifa, "oauth_user": None, "error": "Ingresá un email válido."},
                status_code=422,
            )
        plataforma = PlataformaOrigen.web
        plataforma_uid = email
        plataforma_handle = email
        email_participante = email
        nombre_participante = None

    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)
        if not rifa or rifa.estado != EstadoRifa.abierta:
            return HTMLResponse("<h1>Rifa no disponible.</h1>", status_code=404)

        es_gratis = rifa.precio_ticket == 0

        if es_gratis:
            cantidad = 1
            ya_participa = await contar_tickets_usuario(session, rifa_id, plataforma_uid)
            if ya_participa > 0:
                return templates.TemplateResponse(
                    request, "rifa.html",
                    {"rifa": rifa, "oauth_user": oauth_user,
                     "error": "Ya estás participando en esta rifa."},
                    status_code=422,
                )

        mp_token = None
        if not es_gratis:
            result = await session.execute(
                select(Server).where(Server.id == rifa.server_id)
            )
            server = result.scalar_one_or_none()
            if server and server.mp_access_token_encrypted:
                mp_token = decrypt_token(server.mp_access_token_encrypted)

            if not mp_token:
                return templates.TemplateResponse(
                    request, "rifa.html",
                    {"rifa": rifa, "oauth_user": oauth_user,
                     "error": "Esta rifa no tiene pagos configurados todavía."},
                    status_code=503,
                )

        try:
            tickets = await crear_ticket(
                session=session,
                rifa_id=rifa_id,
                cantidad=cantidad,
                plataforma=plataforma,
                plataforma_uid=plataforma_uid,
                plataforma_handle=plataforma_handle,
                nombre_participante=nombre_participante,
                email_participante=email_participante,
            )
        except ValueError as e:
            return templates.TemplateResponse(
                request, "rifa.html",
                {"rifa": rifa, "oauth_user": oauth_user, "error": str(e)},
                status_code=422,
            )

        if es_gratis:
            await confirmar_tickets_gratis(session, tickets)
            ids_tickets = ",".join(str(t.id) for t in tickets)
            return RedirectResponse(
                f"/pago/exito?external_reference={rifa.id}:{ids_tickets}",
                status_code=303,
            )

        init_point = await asignar_link_pago(
            session=session,
            tickets=tickets,
            mp_token=mp_token,
            webhook_base_url=WEBHOOK_BASE_URL,
            rifa=rifa,
        )

    return RedirectResponse(init_point, status_code=303)


# ─────────────────────────────────────────────
# OAUTH — GOOGLE
# ─────────────────────────────────────────────

@router.get("/auth/google")
async def auth_google(request: Request, rifa_id: int):
    nonce = secrets.token_urlsafe(16)
    request.session["oauth_nonce"] = nonce
    request.session["oauth_rifa_id"] = rifa_id
    return RedirectResponse(google_auth_url(rifa_id, nonce))


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = None, state: str = "", error: str = None):
    if error or not code:
        return RedirectResponse("/")

    parts = state.split(":", 1)
    if len(parts) != 2 or parts[1] != request.session.get("oauth_nonce"):
        return HTMLResponse("Estado inválido.", status_code=400)

    rifa_id = request.session.get("oauth_rifa_id")

    try:
        user = await google_exchange_code(code)
    except Exception:
        rifa_id = rifa_id or 1
        return RedirectResponse(f"/rifa/{rifa_id}?error=google")

    request.session["oauth_user"] = user
    request.session.pop("oauth_nonce", None)
    return RedirectResponse(f"/rifa/{rifa_id}")



# ─────────────────────────────────────────────
# LOGOUT
# ─────────────────────────────────────────────

@router.get("/auth/logout")
async def logout(request: Request, rifa_id: int = None):
    request.session.clear()
    dest = f"/rifa/{rifa_id}" if rifa_id else "/"
    return RedirectResponse(dest)


# ─────────────────────────────────────────────
# RETORNO DE PAGO
# ─────────────────────────────────────────────

@router.get("/pago/exito", response_class=HTMLResponse)
async def pago_exito(request: Request, external_reference: str = ""):
    rifa_id = None
    codigos = []
    rifa_nombre = ""

    if external_reference:
        parts = external_reference.split(":")
        if len(parts) == 2:
            try:
                rifa_id = int(parts[0])
                ticket_ids = [int(i) for i in parts[1].split(",") if i.isdigit()]
            except ValueError:
                ticket_ids = []

            if rifa_id and ticket_ids:
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
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:80px;background:#030712;color:#9ca3af">
    <div style="font-size:3rem;margin-bottom:1rem">⏳</div>
    <h1 style="color:white">Pago pendiente</h1>
    <p>Tu pago está siendo procesado. Cuando se acredite, tu ticket quedará confirmado.</p>
    </body></html>
    """)


# ─────────────────────────────────────────────
# PÁGINAS LEGALES
# ─────────────────────────────────────────────

@router.get("/privacidad", response_class=HTMLResponse)
async def privacidad(request: Request):
    return templates.TemplateResponse(request, "privacidad.html", {})


@router.get("/terminos", response_class=HTMLResponse)
async def terminos(request: Request):
    return templates.TemplateResponse(request, "terminos.html", {})
