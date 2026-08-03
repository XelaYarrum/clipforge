"""Screen recording, built in — so a software demo is a button, not a second app.

The 'split' and 'corner' layouts need a recording of the thing being demonstrated.
Without this, that means opening OBS, recording, finding the file, and pasting a
path. Three of those four steps are the kind of manual grunt the rest of ClipForge
exists to remove.

Windows only, because gdigrab is a Windows capture device. It degrades honestly:
on anything else the provider reports itself unavailable and the file-path box on
the Twin page still works.

Two details that are load-bearing:

* Every ffmpeg call runs with CREATE_NO_WINDOW, so no console flashes onto his
  screen when a capture starts.

* That choice has a consequence, and it is the whole design of stop(). With no
  console, ffmpeg can receive neither its quit key ("q" on stdin) nor a
  Ctrl-Break — both were implemented, measured, and observed to hit their full
  timeout and get killed anyway, each wasted second recording more screen he did
  not want. So the capture can ONLY ever be killed.

  A plain MP4 killed mid-write is a dead file: the index is written at the very
  end, so what is left has a plausible size and will not open. Measured here, on
  the first real test: 512 KB, no duration, unplayable.

  The fix is not a better signal, it is a container that is never in an invalid
  state. Recording goes to FRAGMENTED MP4 with packets flushed as they are
  produced, so the file is complete and playable at every instant and a kill
  costs at most the last second. It is remuxed to a normal MP4 on stop for
  compatibility. Stopping is now immediate.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path

from . import media
from .contract import TwinError, TWIN_DIR

IS_WINDOWS = os.name == "nt"
SCREEN_DIR = TWIN_DIR / "screen"
STATE_PATH = TWIN_DIR / "recording.json"

# Windows that are always present and never what he means to record.
_IGNORE_TITLES = {
    "Program Manager", "Windows Input Experience", "Settings",
    "Microsoft Text Input Application", "Windows Shell Experience Host",
}

# No window is created for the capture process, so no console flashes on screen.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
_CAPTURE_FLAGS = _NO_WINDOW


# Capture is capped at this width. Two reasons, both measured:
#   * NVENC's H.264 encoder refuses anything over 4096 pixels wide, and a
#     dual-monitor desktop here is 4480.
#   * The finished video is 1080 wide. Capturing at 4480 and then throwing three
#     quarters of every pixel away costs encode time for nothing.
MAX_CAPTURE_WIDTH = 2560

_encoder_cache: list[str] | None = None


def _capture_size(target: str) -> tuple[int, int]:
    """The pixel size of what is about to be recorded, before it is recorded."""
    if not IS_WINDOWS:
        return 0, 0
    if target in ("", "desktop"):
        user32 = ctypes.windll.user32
        # The VIRTUAL screen — the bounding box of every monitor, not just the
        # primary one. SM_CXSCREEN would silently record one monitor's worth.
        return user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    match = next((w for w in list_windows() if w["title"] == target), None)
    return (match["width"], match["height"]) if match else (0, 0)


def _scale_filter(target: str) -> str:
    """A concrete scale, worked out from the real size rather than an expression.

    ffmpeg filter expressions can do this conditionally, but they need commas
    escaped inside a filtergraph and are a common source of a filter that silently
    does nothing. The size is knowable here, so it is computed here.
    """
    width, height = _capture_size(target)
    if width <= 0 or height <= 0:
        # Unknown size: just guarantee even dimensions, which h264 requires.
        return "crop=trunc(iw/2)*2:trunc(ih/2)*2"
    if width <= MAX_CAPTURE_WIDTH:
        return "crop=trunc(iw/2)*2:trunc(ih/2)*2"
    scaled_height = int(round(height * MAX_CAPTURE_WIDTH / width / 2)) * 2
    return f"scale={MAX_CAPTURE_WIDTH}:{scaled_height}:flags=bicubic"


def _video_encoder() -> list[str]:
    """The encoder arguments for capture, hardware if this card has it.

    Software encoding is why the first version dropped a third of every frame.
    MEASURED: whole-desktop capture at 4480x1600 ran at 67.8% of real time on
    libx264, which does not look like dropped frames — it looks like a demo that
    plays too fast and is a third too short. The graphics card encodes the same
    stream without touching the processor, which is also busy running the rest of
    ClipForge.
    """
    global _encoder_cache
    if _encoder_cache is not None:
        return _encoder_cache

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
        has_nvenc = "h264_nvenc" in (result.stdout or "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        has_nvenc = False

    if has_nvenc:
        # p4 is the balanced preset; constant-quality 23 is visually lossless for
        # screen content, which is mostly flat colour and text.
        _encoder_cache = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"]
    else:
        _encoder_cache = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20"]
    return _encoder_cache


def encoder_name() -> str:
    return "graphics card" if "h264_nvenc" in _video_encoder() else "processor"


def available() -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Screen recording is built for Windows only on this machine."
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-devices"],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return False, f"ffmpeg isn't answering: {error}"
    if "gdigrab" not in (result.stdout or "") + (result.stderr or ""):
        return False, "This ffmpeg build has no gdigrab capture device."
    return True, "Ready."


def list_windows() -> list[dict]:
    """Visible, titled, non-minimised top-level windows he could record."""
    if not IS_WINDOWS:
        return []

    user32 = ctypes.windll.user32
    windows: list[dict] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.IsIconic(hwnd):  # minimised windows capture as black
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title or title in _IGNORE_TITLES:
            return True

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 200 or height < 150:  # tooltips, tray popups
            return True
        windows.append({"title": title, "width": width, "height": height})
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    windows.sort(key=lambda w: w["title"].lower())
    return windows


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _process_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return str(pid) in (result.stdout or "")


# The Popen handle only exists in the process that started it. State on disk is
# what survives a page reload, so both are kept and the disk copy is the truth.
_process: subprocess.Popen | None = None


def status() -> dict:
    """Whether something is recording right now, and for how long."""
    state = _read_state()
    if not state.get("pid"):
        return {"recording": False}
    if not _process_alive(state["pid"]):
        # ffmpeg died on its own — a full disk, a closed window. Do not report a
        # recording that is not happening.
        _write_state({})
        return {"recording": False, "note": "The last recording stopped on its own."}
    started = state.get("started_at", "")
    seconds = 0.0
    if started:
        seconds = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
    return {
        "recording": True,
        "target": state.get("target", ""),
        "path": state.get("path", ""),
        "seconds": round(seconds, 1),
    }


def start(target: str = "desktop", framerate: int = 30) -> dict:
    """Begin capturing. `target` is 'desktop' or an exact window title."""
    global _process

    ok, reason = available()
    if not ok:
        raise TwinError(reason)
    if status()["recording"]:
        raise TwinError("Something is already recording — stop that first.")

    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    # A fragmented .part.mp4 while recording — see the module docstring. It is
    # remuxed into a normal .mp4 on stop.
    out_path = SCREEN_DIR / f"screen_{stamp}.part.mp4"

    source = "desktop" if target in ("", "desktop") else f"title={target}"

    _process = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "gdigrab", "-framerate", str(framerate),
         "-draw_mouse", "1", "-i", source,
         # yuv420p and an even-dimension crop: a window with an odd pixel width
         # produces a file that half the players in the world refuse to open.
         "-vf", _scale_filter(target),
         *_video_encoder(),
         "-pix_fmt", "yuv420p",
         # A keyframe twice a second, so a fragment closes twice a second. The
         # unflushed final fragment is what gets lost when the capture is killed,
         # so a shorter one means less of the ending disappears. MEASURED: total
         # loss is a constant 1.5-1.8s of startup and tail regardless of length —
         # 1.5s missing from an 8 second capture, still only 1.8s from a 25 second
         # one. It is overhead at the edges, not frames dropped throughout, so a
         # real demo is not sped up.
         "-g", "15",
         # THE reason a killed capture still plays. A normal MP4 only becomes
         # readable when its index is written at the very end; a fragmented one
         # writes a complete, self-contained chunk as it goes. flush_packets sends
         # each one to disk immediately rather than holding it in a buffer — the
         # buffer is exactly what was being lost.
         "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
         "-flush_packets", "1",
         str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=_CAPTURE_FLAGS,
    )

    # ffmpeg fails fast on a window title it cannot find, so a short wait tells
    # the difference between "recording" and "already dead" while he is still
    # looking at the page.
    time.sleep(1.2)
    if _process.poll() is not None:
        error = (_process.stderr.read() or b"").decode("utf-8", "replace")[-400:]
        _process = None
        out_path.unlink(missing_ok=True)
        if "Could not find" in error or "gdigrab" in error:
            raise TwinError(
                f"Couldn't find a window called '{target}'. It may have closed or been "
                "minimised — reload the page to refresh the list."
            )
        raise TwinError(f"Recording wouldn't start: {error}")

    _write_state({
        "pid": _process.pid,
        "path": str(out_path),
        "target": target,
        "started_at": datetime.now(UTC).isoformat(),
    })
    return {"path": str(out_path), "target": target}


def stop() -> dict:
    """End the capture and return the finished file."""
    global _process

    _process_stderr = ""
    state = _read_state()
    path = state.get("path", "")
    if not path:
        raise TwinError("Nothing is recording.")

    if _process is not None and _process.poll() is None:
        # Terminate, immediately, with no polite handshake first.
        #
        # ffmpeg's documented ways to stop it cleanly — "q" on stdin, or a
        # Ctrl-Break — both need it to own a console, and these captures
        # deliberately have none so that nothing flashes onto his screen. Both were
        # tried and MEASURED here: each one hit its full timeout and then had to be
        # killed anyway, having recorded ten more seconds of screen he did not ask
        # for while waiting.
        #
        # So the container does the work instead of the signal. The capture is
        # fragmented MP4, which is complete and playable at every instant, and a
        # hard kill costs at most the last fragment. Stopping is now instant.
        _process.terminate()
        try:
            _process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _process.kill()
        try:
            # Kept so a failed capture can quote ffmpeg's own last words rather
            # than making him guess what went wrong.
            _process_stderr = (_process.stderr.read() or b"").decode("utf-8", "replace")
        except (OSError, ValueError):
            _process_stderr = ""
        _process = None
    elif state.get("pid") and _process_alive(state["pid"]):
        # Started by an earlier run of the app; no handle to write to.
        subprocess.run(["taskkill", "/PID", str(state["pid"]), "/F"],
                       capture_output=True, timeout=20, creationflags=_NO_WINDOW)

    _write_state({})

    captured = Path(path)
    if not captured.exists() or captured.stat().st_size < 20_000:
        # Say WHY. "Came out empty" with no reason is the message that sends him
        # back here to guess; ffmpeg already knows and just needs quoting.
        detail = ""
        if _process_stderr:
            detail = _process_stderr.strip().splitlines()[-1][:200] if _process_stderr.strip() else ""
        captured.unlink(missing_ok=True)
        raise TwinError(
            "That recording came out empty. If you picked a window, it has to stay "
            "visible on screen the whole time — a window that is minimised or fully "
            "covered captures nothing."
            + (f" ffmpeg said: {detail}" if detail else "")
        )

    # Remux to MP4 FIRST, then check. ffmpeg rewrites the container on the way,
    # which repairs the missing duration that a stopped capture always leaves
    # behind — so the file is inspected in the state it will actually be used in.
    # Checking before the remux rejected a perfectly good recording purely because
    # its header had no duration in it yet.
    #
    # No re-encode, so this is a second or two and the picture is untouched. If it
    # fails, the Matroska file is kept and still works everywhere in ClipForge:
    # losing a good recording over a container is not a trade worth making.
    final = captured.with_name(captured.name.replace(".part.mp4", ".mp4"))
    remux = subprocess.run(
        ["ffmpeg", "-y", "-i", str(captured), "-c", "copy", "-movflags", "+faststart", str(final)],
        capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW,
    )
    if remux.returncode == 0 and final.exists() and final.stat().st_size > 20_000:
        captured.unlink(missing_ok=True)
        result_path = final
    else:
        final.unlink(missing_ok=True)
        result_path = captured

    # Prove it is playable rather than merely present.
    try:
        probe = media.probe(str(result_path))
    except TwinError as error:
        raise TwinError(f"The recording didn't finish cleanly: {error}") from error
    if not probe["has_video"] or probe["seconds"] < 0.5:
        result_path.unlink(missing_ok=True)
        raise TwinError("That recording has no usable picture in it.")

    return {
        "path": str(result_path),
        "seconds": probe["seconds"],
        "width": probe["width"],
        "height": probe["height"],
    }


def recordings(limit: int = 20) -> list[dict]:
    """Finished captures, newest first, for picking one on the page."""
    if not SCREEN_DIR.exists():
        return []
    items = []
    # .part.mp4 files are included too: a capture whose remux failed is still a
    # perfectly playable fragmented MP4, and dropping it from the list would lose
    # a recording over nothing.
    candidates = [*SCREEN_DIR.glob("*.mp4"), *SCREEN_DIR.glob("*.mkv")]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        if path.stat().st_size < 20_000:
            continue
        try:
            probe = media.probe(str(path))
        except TwinError:
            continue
        items.append({
            "path": str(path),
            "name": path.name,
            "seconds": probe["seconds"],
            "width": probe["width"],
            "height": probe["height"],
        })
        if len(items) >= limit:
            break
    return items
