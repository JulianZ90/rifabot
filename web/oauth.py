import os
from urllib.parse import urlencode
import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL", "")


def google_auth_url(nonce: str, rifa_id: int = None, next_url: str = None) -> str:
    # state encodes the context: "rifa:{id}:{nonce}" or "next:{url}:{nonce}"
    if next_url:
        state = f"next:{next_url}:{nonce}"
    else:
        state = f"rifa:{rifa_id}:{nonce}"
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{WEBHOOK_BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
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
        "uid": data["email"],
        "email": data["email"],
        "name": data.get("name", data["email"]),
        "picture": data.get("picture"),
        "provider": "google",
    }


def fb_auth_url(rifa_id: int, nonce: str) -> str:
    params = urlencode({
        "client_id": FACEBOOK_APP_ID,
        "redirect_uri": f"{WEBHOOK_BASE_URL}/auth/facebook/callback",
        "scope": "email,public_profile",
        "state": f"{rifa_id}:{nonce}",
        "response_type": "code",
    })
    return f"https://www.facebook.com/v19.0/dialog/oauth?{params}"


async def fb_exchange_code(code: str) -> dict:
    """Retorna {"uid", "email", "name", "picture", "provider"}."""
    async with httpx.AsyncClient() as client:
        r = await client.get("https://graph.facebook.com/v19.0/oauth/access_token", params={
            "client_id": FACEBOOK_APP_ID,
            "client_secret": FACEBOOK_APP_SECRET,
            "redirect_uri": f"{WEBHOOK_BASE_URL}/auth/facebook/callback",
            "code": code,
        })
        r.raise_for_status()
        access_token = r.json()["access_token"]

        r = await client.get("https://graph.facebook.com/me", params={
            "fields": "id,name,email",
            "access_token": access_token,
        })
        r.raise_for_status()
        data = r.json()

    return {
        "uid": data["id"],
        "email": data.get("email"),
        "name": data.get("name", f"Usuario Facebook {data['id']}"),
        "picture": None,
        "provider": "facebook",
    }
