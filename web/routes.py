import asyncio
import json
import os
import secrets
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import select

from db.database import get_session
from db.models import Server, EstadoRifa, PlataformaOrigen, Ticket
from core.rifa_service import (
    get_rifa,
    crear_ticket,
    asignar_link_pago,
    confirmar_tickets_gratis,
    contar_tickets_usuario,
    get_admin_by_email,
    get_numeros_ocupados,
)
from utils.crypto import decrypt_token
from web.oauth import google_auth_url, google_exchange_code, fb_auth_url, fb_exchange_code
from web.presence import get_presence_snapshot, update_presence, clear_presence, _sse_queues

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

    numeros_ocupados: list[int] = []
    if rifa.es_numerada:
        async with get_session() as session2:
            numeros_ocupados = await get_numeros_ocupados(session2, rifa_id)

    return templates.TemplateResponse(
        request, "rifa.html",
        {
            "rifa": rifa,
            "oauth_user": oauth_user,
            "numeros_ocupados": numeros_ocupados,
        }
    )


@router.post("/rifa/{rifa_id}/participar")
async def participar(
    request: Request,
    rifa_id: int,
    cantidad: int = Form(None),
    numeros_str: str = Form(""),
):
    oauth_user = request.session.get("oauth_user")

    if not oauth_user:
        return RedirectResponse(f"/rifa/{rifa_id}", status_code=303)

    plataforma = PlataformaOrigen(oauth_user["provider"])
    plataforma_uid = oauth_user.get("uid") or oauth_user["email"]
    plataforma_handle = oauth_user["name"]
    email_participante = oauth_user.get("email")
    nombre_participante = oauth_user["name"]

    # Parse numeros_str for numbered rifas ("42,43,5")
    numeros: list[int] | None = None
    if numeros_str.strip():
        try:
            numeros = [int(x) for x in numeros_str.split(",") if x.strip()]
        except ValueError:
            numeros = None

    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)
        if not rifa or rifa.estado != EstadoRifa.abierta:
            return HTMLResponse("<h1>Rifa no disponible.</h1>", status_code=404)

        es_gratis = rifa.precio_ticket == 0
        numeros_ocupados = await get_numeros_ocupados(session, rifa_id) if rifa.es_numerada else []

        def rifa_ctx(extra: dict = {}):
            return {"rifa": rifa, "oauth_user": oauth_user,
                    "numeros_ocupados": numeros_ocupados, **extra}

        if es_gratis and not rifa.es_numerada:
            cantidad = 1
            ya_participa = await contar_tickets_usuario(session, rifa_id, plataforma_uid)
            if ya_participa > 0:
                return templates.TemplateResponse(
                    request, "rifa.html",
                    rifa_ctx({"error": "Ya estás participando en esta rifa."}),
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
                    rifa_ctx({"error": "Esta rifa no tiene pagos configurados todavía."}),
                    status_code=503,
                )

        try:
            tickets = await crear_ticket(
                session=session,
                rifa_id=rifa_id,
                cantidad=cantidad or 1,
                plataforma=plataforma,
                plataforma_uid=plataforma_uid,
                plataforma_handle=plataforma_handle,
                nombre_participante=nombre_participante,
                email_participante=email_participante,
                numeros=numeros,
            )
        except ValueError as e:
            return templates.TemplateResponse(
                request, "rifa.html",
                rifa_ctx({"error": str(e)}),
                status_code=422,
            )

        # Clear presence for selected numbers after purchase
        if rifa.es_numerada and numeros:
            await clear_presence(rifa_id, plataforma_uid)

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
# PRESENCE — SSE + select
# ─────────────────────────────────────────────

@router.get("/rifa/{rifa_id}/presence/stream")
async def presence_stream(request: Request, rifa_id: int):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues[rifa_id].append(queue)

    async def event_gen():
        try:
            # Send initial snapshot
            snapshot = get_presence_snapshot(rifa_id)
            yield f"data: {json.dumps(snapshot)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep-alive comment
        finally:
            try:
                _sse_queues[rifa_id].remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/rifa/{rifa_id}/presence/select")
async def presence_select(request: Request, rifa_id: int):
    body = await request.json()
    uid = body.get("uid", "")
    numeros = body.get("numeros", [])
    if uid:
        await update_presence(rifa_id, uid, numeros)
    return {"ok": True}


# ─────────────────────────────────────────────
# OAUTH — GOOGLE
# ─────────────────────────────────────────────

@router.get("/auth/google")
async def auth_google(request: Request, rifa_id: int = None, next: str = None):
    nonce = secrets.token_urlsafe(16)
    request.session["oauth_nonce"] = nonce
    if next:
        request.session["oauth_next"] = next
        return RedirectResponse(google_auth_url(nonce, next_url=next))
    else:
        request.session["oauth_rifa_id"] = rifa_id
        return RedirectResponse(google_auth_url(nonce, rifa_id=rifa_id))


@router.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = None, state: str = "", error: str = None):
    if error or not code:
        return RedirectResponse("/")

    # state format: "rifa:{id}:{nonce}" or "next:{url}:{nonce}"
    parts = state.split(":", 2)
    if len(parts) < 3 or parts[2] != request.session.get("oauth_nonce"):
        return HTMLResponse("Estado inválido.", status_code=400)

    kind = parts[0]
    context = parts[1]

    try:
        user = await google_exchange_code(code)
    except Exception:
        rifa_id = request.session.get("oauth_rifa_id") or 1
        return RedirectResponse(f"/rifa/{rifa_id}?error=google")

    request.session["oauth_user"] = user
    request.session.pop("oauth_nonce", None)

    # Check if this user is an admin
    async with get_session() as session:
        admin = await get_admin_by_email(session, user["email"])
    if admin:
        request.session["is_admin"] = True

    if kind == "next":
        return RedirectResponse(context)
    else:
        rifa_id = request.session.get("oauth_rifa_id") or context
        return RedirectResponse(f"/rifa/{rifa_id}")



# ─────────────────────────────────────────────
# OAUTH — FACEBOOK
# ─────────────────────────────────────────────

@router.get("/auth/facebook")
async def auth_facebook(request: Request, rifa_id: int):
    nonce = secrets.token_urlsafe(16)
    request.session["oauth_nonce"] = nonce
    request.session["oauth_rifa_id"] = rifa_id
    return RedirectResponse(fb_auth_url(rifa_id, nonce))


@router.get("/auth/facebook/callback")
async def auth_facebook_callback(request: Request, code: str = None, state: str = "", error: str = None):
    if error or not code:
        rifa_id = request.session.get("oauth_rifa_id", 1)
        return RedirectResponse(f"/rifa/{rifa_id}")

    parts = state.split(":", 1)
    if len(parts) != 2 or parts[1] != request.session.get("oauth_nonce"):
        return HTMLResponse("Estado inválido.", status_code=400)

    rifa_id = request.session.get("oauth_rifa_id")

    try:
        user = await fb_exchange_code(code)
    except Exception:
        rifa_id = rifa_id or 1
        return RedirectResponse(f"/rifa/{rifa_id}?error=facebook")

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
