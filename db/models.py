from sqlalchemy import Column, String, Integer, ForeignKey, Enum, DateTime, Numeric, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
import enum

Base = declarative_base()


class EstadoRifa(str, enum.Enum):
    abierta = "abierta"
    cerrada = "cerrada"
    sorteada = "sorteada"
    cancelada = "cancelada"


class EstadoTicket(str, enum.Enum):
    pendiente = "pendiente"      # pago iniciado, esperando confirmación de MP
    confirmado = "confirmado"    # pago aprobado
    rechazado = "rechazado"      # pago rechazado o expirado


class PlataformaOrigen(str, enum.Enum):
    discord = "discord"
    instagram = "instagram"
    tiktok = "tiktok"
    web = "web"
    google = "google"
    microsoft = "microsoft"
    facebook = "facebook"


class Server(Base):
    """
    Cada servidor de Discord que usa el bot.
    Guarda el access token de MP encriptado.
    """
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True)
    discord_server_id = Column(String, unique=True, nullable=False, index=True)
    mp_access_token_encrypted = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    rifas = relationship("Rifa", back_populates="server")


class Rifa(Base):
    """
    Una rifa. Sin límite de tickets — cierra cuando el admin lo decide
    o cuando llega a la fecha límite opcional.
    """
    __tablename__ = "rifas"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    nombre = Column(String, nullable=False)
    descripcion = Column(String, nullable=True)
    imagen_url = Column(String, nullable=True)            # para mostrar en la landing
    precio_ticket = Column(Numeric(10, 2), nullable=False)
    max_tickets_por_persona = Column(Integer, default=10, nullable=False)
    estado = Column(Enum(EstadoRifa), default=EstadoRifa.abierta, nullable=False)
    canal_discord_id = Column(String, nullable=True)      # null si se creó desde la web
    mensaje_discord_id = Column(String, nullable=True)    # ID del mensaje en Discord
    fecha_cierre = Column(DateTime(timezone=True), nullable=True)  # cierre automático opcional
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    cerrada_at = Column(DateTime(timezone=True), nullable=True)

    server = relationship("Server", back_populates="rifas")
    tickets = relationship("Ticket", back_populates="rifa", lazy="selectin")
    sorteo = relationship("Sorteo", back_populates="rifa", uselist=False, lazy="selectin")


class Ticket(Base):
    """
    Un ticket de participación. Se genera al iniciar el pago
    y se confirma cuando MP aprueba.
    Cada ticket tiene un código único (ej: TK-4F2A).
    """
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True)
    rifa_id = Column(Integer, ForeignKey("rifas.id"), nullable=False)
    codigo = Column(String, nullable=False, index=True)   # TK-XXXX, único por rifa

    # Participante
    plataforma = Column(Enum(PlataformaOrigen), nullable=True)  # origen del participante
    plataforma_uid = Column(String, nullable=True)               # ID en la plataforma de origen
    plataforma_handle = Column(String, nullable=True)            # @username o nombre para mostrar
    nombre_participante = Column(String, nullable=True)          # nombre real (opcional)
    email_participante = Column(String, nullable=True)           # para notificación por email
    telefono_participante = Column(String, nullable=True)        # para notificación por WhatsApp

    # Pago
    mp_payment_id = Column(String, nullable=True, index=True)
    mp_preference_id = Column(String, nullable=True)
    mp_payer_email = Column(String, nullable=True)
    estado = Column(Enum(EstadoTicket), default=EstadoTicket.pendiente, nullable=False)

    creado_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmado_at = Column(DateTime(timezone=True), nullable=True)

    rifa = relationship("Rifa", back_populates="tickets")

    __table_args__ = (
        UniqueConstraint("rifa_id", "codigo", name="uq_ticket_codigo_por_rifa"),
    )


class Sorteo(Base):
    """
    Resultado del sorteo. Guarda el ticket ganador y un hash
    verificable para transparencia.
    """
    __tablename__ = "sorteos"

    id = Column(Integer, primary_key=True)
    rifa_id = Column(Integer, ForeignKey("rifas.id"), unique=True, nullable=False)
    ticket_ganador_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    seed = Column(String, nullable=True)           # semilla usada para el random
    hash_resultado = Column(String, nullable=True)  # hash SHA256 para verificación
    realizado_at = Column(DateTime(timezone=True), server_default=func.now())

    rifa = relationship("Rifa", back_populates="sorteo")
    ticket_ganador = relationship("Ticket", lazy="selectin")
