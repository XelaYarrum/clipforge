"""Metadata writer — turns a scored clip into per-platform title/description.

Deterministic and offline. The clip finder already produced a hook line, which is
the best short-form title we have; this shapes it to each platform's limits and
conventions rather than inventing new copy.
"""

from __future__ import annotations

from .connectors import LIMITS

DEFAULT_HANDLE = "@clipforge"

# YouTube Shorts needs the #shorts tag to be treated as a short.
PLATFORM_TAGS = {
    "youtube": ["#shorts"],
    "tiktok": ["#fyp"],
    "instagram": ["#reels"],
}


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build(
    platform: str,
    hook: str | None,
    clip_text: str | None,
    profile: dict | None = None,
) -> dict:
    """Build metadata for one clip on one platform."""
    limits = LIMITS[platform]
    profile = profile or {}

    title = (hook or "").strip() or (clip_text or "").strip() or "Untitled clip"
    title = _truncate(title, limits["title"])

    handle = (profile.get("handle") or "").strip() or DEFAULT_HANDLE

    extra = (profile.get("extra_hashtags") or "").replace(",", " ").split()
    extra = [t if t.startswith("#") else f"#{t}" for t in extra if t.strip()]
    # The platform's own tag first — it's the one that decides placement.
    tags = " ".join(PLATFORM_TAGS[platform] + extra)

    body = (clip_text or "").strip()
    parts = [p for p in (body, handle, tags) if p]
    description = _truncate("\n\n".join(parts), limits["description"])

    return {"title": title, "description": description, "platform": platform}
