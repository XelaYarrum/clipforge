"""Pull a source video from a URL with yt-dlp.

Only ever point this at videos you own or are licensed to repurpose — the rights
record is required before a source can be saved, and this doesn't bypass that.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 3600
VIDEO_EXTENSIONS = ("mp4", "mkv", "webm", "mov", "m4v")

# Invoke yt-dlp through THIS interpreter rather than a bare `yt-dlp` on PATH — it
# isn't on PATH on this machine, and depending on it would break the pipeline
# depending on how the launcher happened to be started.
YTDLP = [sys.executable, "-m", "yt_dlp"]


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    # A list of args, never shell=True: the URL is untrusted input.
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "yt-dlp failed").strip()[:1000])
    return result


def probe(url: str) -> dict:
    result = _run(YTDLP + ["--dump-single-json", "--no-playlist", url], PROBE_TIMEOUT)
    data = json.loads(result.stdout)
    return {
        "title": data.get("title") or "Untitled",
        "duration_sec": data.get("duration") or 0,
        "uploader": data.get("uploader") or "",
        "id": data.get("id") or "",
        "webpage_url": data.get("webpage_url") or url,
    }


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def download(url: str, dest_dir: str, max_height: int = 1080) -> dict:
    """Download one video into dest_dir and describe what landed."""
    info = probe(url)  # once — each probe is a network round trip
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    _run(
        YTDLP
        + [
            "--no-playlist",
            "-f",
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
            "-o",
            str(dest / "%(id)s.%(ext)s"),
            "--merge-output-format",
            "mp4",
            url,
        ],
        DOWNLOAD_TIMEOUT,
    )

    for ext in VIDEO_EXTENSIONS:
        candidate = dest / f"{info['id']}.{ext}"
        if candidate.exists():
            path = candidate
            break
    else:
        raise RuntimeError("yt-dlp reported success but no video file was produced")

    sha256, byte_size = _sha256_and_size(path)
    return {
        "path": str(path),
        "sha256": sha256,
        "byte_size": byte_size,
        "title": info["title"],
        "duration_sec": info["duration_sec"],
        "id": info["id"],
        "uploader": info["uploader"],
        "webpage_url": info["webpage_url"],
    }
