"""The channel profile — ClipForge's sense of what this channel IS.

One row. It feeds two places that otherwise guess: the clip finder (so the local
model scores clips for THIS audience rather than "is this generally interesting")
and the metadata writer (handle, hashtags).

Empty is allowed — the pipeline works without a profile, it just scores blind.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone

FIELDS = ("handle", "niche", "audience", "tone", "extra_hashtags", "avoid")

DEFAULTS = {
    "handle": "@clipforge",
    "niche": "",
    "audience": "",
    "tone": "",
    "extra_hashtags": "",
    "avoid": "",
}


def setup_profile_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            handle TEXT NOT NULL DEFAULT '@clipforge',
            niche TEXT NOT NULL DEFAULT '',
            audience TEXT NOT NULL DEFAULT '',
            tone TEXT NOT NULL DEFAULT '',
            extra_hashtags TEXT NOT NULL DEFAULT '',
            avoid TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT OR IGNORE INTO channel_profile (id, updated_at) VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    connection.commit()


def load(db_connection) -> dict:
    with closing(db_connection()) as c:
        setup_profile_table(c)
        row = c.execute("SELECT * FROM channel_profile WHERE id = 1").fetchone()
    if row is None:
        return dict(DEFAULTS)
    return {f: (row[f] or DEFAULTS[f]) for f in FIELDS}


def save(db_connection, values: dict) -> None:
    with closing(db_connection()) as c:
        setup_profile_table(c)
        c.execute(
            """
            UPDATE channel_profile
            SET handle=?, niche=?, audience=?, tone=?, extra_hashtags=?, avoid=?, updated_at=?
            WHERE id = 1
            """,
            (
                (values.get("handle") or DEFAULTS["handle"]).strip(),
                (values.get("niche") or "").strip(),
                (values.get("audience") or "").strip(),
                (values.get("tone") or "").strip(),
                (values.get("extra_hashtags") or "").strip(),
                (values.get("avoid") or "").strip(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        c.commit()


def scoring_context(profile: dict) -> str:
    """The profile as a paragraph the clip-scoring model can actually use."""
    bits = []
    if profile.get("niche"):
        bits.append(f"This channel is about: {profile['niche']}.")
    if profile.get("audience"):
        bits.append(f"Its audience is: {profile['audience']}.")
    if profile.get("tone"):
        bits.append(f"Its tone is: {profile['tone']}.")
    if profile.get("avoid"):
        bits.append(f"NEVER pick clips about: {profile['avoid']}.")
    if not bits:
        return ""
    bits.append(
        "Score clips by how well they serve THIS channel and audience, not by how "
        "interesting they are in general."
    )
    return " ".join(bits)


def hashtags(profile: dict) -> list[str]:
    raw = (profile.get("extra_hashtags") or "").replace(",", " ")
    return [t if t.startswith("#") else f"#{t}" for t in raw.split() if t.strip()]
