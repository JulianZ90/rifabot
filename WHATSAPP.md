# WhatsApp Bot — Plan de Implementación

Automatización del flujo de rifas numeradas por WhatsApp usando Twilio, reutilizando la infraestructura existente de RifaBot (FastAPI, MercadoPago, RANDOM.ORG, PostgreSQL).

## Arquitectura objetivo

```
Admin (WhatsApp) ──┐
                   ├──▶ POST /webhook/whatsapp ──▶ rifa_service.py ──▶ DB
Participante (WA) ─┘
                                                 ──▶ utils/twilio_wa.py ──▶ Twilio API ──▶ WhatsApp
MP webhook ──────────────────────────────────────▶ POST /webhook/mp ──▶ confirmar tickets ──▶ notificar WA
```

La integración es **aditiva** — Discord sigue funcionando sin cambios.

---

## Nuevas variables de entorno

```env
TWILIO_ACCOUNT_SID=ACxxx...
TWILIO_AUTH_TOKEN=xxx...
TWILIO_WHATSAPP_FROM=+14155238886   # número Twilio (sandbox o aprobado)
WHATSAPP_ADMIN_PHONE=+5491155559999  # teléfono del admin (tu amigo)
```

Twilio Sandbox es gratuito para pruebas. Para producción se necesita un número aprobado por Meta.

---

## Cambios en base de datos

Columnas nuevas (se agregan automáticamente en el arranque con `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`):

| Tabla | Columna nueva | Tipo | Descripción |
|-------|--------------|------|-------------|
| `servers` | `whatsapp_admin_phone` | `VARCHAR UNIQUE` | Teléfono del admin que creó la rifa |
| `servers` | `discord_server_id` | quitar `NOT NULL` | Ahora opcional (para servers WA-only) |
| `rifas` | `total_numeros` | `INTEGER` | Si es NULL → usa códigos TK-XXXX; si es N → rifa numerada 1..N |
| `tickets` | `numero` | `INTEGER` | Número específico elegido (solo rifas numeradas) |

También se agrega `whatsapp` al enum `PlataformaOrigen`.

---

## Archivos a crear / modificar

### Nuevo: `utils/twilio_wa.py`

Wrapper async para enviar mensajes de WhatsApp via Twilio REST API:

```python
async def enviar_mensaje(to: str, body: str) -> None
```

- Usa `httpx` (ya en requirements) para no bloquear el event loop
- Si `TWILIO_ACCOUNT_SID` no está configurado, loguea warning y no falla

### Nuevo: `webhooks/whatsapp_webhook.py`

Router FastAPI con un único endpoint `POST /webhook/whatsapp`.

Twilio envía `application/x-www-form-urlencoded` con:
- `From`: `whatsapp:+549...`
- `Body`: texto del mensaje

El handler identifica si el remitente es admin (compara con `WHATSAPP_ADMIN_PHONE`) y despacha al flujo correspondiente.

**Estado de conversación** — dict en memoria, se pierde si el proceso reinicia (aceptable):

```python
conversation_states: dict[str, dict] = {}
# { "+549...": { "state": "idle" | "eligiendo_numero", "rifa_id": int | None } }
```

### Modificar: `db/models.py`

- Agregar `whatsapp` a `PlataformaOrigen`
- `Server.discord_server_id`: `nullable=True`
- Agregar `Server.whatsapp_admin_phone`
- Agregar `Rifa.total_numeros`
- Agregar `Ticket.numero`

### Modificar: `db/database.py`

Agregar las sentencias `ALTER TABLE` después de `create_all()` en `init_db()`.

### Modificar: `core/rifa_service.py`

Funciones nuevas (sin tocar las existentes):

```python
get_or_create_server_wa(session, admin_phone)
get_rifas_abiertas_wa(session, admin_phone)
get_numeros_disponibles(session, rifa_id) -> list[int]
crear_ticket_numerado(session, rifa_id, numero, plataforma_uid, nombre, telefono) -> Ticket
crear_rifa_wa(session, admin_phone, nombre, descripcion, precio, total_numeros, max_tickets) -> Rifa
cancelar_rifa_wa(session, rifa_id, admin_phone) -> bool
```

### Modificar: `webhooks/mp_webhook.py`

Después de confirmar tickets, si el ticket tiene `telefono_participante`, enviar notificación WA:

