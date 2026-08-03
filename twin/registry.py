"""Which provider is in use, and what each one is currently able to do.

Selection rule, in order:
  1. an explicit pin in data/config/twin.json — Alexander's choice always wins
  2. the first provider in PREFERENCE order that reports itself ready
  3. nothing, and a sentence naming what every candidate was missing

Rule 3 is the one that matters. "No voice provider available" is useless; "the
free one needs its model downloading, the paid one needs a key" tells him which
of the two he would rather do.

Free-and-local providers come first in PREFERENCE on purpose. A paid provider is
never selected automatically — if a key happens to exist, it still has to be
pinned deliberately, because a silent switch to a metered service is a bill he
did not agree to.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contract import Provider, TwinError
from .face_footage import FootageFace
from .voice_chatterbox import ChatterboxVoice
from .voice_elevenlabs import ElevenLabsVoice

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "twin.json"

VOICE_PREFERENCE: list[type] = [ChatterboxVoice, ElevenLabsVoice]
FACE_PREFERENCE: list[type] = [FootageFace]

try:  # optional: only present once its model is installed
    from .face_lipsync import LipSyncFace

    FACE_PREFERENCE.insert(0, LipSyncFace)
except ImportError:  # pragma: no cover - the module is always present in-tree
    pass


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(values: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = {**_config(), **values}
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def _all(kind: str) -> list[Provider]:
    classes = VOICE_PREFERENCE if kind == "voice" else FACE_PREFERENCE
    return [cls() for cls in classes]


def catalogue() -> dict:
    """Every provider and its live status — this is what the Twin page renders."""
    return {
        "voice": [p.describe() for p in _all("voice")],
        "face": [p.describe() for p in _all("face")],
        "pinned": {"voice": _config().get("voice_provider"), "face": _config().get("face_provider")},
    }


def _select(kind: str) -> Provider:
    providers = _all(kind)
    pinned = _config().get(f"{kind}_provider")

    if pinned:
        match = next((p for p in providers if p.NAME == pinned), None)
        if match is None:
            raise TwinError(
                f"twin.json pins the {kind} provider to {pinned!r}, which doesn't exist. "
                f"Choices are: {', '.join(p.NAME for p in providers)}"
            )
        state = match.available()
        if not state.ready:
            raise TwinError(
                f"{match.LABEL} is selected but can't run: {' '.join(state.missing)}"
            )
        return match

    for provider in providers:
        if provider.COST == "paid":
            continue  # never auto-select something that bills him
        if provider.available().ready:
            return provider

    reasons = []
    for provider in providers:
        state = provider.available()
        if not state.ready:
            reasons.append(f"{provider.LABEL}: {' '.join(state.missing)}")
    raise TwinError(
        f"No {kind} provider can run right now. " + "  ".join(reasons)
    )


def voice_provider() -> Provider:
    return _select("voice")


def face_provider() -> Provider:
    return _select("face")


def provider_by_name(kind: str, name: str) -> Provider:
    match = next((p for p in _all(kind) if p.NAME == name), None)
    if match is None:
        raise TwinError(f"unknown {kind} provider: {name}")
    return match
