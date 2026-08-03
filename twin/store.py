"""The Digital Human's records.

Four tables, added to the existing clipforge.db rather than a second database,
because a twin video ends up in the same renders/posting queue as a clipped one
and a join across two files would be the first thing to rot.

  twin_voices   one row per cloned voice — the reference recording and the handle
  twin_plates   one row per piece of real footage of him (a "presenter plate")
  twin_scripts  one row per generated script, with the brief that produced it
  twin_videos   one row per assembled video, and every stage's artifact path

twin_videos carries a path per stage on purpose. When a render fails at compose,
the narration WAV is still on disk and still good — re-running should not re-do
the expensive stages. That is the same idempotence rule run.py already follows.
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


def setup(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS twin_voices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            handle TEXT NOT NULL,
            reference_path TEXT,
            reference_seconds REAL,
            status TEXT NOT NULL DEFAULT 'ready',
            error_message TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS twin_plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'presenter',
            path TEXT NOT NULL UNIQUE,
            seconds REAL,
            width INTEGER,
            height INTEGER,
            has_audio INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS twin_scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brief TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT,
            hook TEXT,
            body TEXT NOT NULL,
            call_to_action TEXT,
            seconds_estimate REAL,
            model_used TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS twin_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,
            voice_id INTEGER,
            plate_id INTEGER,
            screen_path TEXT,
            audio_path TEXT,
            face_path TEXT,
            output_path TEXT,
            stage TEXT NOT NULL DEFAULT 'queued',
            status TEXT NOT NULL DEFAULT 'queued',
            error_message TEXT,
            words_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (script_id) REFERENCES twin_scripts(id),
            FOREIGN KEY (voice_id) REFERENCES twin_voices(id),
            FOREIGN KEY (plate_id) REFERENCES twin_plates(id)
        )
        """
    )
    connection.commit()


# ---------------------------------------------------------------- voices


def add_voice(db, name: str, provider: str, handle: str, reference_path: str, seconds: float) -> int:
    with closing(db()) as c:
        setup(c)
        existing = c.execute("SELECT COUNT(*) AS n FROM twin_voices").fetchone()["n"]
        cursor = c.execute(
            """
            INSERT INTO twin_voices
            (name, provider, handle, reference_path, reference_seconds, status, is_default, created_at)
            VALUES (?, ?, ?, ?, ?, 'ready', ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                provider=excluded.provider, handle=excluded.handle,
                reference_path=excluded.reference_path,
                reference_seconds=excluded.reference_seconds,
                status='ready', error_message=NULL
            """,
            (name, provider, handle, reference_path, seconds, 1 if existing == 0 else 0, _now()),
        )
        c.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        return c.execute("SELECT id FROM twin_voices WHERE name = ?", (name,)).fetchone()["id"]


def voices(db) -> list[dict]:
    with closing(db()) as c:
        setup(c)
        return [dict(r) for r in c.execute("SELECT * FROM twin_voices ORDER BY id DESC").fetchall()]