```python
await enviar_mensaje(ticket.telefono_participante,
    f"✅ ¡Pago confirmado! Tu número {ticket.numero or ticket.codigo} para '{rifa.nombre}' está asegurado.")
```

Además, incluir el nuevo router:

```python
from webhooks.whatsapp_webhook import router as wa_router
app.include_router(wa_router)
```

### Modificar: `requirements.txt`

Agregar `twilio==9.x.x` (o usar solo `httpx` que ya está instalado para llamar a la API REST directamente, evitando la dependencia).

---

## Flujo completo de conversación

### Comandos del admin

| Mensaje | Acción |
|---------|--------|
| `crear Moto 5000 100` | Crea rifa "Moto" a $5000, con 100 números |
| `crear Moto 5000` | Crea rifa con códigos aleatorios (sin números) |
| `lista` | Lista rifas abiertas con IDs |
| `estado 3` | Muestra estado, tickets confirmados y pendientes de la rifa 3 |
| `sortear 3` | Ejecuta sorteo, notifica ganador por WA al admin y al ganador |
| `cerrar 3` | Cierra la rifa sin sortear |
| `cancelar 3` | Cancela la rifa y marca tickets pendientes como rechazados |
| `setup APP-US...abc` | Configura el token de MercadoPago |
| `ayuda` | Muestra estos comandos |

### Comandos del participante

| Mensaje / Estado | Acción |
|-----------------|--------|
| cualquier texto (estado `idle`) | Lista rifas disponibles |
| `participar 3` | Muestra números disponibles de la rifa 3 |
| `42` (estado `eligiendo_numero`) | Reserva el número 42, devuelve link de MP |
| `mis tickets` | Muestra tickets confirmados del usuario |
| `cancelar` | Vuelve a idle |
| `ayuda` | Muestra estos comandos |

### Ejemplo de sesión completa

```
[Admin]
Admin: crear Moto Honda 5000 100
Bot:   ✅ Rifa #4 creada: "Moto Honda" — $5000/número — 100 números (1 al 100)

[Participante]
Juan:  hola
Bot:   Rifas disponibles:
       #4 — Moto Honda | $5000 | 98 números libres

Juan:  participar 4
Bot:   Rifa: Moto Honda — $5000 por número
       Números disponibles: 1,2,4,5,7,8,9... (98 libres)
       Escribí el número que querés:

Juan:  42
Bot:   Reservé el número 42 🎟️
       Tenés 15 min para pagar antes de que se libere:
       👉 https://www.mercadopago.com.ar/checkout/...

[Webhook MP — pago aprobado]
Bot → Juan:  ✅ ¡Pago confirmado! Número 42 de "Moto Honda" está asegurado. ¡Buena suerte!

[Admin]
Admin: sortear 4
Bot → Admin: 🎉 Ganador de "Moto Honda":
             Número 42 — Juan García (+5491155559999)
             Verificable en random.org · Serial #9812345

Bot → Juan:  🏆 ¡Felicitaciones! Ganaste la rifa "Moto Honda".
             Tu número 42 fue el elegido. El admin se va a contactar con vos.
```

---

## Configuración de Twilio

### Sandbox (gratis para pruebas)

1. Crear cuenta en [twilio.com](https://twilio.com)
2. Ir a **Messaging > Try it out > Send a WhatsApp message**
3. Cada participante debe enviar `join [palabra-sandbox]` al número de Twilio
4. En la configuración del sandbox, setear:
   - **When a message comes in**: `https://tu-url-railway/webhook/whatsapp` (POST)
5. Obtener `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` del dashboard
6. El número del sandbox es `+14155238886` (puede variar)

### Producción

Requiere solicitar un número de WhatsApp Business aprobado por Meta a través de Twilio. El proceso tarda algunos días.

---

## Orden de implementación sugerido

1. `db/models.py` — agregar columnas y enum
2. `db/database.py` — agregar migraciones en `init_db()`
3. `utils/twilio_wa.py` — wrapper de envío
4. `core/rifa_service.py` — funciones nuevas de WA
5. `webhooks/whatsapp_webhook.py` — handler con máquina de estados
6. `webhooks/mp_webhook.py` — notificación WA al confirmar pago + incluir router
7. `requirements.txt` — agregar twilio (o no, si se usa httpx directo)
8. Deploy en Railway + configurar webhook en Twilio Console
