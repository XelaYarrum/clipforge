"""The real posting connectors.

Each one implements the same PostInterface the mocks do, so run.py doesn't know or
care which it's talking to. They are fully written; each is inert only because its
credentials don't exist yet, and each says exactly what it's missing rather than
failing vaguely.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

from . import credentials, hosting, oauth
from .connectors import PostInterface


class NotConnected(RuntimeError):
    """Raised when a platform hasn't been granted yet. The scheduler treats this
    as 'not now' rather than 'this clip is broken'."""


def _require_token(platform: str) -> str:
    token = oauth.valid_access_token(platform)
    if not token:
        st = credentials.status(platform)
        missing = ", ".join(st["missing"]) if st["missing"] else "the one-time Connect click"
        raise NotConnected(f"{platform} is not connected yet — still needs: {missing}")
    return token


# ===================================================================== YouTube


class LiveYouTube(PostInterface):
    PLATFORM = "youtube"

    def upload(self, clip_path: str, metadata: dict) -> str:
        token = _require_token("youtube")
        path = Path(clip_path)

        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata.get("description") or "",
                "categoryId": "22",
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }

        # Resumable upload: start a session, then send the bytes to the session URL.
        start = httpx.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(path.stat().st_size),
            },
            json=body,
            timeout=60,
        )
        if start.status_code >= 400:
            raise RuntimeError(f"youtube upload init failed: {start.text[:400]}")

        session_url = start.headers.get("location")
        if not session_url:
            raise RuntimeError("youtube did not return an upload session URL")

        with path.open("rb") as f:
            done = httpx.put(
                session_url,
                content=f.read(),
                headers={"Content-Type": "video/*"},
                timeout=1800,
            )
        if done.status_code >= 400:
            raise RuntimeError(f"youtube upload failed: {done.text[:400]}")

        return done.json()["id"]

    def check_status(self, post_id: str) -> str:
        token = _require_token("youtube")
        r = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "status", "id": post_id},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        items = r.json().get("items") or []
        if not items:
            return "unknown"
        return items[0]["status"].get("uploadStatus", "unknown")


# ====================================================================== TikTok


class LiveTikTok(PostInterface):
    PLATFORM = "tiktok"

    def upload(self, clip_path: str, metadata: dict) -> str:
        token = _require_token("tiktok")
        path = Path(clip_path)
        size = path.stat().st_size

        # Ask the account what privacy levels it actually allows. An unaudited app
        # only gets SELF_ONLY, so we take what's offered rather than assuming.
        info = httpx.post(
            "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if info.status_code >= 400:
            raise RuntimeError(f"tiktok creator_info failed: {info.text[:400]}")
        options = (info.json().get("data") or {}).get("privacy_level_options") or []
        privacy = "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in options else "SELF_ONLY"

        init = httpx.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": metadata["title"],
                    "privacy_level": privacy,
                    "disable_comment": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": size,
                    "chunk_size": size,
                    "total_chunk_count": 1,
                },
            },
            timeout=60,
        )
        if init.status_code >= 400:
            raise RuntimeError(f"tiktok init failed: {init.text[:400]}")

        data = init.json()["data"]
        publish_id = data["publish_id"]

        with path.open("rb") as f:
            put = httpx.put(
                data["upload_url"],
                content=f.read(),
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{size - 1}/{size}",
                },
                timeout=1800,
            )
        if put.status_code >= 400:
            raise RuntimeError(f"tiktok upload failed: {put.text[:400]}")

        return publish_id

    def check_status(self, post_id: str) -> str:
        token = _require_token("tiktok")
        r = httpx.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": post_id},
            timeout=30,
        )
        return (r.json().get("data") or {}).get("status", "unknown")


# =================================================================== Instagram


class LiveInstagram(PostInterface):
    PLATFORM = "instagram"

    def upload(self, clip_path: str, metadata: dict) -> str:
        token = _require_token("instagram")
        client = credentials.load_client("instagram") or {}
        ig_user_id = client.get("ig_user_id")
        if not ig_user_id:
            raise NotConnected("instagram is missing ig_user_id")

        # Instagram will not take the file — it fetches it. So the clip goes up,
        # gets fetched, and comes straight back down.
        public_url, key = hosting.publish(clip_path)
        try:
            create = httpx.post(
                f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": public_url,
                    "caption": metadata.get("description") or metadata["title"],
                    "access_token": token,
                },
                timeout=120,
            )
            if create.status_code >= 400:
                raise RuntimeError(f"instagram container failed: {create.text[:400]}")
            container_id = create.json()["id"]

            # The container must finish processing before it can be published.
            deadline = time.time() + 300
            while time.time() < deadline:
                st = httpx.get(
                    f"https://graph.facebook.com/v21.0/{container_id}",
                    params={"fields": "status_code", "access_token": token},
                    timeout=30,
                ).json()
                code = st.get("status_code")
                if code == "FINISHED":
                    break
                if code == "ERROR":
                    raise RuntimeError("instagram failed to process the video")
                time.sleep(5)
            else:
                raise RuntimeError("instagram processing timed out after 5 minutes")

            publish = httpx.post(
                f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
                data={"creation_id": container_id, "access_token": token},
                timeout=60,
            )
            if publish.status_code >= 400:
                raise RuntimeError(f"instagram publish failed: {publish.text[:400]}")
            return publish.json()["id"]
        finally:
            # Always take the file back down, even if the post failed.
            hosting.cleanup(key)

    def check_status(self, post_id: str) -> str:
        return "completed" if post_id else "unknown"


LIVE_CONNECTORS = {
    "youtube": LiveYouTube,
    "tiktok": LiveTikTok,
    "instagram": LiveInstagram,
}
