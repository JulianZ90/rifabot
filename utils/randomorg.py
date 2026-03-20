import os
import httpx

RANDOMORG_API_KEY = os.getenv("RANDOMORG_API_KEY", "")


async def sortear_indice(total: int) -> tuple[int, str | None, str | None]:
    """
    Pide a RANDOM.ORG un entero aleatorio en [0, total-1] usando la Signed API.
    Retorna (indice, serial_number, signature).

    Si RANDOMORG_API_KEY no está configurado, usa random local como fallback.
    """
    if not RANDOMORG_API_KEY:
        import random
        return random.randint(0, total - 1), None, None

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://api.random.org/json-rpc/4/invoke",
            json={
                "jsonrpc": "2.0",
                "method": "generateSignedIntegers",
                "params": {
                    "apiKey": RANDOMORG_API_KEY,
                    "n": 1,
                    "min": 0,
                    "max": total - 1,
                    "replacement": True,
                },
                "id": 1,
            },
        )
        r.raise_for_status()
        data = r.json()

    result = data["result"]
    indice = result["random"]["data"][0]
    serial = str(result["random"]["serialNumber"])
    signature = result["signature"]
    return indice, serial, signature
