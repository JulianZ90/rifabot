# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

RifaBot is a Discord raffle bot with integrated MercadoPago payments and verifiable lottery draws via RANDOM.ORG. It runs a Discord bot, a FastAPI web server, and a background scheduler concurrently from a single process.

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example.template .env

# Run everything (bot + web server + scheduler)
python main.py
```

## Testing

**Siempre escribir tests para código nuevo.** Ante cualquier feature, bugfix o refactor, agregar o actualizar tests en `tests/`. Usar `pytest` con `pytest-asyncio` para código async.

Correr tests:
```bash
pytest
```

Si el directorio `tests/` o el archivo de test para el módulo modificado no existe, crearlo. No omitir este paso bajo ninguna circunstancia — ni aunque el usuario no lo pida explícitamente.

## Environment Variables

Key vars required (see `.env.example.template` for full list):
- `DISCORD_TOKEN` — Discord bot token
- `DATABASE_URL` — `postgresql+asyncpg://...` (async driver required)
- `ENCRYPTION_KEY` — Fernet key (`Fernet.generate_key()`)
- `WEBHOOK_BASE_URL` — Public URL for MercadoPago webhooks
- `SESSION_SECRET_KEY` — Cookie signing (`secrets.token_hex(32)`)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — OAuth SSO
- `RANDOMORG_API_KEY` — Signed random numbers (fallback: local SHA256)

## Architecture

`main.py` runs three concurrent async tasks:
1. **Discord bot** (`bot/`) — slash commands for admins and users
2. **FastAPI web server** (`web/` + `webhooks/`) — raffle landing pages, Google OAuth, MercadoPago webhooks
3. **Scheduler** (`bot/scheduler.py`) — polls every 60s to auto-close expired raffles

### Core Flow

- **Creating a raffle**: Admin uses `/rifa_crear` → stored in DB as `abierta`
- **Participation**: User calls `/participar` (Discord) or visits `/rifa/{id}` (web) → ticket created as `pendiente`
- **Payment**: MercadoPago Checkout Pro → webhook hits `/webhook/mp` → ticket confirmed (`confirmado`) → bot DMs user
- **Lottery**: Admin uses `/rifa_sortear` or scheduler auto-triggers → calls RANDOM.ORG Signed API → winner mapped by ticket index → result stored with `serialNumber` + signature for public verification

### Data Models (`db/models.py`)

- **Server** — Discord guild + Fernet-encrypted MercadoPago access token
- **Rifa** — Raffle with states: `abierta → cerrada / sorteada / cancelada`
- **Ticket** — Participation with states: `pendiente → confirmado / rechazado`; tracks platform (`discord`, `google`, `web`)
- **Sorteo** — Lottery result with RANDOM.ORG `serialNumber` and `signature` for verification

### Key Files

| File | Responsibility |
|------|---------------|
| `main.py` | Process entry point, concurrent startup |
| `core/rifa_service.py` | All business logic (raffle lifecycle, ticket management, lottery execution) |
| `bot/commands.py` | Discord slash command definitions and handlers |
| `bot/scheduler.py` | Background loop for auto-closing raffles |
| `web/routes.py` | FastAPI routes (landing page, OAuth callback, participation) |
| `webhooks/mp_webhook.py` | MercadoPago webhook receiver + FastAPI app setup |
| `utils/mp.py` | MercadoPago API wrapper |
| `utils/randomorg.py` | RANDOM.ORG Signed API client |
| `utils/crypto.py` | Fernet encrypt/decrypt for MP tokens |

### Async Pattern

Everything is async throughout — SQLAlchemy with asyncpg, httpx for HTTP, discord.py 2.x, FastAPI. Always use `async/await` and `async with` for DB sessions.

## Lottery Verifiability

The lottery winner is determined by calling RANDOM.ORG's Signed API to get a random integer in `[1, n]` where `n` is the count of confirmed tickets. Tickets are sorted by ID, and the winner is the ticket at that index. The `Sorteo` record stores the RANDOM.ORG `serialNumber` and cryptographic `signature` so anyone can verify the result at random.org/verification. If `RANDOMORG_API_KEY` is not set, falls back to a local SHA256-based method.

## Deployment

Deployed to Railway via `Procfile`:
```
web: python main.py
```
The web server listens on `PORT` env var (default 8000).
