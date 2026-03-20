import discord
from discord.ext import commands
from discord import app_commands
from decimal import Decimal
import os
import logging
from dotenv import load_dotenv

from db.database import get_session
from core.rifa_service import (
    configurar_mp_token,
    crear_rifa,
    get_rifas_abiertas,
    get_rifa,
    crear_ticket,
    asignar_link_pago,
    get_mp_token,
    realizar_sorteo,
    cerrar_rifa,
)
from db.models import Ticket, Rifa, EstadoTicket

load_dotenv()
logger = logging.getLogger(__name__)

WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")


class RifaBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Comandos slash sincronizados")

    async def on_ready(self):
        logger.info(f"Bot conectado como {self.user}")

    async def notificar_pago_confirmado(self, tickets: list[Ticket], rifa: Rifa):
        """Notifica en Discord cuando un pago se confirma."""
        try:
            canal = self.get_channel(int(rifa.canal_discord_id))
            if not canal:
                return

            codigos = " ".join(f"`{t.codigo}`" for t in tickets)
            cantidad = len(tickets)
            await canal.send(
                f"✅ <@{tickets[0].discord_user_id}> tu pago fue confirmado! "
                f"Recibiste {cantidad} ticket{'s' if cantidad > 1 else ''} para **{rifa.nombre}**: {codigos} 🎟️"
            )
        except Exception as e:
            logger.error(f"Error notificando pago: {e}")


bot = RifaBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "❌ Ocurrió un error inesperado."
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ No tenés permisos para usar este comando."
    else:
        logger.error(f"Error en comando {interaction.command}: {error}", exc_info=error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def crear_embed_rifa(rifa: Rifa) -> discord.Embed:
    tickets_confirmados = [t for t in rifa.tickets if t.estado == EstadoTicket.confirmado]
    tickets_pendientes = [t for t in rifa.tickets if t.estado == EstadoTicket.pendiente]

    embed = discord.Embed(
        title=f"🎟️ {rifa.nombre}",
        description=rifa.descripcion or "",
        color=discord.Color.gold(),
    )
    embed.add_field(name="💰 Precio por ticket", value=f"${rifa.precio_ticket}", inline=True)
    embed.add_field(name="✅ Tickets vendidos", value=str(len(tickets_confirmados)), inline=True)
    embed.add_field(name="🟡 Pagos pendientes", value=str(len(tickets_pendientes)), inline=True)
    if rifa.fecha_cierre:
        embed.add_field(
            name="⏰ Cierre",
            value=f"<t:{int(rifa.fecha_cierre.timestamp())}:R>",
            inline=True,
        )
    embed.set_footer(text=f"ID de la rifa: {rifa.id} · Usá /participar para comprar tickets")
    return embed


# ─────────────────────────────────────────────
# COMANDOS
# ─────────────────────────────────────────────

@bot.tree.command(name="rifa_setup", description="Configura tu token de MercadoPago")
@app_commands.checks.has_permissions(administrator=True)
async def rifa_setup(interaction: discord.Interaction, access_token: str):
    await interaction.response.defer(ephemeral=True)
    async with get_session() as session:
        await configurar_mp_token(session, str(interaction.guild_id), access_token)
    await interaction.followup.send(
        "✅ Token de MercadoPago configurado. Ya podés crear rifas con `/rifa_crear`.",
        ephemeral=True,
    )


@bot.tree.command(name="rifa_crear", description="Crea una nueva rifa en este canal")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    nombre="Nombre del premio",
    precio="Precio por ticket en ARS",
    descripcion="Descripción del premio (opcional)",
    max_tickets="Máximo de tickets por persona (default: 10)",
)
async def rifa_crear(
    interaction: discord.Interaction,
    nombre: str,
    precio: float,
    descripcion: str = "",
    max_tickets: int = 10,
):
    await interaction.response.defer()

    async with get_session() as session:
        mp_token = await get_mp_token(session, str(interaction.guild_id))
        if not mp_token:
            await interaction.followup.send(
                "❌ Primero configurá el token de MercadoPago con `/rifa_setup`.",
                ephemeral=True,
            )
            return

        rifa = await crear_rifa(
            session=session,
            discord_server_id=str(interaction.guild_id),
            nombre=nombre,
            descripcion=descripcion,
            precio=Decimal(str(precio)),
            max_tickets_por_persona=max_tickets,
            canal_id=str(interaction.channel_id),
        )
        rifa = await get_rifa(session, rifa.id)  # reload con tickets cargados

        embed = crear_embed_rifa(rifa)
        mensaje = await interaction.followup.send(embed=embed)
        rifa.mensaje_discord_id = str(mensaje.id)

    await interaction.channel.send(
        f"🎉 ¡Nueva rifa abierta! **{nombre}** — ${precio} por ticket.\n"
        f"Usá `/participar rifa_id:{rifa.id}` para comprar tickets."
    )


