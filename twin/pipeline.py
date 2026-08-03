"""The Digital Human pipeline — a brief goes in, a finished vertical video comes out.

    brief -> script -> narration -> presenter video -> captions + render -> QC -> post queue

Every stage writes its artifact path to the twin_videos row before moving on, and
every stage is skipped when its artifact already exists on disk. That is what
makes a failed render cheap: the narration took a minute of GPU time and is still
good, so re-running resumes at compose instead of re-speaking the script. run.py
already works this way for clips; this matches it deliberately.

Nothing in this file names a vendor. It asks the registry for a voice and a face
and uses whatever answers.
"""

from __future__ import annotations

import json
from pathlib import Path

import fleet

from . import compose, registry, script as script_writer, store
from .contract import FACE_DIR, TwinError, VIDEO_DIR, VOICE_DIR, ensure_dirs

STAGES = ("script", "narration", "presenter", "render", "done")


def _artifact_ok(path: str | None) -> bool:
    return bool(path) and Path(path).exists() and Path(path).stat().st_size > 1000


def create(
    db,
    brief: str,
    kind: str = "demo",
    target_seconds: int = 45,
    voice_id: int | None = None,
    plate_id: int | None = None,
    screen_path: str = "",
    channel_context: str = "",
) -> int:
    """Write the script and queue the video. Returns the twin_videos id.

    The script is written synchronously because it is the one stage Alexander may
    want to read and reject before a minute of GPU time is spent on it.
    """
    ensure_dirs()
    result = script_writer.write(brief, kind, channel_context, target_seconds)
    script_id = store.add_script(db, brief, kind, result, result["model_used"])

    if voice_id is None:
        default = store.default_voice(db)
        voice_id = default["id"] if default else None
    if plate_id is None:
        default = store.default_plate(db, "presenter")
        plate_id = default["id"] if default else None

    return store.add_video(db, script_id, voice_id, plate_id, screen_path)


