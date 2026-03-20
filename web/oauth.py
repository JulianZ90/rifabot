import os
from urllib.parse import urlencode
import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")


def google_auth_url(rifa_id: int, nonce: str) -> str:
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{WEBHOOK_BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": f"{rifa_id}:{nonce}",
        "access_type": "online",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


async def google_exchange_code(code: str) -> dict:
    """Retorna {"email", "name", "picture"}."""
    async with httpx.AsyncClient() as client:
        r = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{WEBHOOK_BASE_URL}/auth/google/callback",
            "grant_type": "authorization_code",
        })
        r.raise_for_status()
        access_token = r.json()["access_token"]

        r = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        data = r.json()

    if not data.get("email_verified"):
        raise ValueError("Email de Google no verificado.")

    return {
        "email": data["email"],
        "name": data.get("name", data["email"]),
        "picture": data.get("picture"),
        "provider": "google",
    }