@bot.tree.command(name="participar", description="Comprá tickets para una rifa")
@app_commands.describe(
    rifa_id="ID de la rifa",
    cantidad="Cantidad de tickets a comprar (default: 1)",
)
async def participar(
    interaction: discord.Interaction,
    rifa_id: int,
    cantidad: int = 1,
):
    await interaction.response.defer(ephemeral=True)

    if cantidad < 1 or cantidad > 50:
        await interaction.followup.send("❌ La cantidad debe ser entre 1 y 50.", ephemeral=True)
        return

    async with get_session() as session:
        mp_token = await get_mp_token(session, str(interaction.guild_id))
        if not mp_token:
            await interaction.followup.send(
                "❌ Este servidor no tiene MercadoPago configurado.", ephemeral=True
            )
            return

        try:
            tickets = await crear_ticket(
                session=session,
                rifa_id=rifa_id,
                cantidad=cantidad,
                discord_user_id=str(interaction.user.id),
                discord_user_name=str(interaction.user.display_name),
            )
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        rifa = await get_rifa(session, rifa_id)
        link_pago = await asignar_link_pago(
            session=session,
            tickets=tickets,
            mp_token=mp_token,
            webhook_base_url=WEBHOOK_BASE_URL,
            rifa=rifa,
        )

    total = float(rifa.precio_ticket) * cantidad
    codigos = " ".join(f"`{t.codigo}`" for t in tickets)

    embed = discord.Embed(
        title="🎟️ Tickets reservados",
        description=(
            f"Reservaste **{cantidad} ticket{'s' if cantidad > 1 else ''}** para **{rifa.nombre}**.\n\n"
            f"**Tus códigos:** {codigos}\n\n"
            f"Completá el pago para confirmar tu participación."
        ),
        color=discord.Color.yellow(),
    )
    embed.add_field(name="💰 Total", value=f"${total:,.0f}")
    embed.set_footer(text="Los tickets se cancelan si no se paga en 15 minutos.")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="💳 Pagar con MercadoPago",
        style=discord.ButtonStyle.link,
        url=link_pago,
    ))

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="mis_tickets", description="Ves tus tickets en una rifa")
@app_commands.describe(rifa_id="ID de la rifa")
async def mis_tickets(interaction: discord.Interaction, rifa_id: int):
    await interaction.response.defer(ephemeral=True)

    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)
        if not rifa:
            await interaction.followup.send("❌ Rifa no encontrada.", ephemeral=True)
            return

        mis = [
            t for t in rifa.tickets
            if t.discord_user_id == str(interaction.user.id)
        ]

    if not mis:
        await interaction.followup.send(
            f"No tenés tickets en la rifa **{rifa.nombre}**.", ephemeral=True
        )
        return

    confirmados = [t for t in mis if t.estado == EstadoTicket.confirmado]
    pendientes = [t for t in mis if t.estado == EstadoTicket.pendiente]

    texto = f"**Tus tickets en {rifa.nombre}:**\n\n"
    if confirmados:
        codigos = " ".join(f"`{t.codigo}`" for t in confirmados)
        texto += f"✅ Confirmados: {codigos}\n"
    if pendientes:
        codigos = " ".join(f"`{t.codigo}`" for t in pendientes)
        texto += f"🟡 Pendientes de pago: {codigos}\n"

    await interaction.followup.send(texto, ephemeral=True)


@bot.tree.command(name="rifa_estado", description="Muestra el estado actual de una rifa")
@app_commands.describe(rifa_id="ID de la rifa")
async def rifa_estado(interaction: discord.Interaction, rifa_id: int):
    await interaction.response.defer()

    async with get_session() as session:
        rifa = await get_rifa(session, rifa_id)

    if not rifa:
        await interaction.followup.send("❌ Rifa no encontrada.")
        return

    embed = crear_embed_rifa(rifa)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rifa_sortear", description="Realiza el sorteo de una rifa")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(rifa_id="ID de la rifa a sortear")
async def rifa_sortear(interaction: discord.Interaction, rifa_id: int):
    await interaction.response.defer()

    async with get_session() as session:
        sorteo = await realizar_sorteo(session, rifa_id)

        if not sorteo:
            await interaction.followup.send("❌ No hay tickets confirmados para sortear.")
            return

        rifa = await get_rifa(session, rifa_id)
        ganador = sorteo.ticket_ganador

    embed = discord.Embed(
        title="🏆 ¡Tenemos ganador!",
        description=f"**Rifa:** {rifa.nombre}",
        color=discord.Color.green(),
    )
    embed.add_field(name="🎟️ Ticket ganador", value=f"`{ganador.codigo}`", inline=True)
    if ganador.discord_user_id:
        embed.add_field(name="👤 Ganador", value=f"<@{ganador.discord_user_id}>", inline=True)
    else:
        embed.add_field(name="👤 Ganador", value=ganador.nombre_participante or "Anónimo", inline=True)
    embed.set_footer(text=f"Hash de verificación: {sorteo.hash_resultado[:16]}...")

    mencionar = f"<@{ganador.discord_user_id}>" if ganador.discord_user_id else ganador.nombre_participante
    await interaction.followup.send(
        content=f"🎊 ¡Felicitaciones {mencionar}!",
        embed=embed,
    )


@bot.tree.command(name="rifa_lista", description="Muestra las rifas abiertas")
async def rifa_lista(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    async with get_session() as session:
        rifas = await get_rifas_abiertas(session, str(interaction.guild_id))

    if not rifas:
        await interaction.followup.send("No hay rifas abiertas en este momento.", ephemeral=True)
        return

    texto = "\n".join([
        f"**ID {r.id}** — {r.nombre} · ${r.precio_ticket}/ticket · "
        f"{len([t for t in r.tickets if t.estado == EstadoTicket.confirmado])} vendidos"
        for r in rifas
    ])
    await interaction.followup.send(f"**Rifas abiertas:**\n{texto}", ephemeral=True)
