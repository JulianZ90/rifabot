import asyncio
import logging
import os
import sys
import uvicorn
from dotenv import load_dotenv

from bot.commands import bot
from webhooks.mp_webhook import app as webhook_app, set_bot
from db.database import init_db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
# Railway inyecta PORT; WEBHOOK_PORT como fallback para dev local
WEBHOOK_PORT = int(os.getenv("PORT") or os.getenv("WEBHOOK_PORT", 8000))


async def main():
    # Inicializar base de datos
    await init_db()
    logger.info("Base de datos inicializada")

    # Inyectar el bot en el webhook handler
    set_bot(bot)

    # Levantar el servidor de webhooks en background
    config = uvicorn.Config(webhook_app, host="0.0.0.0", port=WEBHOOK_PORT, log_level="info")
    server = uvicorn.Server(config)

    # Correr bot y webhook server de forma concurrente
    await asyncio.gather(
        bot.start(DISCORD_TOKEN),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