def build(db, video_id: int, layout: str = "full", handle: str = "",
          caption_style: str = "karaoke") -> dict:
    """Run every stage this video still owes. Safe to call again after a failure."""
    ensure_dirs()
    row = store.get_video(db, video_id)
    if row is None:
        raise TwinError(f"no twin video with id {video_id}")

    script_row = store.get_script(db, row["script_id"]) if row["script_id"] else None
    if script_row is None:
        raise TwinError("that video has no script attached")

    store.update_video(db, video_id, status="building", error_message=None)

    try:
        # ---------------------------------------------------------- narration
        audio_path = row["audio_path"]
        if not _artifact_ok(audio_path):
            store.update_video(db, video_id, stage="narration")
            voice_row = None
            if row["voice_id"]:
                voice_row = next((v for v in store.voices(db) if v["id"] == row["voice_id"]), None)
            if voice_row is None:
                voice_row = store.default_voice(db)
            if voice_row is None:
                raise TwinError(
                    "no cloned voice yet — add one on the Twin page from about twenty seconds "
                    "of you talking, then run this again"
                )

            provider = registry.provider_by_name("voice", voice_row["provider"])
            state = provider.available()
            if not state.ready:
                raise TwinError(f"{provider.LABEL} can't run: {' '.join(state.missing)}")

            spoken = script_writer.spoken_text(script_row)
            audio_path = str(VOICE_DIR / f"narration_{video_id:05d}.wav")
            provider.speak(voice_row["handle"], spoken, audio_path)
            store.update_video(db, video_id, audio_path=audio_path)

        # ---------------------------------------------------------- presenter
        face_path = row["face_path"]
        if not _artifact_ok(face_path):
            store.update_video(db, video_id, stage="presenter")
            plate_row = None
            if row["plate_id"]:
                plate_row = next((p for p in store.plates(db) if p["id"] == row["plate_id"]), None)
            if plate_row is None:
                plate_row = store.default_plate(db, "presenter")
            if plate_row is None:
                raise TwinError(
                    "no presenter footage yet — add a short video or a photo of yourself on the "
                    "Twin page, then run this again"
                )

            provider = registry.face_provider()
            face_path = str(FACE_DIR / f"presenter_{video_id:05d}.mp4")
            try:
                provider.render(plate_row["path"], audio_path, face_path)
            except TwinError as error:
                # Lip-sync needs a face it can find. A screen recording, a product
                # shot, or b-roll of his hands has none, and that is a perfectly
                # good video — it just cannot have its mouth re-driven.
                #
                # Installing LatentSync made it the preferred provider, and without
                # this the whole video failed on any plate without a face. Falling
                # back to plain footage keeps the video and loses only the lip-sync.
                fallback = registry.fallback_face_provider(provider)
                if fallback is None:
                    raise
                fallback.render(plate_row["path"], audio_path, face_path)
                store.update_video(
                    db, video_id,
                    error_message=(
                        f"Used your footage as-is: {provider.LABEL} couldn't work with that "
                        f"plate ({str(error)[:120]}). The video is fine, the mouth just "
                        "isn't synced."
                    ),
                )
            store.update_video(db, video_id, face_path=face_path)

        # ---------------------------------------------------------- render
        output_path = row["output_path"]
        if not _artifact_ok(output_path):
            store.update_video(db, video_id, stage="render")
            output_path = str(VIDEO_DIR / f"twin_{video_id:05d}.mp4")
            words = json.loads(row["words_json"]) if row["words_json"] else None
            result = compose.build(
                presenter_path=face_path,
                audio_path=audio_path,
                out_path=output_path,
                handle=handle,
                screen_path=row["screen_path"] or "",
                layout=layout,
                words=words,
                caption_style=caption_style,
            )
            store.update_video(db, video_id, output_path=output_path, words=result["words"])

        passed, reason = compose.qc(output_path)
        if not passed:
            raise TwinError(f"the finished video failed its quality check: {reason}")

        store.update_video(db, video_id, stage="done", status="done", error_message=None)
        return {"video_id": video_id, "output_path": output_path, "status": "done"}

    except (TwinError, fleet.FleetError) as error:
        store.update_video(db, video_id, status="error", error_message=str(error)[:500])
        return {"video_id": video_id, "status": "error", "error": str(error)}
    except Exception as error:  # noqa: BLE001 — a row must never stick on 'building'
        store.update_video(db, video_id, status="error", error_message=str(error)[:500])
        return {"video_id": video_id, "status": "error", "error": str(error)}


def process_pending(db, layout: str = "full", handle: str = "",
                    caption_style: str = "karaoke") -> list[dict]:
    """Build everything queued. This is what run.py calls each pass."""
    results = []
    for row in store.pending_videos(db):
        results.append(build(db, row["id"], layout=layout, handle=handle,
                             caption_style=caption_style))
    return results


def readiness(db) -> dict:
    """What still stands between him and a finished video. Rendered on the Twin page."""
    ensure_dirs()
    blocking: list[str] = []

    fleet_state = fleet.status()
    if not fleet_state["installed"]:
        blocking.append("Ollama isn't running, so no script can be written.")
    elif fleet_state["roles"]["write"]["error"]:
        blocking.append(fleet_state["roles"]["write"]["error"])

    try:
        voice = registry.voice_provider()
        voice_ready, voice_note = True, voice.LABEL
    except TwinError as error:
        voice_ready, voice_note = False, str(error)
        blocking.append(str(error))

    if not store.voices(db):
        blocking.append("No voice has been cloned yet — that needs about twenty seconds of you talking.")
    if not store.plates(db, "presenter"):
        blocking.append("No presenter footage yet — a short video or even one photo of you is enough to start.")

    try:
        face = registry.face_provider()
        face_ready, face_note = True, face.LABEL
    except TwinError as error:
        face_ready, face_note = False, str(error)
        blocking.append(str(error))

    return {
        "ready": not blocking,
        "blocking": blocking,
        "fleet": fleet_state,
        "voice": {"ready": voice_ready, "note": voice_note},
        "face": {"ready": face_ready, "note": face_note},
        "voices": len(store.voices(db)),
        "plates": len(store.plates(db, "presenter")),
    }
