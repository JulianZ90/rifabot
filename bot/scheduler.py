import asyncio
import logging
import discord

from db.database import get_session
from db.models import PlataformaOrigen
from core.rifa_service import get_rifas_para_sortear, realizar_sorteo

logger = logging.getLogger(__name__)


async def scheduler_loop(bot: discord.Client):
    """Loop background que verifica cada 60 segundos si hay rifas vencidas para sortear."""
    await bot.wait_until_ready()
    logger.info("Scheduler iniciado.")

    while not bot.is_closed():
        try:
            await verificar_rifas_vencidas(bot)
        except Exception as e:
            logger.error(f"Scheduler: error inesperado: {e}", exc_info=e)
        await asyncio.sleep(60)


async def verificar_rifas_vencidas(bot: discord.Client):
    async with get_session() as session:
        rifas = await get_rifas_para_sortear(session)

    if not rifas:
        return

    for rifa in rifas:
        logger.info(f"Scheduler: rifa {rifa.id} '{rifa.nombre}' venció, sorteando automáticamente...")
        try:
            async with get_session() as session:
                sorteo = await realizar_sorteo(session, rifa.id)

            if not sorteo:
                logger.warning(f"Scheduler: rifa {rifa.id} sin tickets confirmados, se cierra sin ganador.")
                await notificar_sin_ganador(bot, rifa)
                continue

            await postear_ganador(bot, rifa, sorteo)

        except Exception as e:
            logger.error(f"Scheduler: error sorteando rifa {rifa.id}: {e}", exc_info=e)


async def postear_ganador(bot: discord.Client, rifa, sorteo):
    if not rifa.canal_discord_id:
        logger.warning(f"Scheduler: rifa {rifa.id} no tiene canal de Discord configurado.")
        return

    canal = bot.get_channel(int(rifa.canal_discord_id))
    if not canal:
        logger.warning(f"Scheduler: canal {rifa.canal_discord_id} no encontrado para rifa {rifa.id}.")
        return

    ganador = sorteo.ticket_ganador

    embed = discord.Embed(
        title="⏰ Sorteo automático — ¡Tenemos ganador!",
        description=f"**Rifa:** {rifa.nombre}",
        color=discord.Color.green(),
    )
    embed.add_field(name="🎟️ Ticket ganador", value=f"`{ganador.codigo}`", inline=True)

    if ganador.plataforma == PlataformaOrigen.discord:
        embed.add_field(name="👤 Ganador", value=f"<@{ganador.plataforma_uid}>", inline=True)
    elif ganador.plataforma_handle:
        embed.add_field(name="👤 Ganador", value=f"{ganador.plataforma_handle} ({ganador.plataforma.value})", inline=True)
    else:
        embed.add_field(name="👤 Ganador", value=ganador.nombre_participante or "Anónimo", inline=True)

    if sorteo.seed and sorteo.seed.startswith("randomorg:"):
        serial = sorteo.seed.split("serial=")[1].split(":")[0]
        embed.set_footer(text=f"Verificable en random.org · Serial #{serial}")
    else:
        embed.set_footer(text=f"Hash: {sorteo.hash_resultado[:20]}...")

    if ganador.plataforma == PlataformaOrigen.discord:
        mencionar = f"<@{ganador.plataforma_uid}>"
    else:
        mencionar = ganador.plataforma_handle or ganador.nombre_participante or "el ganador"

    await canal.send(
        content=f"🎊 ¡Felicitaciones {mencionar}!",
        embed=embed,
    )
    logger.info(f"Scheduler: ganador de rifa {rifa.id} anunciado en canal {rifa.canal_discord_id}.")


async def notificar_sin_ganador(bot: discord.Client, rifa):
    if not rifa.canal_discord_id:
        return

    canal = bot.get_channel(int(rifa.canal_discord_id))
    if not canal:
        return

    await canal.send(
        f"⏰ La rifa **{rifa.nombre}** cerró automáticamente pero no había tickets confirmados. "
        f"No hay ganador."
    )
