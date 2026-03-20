# RifaBot — Contexto del proyecto

## Qué es
Bot de Discord para organizar rifas con pagos integrados via MercadoPago.
Pensado para ser usado por cualquier servidor de Discord (multi-tenant).

## Decisiones de diseño tomadas

### Sistema de tickets (sin números)
- Se descartó el sistema de números fijos porque obliga a definir una cantidad máxima
- Cada participante recibe un ticket con código único (ej: `TK-4F2A`) al pagar
- La rifa cierra cuando el admin lo decide, no cuando se agotan números
- Se puede comprar más de un ticket para tener más chances

### Multi-plataforma (en progreso)
- Discord es el canal principal pero NO el único
- Próximo paso: landing page web para que participen usuarios de Instagram/TikTok
- Los tickets de la landing tienen `nombre_participante` + `email` en lugar de `discord_user_id`

### MercadoPago
- Cada servidor de Discord configura su propio Access Token de MP
- Los tokens se guardan encriptados en DB con Fernet
- Flujo: usuario elige cantidad → bot crea tickets pendientes → genera link de pago MP → webhook confirma

### Transparencia del sorteo
- El sorteo guarda `seed` y `hash_resultado` (SHA256) para que sea auditable
- Cualquiera puede verificar que el resultado fue legítimo

## Stack
- Python 3.11 (no usar 3.13, asyncpg no compila)
- discord.py 2.3.2
- FastAPI (servidor de webhooks de MP)
- SQLAlchemy async + asyncpg
- PostgreSQL
- MercadoPago SDK Python
- Railway para hosting (pendiente)

## Estado actual
- [x] Modelos de DB (Server, Rifa, Ticket, Sorteo)
- [x] Lógica de negocio (rifa_service.py)
- [x] Comandos Discord (/rifa_crear, /participar, /mis_tickets, /rifa_sortear, etc.)
- [x] Webhook de MercadoPago (FastAPI)
- [x] Encriptación de tokens MP
- [ ] DB levantada (falta PostgreSQL local o Railway)
- [ ] Bot corriendo (falta DISCORD_TOKEN en .env)
- [ ] Webhook público (falta ngrok o Railway)
- [ ] Landing page web (próximo milestone)

## Estructura
```
rifabot/
├── main.py              ← Entry point, levanta bot + webhook server
├── bot/
│   └── commands.py      ← Comandos slash de Discord
├── core/
│   └── rifa_service.py  ← Lógica de negocio
├── db/
│   ├── models.py        ← Modelos SQLAlchemy
│   └── database.py      ← Conexión async PostgreSQL
├── webhooks/
│   └── mp_webhook.py    ← FastAPI para notificaciones de MP
└── utils/
    ├── crypto.py        ← Encriptación tokens MP con Fernet
    └── mp.py            ← Wrapper MercadoPago SDK
```

## Variables de entorno necesarias (.env)
```
DISCORD_TOKEN=           # token del bot de Discord
DISCORD_CLIENT_ID=       # client ID de la app de Discord
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/rifabot
ENCRYPTION_KEY=          # generado con Fernet.generate_key()
WEBHOOK_BASE_URL=        # URL pública (ngrok en dev, Railway en prod)
WEBHOOK_PORT=8000
RESERVA_TIMEOUT_MINUTOS=15
```

## Próximos pasos
1. Levantar PostgreSQL (local o Railway)
2. Completar DISCORD_TOKEN en .env
3. Levantar ngrok para el webhook
4. Correr `python main.py` y probar en un servidor de Discord
5. Construir landing page web (FastAPI + HTML simple)
