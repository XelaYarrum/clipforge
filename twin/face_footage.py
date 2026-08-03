"""Face provider: your own footage — free, local, and the one that works today.

The obvious way to build a digital twin is to generate a person. This does the
opposite: it uses real recorded footage of Alexander and lets the cloned voice
carry the words. No model, no download, no GPU, no lip-sync.

That sounds like the weak option and mostly it is not, because of what these
videos actually are. A software demo is ninety percent screen recording. A build
showcase is the thing being built. The face is a few seconds of hook at the top.
For every one of those the presenter footage is B-roll, and B-roll does not need
a matching mouth — which means one filming session covers unlimited videos, at
zero cost, forever.

Where it IS the weak option is a straight talking-head where the mouth is on
screen the whole time. For that, face_lipsync is the provider — this one is
honest about the difference rather than shipping a video with a mouth out of sync
and calling it done.

Accepts a video plate or a single still image.
"""

from __future__ import annotations

from pathlib import Path

from . import media
from .contract import Availability, FaceProvider, TwinError, WORK_DIR

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class FootageFace(FaceProvider):
    NAME = "footage"
    LABEL = "Your own footage (no lip-sync)"
    COST = "free"
    RUNS = "local"
    ACCEPTS = ("video", "image")
    NOTE = ("Uses real footage of you, looped to fit the narration. Costs nothing and needs no "
            "model. The mouth won't match the words, so it's right for screen demos and B-roll "
            "and wrong for a close-up talking head.")

    def available(self) -> Availability:
        # ffmpeg is the only dependency, and app.py already requires it to render
        # any clip at all — so if ClipForge works, this provider works.
        try:
            media._run(["ffmpeg", "-version"], timeout=30)
        except TwinError as error:
            return Availability(ready=False, missing=["ffmpeg isn't on PATH."], detail=str(error))
        return Availability(ready=True, detail="Ready — no model needed.")

    def render(self, plate: str, audio: str, out_path: str) -> str:
        plate_path = Path(plate)
        if not plate_path.exists():
            raise TwinError(f"couldn't find that footage: {plate}")

        narration = media.probe(audio)
        seconds = narration["seconds"]
        if seconds <= 0:
            raise TwinError("the narration track has no length — nothing to cover")

        WORK_DIR.mkdir(parents=True, exist_ok=True)
        if plate_path.suffix.lower() in IMAGE_SUFFIXES:
            return media.still_to_video(str(plate_path), seconds, out_path)

        info = media.probe(str(plate_path))
        if not info["has_video"]:
            raise TwinError("that file has no picture in it")
        return media.loop_video_to_length(str(plate_path), seconds, out_path)
