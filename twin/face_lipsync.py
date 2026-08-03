"""Face provider: LatentSync — free, local, and the one that makes the mouth match.

This is the piece that turns "footage of him with a voiceover" into "him saying
it". It takes real video of Alexander and re-drives the mouth region from the
narration audio, which is roughly what the paid avatar services do behind their
API — the difference is that this runs on his own card and the footage stays his.

It is deliberately the LAST thing to depend on. The model is a multi-gigabyte
download with its own repository, so this provider reports itself unavailable
with install instructions rather than pretending, and the pipeline falls through
to face_footage, which needs nothing. A digital twin that cannot make a video
until a 5 GB download finishes is not a working digital twin.

Nothing here downloads anything on its own. INSTALL.md in the checkpoint folder
is written by prepare(), so the install is a documented step he can run or skip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import media
from .contract import Availability, FaceProvider, TwinError, WORK_DIR

APP_DIR = Path(__file__).resolve().parent.parent
# Kept outside data/ because it is a downloaded dependency, not project data, and
# it should survive a data wipe.
LATENTSYNC_DIR = Path(os.environ.get("LATENTSYNC_DIR", str(APP_DIR / "models" / "latentsync")))
CHECKPOINT = LATENTSYNC_DIR / "checkpoints" / "latentsync_unet.pt"
WHISPER_CKPT = LATENTSYNC_DIR / "checkpoints" / "whisper" / "tiny.pt"
INFERENCE_SCRIPT = LATENTSYNC_DIR / "scripts" / "inference.py"
# stage2_512.yaml is what the project's own inference.sh uses for 1.6. An earlier
# guess at stage2_efficient.yaml would have run a different model configuration
# than the weights were released for — reading their launch script beats assuming.
CONFIG_FILE = LATENTSYNC_DIR / "configs" / "unet" / "stage2_512.yaml"

# LatentSync works in 16 kHz mono and expects 25 fps video; feeding it anything
# else produces a subtly early or late mouth that is hard to diagnose by eye.
REQUIRED_FPS = 25
REQUIRED_AUDIO_RATE = 16000


def install_notes() -> list[str]:
    """The steps that were actually run here, not a guess at them.

    The last one matters more than it looks. LatentSync's requirements.txt pins
    torch==2.5.1, and installing that pin replaces a CUDA build with a
    processor-only one on an RTX 50-series card — after which everything still
    appears to work and is roughly twenty times slower, with no error anywhere.
    """
    return [
        "Clone the model repository into models/latentsync:",
        "    git clone --depth 1 https://github.com/bytedance/LatentSync models/latentsync",
        "Download the weights (about 9.6 GB) into models/latentsync/checkpoints:",
        "    python -c \"from huggingface_hub import snapshot_download as d; "
        "d('ByteDance/LatentSync-1.6', local_dir='models/latentsync/checkpoints')\"",
        "Install its Python packages, but SKIP every torch line in that file:",
        "    (install models/latentsync/requirements.txt minus torch, torchvision, torchaudio)",
        "Then confirm the graphics card is still in use:",
        "    python -c \"import torch; print(torch.cuda.is_available())\"",
    ]


class LipSyncFace(FaceProvider):
    NAME = "lipsync"
    LABEL = "LatentSync (local lip-sync)"
    COST = "free"
    RUNS = "local"
    ACCEPTS = ("video",)
    NOTE = ("Re-drives your mouth from the narration so it matches the words. Free and runs on "
            "this PC's graphics card, but needs a one-time five gigabyte model download.")

    def available(self) -> Availability:
        missing: list[str] = []
        if not LATENTSYNC_DIR.exists():
            missing.append("The LatentSync model folder hasn't been downloaded yet.")
        elif not INFERENCE_SCRIPT.exists():
            missing.append("models/latentsync exists but the repository looks incomplete.")
        if not CHECKPOINT.exists():
            missing.append("The lip-sync weights (about 5 GB) haven't been downloaded.")

        if not missing:
            try:
                import diffusers  # noqa: F401
                import omegaconf  # noqa: F401
            except Exception as error:  # noqa: BLE001
                missing.append("Its extra Python packages aren't installed yet.")
                return Availability(ready=False, missing=missing, detail=str(error)[:300])

        if missing:
            return Availability(
                ready=False, missing=missing,
                detail="Until this is installed ClipForge uses your own footage instead, which "
                       "needs nothing. Setup steps: " + "  ".join(install_notes()),
            )
        return Availability(ready=True, detail=f"Ready. Model at {LATENTSYNC_DIR}.")

    def _prepare_inputs(self, plate: str, audio: str, stem: str) -> tuple[str, str]:
        """Force the frame rate and sample rate LatentSync assumes."""
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        video_out = WORK_DIR / f"{stem}_ls_in.mp4"
        audio_out = WORK_DIR / f"{stem}_ls_in.wav"

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(plate), "-r", str(REQUIRED_FPS),
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", str(video_out)],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            raise TwinError(f"couldn't prepare the footage: {(result.stderr or '')[-400:]}")

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(audio), "-ac", "1", "-ar", str(REQUIRED_AUDIO_RATE),
             "-c:a", "pcm_s16le", str(audio_out)],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:
            raise TwinError(f"couldn't prepare the narration: {(result.stderr or '')[-400:]}")
        return str(video_out), str(audio_out)

    def render(self, plate: str, audio: str, out_path: str) -> str:
        state = self.available()
        if not state.ready:
            raise TwinError(f"{self.LABEL} isn't installed: {' '.join(state.missing)}")

        narration = media.probe(audio)
        plate_info = media.probe(plate)
        if plate_info["seconds"] < narration["seconds"]:
            # LatentSync consumes frames one-for-one; a plate shorter than the
            # narration silently truncates the video rather than looping.
            looped = WORK_DIR / f"{Path(out_path).stem}_looped.mp4"
            plate = media.loop_video_to_length(
                plate, narration["seconds"], str(looped),
                plate_info["width"] or 1080, plate_info["height"] or 1920,
            )

        stem = Path(out_path).stem
        video_in, audio_in = self._prepare_inputs(plate, audio, stem)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            # `-m scripts.inference`, not the file path. The module imports its own
            # package relatively, and running it as a loose script breaks those
            # imports. This is how the project's inference.sh invokes it.
            sys.executable, "-m", "scripts.inference",
            "--unet_config_path", str(CONFIG_FILE),
            "--inference_ckpt_path", str(CHECKPOINT),
            "--video_path", video_in,
            "--audio_path", audio_in,
            "--video_out_path", str(out_path),
            "--inference_steps", "20",
            "--guidance_scale", "1.5",
            # Caches repeated denoising steps. Roughly halves the time for no
            # visible difference, which matters on a 12 GB card.
            "--enable_deepcache",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=7200, cwd=str(LATENTSYNC_DIR)
            )
        except subprocess.TimeoutExpired as error:
            raise TwinError("lip-sync ran for two hours and was stopped") from error

        if result.returncode != 0 or not Path(out_path).exists():
            raise TwinError(f"lip-sync failed: {(result.stderr or result.stdout or '')[-600:]}")
        return str(out_path)


def write_install_notes() -> Path:
    """Leave the setup steps beside the folder they refer to, not only in chat."""
    LATENTSYNC_DIR.parent.mkdir(parents=True, exist_ok=True)
    notes = LATENTSYNC_DIR.parent / "LATENTSYNC-INSTALL.md"
    notes.write_text(
        "# Optional: local lip-sync\n\n"
        "ClipForge makes videos without this. Install it only when you want the mouth in your\n"
        "presenter footage to match the words instead of the footage being B-roll.\n\n"
        "Run these from the ClipForge folder:\n\n```\n"
        + "\n".join(line for line in install_notes() if line.startswith("    ")).replace("    ", "")
        + "\n```\n\nThen reload the Twin page — it checks for the files every time it loads.\n",
        encoding="utf-8",
    )
    return notes


def status_json() -> str:
    return json.dumps(LipSyncFace().describe(), indent=2)
