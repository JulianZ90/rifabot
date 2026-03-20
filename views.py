import discord
from db.models import Rifa, EstadoBoleto
from db.database import get_session
from core.rifa_service import (
    get_numeros_disponibles,
    reservar_numero,
    asignar_link_pago,
    get_mp_token,
)
import logging
import os

logger = logging.getLogger(__name__)
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")
RESERVA_TIMEOUT = int(os.getenv("RESERVA_TIMEOUT_MINUTOS", 15))


class SeleccionNumeroModal(discord.ui.Modal, title="Elegí tu número"):
    """Modal que aparece cuando el usuario quiere elegir un número específico."""

    numero = discord.ui.TextInput(
        label="Número",
        placeholder="Ej: 42",
        min_length=1,
        max_length=4,
    )

    def __init__(self, rifa: Rifa, disponibles: list[int]):
        super().__init__()
        self.rifa = rifa
        self.disponibles = disponibles

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            numero = int(self.numero.value)
        except ValueError:
            await interaction.followup.send("❌ Ingresá un número válido.", ephemeral=True)
            return

        if numero not in self.disponibles:
            await interaction.followup.send(
                f"❌ El número {numero} no está disponible. Elegí otro.",
                ephemeral=True,
            )
            return

        await procesar_reserva(interaction, self.rifa, numero)


class GrillaView(discord.ui.View):
    """
    Vista principal de la rifa con botones para elegir número.
    Muestra hasta 25 botones (límite de Discord por fila × 5 filas).
    Para rifas grandes, usa el modal de ingreso manual.
    """

    def __init__(self, rifa: Rifa, disponibles: list[int]):
        super().__init__(timeout=None)  # Persistente
        self.rifa = rifa
        self.disponibles = disponibles

        # Si la rifa tiene 25 números o menos, mostramos botones directos
        if rifa.max_boletos <= 25:
            self._agregar_botones_directos()
        else:
            # Para rifas grandes, botón para abrir modal de selección
            self._agregar_boton_elegir()

    def _agregar_botones_directos(self):
        todos = list(range(1, self.rifa.max_boletos + 1))
        for numero in todos:
            disponible = numero in self.disponibles
            btn = NumeroButton(
                numero=numero,
                disponible=disponible,
                rifa=self.rifa,
                disponibles=self.disponibles,
            )
            self.add_item(btn)

    def _agregar_boton_elegir(self):
        btn = discord.ui.Button(
            label="🎯 Elegir número",
            style=discord.ButtonStyle.primary,
            custom_id=f"elegir_{self.rifa.id}",
        )
        btn.callback = self._elegir_callback
        self.add_item(btn)

        btn_ver = discord.ui.Button(
            label="📋 Ver disponibles",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ver_{self.rifa.id}",
        )
        btn_ver.callback = self._ver_callback
        self.add_item(btn_ver)

    async def _elegir_callback(self, interaction: discord.Interaction):
        async with get_session() as session:
            disponibles = await get_numeros_disponibles(session, self.rifa.id)
        modal = SeleccionNumeroModal(self.rifa, disponibles)
        await interaction.response.send_modal(modal)

    async def _ver_callback(self, interaction: discord.Interaction):
        async with get_session() as session:
            disponibles = await get_numeros_disponibles(session, self.rifa.id)

        if not disponibles:
            await interaction.response.send_message(
                "No quedan números disponibles.", ephemeral=True
            )
            return

        # Mostrar en grupos de 20
        chunks = [disponibles[i:i+20] for i in range(0, min(len(disponibles), 100), 20)]
        texto = "**Números disponibles:**\n"
        for chunk in chunks:
            texto += " ".join(f"`{n}`" for n in chunk) + "\n"
        if len(disponibles) > 100:
            texto += f"...y {len(disponibles) - 100} más."

        await interaction.response.send_message(texto, ephemeral=True)


class NumeroButton(discord.ui.Button):
    """Botón individual para un número de la rifa."""

    def __init__(self, numero: int, disponible: bool, rifa: Rifa, disponibles: list[int]):
        style = discord.ButtonStyle.success if disponible else discord.ButtonStyle.danger
        super().__init__(
            label=str(numero),
            style=style,
            disabled=not disponible,
            custom_id=f"num_{rifa.id}_{numero}",
        )
        self.numero = numero
        self.rifa = rifa
        self.disponibles = disponibles

    async def callback(self, interaction: discord.Interaction):
        if self.numero not in self.disponibles:
            await interaction.response.send_message(
                f"❌ El número {self.numero} ya fue tomado.", ephemeral=True
            )
            return
        await procesar_reserva(interaction, self.rifa, self.numero)


async def procesar_reserva(interaction: discord.Interaction, rifa: Rifa, numero: int):
    """Lógica compartida para reservar un número y generar el link de pago."""
    await interaction.response.defer(ephemeral=True)

    async with get_session() as session:
        boleto = await reservar_numero(
            session=session,
            rifa_id=rifa.id,
            numero=numero,
            discord_user_id=str(interaction.user.id),
            discord_user_name=str(interaction.user.display_name),
        )

        if not boleto:
            await interaction.followup.send(
                f"❌ El número **{numero}** acaba de ser tomado por alguien más. Elegí otro.",
                ephemeral=True,
            )
            return

        mp_token = await get_mp_token(session, str(interaction.guild_id))
        if not mp_token:
            await interaction.followup.send(
                "❌ Error de configuración: token de MP no encontrado.",
                ephemeral=True,
            )
            return

        link_pago = await asignar_link_pago(
            session=session,
            boleto=boleto,
            mp_token=mp_token,
            webhook_base_url=WEBHOOK_BASE_URL,
        )

    embed = discord.Embed(
        title="🎟️ Número reservado",
        description=(
            f"Reservaste el número **{numero}** para la rifa **{rifa.nombre}**.\n\n"
            f"Tenés **{RESERVA_TIMEOUT} minutos** para completar el pago.\n"
            f"Si no pagás, el número se libera automáticamente."
        ),
        color=discord.Color.yellow(),
    )
    embed.add_field(name="💰 Total a pagar", value=f"${rifa.precio_boleto}")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="💳 Pagar con MercadoPago",
        style=discord.ButtonStyle.link,
        url=link_pago,
    ))

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
