from fastapi import FastAPI, Request, HTTPException
from db.database import get_session
from core.rifa_service import confirmar_pago, get_rifa
from utils.mp import verificar_pago
from sqlalchemy import select
from db.models import Server
from utils.crypto import decrypt_token
import logging

logger = logging.getLogger(__name__)

app = FastAPI()

# Referencia al bot de Discord (se inyecta desde main.py)
_discord_bot = None

def set_bot(bot):
    global _discord_bot
    _discord_bot = bot


@app.post("/webhook/mp")
async def mercadopago_webhook(request: Request):
    """
    MP llama a este endpoint cuando hay una novedad en un pago.
    Documentación: https://www.mercadopago.com.ar/developers/es/docs/your-integrations/notifications/webhooks
    """
    data = await request.json()
    logger.info(f"Webhook MP recibido: {data}")

    # MP manda distintos tipos de notificaciones
    topic = data.get("type") or data.get("topic")

    if topic != "payment":
        return {"status": "ignored"}

    payment_id = str(data.get("data", {}).get("id") or data.get("id"))
    if not payment_id:
        raise HTTPException(status_code=400, detail="payment_id no encontrado")

    async with get_session() as session:
        # Necesitamos el token de MP del servidor para verificar el pago
        # Lo obtenemos a partir del external_reference del pago
        # Primero buscamos el boleto por mp_preference_id para saber de qué servidor es
        # Como alternativa, verificamos con el token del servidor correspondiente

        # Estrategia: buscar el boleto que tenga este payment_id pendiente
        # Para eso, primero llamamos a MP con un token para obtener el external_reference
        # Usamos el primer server que tenga token configurado (mejorable con un token global)
        
        # TODO: En producción, considerar un token de aplicación de MP para verificar
        # sin depender del token de cada servidor
        
        result = await session.execute(
            select(Server).where(Server.mp_access_token_encrypted.isnot(None))
        )
        servers = result.scalars().all()

        pago = None
        mp_token_usado = None
        for server in servers:
            token = decrypt_token(server.mp_access_token_encrypted)
            pago = await verificar_pago(token, payment_id)
            if pago:
                mp_token_usado = token
                break

        if not pago:
            logger.warning(f"No se pudo verificar el pago {payment_id}")
            return {"status": "not_found"}

        if pago.get("status") != "approved":
            logger.info(f"Pago {payment_id} con estado: {pago.get('status')}, ignorado")
            return {"status": "not_approved"}

        external_reference = pago.get("external_reference", "")
        boleto = await confirmar_pago(session, payment_id, external_reference)

        if not boleto:
            logger.warning(f"No se encontró boleto para external_reference: {external_reference}")
            return {"status": "boleto_not_found"}

        logger.info(f"Boleto {boleto.numero} confirmado para user {boleto.discord_user_id}")

        # Notificar al bot de Discord
        if _discord_bot:
            rifa = await get_rifa(session, boleto.rifa_id)
            await _discord_bot.notificar_pago_confirmado(boleto, rifa)

    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