def default_voice(db) -> dict | None:
    with closing(db()) as c:
        setup(c)
        row = c.execute(
            "SELECT * FROM twin_voices WHERE status='ready' ORDER BY is_default DESC, id ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def set_default_voice(db, voice_id: int) -> None:
    with closing(db()) as c:
        setup(c)
        c.execute("UPDATE twin_voices SET is_default = 0")
        c.execute("UPDATE twin_voices SET is_default = 1 WHERE id = ?", (voice_id,))
        c.commit()


def delete_voice(db, voice_id: int) -> None:
    with closing(db()) as c:
        setup(c)
        c.execute("DELETE FROM twin_voices WHERE id = ?", (voice_id,))
        c.commit()


# ---------------------------------------------------------------- plates


def add_plate(db, name: str, kind: str, path: str, probe: dict, notes: str = "") -> int:
    with closing(db()) as c:
        setup(c)
        existing = c.execute("SELECT COUNT(*) AS n FROM twin_plates WHERE kind = ?", (kind,)).fetchone()["n"]
        cursor = c.execute(
            """
            INSERT INTO twin_plates
            (name, kind, path, seconds, width, height, has_audio, notes, is_default, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                name=excluded.name, seconds=excluded.seconds, width=excluded.width,
                height=excluded.height, has_audio=excluded.has_audio, notes=excluded.notes
            """,
            (
                name, kind, path,
                probe.get("seconds"), probe.get("width"), probe.get("height"),
                1 if probe.get("has_audio") else 0, notes,
                1 if existing == 0 else 0, _now(),
            ),
        )
        c.commit()
        if cursor.lastrowid:
            return cursor.lastrowid
        return c.execute("SELECT id FROM twin_plates WHERE path = ?", (path,)).fetchone()["id"]


def plates(db, kind: str | None = None) -> list[dict]:
    with closing(db()) as c:
        setup(c)
        if kind:
            rows = c.execute("SELECT * FROM twin_plates WHERE kind = ? ORDER BY id DESC", (kind,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM twin_plates ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def default_plate(db, kind: str = "presenter") -> dict | None:
    with closing(db()) as c:
        setup(c)
        row = c.execute(
            "SELECT * FROM twin_plates WHERE kind = ? ORDER BY is_default DESC, id ASC LIMIT 1", (kind,)
        ).fetchone()
    return dict(row) if row else None


def set_default_plate(db, plate_id: int) -> None:
    with closing(db()) as c:
        setup(c)
        row = c.execute("SELECT kind FROM twin_plates WHERE id = ?", (plate_id,)).fetchone()
        if not row:
            return
        c.execute("UPDATE twin_plates SET is_default = 0 WHERE kind = ?", (row["kind"],))
        c.execute("UPDATE twin_plates SET is_default = 1 WHERE id = ?", (plate_id,))
        c.commit()


def delete_plate(db, plate_id: int) -> None:
    with closing(db()) as c:
        setup(c)
        c.execute("DELETE FROM twin_plates WHERE id = ?", (plate_id,))
        c.commit()


# ---------------------------------------------------------------- scripts


def add_script(db, brief: str, kind: str, parts: dict, model_used: str) -> int:
    with closing(db()) as c:
        setup(c)
        cursor = c.execute(
            """
            INSERT INTO twin_scripts
            (brief, kind, title, hook, body, call_to_action, seconds_estimate, model_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief, kind, parts.get("title"), parts.get("hook"), parts.get("body", ""),
                parts.get("call_to_action"), parts.get("seconds_estimate"), model_used, _now(),
            ),
        )
        c.commit()
        return cursor.lastrowid


def scripts(db, limit: int = 50) -> list[dict]:
    with closing(db()) as c:
        setup(c)
        rows = c.execute("SELECT * FROM twin_scripts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_script(db, script_id: int) -> dict | None:
    with closing(db()) as c:
        setup(c)
        row = c.execute("SELECT * FROM twin_scripts WHERE id = ?", (script_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- videos


def add_video(db, script_id: int, voice_id: int | None, plate_id: int | None, screen_path: str = "") -> int:
    with closing(db()) as c:
        setup(c)
        cursor = c.execute(
            """
            INSERT INTO twin_videos
            (script_id, voice_id, plate_id, screen_path, stage, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?)
            """,
            (script_id, voice_id, plate_id, screen_path or None, _now(), _now()),
        )
        c.commit()
        return cursor.lastrowid


def update_video(db, video_id: int, **fields) -> None:
    """Set any subset of columns. words_json is encoded here so callers pass a list."""
    if "words" in fields:
        fields["words_json"] = json.dumps(fields.pop("words"))
    allowed = {
        "screen_path", "audio_path", "face_path", "output_path",
        "stage", "status", "error_message", "words_json",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with closing(db()) as c:
        setup(c)
        c.execute(
            f"UPDATE twin_videos SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), video_id),
        )
        c.commit()


def videos(db, limit: int = 50) -> list[dict]:
    with closing(db()) as c:
        setup(c)
        rows = c.execute(
            """
            SELECT v.*, s.title AS script_title, s.hook AS script_hook, s.kind AS script_kind
            FROM twin_videos v LEFT JOIN twin_scripts s ON s.id = v.script_id
            ORDER BY v.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_video(db, video_id: int) -> dict | None:
    with closing(db()) as c:
        setup(c)
        row = c.execute("SELECT * FROM twin_videos WHERE id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def pending_videos(db) -> list[dict]:
    """Everything the runner still owes work on, oldest first."""
    with closing(db()) as c:
        setup(c)
        rows = c.execute(
            "SELECT * FROM twin_videos WHERE status IN ('queued','building') ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]
