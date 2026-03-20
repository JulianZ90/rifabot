# 🎟️ RifaBot

Bot de rifas para Discord con landing page web, pagos integrados via MercadoPago y sorteo verificable a través de [RANDOM.ORG](https://random.org).

---

## ¿Qué es RifaBot?

RifaBot permite organizar rifas transparentes y automatizadas directamente desde un servidor de Discord. Los administradores crean y gestionan las rifas con comandos slash, los participantes compran tickets pagando con MercadoPago, y el ganador se elige mediante un número verdaderamente aleatorio generado por RANDOM.ORG — un resultado que cualquier participante puede verificar de forma independiente.

Además de Discord, los participantes pueden sumarse desde la web usando su cuenta de Google o directamente con su email, sin necesidad de tener Discord.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────┐
│                      Railway (PaaS)                     │
│                                                         │
│  ┌─────────────────┐      ┌────────────────────────┐    │
│  │   Discord Bot   │      │    FastAPI Web Server  │    │
│  │  (discord.py)   │      │  (Uvicorn + Jinja2)    │    │
│  │                 │      │                        │    │
│  │  Slash commands │      │  /rifa/{id}  landing   │    │
│  │  para admins    │      │  /auth/google  SSO     │    │
│  │  y usuarios     │      │  /webhooks/mp  pagos   │    │
│  └────────┬────────┘      └───────────┬────────────┘    │
│           │                           │                 │
│           └──────────┬────────────────┘                 │
│                      │                                  │
│           ┌──────────▼────────────┐                     │
│           │  PostgreSQL (asyncpg) │                     │
│           │  SQLAlchemy async     │                     │
│           └───────────────────────┘                     │
└─────────────────────────────────────────────────────────┘
         │                          │
         ▼                          ▼
  MercadoPago API             RANDOM.ORG API
  (checkout + webhooks)       (sorteo verificable)
```

**Componentes principales:**

| Componente | Tecnología |
|---|---|
| Bot de Discord | discord.py 2.3.2 + slash commands |
| Web server | FastAPI + Jinja2 + Tailwind CSS |
| Base de datos | PostgreSQL · SQLAlchemy async (asyncpg) |
| Pagos | MercadoPago Checkout Pro |
| Sorteo | RANDOM.ORG Signed API |
| Autenticación web | Google OAuth 2.0 + email |
| Sesiones | Starlette SessionMiddleware (cookies firmadas) |
| Deploy | Railway |

---

## Por qué RANDOM.ORG

El sorteo podría hacerse con el generador de números pseudoaleatorios de Python (`random`), pero este tiene un problema de confianza: quien corre el código elige cuándo ejecutarlo, lo que en teoría permitiría repetir el sorteo hasta obtener el resultado deseado.

**RANDOM.ORG** resuelve esto de dos maneras:

1. **Aleatoriedad real:** los números se generan a partir de ruido atmosférico, no de algoritmos matemáticos deterministas.
2. **Resultado verificable:** la API Signed devuelve un `serialNumber` único y una firma criptográfica. Cualquier participante puede ingresar ese serial en [random.org/verification](https://random.org/verification) y confirmar que el número fue generado por RANDOM.ORG en ese momento exacto y no fue alterado.

Cuando se realiza un sorteo, el bot de Discord muestra el serial en el mensaje del ganador:

```
🏆 ¡Tenemos ganador!
Rifa: PlayStation 5
🎟️ Ticket ganador: TK-4F2A
👤 Ganador: @usuario
Verificable en random.org · Serial #12345
```

---

## Cómo se mapea el número al ganador

RANDOM.ORG devuelve un número entero en el rango `[0, total_tickets - 1]`. Los tickets confirmados se ordenan por fecha de creación (ID ascendente) y el número actúa como índice:

```
Tickets (ordenados por ID):
  #0  → TK-0001  (Usuario A, compró a las 15:00)
  #1  → TK-0002  (Usuario A, compró a las 15:00)
  #2  → TK-0003  (Usuario B, compró a las 15:02)
  ...
  #N  → TK-XXXX  (Usuario Z)

RANDOM.ORG genera: 2  →  TK-0003  →  Usuario B gana
```

Cada ticket tiene igual probabilidad. Comprar más tickets aumenta las chances proporcionalmente.

---

## Comandos de Discord

### Comandos de administrador

> Requieren permiso de **Administrador** en el servidor.

---

#### `/rifa_setup`
Configura el token de MercadoPago para el servidor. Debe ejecutarse una sola vez (o cuando se renueve el token).

| Parámetro | Descripción |
|---|---|
| `access_token` | Token de acceso de MercadoPago (se guarda encriptado) |

---

#### `/rifa_crear`
Crea una nueva rifa en el canal donde se ejecuta el comando. Publica un embed con la información y el ID de la rifa.

| Parámetro | Requerido | Descripción |
|---|---|---|
| `nombre` | ✅ | Nombre del premio |
| `precio` | ✅ | Precio por ticket en ARS. Usar `0` para rifa gratuita |
| `descripcion` | ❌ | Descripción del premio |
| `max_tickets` | ❌ | Máximo de tickets por persona (default: 10) |

> **Rifas gratuitas:** si el precio es `$0`, se muestra como **GRATIS** en la landing web, no requiere pago y cada persona puede participar una sola vez.

---

#### `/rifa_sortear`
Realiza el sorteo de una rifa. Llama a RANDOM.ORG para obtener el número ganador, guarda el resultado con el serial de verificación y anuncia al ganador en el canal.

| Parámetro | Descripción |
|---|---|
| `rifa_id` | ID de la rifa a sortear |

---

#### `/rifa_borrar`
Cancela una rifa (borrado lógico — no se elimina de la base de datos). Los tickets pendientes quedan rechazados. No se puede cancelar una rifa que ya fue sorteada.

| Parámetro | Descripción |
|---|---|
| `rifa_id` | ID de la rifa a cancelar |

---

### Comandos para todos los usuarios

---

#### `/participar`
Compra tickets para una rifa. Genera los tickets en estado pendiente y devuelve un botón de pago de MercadoPago. Los tickets se cancelan automáticamente si el pago no se completa en 15 minutos.

| Parámetro | Descripción |
|---|---|
| `rifa_id` | ID de la rifa |
| `cantidad` | Cantidad de tickets (default: 1, máximo: 50) |

---

#### `/mis_tickets`
Muestra los tickets propios en una rifa determinada, separados por estado (confirmados / pendientes de pago).

| Parámetro | Descripción |
|---|---|
| `rifa_id` | ID de la rifa |

---

#### `/rifa_lista`
Muestra las rifas abiertas en el servidor.

- **Usuarios regulares:** ven nombre, ID y precio por ticket.
- **Administradores:** además ven tickets confirmados y pendientes de pago.

---

#### `/rifa_estado`
Muestra el estado actual de una rifa específica con un embed detallado (tickets vendidos, pendientes, precio, fecha de cierre si tiene).

| Parámetro | Descripción |
|---|---|
| `rifa_id` | ID de la rifa |

---

## Landing page web

Cada rifa tiene una URL pública:

```
https://rifabot-production.up.railway.app/rifa/{id}
```

Desde la landing, los participantes pueden:

1. **Iniciar sesión con Google** — se registran con su nombre y email de Google.
2. **Ingresar su email** — sin SSO, solo con dirección de correo.
3. **Comprar tickets** y ser redirigidos al checkout de MercadoPago.

Para rifas gratuitas, la landing muestra el botón "Participar gratis" sin redirigir a MercadoPago.

---

## Flujo de pago

```
Usuario /participar (Discord) o landing web
            │
            ▼
   Tickets creados (estado: pendiente)
            │
            ▼
   MercadoPago Checkout Pro
            │
            ├── Pago aprobado ──► Webhook /webhooks/mp
            │                           │
            │                     Tickets → confirmado
            │                     Notificación en Discord
            │
            └── Sin pago (15 min) ──► Tickets → rechazado
```

---

## Variables de entorno

```env
# Discord
DISCORD_TOKEN=

# Base de datos
DATABASE_URL=postgresql+asyncpg://...

# Web
WEBHOOK_BASE_URL=https://rifabot-production.up.railway.app
SESSION_SECRET_KEY=       # clave para firmar cookies de sesión

# Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# RANDOM.ORG
RANDOMORG_API_KEY=        # si no se configura, usa random local como fallback

# Encriptación del token de MP
ENCRYPTION_KEY=

# Railway (inyectado automáticamente)
PORT=
```

---

## Estructura del proyecto

```
rifabot/
├── main.py                  ← Entry point: levanta bot + web server
├── bot/
│   └── commands.py          ← Comandos slash de Discord
├── core/
│   └── rifa_service.py      ← Lógica de negocio
├── db/
│   ├── models.py            ← Modelos SQLAlchemy (Server, Rifa, Ticket, Sorteo)
│   └── database.py          ← Conexión async PostgreSQL
├── web/
│   ├── routes.py            ← Rutas FastAPI (landing, OAuth, pagos, legales)
│   ├── oauth.py             ← Helpers Google OAuth 2.0
│   └── templates/           ← Jinja2 (base, rifa, exito, privacidad, terminos)
├── webhooks/
│   └── mp_webhook.py        ← Webhook MercadoPago
└── utils/
    ├── crypto.py            ← Encriptación tokens MP con Fernet
    ├── mp.py                ← Wrapper MercadoPago SDK
    └── randomorg.py         ← Cliente RANDOM.ORG Signed API
```

---

## Licencia

Uso privado. Todos los derechos reservados — Encapsulados.
