"""Where each platform's secrets live, and what's still missing.

Everything sits under config/credentials/, which is git-ignored. Nothing in here
ever prints a secret's value — status() reports only presence, because the whole
point is that the owner can see what's connected without the values leaking into
a screenshot or a log.
"""

from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CRED_DIR = APP_DIR / "config" / "credentials"

# What each platform needs, in the order it must be obtained.
REQUIREMENTS = {
    "youtube": {
        "client_file": "youtube.json",
        "keys": [],  # Google ships a JSON file rather than loose keys.
        "human": "the OAuth client JSON downloaded from Google Cloud Console",
    },
    "tiktok": {
        "client_file": "tiktok.json",
        "keys": ["client_key", "client_secret"],
        "human": "client key + client secret from developers.tiktok.com",
    },
    "instagram": {
        "client_file": "instagram.json",
        "keys": ["app_id", "app_secret", "ig_user_id"],
        "human": "app ID + app secret from developers.facebook.com, plus the Instagram user id",
    },
}


def _path(name: str) -> Path:
    return CRED_DIR / name


def load_client(platform: str) -> dict | None:
    """The app's own credentials (not the user's token). None if absent."""
    path = _path(REQUIREMENTS[platform]["client_file"])
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def token_path(platform: str) -> Path:
    return _path(f"{platform}_token.json")


def load_token(platform: str) -> dict | None:
    path = token_path(platform)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_token(platform: str, token: dict) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    path = token_path(platform)
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")


def save_client(platform: str, values: dict) -> None:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(REQUIREMENTS[platform]["client_file"])
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")


def status(platform: str) -> dict:
    """Presence only — never values."""
    req = REQUIREMENTS[platform]
    client = load_client(platform)
    missing = []

    if client is None:
        missing.append(req["human"])
    else:
        for key in req["keys"]:
            if not str(client.get(key) or "").strip():
                missing.append(key)

    token = load_token(platform)
    return {
        "platform": platform,
        "has_client": client is not None and not missing,
        "has_token": token is not None,
        "missing": missing,
        "ready": client is not None and not missing and token is not None,
        "needs": req["human"],
    }


def all_status() -> list[dict]:
    return [status(p) for p in REQUIREMENTS]
