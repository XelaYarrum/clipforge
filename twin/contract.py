"""The Digital Human contract.

Nothing else in ClipForge — not the pipeline, not the pages, not a future product
built on top of this — is allowed to know that ElevenLabs or HeyGen or Chatterbox
exist. They all talk to a VoiceProvider and a FaceProvider.

That is the whole point. Providers are rented or owned, free or paid, local or
cloud, and which one is in use is a row in a config file rather than an edit
across the codebase. When a better free model lands, it becomes a new file in
this folder and nothing above it changes.

The idiom follows post/connectors.py, which already does this for the three
social platforms: a provider that cannot run says exactly what it is missing
instead of pretending, and the caller decides whether that is fatal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
# Overridable so a selftest writes into a scratch directory instead of the real
# library. A test that produces its evidence inside the product's own output is
# a test that can leave junk behind and be mistaken for real work.
TWIN_DIR = Path(os.environ.get("CLIPFORGE_TWIN_DIR", str(APP_DIR / "data" / "twin")))
VOICE_DIR = TWIN_DIR / "voice"        # reference recordings + generated narration
PLATE_DIR = TWIN_DIR / "plates"       # his filmed presenter footage
FACE_DIR = TWIN_DIR / "face"          # lip-synced / generated presenter video
VIDEO_DIR = TWIN_DIR / "video"        # finished 1080x1920 output
WORK_DIR = TWIN_DIR / "work"          # scratch; safe to delete


def ensure_dirs() -> None:
    for directory in (TWIN_DIR, VOICE_DIR, PLATE_DIR, FACE_DIR, VIDEO_DIR, WORK_DIR):
        directory.mkdir(parents=True, exist_ok=True)


class TwinError(RuntimeError):
    """Every failure a human might read. The message is the deliverable."""


@dataclass
class Availability:
    """Whether a provider can run, and if not, precisely what is missing.

    `missing` is a list of human sentences, not error codes — it is rendered
    straight onto the page. A provider that returns ready=False with an empty
    `missing` list is a bug, and the selftest asserts against it.
    """

    ready: bool
    missing: list[str] = field(default_factory=list)
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ready and not self.missing:
            raise TwinError(
                "a provider said it isn't ready but named nothing missing — "
                "that is the failure mode this class exists to prevent"
            )


class Provider:
    """Shared by voice and face. Subclasses set the four class attributes."""

    NAME = ""                 # stable id, used in the database and config
    LABEL = ""                # what Alexander sees
    COST = "free"             # "free" | "paid"
    RUNS = "local"            # "local" | "cloud"
    NOTE = ""                 # one line: what this actually is

    def available(self) -> Availability:
        raise NotImplementedError

    def describe(self) -> dict:
        state = self.available()
        return {
            "name": self.NAME,
            "label": self.LABEL,
            "cost": self.COST,
            "runs": self.RUNS,
            "note": self.NOTE,
            "ready": state.ready,
            "missing": state.missing,
            "detail": state.detail,
        }


class VoiceProvider(Provider):
    """Turns text into Alexander's voice.

    clone() is one-time per voice and returns an opaque handle. For a local
    model the handle is a path to the reference recording; for a cloud service it
    is that service's voice id. Callers must never look inside it.
    """

    def clone(self, name: str, reference_audio: str) -> str:
        raise NotImplementedError

    def speak(self, handle: str, text: str, out_path: str) -> str:
        """Write a WAV of `text` spoken in the cloned voice. Returns out_path."""
        raise NotImplementedError


class FaceProvider(Provider):
    """Turns a narration track into video of Alexander presenting it.

    `plate` is a path to real footage of him — the thing that makes this him and
    not a generated stranger. A provider that generates from a still image
    accepts an image path here instead; providers declare which via ACCEPTS.
    """

    ACCEPTS = ("video",)      # any of: "video", "image"

    def render(self, plate: str, audio: str, out_path: str) -> str:
        """Write video of the plate speaking the audio. Returns out_path."""
        raise NotImplementedError


def probe_error(provider: Provider, error: Exception) -> Availability:
    """Turn an import/runtime failure into an Availability rather than a crash.

    Used by every provider's available(). A provider whose dependency is absent
    is a provider that is not ready — it is not a broken ClipForge.
    """
    return Availability(
        ready=False,
        missing=[f"{provider.LABEL} isn't installed on this PC yet."],
        detail=str(error)[:400],
    )
