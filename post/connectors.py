"""Platform connectors.

Every connector implements PostInterface. The MOCK connectors are the default and
touch no network at all — they record what WOULD have been posted. The live
connectors are deliberately unbuilt until the owner completes the one-time OAuth
grant for that platform; they fail loudly rather than silently pretending.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
MOCK_LOG = APP_DIR / "data" / "mock_posts.log"

# Per-platform metadata limits, from each platform's published docs.
LIMITS = {
    "youtube": {"title": 100, "description": 5000},
    "tiktok": {"title": 2200, "description": 2200},
    "instagram": {"title": 2200, "description": 2200},
}


class PostInterface:
    """The contract every connector honours. Method names are load-bearing."""

    PLATFORM = ""

    def validate_metadata(self, metadata: dict) -> bool:
        limits = LIMITS[self.PLATFORM]
        title = metadata.get("title") or ""
        description = metadata.get("description") or ""
        if not title.strip():
            return False
        return len(title) <= limits["title"] and len(description) <= limits["description"]

    def upload(self, clip_path: str, metadata: dict) -> str:
        raise NotImplementedError

    def check_status(self, post_id: str) -> str:
        raise NotImplementedError


class _MockConnector(PostInterface):
    """Records the post to a log instead of sending it anywhere."""

    def upload(self, clip_path: str, metadata: dict) -> str:
        if not Path(clip_path).exists():
            raise FileNotFoundError(f"clip not found: {clip_path}")

        stamp = datetime.now(timezone.utc).isoformat()
        post_id = f"mock_{self.PLATFORM}_{stamp}"

        MOCK_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": stamp,
            "platform": self.PLATFORM,
            "post_id": post_id,
            "clip": clip_path,
            "title": metadata.get("title"),
            "description": metadata.get("description"),
        }
        with open(MOCK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        return post_id

    def check_status(self, post_id: str) -> str:
        return "completed"


class MockYouTubeConnector(_MockConnector):
    PLATFORM = "youtube"


class MockTikTokConnector(_MockConnector):
    PLATFORM = "tiktok"


class MockInstagramConnector(_MockConnector):
    PLATFORM = "instagram"


PLATFORMS = ("youtube", "tiktok", "instagram")

_MOCK = {
    "youtube": MockYouTubeConnector,
    "tiktok": MockTikTokConnector,
    "instagram": MockInstagramConnector,
}


def get_connector(platform: str, live: bool = False) -> PostInterface:
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform}")
    if not live:
        return _MOCK[platform]()
    # Imported here, not at module top: the live connectors pull in httpx/boto3 and
    # the mock path must stay importable even if the posting extras aren't present.
    from .live import LIVE_CONNECTORS

    return LIVE_CONNECTORS[platform]()
