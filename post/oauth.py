"""OAuth flows for the three platforms.

Every callback comes back to the dashboard itself:
    http://127.0.0.1:8000/oauth/<platform>/callback
which is the redirect URI registered in each platform's developer portal. Keeping
one redirect shape across all three means one thing to get right, not three.

Alexander clicks Connect once per platform. After that the tokens refresh on their
own and nothing needs a human again.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import httpx

from . import credentials

REDIRECT_BASE = "http://127.0.0.1:8000/oauth"

SCOPES = {
    "youtube": ["https://www.googleapis.com/auth/youtube.upload"],
    # video.publish is the one that actually posts; creator.info.query is required
    # before a direct post so we know which privacy levels the account allows.
    "tiktok": ["user.info.basic", "video.publish", "video.upload"],
    "instagram": ["instagram_business_basic", "instagram_business_content_publish"],
}

# Short-lived CSRF state, kept in memory: a restart just means clicking Connect again.
_pending_state: dict[str, str] = {}


def redirect_uri(platform: str) -> str:
    return f"{REDIRECT_BASE}/{platform}/callback"


def _new_state(platform: str) -> str:
    state = secrets.token_urlsafe(24)
    _pending_state[platform] = state
    return state


def check_state(platform: str, state: str) -> bool:
    expected = _pending_state.pop(platform, None)
    return bool(expected) and secrets.compare_digest(expected, state)


# ----------------------------------------------------------------- authorize URLs


def authorize_url(platform: str) -> str:
    client = credentials.load_client(platform)
    if client is None:
        raise RuntimeError(f"{platform}: credentials not added yet")
    state = _new_state(platform)

    if platform == "youtube":
        installed = client.get("installed") or client.get("web") or {}
        client_id = installed.get("client_id")
        if not client_id:
            raise RuntimeError("youtube.json doesn't look like an OAuth client file")
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri("youtube"),
                "response_type": "code",
                "scope": " ".join(SCOPES["youtube"]),
                "access_type": "offline",
                "prompt": "consent",  # forces a refresh_token on re-consent
                "state": state,
            }
        )

    if platform == "tiktok":
        return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
            {
                "client_key": client["client_key"],
                "scope": ",".join(SCOPES["tiktok"]),
                "response_type": "code",
                "redirect_uri": redirect_uri("tiktok"),
                "state": state,
            }
        )

    if platform == "instagram":
        return "https://www.facebook.com/v21.0/dialog/oauth?" + urlencode(
            {
                "client_id": client["app_id"],
                "redirect_uri": redirect_uri("instagram"),
                "response_type": "code",
                "scope": ",".join(SCOPES["instagram"]),
                "state": state,
            }
        )

    raise ValueError(platform)


# ----------------------------------------------------------------- code -> token


def _stamp(token: dict) -> dict:
    # Store an absolute expiry: a relative expires_in is meaningless once saved.
    if "expires_in" in token:
        token["expires_at"] = time.time() + float(token["expires_in"]) - 60
    return token


def exchange_code(platform: str, code: str) -> dict:
    client = credentials.load_client(platform)
    if client is None:
        raise RuntimeError(f"{platform}: credentials not added yet")

    if platform == "youtube":
        installed = client.get("installed") or client.get("web") or {}
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": installed["client_id"],
                "client_secret": installed["client_secret"],
                "redirect_uri": redirect_uri("youtube"),
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    elif platform == "tiktok":
        r = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client["client_key"],
                "client_secret": client["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri("tiktok"),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
    elif platform == "instagram":
        r = httpx.get(
            "https://graph.facebook.com/v21.0/oauth/access_token",
            params={
                "client_id": client["app_id"],
                "client_secret": client["app_secret"],
                "redirect_uri": redirect_uri("instagram"),
                "code": code,
            },
            timeout=30,
        )
    else:
        raise ValueError(platform)

    if r.status_code >= 400:
        raise RuntimeError(f"{platform} token exchange failed: {r.text[:400]}")

    token = _stamp(r.json())
    credentials.save_token(platform, token)
    return token


def refresh(platform: str) -> dict | None:
    """Refresh in place. Returns the new token, or None if it can't be refreshed."""
    client = credentials.load_client(platform)
    token = credentials.load_token(platform)
    if client is None or token is None:
        return None

    if platform == "youtube":
        if not token.get("refresh_token"):
            return None
        installed = client.get("installed") or client.get("web") or {}
        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": installed["client_id"],
                "client_secret": installed["client_secret"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    elif platform == "tiktok":
        if not token.get("refresh_token"):
            return None
        r = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client["client_key"],
                "client_secret": client["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            },
            timeout=30,
        )
    else:
        # Instagram long-lived tokens are exchanged, not refreshed on this endpoint.
        return None

    if r.status_code >= 400:
        return None

    new = _stamp(r.json())
    # A refresh response often omits the refresh_token; keep the old one.
    new.setdefault("refresh_token", token.get("refresh_token"))
    credentials.save_token(platform, new)
    return new


def valid_access_token(platform: str) -> str | None:
    """The access token, refreshed first if it's expired."""
    token = credentials.load_token(platform)
    if token is None:
        return None
    if token.get("expires_at") and token["expires_at"] < time.time():
        token = refresh(platform) or token
    return token.get("access_token")
