from __future__ import annotations
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from db.database import get_session
from core.rifa_service import (
    get_admin_by_email,
    get_web_server_id,
    get_rifas_admin,
    get_rifa,
    crear_rifa,
    cancelar_rifa,
    realizar_sorteo,
    configurar_mp_token,
    get_mp_token,
)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
admin_router = APIRouter(prefix="/admin")


def _get_admin_user(request: Request) -> dict | None:
    user = request.session.get("oauth_user")
    if not user or not request.session.get("is_admin"):
        return None
    return user


@admin_router.get("")
async def admin_index(request: Request):
    return RedirectResponse("/admin/rifas")


@admin_router.get("/rifas", response_class=HTMLResponse)
async def admin_rifas(request: Request):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    async with get_session() as session:
        rifas = await get_rifas_admin(session, user["email"])

    return templates.TemplateResponse(
        request, "admin/rifas.html", {"user": user, "rifas": rifas}
    )


@admin_router.get("/nueva-rifa", response_class=HTMLResponse)
async def admin_nueva_rifa_form(request: Request):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")
    return templates.TemplateResponse(request, "admin/nueva_rifa.html", {"user": user, "error": None})


@admin_router.post("/nueva-rifa")
async def admin_nueva_rifa_submit(
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    precio: str = Form(...),
    max_tickets: int = Form(10),
    fecha_cierre: str = Form(""),
    es_numerada: str = Form(""),
    numero_desde: str = Form(""),
    numero_hasta: str = Form(""),
):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    def error(msg):
        return templates.TemplateResponse(
            request, "admin/nueva_rifa.html",
            {"user": user, "error": msg},
            status_code=422,
        )

    try:
        precio_decimal = Decimal(precio.replace(",", "."))
    except InvalidOperation:
        return error("Precio inválido.")

    fecha = None
    if fecha_cierre.strip():
        try:
            fecha = datetime.fromisoformat(fecha_cierre).replace(tzinfo=timezone.utc)
        except ValueError:
            return error("Fecha de cierre inválida.")

    numerada = es_numerada == "on"
    num_desde = num_hasta = None
    if numerada:
        try:
            num_desde = int(numero_desde)
            num_hasta = int(numero_hasta)
        except (ValueError, TypeError):
            return error("Para rifas numeradas debés indicar el rango de números.")

    server_id = get_web_server_id(user["email"])
    try:
        async with get_session() as session:
            rifa = await crear_rifa(
                session=session,
                discord_server_id=server_id,
                nombre=nombre,
                descripcion=descripcion,
                precio=precio_decimal,
                max_tickets_por_persona=max_tickets,
                fecha_cierre=fecha,
                es_numerada=numerada,
                numero_desde=num_desde,
                numero_hasta=num_hasta,
            )
    except ValueError as e:
        return error(str(e))

    return RedirectResponse("/admin/rifas", status_code=303)


@admin_router.post("/rifa/{rifa_id}/sortear")
async def admin_sortear(request: Request, rifa_id: int):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    server_id = get_web_server_id(user["email"])
    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)
        if not rifa:
            return HTMLResponse("Rifa no encontrada.", status_code=404)
        # Verify ownership
        from sqlalchemy import select
        from db.models import Server
        result = await session.execute(
            select(Server).where(Server.id == rifa.server_id)
        )
        server = result.scalar_one_or_none()
        if not server or server.discord_server_id != server_id:
            return HTMLResponse("No autorizado.", status_code=403)

        sorteo = await realizar_sorteo(session, rifa_id)
        if not sorteo:
            return RedirectResponse("/admin/rifas?error=sin_tickets", status_code=303)

    return RedirectResponse("/admin/rifas", status_code=303)


@admin_router.post("/rifa/{rifa_id}/cancelar")
async def admin_cancelar(request: Request, rifa_id: int):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    server_id = get_web_server_id(user["email"])
    async with get_session() as session:
        ok = await cancelar_rifa(session, rifa_id, server_id)

    if not ok:
        return HTMLResponse("No se pudo cancelar la rifa.", status_code=400)
    return RedirectResponse("/admin/rifas", status_code=303)


@admin_router.get("/configuracion", response_class=HTMLResponse)
async def admin_config_form(request: Request):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    server_id = get_web_server_id(user["email"])
    async with get_session() as session:
        token = await get_mp_token(session, server_id)

    tiene_token = bool(token)
    return templates.TemplateResponse(
        request, "admin/configuracion.html",
        {"user": user, "tiene_token": tiene_token, "guardado": False, "error": None}
    )


@admin_router.post("/configuracion")
async def admin_config_submit(request: Request, mp_token: str = Form(...)):
    user = _get_admin_user(request)
    if not user:
        return RedirectResponse("/admin/login")

    token = mp_token.strip()
    if not token:
        return templates.TemplateResponse(
            request, "admin/configuracion.html",
            {"user": user, "tiene_token": False, "guardado": False, "error": "El token no puede estar vacío."},
            status_code=422,
        )

    server_id = get_web_server_id(user["email"])
    async with get_session() as session:
        await configurar_mp_token(session, server_id, token)

    return templates.TemplateResponse(
        request, "admin/configuracion.html",
        {"user": user, "tiene_token": True, "guardado": True, "error": None}
    )


@admin_router.get("/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    user = _get_admin_user(request)
    if user:
        return RedirectResponse("/admin/rifas")
    return templates.TemplateResponse(request, "admin/login.html", {})
