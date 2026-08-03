"""Voice provider: ElevenLabs — paid, cloud, optional.

Present so the choice stays open, not because it is needed. Chatterbox does this
job for nothing on this PC. This exists for the day a client wants a voice
ClipForge cannot run locally, or a language the local model handles badly.

It is inert until an API key exists, and it says so. No key is read from any
shared location — only from this project's own config file, which is gitignored.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .contract import Availability, TwinError, VoiceProvider, VOICE_DIR

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "elevenlabs.json"
API_ROOT = "https://api.elevenlabs.io/v1"
# eleven_multilingual_v2 is the quality model; flash is cheaper and faster but
# noticeably flatter on long narration, which is what this is used for.
MODEL_ID = "eleven_multilingual_v2"


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if key:
        return key
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("api_key", "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def save_key(key: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"api_key": key.strip()}, indent=2), encoding="utf-8")


def _request(path: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    key = _api_key()
    if not key:
        raise TwinError("no ElevenLabs API key is saved")
    request = urllib.request.Request(
        f"{API_ROOT}{path}", data=body, method=method,
        headers={"xi-api-key": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise TwinError(f"ElevenLabs refused ({error.code}): {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TwinError(f"couldn't reach ElevenLabs: {error}") from error


class ElevenLabsVoice(VoiceProvider):
    NAME = "elevenlabs"
    LABEL = "ElevenLabs (paid, online)"
    COST = "paid"
    RUNS = "cloud"
    NOTE = ("A paid online voice service. ClipForge does not need it — Chatterbox does the "
            "same job free on this PC. Kept wired so it can be switched on if a job ever needs it.")

    def available(self) -> Availability:
        if not _api_key():
            return Availability(
                ready=False,
                missing=["An ElevenLabs API key. Add it on the Twin page, or leave it — "
                         "the free local voice covers this."],
            )
        try:
            body, _ = _request("/user/subscription")
            info = json.loads(body.decode("utf-8"))
        except TwinError as error:
            return Availability(ready=False, missing=["That ElevenLabs key was rejected."], detail=str(error))
        used = info.get("character_count", 0)
        limit = info.get("character_limit", 0)
        return Availability(ready=True, detail=f"Key accepted. {used:,} of {limit:,} characters used this period.")

    def clone(self, name: str, reference_audio: str) -> str:
        """Create a cloned voice and return ElevenLabs' voice id as the handle."""
        source = Path(reference_audio)
        if not source.exists():
            raise TwinError(f"couldn't find that recording: {reference_audio}")

        boundary = "----ClipForgeVoiceClone"
        parts: list[bytes] = []

        def field(field_name: str, value: str) -> None:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"\r\n\r\n{value}\r\n".encode()
            )

        field("name", name)
        field("description", "Cloned by ClipForge")
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"{source.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
        )
        parts.append(source.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        body, _ = _request(
            "/voices/add", method="POST", body=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        voice_id = json.loads(body.decode("utf-8")).get("voice_id", "")
        if not voice_id:
            raise TwinError("ElevenLabs accepted the recording but returned no voice id")
        return voice_id

    def speak(self, handle: str, text: str, out_path: str) -> str:
        payload = json.dumps({
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3},
        }).encode("utf-8")
        # WAV out, so the rest of the pipeline never has to care which provider spoke.
        body, _ = _request(
            f"/text-to-speech/{handle}?output_format=pcm_24000",
            method="POST", body=payload,
            headers={"Content-Type": "application/json", "Accept": "audio/pcm"},
        )
        raw_path = Path(out_path).with_suffix(".pcm")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(body)
        try:
            from . import media
            import subprocess
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
                 "-i", str(raw_path), "-c:a", "pcm_s16le", str(out_path)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                raise TwinError(f"couldn't convert the returned audio: {(result.stderr or '')[-400:]}")
            media.probe(str(out_path))
        finally:
            raw_path.unlink(missing_ok=True)
        return str(out_path)


def voice_dir() -> Path:
    return VOICE_DIR
