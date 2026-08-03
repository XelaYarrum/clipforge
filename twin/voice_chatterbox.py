"""Voice provider: Chatterbox (Resemble AI) — free, local, MIT-licensed.

This is the default and the one that matters. It clones from a short reference
recording with no training step and no account, it runs on this PC's GPU, and its
weights are MIT — which means the voice it makes can be used in something sold,
unlike several otherwise-good open models whose licences are research-only.

"Zero-shot" means there is no training run: the reference recording is handed to
the model at generation time, every time. So the handle this provider returns IS
the path to the normalised reference WAV. Nothing is uploaded and nothing expires.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from . import media
from .contract import Availability, TwinError, VoiceProvider, VOICE_DIR, WORK_DIR, probe_error

# Chatterbox quality falls off on long inputs, so text is spoken in chunks and
# joined. 260 characters is a comfortable sentence group at normal speaking pace.
MAX_CHUNK_CHARS = 260
MIN_REFERENCE_SECONDS = 6.0

_model = None
_model_device = ""
_lock = threading.Lock()  # one GPU, one 12 GB card: generation is serialised


def _split_for_speech(text: str) -> list[str]:
    """Group sentences into chunks the model handles well, never mid-sentence.

    Splitting mid-sentence is audible — the model re-chooses its intonation at
    every chunk boundary, so a boundary inside a clause sounds like a stumble.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        # A single sentence longer than the limit is split on commas as a last
        # resort, then hard-split, so nothing is ever silently dropped.
        while len(sentence) > MAX_CHUNK_CHARS:
            cut = sentence.rfind(", ", 0, MAX_CHUNK_CHARS)
            cut = cut + 1 if cut > 60 else MAX_CHUNK_CHARS
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= MAX_CHUNK_CHARS:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _load_model():
    """Load once and keep it resident. Returns (model, device)."""
    global _model, _model_device
    if _model is not None:
        return _model, _model_device

    import torch
    from chatterbox.tts import ChatterboxTTS

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        _model = ChatterboxTTS.from_pretrained(device=device)
    except Exception:
        if device == "cpu":
            raise
        # A CUDA-side failure (out of memory, driver mismatch) should degrade to a
        # slow voice rather than no voice.
        device = "cpu"
        _model = ChatterboxTTS.from_pretrained(device=device)
    _model_device = device
    return _model, _model_device


class ChatterboxVoice(VoiceProvider):
    NAME = "chatterbox"
    LABEL = "Chatterbox (local)"
    COST = "free"
    RUNS = "local"
    NOTE = ("Clones your voice from about 20 seconds of you talking. Runs on this PC's "
            "graphics card, costs nothing per video, and its licence allows commercial use.")

    def available(self) -> Availability:
        try:
            import torch  # noqa: F401
            import chatterbox.tts  # noqa: F401
        except Exception as error:  # noqa: BLE001 — a missing dependency is a status, not a crash
            return probe_error(self, error)

        import torch
        if not torch.cuda.is_available():
            return Availability(
                ready=True,
                detail="Installed, but running on the processor rather than the graphics card — "
                       "expect roughly a minute of waiting per minute of speech.",
            )
        return Availability(ready=True, detail=f"Ready on {torch.cuda.get_device_name(0)}.")

    def clone(self, name: str, reference_audio: str) -> str:
        """Normalise the recording and return it as the handle. No upload, no training."""
        source = Path(reference_audio)
        if not source.exists():
            raise TwinError(f"couldn't find that recording: {reference_audio}")

        info = media.probe(str(source))
        if not info["has_audio"]:
            raise TwinError("that file has no sound in it")
        if info["seconds"] < MIN_REFERENCE_SECONDS:
            raise TwinError(
                f"that recording is {info['seconds']:.1f} seconds long. "
                f"Chatterbox needs at least {MIN_REFERENCE_SECONDS:.0f} seconds of clear speech "
                "— about 20 is where it starts sounding like you."
            )

        safe = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "voice"
        out_path = VOICE_DIR / f"ref_{safe}.wav"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return media.to_reference_wav(str(source), str(out_path))

    def speak(self, handle: str, text: str, out_path: str) -> str:
        chunks = _split_for_speech(text)
        if not chunks:
            raise TwinError("there is no text to speak")
        if not Path(handle).exists():
            raise TwinError(
                "the reference recording for this voice is gone from disk — re-add the voice"
            )

        # soundfile, not torchaudio.save: torchaudio 2.11 removed its own encoder
        # and delegates to torchcodec, which is a separate install. soundfile
        # arrives with Chatterbox anyway, so this is one dependency fewer.
        import soundfile

        with _lock:
            model, device = _load_model()
            WORK_DIR.mkdir(parents=True, exist_ok=True)
            parts: list[str] = []
            try:
                for index, chunk in enumerate(chunks):
                    wav = model.generate(chunk, audio_prompt_path=str(handle))
                    part_path = WORK_DIR / f"speech_{Path(out_path).stem}_{index:03d}.wav"
                    # (1, samples) -> (samples,); soundfile wants frames first.
                    samples = wav.detach().cpu().squeeze(0).numpy()
                    soundfile.write(str(part_path), samples, model.sr, subtype="PCM_16")
                    parts.append(str(part_path))

                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                media.concat_wavs(parts, str(out_path))
            finally:
                for part in parts:
                    Path(part).unlink(missing_ok=True)

        if not Path(out_path).exists():
            raise TwinError(f"speech was generated on {device} but no file was written")
        return str(out_path)
