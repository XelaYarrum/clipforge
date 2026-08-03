"""The Twin page's routes.

Kept beside routes.py rather than inside it: routes.py is the posting/accounts
surface and this is the digital-human surface, and they have no shared state.

Imported at the BOTTOM of app.py, after routes.py, so every name it borrows from
app already exists.

Long work goes on a thread pool and the page comes straight back, the same way
transcription and rendering already do. A build that blocks the browser for two
minutes looks identical to a crash.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from urllib.parse import urlencode

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import channel as channel_profile
from app import CLIP_POOL, RENDER_POOL, app, db_connection, page
from twin import compose, media, pipeline, registry, screen, series, store
from twin import page as twin_page
from twin import script as script_writer
from twin.contract import PLATE_DIR, TwinError, VOICE_DIR, ensure_dirs

ALLOWED_AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _redirect(message: str = "", error: str = "") -> RedirectResponse:
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/twin{suffix}", status_code=303)


def _save_upload(upload: UploadFile, destination_dir: Path, allowed: set[str]) -> Path:
    """Copy an upload to disk, keeping its extension. Raises TwinError on a bad type."""
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in allowed:
        raise TwinError(f"that file type isn't accepted here — use one of: {', '.join(sorted(allowed))}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = destination_dir / f"{stamp}_{token_hex(6)}{suffix}"
    try:
        with target.open("xb") as out:
            shutil.copyfileobj(upload.file, out, length=8 * 1024 * 1024)
    finally:
        upload.file.close()
    return target


# ------------------------------------------------------------------ the page


@app.get("/twin", response_class=HTMLResponse)
def twin_get(request: Request) -> HTMLResponse:
    ensure_dirs()
    can_record, why_not = screen.available()
    recording = screen.status()
    # Enumerating windows costs a moment and is pointless mid-capture, when the
    # only control on offer is Stop.
    windows = screen.list_windows() if (can_record and not recording["recording"]) else []
    return page(
        twin_page.twin_page(
            readiness=pipeline.readiness(db_connection),
            catalogue=registry.catalogue(),
            voices=store.voices(db_connection),
            plates=store.plates(db_connection, "presenter"),
            videos=store.videos(db_connection),
            layouts=compose.LAYOUTS,
            kinds=script_writer.KINDS,
            message=request.query_params.get("message", ""),
            error=request.query_params.get("error", ""),
            recording=recording,
            windows=windows,
            recordings=screen.recordings() if can_record else [],
            can_record=can_record,
            why_not_record=why_not,
        )
    )


# ------------------------------------------------------------------ screen


@app.post("/twin/screen/start")
def screen_start(target: str = Form("desktop")) -> RedirectResponse:
    try:
        started = screen.start(target)
    except TwinError as error:
        return _redirect(error=str(error))
    where = "the whole screen" if started["target"] in ("", "desktop") else started["target"]
    return _redirect(message=f"Recording {where}. Come back here and press Stop when you're done.")


@app.post("/twin/screen/stop")
def screen_stop() -> RedirectResponse:
    try:
        finished = screen.stop()
    except TwinError as error:
        return _redirect(error=str(error))
    return _redirect(
        message=f"Recorded {finished['seconds']:.0f} seconds at "
                f"{finished['width']}x{finished['height']}. It's in the Screen recording list now."
    )


# ------------------------------------------------------------------ series


@app.post("/twin/series")
def create_series(
    topic: str = Form(...),
    count: int = Form(5),
    kind: str = Form("demo"),
    target_seconds: int = Form(45),
    layout: str = Form("full"),
    screen_path: str = Form(""),
) -> RedirectResponse:
    ensure_dirs()
    context = channel_profile.scoring_context(channel_profile.load(db_connection))
    try:
        result = series.create_series(
            db_connection, topic, int(count), kind, int(target_seconds),
            channel_context=context, screen_path=screen_path.strip(),
        )
    except Exception as error:  # noqa: BLE001 — fleet failures land here as a sentence
        return _redirect(error=f"Couldn't plan that series: {error}")

    if not result["queued"]:
        return _redirect(error=f"Nothing could be queued. {series.summarise(result)}")

    handle = channel_profile.load(db_connection).get("handle", "")
    for item in result["queued"]:
        RENDER_POOL.submit(pipeline.build, db_connection, item["video_id"], layout, handle)

    return _redirect(
        message=series.summarise(result)
        + " They build one at a time on the graphics card, so give it a few minutes."
    )


# ------------------------------------------------------------------ voice


@app.post("/twin/voice")
def add_voice(name: str = Form(...), audio: UploadFile = File(...)) -> RedirectResponse:
    ensure_dirs()
    try:
        saved = _save_upload(audio, VOICE_DIR / "uploads", ALLOWED_AUDIO | ALLOWED_VIDEO)
    except TwinError as error:
        return _redirect(error=str(error))

    try:
        provider = registry.voice_provider()
        handle = provider.clone(name.strip(), str(saved))
        seconds = media.probe(handle)["seconds"]
        store.add_voice(db_connection, name.strip(), provider.NAME, handle, str(saved), seconds)
    except TwinError as error:
        saved.unlink(missing_ok=True)
        return _redirect(error=str(error))

    return _redirect(
        message=f"Voice '{name.strip()}' added from {seconds:.0f} seconds of audio. "
                "Play the sample to hear how close it is."
    )


@app.get("/twin/voice/{voice_id}/sample")
def voice_sample(voice_id: int) -> FileResponse:
    """Speak one fixed sentence in this voice so he can judge it before using it.

    Generated on demand and cached — a stale sample after a re-clone would be
    worse than no sample.
    """
    match = next((v for v in store.voices(db_connection) if v["id"] == voice_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="No such voice.")

    sample_path = VOICE_DIR / f"sample_{voice_id:05d}.wav"
    if not sample_path.exists():
        try:
            provider = registry.provider_by_name("voice", match["provider"])
            provider.speak(
                match["handle"],
                "This is how I sound. If this is close enough, everything ClipForge makes "
                "from now on will be narrated in this voice.",
                str(sample_path),
            )
        except TwinError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    return FileResponse(str(sample_path), media_type="audio/wav", filename=sample_path.name)


@app.post("/twin/voice/{voice_id}/default")
def voice_default(voice_id: int) -> RedirectResponse:
    store.set_default_voice(db_connection, voice_id)
    return _redirect(message="That voice is now the default.")


@app.post("/twin/voice/{voice_id}/delete")
def voice_delete(voice_id: int) -> RedirectResponse:
    store.delete_voice(db_connection, voice_id)
    (VOICE_DIR / f"sample_{voice_id:05d}.wav").unlink(missing_ok=True)
    return _redirect(message="Voice removed.")


# ------------------------------------------------------------------ footage


@app.post("/twin/plate")
def add_plate(name: str = Form(...), footage: UploadFile = File(...)) -> RedirectResponse:
    ensure_dirs()
    try:
        saved = _save_upload(footage, PLATE_DIR, ALLOWED_VIDEO | ALLOWED_IMAGE)
        probe = media.probe(str(saved))
    except TwinError as error:
        return _redirect(error=str(error))

    if not probe["has_video"]:
        saved.unlink(missing_ok=True)
        return _redirect(error="There's no picture in that file.")

    store.add_plate(db_connection, name.strip(), "presenter", str(saved), probe)
    shape = f"{probe['width']}x{probe['height']}"
    note = ""
    if probe["width"] and probe["height"] and probe["width"] > probe["height"]:
        note = " It's landscape, so it will be cropped to fit a vertical video — expect the sides to go."
    return _redirect(message=f"Footage '{name.strip()}' added ({shape}).{note}")


@app.post("/twin/plate/{plate_id}/default")
def plate_default(plate_id: int) -> RedirectResponse:
    store.set_default_plate(db_connection, plate_id)
    return _redirect(message="That footage is now the default.")


@app.post("/twin/plate/{plate_id}/delete")
def plate_delete(plate_id: int) -> RedirectResponse:
    store.delete_plate(db_connection, plate_id)
    return _redirect(message="Footage removed.")


# ------------------------------------------------------------------ providers


@app.post("/twin/provider")
def pick_provider(kind: str = Form(...), name: str = Form(...)) -> RedirectResponse:
    if kind not in ("voice", "face"):
        raise HTTPException(status_code=404, detail="Unknown provider kind.")
    try:
        provider = registry.provider_by_name(kind, name)
    except TwinError as error:
        return _redirect(error=str(error))

    state = provider.available()
    if not state.ready:
        return _redirect(error=f"{provider.LABEL} can't run yet: {' '.join(state.missing)}")

    registry.save_config({f"{kind}_provider": name})
    extra = ""
    if provider.COST == "paid":
        extra = " That one bills per use — switch back to the free option any time."
    return _redirect(message=f"{provider.LABEL} is now doing the {kind} work.{extra}")


# ------------------------------------------------------------------ videos


@app.post("/twin/create")
def create_video(
    brief: str = Form(...),
    kind: str = Form("demo"),
    target_seconds: int = Form(45),
    layout: str = Form("full"),
    caption_style: str = Form("karaoke"),
    screen_path: str = Form(""),
) -> RedirectResponse:
    ensure_dirs()
    screen_path = screen_path.strip()
    if screen_path and not Path(screen_path).exists():
        return _redirect(error=f"There's no file at {screen_path}.")
    if layout in ("split", "pip") and not screen_path:
        return _redirect(
            error=f"The '{compose.LAYOUTS[layout]}' layout needs a screen recording — "
                  "either add one or choose the full-screen layout."
        )

    context = channel_profile.scoring_context(channel_profile.load(db_connection))
    try:
        video_id = pipeline.create(
            db_connection, brief, kind, int(target_seconds),
            screen_path=screen_path, channel_context=context,
        )
    except Exception as error:  # noqa: BLE001 — fleet failures land here as a sentence
        return _redirect(error=f"Couldn't write the script: {error}")

    handle = channel_profile.load(db_connection).get("handle", "")
    RENDER_POOL.submit(pipeline.build, db_connection, video_id, layout, handle, caption_style)
    return _redirect(
        message="Script written and the video is building. It appears below when it's done — "
                "roughly a minute of waiting per minute of finished video."
    )


@app.post("/twin/video/{video_id}/build")
def build_video(video_id: int, layout: str = Form("full")) -> RedirectResponse:
    row = store.get_video(db_connection, video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such video.")
    handle = channel_profile.load(db_connection).get("handle", "")
    RENDER_POOL.submit(pipeline.build, db_connection, video_id, layout, handle)
    return _redirect(message="Building. Reload in a minute.")


@app.get("/twin/video/{video_id}/play")
def play_video(video_id: int) -> FileResponse:
    row = store.get_video(db_connection, video_id)
    if row is None or not row["output_path"] or not Path(row["output_path"]).exists():
        raise HTTPException(status_code=404, detail="That video hasn't been made yet.")
    path = Path(row["output_path"])
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


# CLIP_POOL is imported so the module's dependency on app's pools is explicit even
# though only RENDER_POOL is used today; both are single-worker by design, because
# one 12 GB card cannot run two generations at once.
_ = CLIP_POOL
