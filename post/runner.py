"""Stage 4 of the pipeline: queue finished renders and post what's due."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app

from . import metadata as metadata_writer
from . import scheduler
from .connectors import PLATFORMS, get_connector


def _waiting_on_setup_errors():
    """Errors that mean 'not ready yet', not 'this clip is bad'."""
    try:
        from .hosting import HostingNotConfigured
        from .live import NotConnected

        return (NotConnected, HostingNotConfigured)
    except ImportError:
        return ()


_WAITING_ON_SETUP = _waiting_on_setup_errors() or (RuntimeError,)


def queue_finished_renders() -> int:
    """Put every completed render into the post queue, once per platform."""
    import channel as channel_profile

    added = 0
    prof = channel_profile.load(app.db_connection)
    with closing(app.db_connection()) as c:
        scheduler.setup_post_tables(c)
        renders = c.execute(
            """
            SELECT renders.id, renders.output_path, clip_candidates.hook,
                   clip_candidates.clip_text
            FROM renders
            JOIN clip_candidates ON clip_candidates.id = renders.candidate_id
            WHERE renders.status = 'done' AND renders.output_path IS NOT NULL
            """
        ).fetchall()

        for render in renders:
            for platform in PLATFORMS:
                exists = c.execute(
                    "SELECT 1 FROM post_queue WHERE render_id = ? AND platform = ?"
                    " AND media_kind = 'clip'",
                    (render["id"], platform),
                ).fetchone()
                if exists:
                    continue
                meta = metadata_writer.build(
                    platform, render["hook"], render["clip_text"], prof
                )
                scheduler.enqueue(c, render["id"], platform, meta, media_kind="clip")
                added += 1
    return added


def queue_finished_twin_videos() -> int:
    """Put every finished twin video into the same post queue a clip goes into.

    A video he scripted and a moment the finder pulled out of a podcast are both
    just a vertical MP4 by this point, and they go out through one scheduler so
    the platform rate caps count them together. Two queues would each think they
    had the whole daily allowance.
    """
    import channel as channel_profile

    added = 0
    prof = channel_profile.load(app.db_connection)
    with closing(app.db_connection()) as c:
        scheduler.setup_post_tables(c)
        exists_table = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='twin_videos'"
        ).fetchone()
        if not exists_table:
            return 0

        videos = c.execute(
            """
            SELECT twin_videos.id, twin_videos.output_path,
                   twin_scripts.hook, twin_scripts.title, twin_scripts.body
            FROM twin_videos
            LEFT JOIN twin_scripts ON twin_scripts.id = twin_videos.script_id
            WHERE twin_videos.status = 'done' AND twin_videos.output_path IS NOT NULL
            """
        ).fetchall()

        for video in videos:
            for platform in PLATFORMS:
                already = c.execute(
                    "SELECT 1 FROM post_queue WHERE render_id = ? AND platform = ?"
                    " AND media_kind = 'twin'",
                    (video["id"], platform),
                ).fetchone()
                if already:
                    continue
                headline = video["hook"] or video["title"] or "New video"
                meta = metadata_writer.build(platform, headline, video["body"] or "", prof)
                scheduler.enqueue(c, video["id"], platform, meta, media_kind="twin")
                added += 1
    return added


def post_due(live: bool = False) -> None:
    with closing(app.db_connection()) as c:
        scheduler.setup_post_tables(c)
        items = scheduler.due_items(c)

        if not items:
            print("  nothing due to post")
            return

        for item in items:
            platform = item["platform"]
            allowed, next_eligible = scheduler.can_post(c, platform)
            if not allowed:
                scheduler.defer(c, item["id"], next_eligible)
                local = next_eligible.astimezone()
                print(
                    f"  {platform}: rate cap reached, deferred '{item['title']}' "
                    f"until {local:%Y-%m-%d %H:%M}"
                )
                continue

            clip_path = item["output_path"]
            if not clip_path or not Path(clip_path).exists():
                scheduler.mark_failed(c, item["id"], "rendered clip file is missing")
                print(f"  {platform}: clip file missing, marked failed")
                continue

            meta = {"title": item["title"], "description": item["description"]}
            connector = get_connector(platform, live=live)

            if not connector.validate_metadata(meta):
                scheduler.mark_failed(c, item["id"], "metadata failed platform validation")
                print(f"  {platform}: metadata rejected")
                continue

            try:
                post_id = connector.upload(clip_path, meta)
                scheduler.mark_posted(c, item["id"], platform, post_id)
                tag = "LIVE" if live else "MOCK"
                print(f"  {tag} {platform}: '{item['title']}' -> {post_id}")
            except _WAITING_ON_SETUP as e:
                # Not connected yet, or hosting not set up. The clip is fine — the
                # account isn't ready. Wait for it instead of burning the clip.
                scheduler.defer(c, item["id"], datetime.now(timezone.utc) + timedelta(hours=1))
                print(f"  {platform}: waiting on setup — {e}")
            except Exception as e:
                scheduler.mark_failed(c, item["id"], str(e))
                print(f"  {platform}: post failed: {e}")


def process(live: bool = False) -> None:
    added = queue_finished_renders() + queue_finished_twin_videos()
    if added:
        print(f"  queued {added} post(s)")
    post_due(live=live)
