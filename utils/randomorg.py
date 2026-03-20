import os
import logging
import httpx

logger = logging.getLogger(__name__)

RANDOMORG_API_KEY = os.getenv("RANDOMORG_API_KEY", "")


async def sortear_indice(total: int) -> tuple[int, str | None, str | None]:
    """
    Pide a RANDOM.ORG un entero aleatorio en [0, total-1] usando la Signed API.
    Retorna (indice, serial_number, signature).

    Si RANDOMORG_API_KEY no está configurado, usa random local como fallback.
    """
    if not RANDOMORG_API_KEY:
        import random
        indice = random.randint(0, total - 1)
        logger.warning(f"RANDOMORG_API_KEY no configurado — usando random local. indice={indice} total={total}")
        return indice, None, None

    payload = {
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
    }
    logger.info(f"RANDOM.ORG request: min=0 max={total - 1} total={total}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post("https://api.random.org/json-rpc/4/invoke", json=payload)
        r.raise_for_status()
        data = r.json()

    logger.info(f"RANDOM.ORG response: {data}")

    result = data["result"]
    indice = result["random"]["data"][0]
    serial = str(result["random"]["serialNumber"])
    signature = result["signature"]
    logger.info(f"RANDOM.ORG resultado: indice={indice} serial={serial}")
    return indice, serial, signature
