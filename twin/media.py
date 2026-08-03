"""ffmpeg/ffprobe helpers shared by the twin stages.

app.py already shells out to ffmpeg directly for clip rendering. This file is the
same idea for the twin, kept separate so the probe results are structured rather
than parsed at each call site.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .contract import TwinError

# ffmpeg writes progress to stderr and can be chatty; the pipeline only ever
# cares about the return code and the last few hundred characters.
_TAIL = 600


def _run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise TwinError(f"{cmd[0]} is not on PATH — install ffmpeg and reopen the terminal") from error
    except subprocess.TimeoutExpired as error:
        raise TwinError(f"{cmd[0]} took longer than {timeout}s and was stopped") from error


def probe(path: str) -> dict:
    """Duration, dimensions and stream presence for any media file."""
    if not Path(path).exists():
        raise TwinError(f"file not found: {path}")
    result = _run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,sample_rate,channels",
         "-of", "json", str(path)],
        timeout=120,
    )
    if result.returncode != 0:
        raise TwinError(f"couldn't read that file: {(result.stderr or '')[-_TAIL:]}")
    info = json.loads(result.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    seconds = float(info.get("format", {}).get("duration", 0) or 0)
    if seconds <= 0 and video:
        # A file whose container header was never finalised — an interrupted screen
        # capture, most often — has no duration recorded even though every frame is
        # present and readable. Counting the packets measures it directly. Reporting
        # zero here would have thrown away a perfectly good recording.
        seconds = _duration_by_counting(path)

    return {
        "seconds": seconds,
        "width": video.get("width"),
        "height": video.get("height"),
        "has_video": bool(video),
        "has_audio": bool(audio),
        "sample_rate": int(audio.get("sample_rate", 0) or 0),
        "channels": int(audio.get("channels", 0) or 0),
    }


def _duration_by_counting(path: str) -> float:
    """Duration from frame count over frame rate. Slower, but works on any file."""
    result = _run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets,r_frame_rate", "-of", "json", str(path)],
        timeout=300,
    )
    if result.returncode != 0:
        return 0.0
    try:
        stream = json.loads(result.stdout or "{}").get("streams", [{}])[0]
        packets = int(stream.get("nb_read_packets", 0) or 0)
        numerator, _, denominator = (stream.get("r_frame_rate") or "0/1").partition("/")
        fps = float(numerator) / float(denominator or 1)
    except (ValueError, IndexError, ZeroDivisionError, json.JSONDecodeError):
        return 0.0
    return round(packets / fps, 3) if fps > 0 else 0.0


def to_reference_wav(src: str, out_path: str, max_seconds: float = 30.0) -> str:
    """Normalise any recording into the shape a voice cloner wants.

    Mono, 24 kHz, level-matched, and trimmed. Voice cloners take a short sample —
    feeding a ten-minute file makes them slower without making them better, and
    a stereo file with one dead channel is a common silent failure.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        "ffmpeg", "-y", "-i", str(src),
        "-t", str(max_seconds),
        "-vn", "-ac", "1", "-ar", "24000",
        "-af", "loudnorm=I=-18:TP=-2:LRA=11",
        "-c:a", "pcm_s16le", str(out_path),
    ], timeout=600)
    if result.returncode != 0 or not Path(out_path).exists():
        raise TwinError(f"couldn't convert that recording: {(result.stderr or '')[-_TAIL:]}")
    return str(out_path)


def concat_wavs(parts: list[str], out_path: str, gap_seconds: float = 0.18) -> str:
    """Join generated speech chunks with a small breath between them.

    Butt-joining chunks makes the delivery sound rushed and robotic at every seam;
    a fifth of a second reads as a natural pause.
    """
    if not parts:
        raise TwinError("nothing to join — no speech was generated")
    if len(parts) == 1 and gap_seconds <= 0:
        return parts[0]

    inputs: list[str] = []
    for part in parts:
        inputs += ["-i", str(part)]

    silence_inputs: list[str] = []
    if gap_seconds > 0 and len(parts) > 1:
        for index in range(len(parts) - 1):
            silence_inputs += [
                "-f", "lavfi", "-t", str(gap_seconds),
                "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
            ]

    # Interleave: part0, silence0, part1, silence1, ... partN
    ordered: list[str] = []
    silence_index = len(parts)
    for index in range(len(parts)):
        ordered.append(f"[{index}:a]")
        if gap_seconds > 0 and index < len(parts) - 1:
            ordered.append(f"[{silence_index}:a]")
            silence_index += 1

    filter_complex = "".join(ordered) + f"concat=n={len(ordered)}:v=0:a=1[out]"
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        ["ffmpeg", "-y", *inputs, *silence_inputs,
         "-filter_complex", filter_complex, "-map", "[out]",
         "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(out_path)],
        timeout=900,
    )
    if result.returncode != 0 or not Path(out_path).exists():
        raise TwinError(f"couldn't join the speech chunks: {(result.stderr or '')[-_TAIL:]}")
    return str(out_path)


def loop_video_to_length(src: str, seconds: float, out_path: str, width: int = 1080, height: int = 1920) -> str:
    """Stretch a short plate to cover a longer narration by looping it.

    A 20-second presenter plate has to carry a 60-second script. Looping is what
    makes one filming session reusable forever, which is the entire economics of
    the free route.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    result = _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-t", f"{seconds:.3f}",
        "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30", str(out_path),
    ], timeout=1800)
    if result.returncode != 0 or not Path(out_path).exists():
        raise TwinError(f"couldn't prepare the presenter footage: {(result.stderr or '')[-_TAIL:]}")
    return str(out_path)


def still_to_video(src: str, seconds: float, out_path: str, width: int = 1080, height: int = 1920) -> str:
    """A single image held for the narration, with a slow push-in so it isn't dead.

    Used when there is no footage yet — a photo still beats a black screen, and it
    keeps the pipeline runnable on day one.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    frames = max(2, int(seconds * 30))
    result = _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(src), "-t", f"{seconds:.3f}",
        "-vf", (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},"
                f"zoompan=z='min(zoom+0.0006,1.12)':d={frames}:s={width}x{height}:fps=30,"
                "setsar=1"),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30", str(out_path),
    ], timeout=1800)
    if result.returncode != 0 or not Path(out_path).exists():
        raise TwinError(f"couldn't build video from that image: {(result.stderr or '')[-_TAIL:]}")
    return str(out_path)
