"""Making a local clip briefly reachable at a public URL.

Only Instagram needs this. Its publishing API will not accept a file upload — it
fetches the video from a public https URL. YouTube and TikTok both take the file
directly, so this whole module exists for one platform.

The clip goes up, Instagram fetches it, the clip comes back down. It is not a
library or a CDN; it's a doorway open for about a minute.

Configured by config/credentials/hosting.json:
    {"endpoint_url": "...", "access_key": "...", "secret_key": "...",
     "bucket": "...", "public_base": "https://..."}
S3-compatible, so Cloudflare R2 / Backblaze B2 / S3 all work unchanged. Absent
that file, hosting is simply unconfigured and Instagram posts defer instead of
failing — the rest of the pipeline is unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from secrets import token_hex

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config" / "credentials" / "hosting.json"


class HostingNotConfigured(RuntimeError):
    pass


def config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    needed = ("endpoint_url", "access_key", "secret_key", "bucket", "public_base")
    if any(not str(cfg.get(k) or "").strip() for k in needed):
        return None
    return cfg


def is_configured() -> bool:
    return config() is not None


def _client(cfg: dict):
    import boto3  # imported lazily so the pipeline runs without hosting configured

    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
    )


def publish(local_path: str) -> tuple[str, str]:
    """Upload the clip. Returns (public_url, key) — pass the key to cleanup()."""
    cfg = config()
    if cfg is None:
        raise HostingNotConfigured(
            "Instagram needs the clip at a public URL, and no hosting is configured. "
            "Add config/credentials/hosting.json (see CONNECT_ACCOUNTS.txt)."
        )

    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(local_path)

    key = f"clipforge/{token_hex(12)}{path.suffix or '.mp4'}"
    client = _client(cfg)
    with path.open("rb") as f:
        client.upload_fileobj(
            f, cfg["bucket"], key, ExtraArgs={"ContentType": "video/mp4"}
        )

    url = f"{cfg['public_base'].rstrip('/')}/{key}"
    return url, key


def cleanup(key: str) -> None:
    """Take the clip back down. Never raises — a leftover file is not worth
    failing a post that already succeeded."""
    cfg = config()
    if cfg is None or not key:
        return
    try:
        _client(cfg).delete_object(Bucket=cfg["bucket"], Key=key)
    except Exception:
        pass
