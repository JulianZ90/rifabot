import httpx


async def crear_preferencia_pago(
    access_token: str,
    titulo: str,
    precio: float,
    external_reference: str,
    notification_url: str,
) -> tuple[str, str]:
    """Crea una preferencia de pago en MP. Retorna (preference_id, init_point)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {
        "items": [{"title": titulo, "quantity": 1, "unit_price": precio, "currency_id": "ARS"}],
        "external_reference": external_reference,
        "notification_url": notification_url,
        "back_urls": {
            "success": notification_url,
            "failure": notification_url,
            "pending": notification_url,
        },
        "auto_return": "approved",
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.mercadopago.com/checkout/preferences",
            json=payload,
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    return data["id"], data["init_point"]


async def verificar_pago(access_token: str, payment_id: str) -> dict | None:
    """Obtiene los datos de un pago de MP. Retorna None si no existe."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.mercadopago.com/v1/payments/{payment_id}",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
