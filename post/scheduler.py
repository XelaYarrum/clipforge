"""Scheduler — decides what may be posted where, and when.

Two different kinds of cap, and they are NOT interchangeable:

  * TikTok and Instagram use a ROLLING 24-hour window. A slot frees up exactly
    24h after the post that filled it, so eligibility is computed from the
    timestamp of the cap-th most recent post, not from a clock reset.
  * YouTube's Data API quota is a CALENDAR day that resets at midnight PACIFIC.
    A videos.insert upload costs 1,600 units against a default 10,000/day, which
    is 6 uploads per day. Counting these against a rolling window (or against a
    UTC day) would silently overshoot the quota and start failing uploads.

A clip that can't post now is DEFERRED to its next eligible time. Nothing is
ever dropped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .connectors import PLATFORMS

PACIFIC = ZoneInfo("America/Los_Angeles")

ROLLING = "rolling"
CALENDAR_PACIFIC = "calendar_pacific"

# (window kind, cap). TikTok's 5 assumes an UNAUDITED app — raise to the audited
# limit only once the app audit actually passes.
CAPS = {
    "youtube": (CALENDAR_PACIFIC, 6),
    "tiktok": (ROLLING, 5),
    "instagram": (ROLLING, 100),
}


def setup_post_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            render_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT NOT NULL,
            description TEXT,
            post_id TEXT,
            not_before TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (render_id, platform),
            FOREIGN KEY (render_id) REFERENCES renders(id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            post_id TEXT,
            posted_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_log_platform_at ON post_log (platform, posted_at)"
    )
    connection.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pacific_day_start(moment: datetime) -> datetime:
    local = moment.astimezone(PACIFIC)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def _next_pacific_midnight(moment: datetime) -> datetime:
    local = moment.astimezone(PACIFIC)
    tomorrow = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return tomorrow.astimezone(timezone.utc)


def can_post(connection, platform: str, now: datetime | None = None):
    """Return (allowed, next_eligible). next_eligible is None when allowed."""
    now = now or _now()
    kind, cap = CAPS[platform]

    if kind == ROLLING:
        window_start = now - timedelta(hours=24)
        rows = connection.execute(
            "SELECT posted_at FROM post_log WHERE platform = ? AND posted_at > ?"
            " ORDER BY posted_at DESC",
            (platform, _iso(window_start)),
        ).fetchall()
        if len(rows) < cap:
            return True, None
        # The cap-th most recent post is the one holding the last slot; that slot
        # frees 24h after it landed.
        blocking = _parse(rows[cap - 1]["posted_at"])
        return False, blocking + timedelta(hours=24)

    day_start = _pacific_day_start(now)
    used = connection.execute(
        "SELECT COUNT(*) AS n FROM post_log WHERE platform = ? AND posted_at >= ?",
        (platform, _iso(day_start)),
    ).fetchone()["n"]
    if used < cap:
        return True, None
    return False, _next_pacific_midnight(now)


def enqueue(connection, render_id: int, platform: str, metadata: dict) -> None:
    stamp = _iso(_now())
    connection.execute(
        """
        INSERT OR IGNORE INTO post_queue
            (render_id, platform, status, title, description, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?, ?, ?)
        """,
        (render_id, platform, metadata["title"], metadata.get("description"), stamp, stamp),
    )
    connection.commit()


def due_items(connection, now: datetime | None = None):
    """Pending queue rows whose deferral has expired."""
    now = now or _now()
    return connection.execute(
        """
        SELECT post_queue.*, renders.output_path
        FROM post_queue
        JOIN renders ON renders.id = post_queue.render_id
        WHERE post_queue.status = 'pending'
          AND (post_queue.not_before IS NULL OR post_queue.not_before <= ?)
        ORDER BY post_queue.id
        """,
        (_iso(now),),
    ).fetchall()


def defer(connection, queue_id: int, until: datetime) -> None:
    connection.execute(
        "UPDATE post_queue SET not_before = ?, updated_at = ? WHERE id = ?",
        (_iso(until), _iso(_now()), queue_id),
    )
    connection.commit()


def mark_posted(connection, queue_id: int, platform: str, post_id: str) -> None:
    stamp = _iso(_now())
    connection.execute(
        "UPDATE post_queue SET status = 'posted', post_id = ?, updated_at = ? WHERE id = ?",
        (post_id, stamp, queue_id),
    )
    connection.execute(
        "INSERT INTO post_log (platform, post_id, posted_at) VALUES (?, ?, ?)",
        (platform, post_id, stamp),
    )
    connection.commit()


def mark_failed(connection, queue_id: int, message: str) -> None:
    connection.execute(
        "UPDATE post_queue SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
        (message[:500], _iso(_now()), queue_id),
    )
    connection.commit()
