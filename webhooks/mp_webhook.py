from fastapi import FastAPI, Request, HTTPException
from db.database import get_session
from core.rifa_service import confirmar_tickets_por_pago, get_rifa
from utils.mp import verificar_pago
from sqlalchemy import select
from db.models import Server
from utils.crypto import decrypt_token
import logging

from web.routes import router as web_router

logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(web_router)

# Referencia al bot de Discord (se inyecta desde main.py)
_discord_bot = None


def set_bot(bot):
    global _discord_bot
    _discord_bot = bot


@app.post("/webhook/mp")
async def mercadopago_webhook(request: Request):
    """
    MP llama a este endpoint cuando hay una novedad en un pago.
    """
    data = await request.json()
    logger.info(f"Webhook MP recibido: {data}")

    topic = data.get("type") or data.get("topic")

    if topic != "payment":
        return {"status": "ignored"}

    payment_id = str(data.get("data", {}).get("id") or data.get("id", ""))
    if not payment_id:
        raise HTTPException(status_code=400, detail="payment_id no encontrado")

    async with get_session() as session:
        # Buscamos todos los servers con token configurado para verificar el pago
        # TODO: en producción considerar un token de aplicación de MP
        result = await session.execute(
            select(Server).where(Server.mp_access_token_encrypted.isnot(None))
        )
        servers = result.scalars().all()

        pago = None
        for server in servers:
            token = decrypt_token(server.mp_access_token_encrypted)
            pago = await verificar_pago(token, payment_id)
            if pago:
                break

        if not pago:
            logger.warning(f"No se pudo verificar el pago {payment_id}")
            return {"status": "not_found"}

        if pago.get("status") != "approved":
            logger.info(f"Pago {payment_id} con estado: {pago.get('status')}, ignorado")
            return {"status": "not_approved"}

        external_reference = pago.get("external_reference", "")
        payer_email = pago.get("payer", {}).get("email")
        tickets = await confirmar_tickets_por_pago(session, payment_id, external_reference, payer_email)

        if not tickets:
            logger.warning(f"No se encontraron tickets para external_reference: {external_reference}")
            return {"status": "tickets_not_found"}

        logger.info(f"{len(tickets)} ticket(s) confirmados para payment_id {payment_id}")

        if _discord_bot:
            rifa = await get_rifa(session, tickets[0].rifa_id)
            if rifa and rifa.canal_discord_id:
                await _discord_bot.notificar_pago_confirmado(tickets, rifa)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
