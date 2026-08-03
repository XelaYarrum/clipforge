"""Assembly — narration plus footage plus captions becomes a 1080x1920 video.

Captions are timed by transcribing ClipForge's OWN generated narration with
faster-whisper. That sounds circular and it is the point: the words are known, so
the transcript is only ever asked for the timings, and because it is listening to
the exact audio that will be in the video the captions cannot drift. Guessing
timings from word counts drifts within about fifteen seconds.

Rendering reuses app.write_ass, which already carries two hard-won details: ASS
[Script Info] needs "Key: Value" with a colon or libass silently ignores PlayRes
and the captions come out huge, and captions go through libass rather than
drawtext because drawtext segfaults on this machine's ffmpeg build.

Three layouts, because a software demo and a talking head are not the same video:
  full   presenter fills the frame
  split  screen recording on top, presenter underneath
  pip    screen recording is the video, presenter in the corner
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import media
from .contract import TwinError, WORK_DIR

WIDTH, HEIGHT = 1080, 1920
LAYOUTS = {
    "full": "Presenter fills the screen",
    "split": "Screen recording on top, you underneath",
    "pip": "Screen recording full frame, you in the corner",
}


def align_words(audio_path: str, model_size: str = "base") -> list[dict]:
    """Word-level timings for the narration ClipForge just generated.

    Falls back from GPU to processor the same way app.run_transcription does. The
    try/except wraps the whole transcription including the segment generator,
    because the generator is lazy — wrapping only the constructor catches nothing.
    """
    def attempt(device: str, compute_type: str) -> list[dict]:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(audio_path, word_timestamps=True, vad_filter=False)
        words: list[dict] = []
        for segment in segments:
            for word in segment.words or []:
                words.append({"start": word.start, "end": word.end, "word": word.word})
        return words

    try:
        return attempt("cuda", "float16")
    except Exception:
        try:
            return attempt("cpu", "int8")
        except Exception as error:
            raise TwinError(f"couldn't time the captions: {str(error)[:300]}") from error


# Caption styles. "karaoke" shows a short phrase with the word being spoken right
# now picked out in colour; "block" shows a static group of words.
#
# Karaoke is the default because it is the single most visible thing separating
# short-form video that looks made by a person from short-form video that looks
# made in 2015. Every professional tool in this category does it, and it costs
# nothing here: word-level timings already exist, because the captions are timed
# by transcribing the narration ClipForge itself generated.
CAPTION_STYLES = ("karaoke", "block")

# ASS colours are &HBBGGRR — byte-reversed from the hex used everywhere else.
# Writing &H42D392 here would give a completely different colour, silently.
_ACTIVE_COLOUR = "&H92D342&"      # the app green, reversed
_WORDS_PER_PHRASE = 3

# A caption holds until the next word begins, so it never blinks out during the
# ordinary pauses in speech. Without this the screen goes blank between words and
# a frame sampled during a breath has no captions at all — which is exactly how
# this was found, on a video that looked like the captions had failed entirely.
#
# The hold is capped: a genuine silence should not leave a stale line sitting
# there, so anything longer than this just fades after a beat.
_MAX_HOLD_SECONDS = 1.2
_TAIL_SECONDS = 0.6


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{hours:01}:{minutes:02}:{rest:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\n", "\\N").replace("{", "").replace("}", "").upper()


def _karaoke_events(words: list[dict], seconds: float) -> str:
    """One event per word: the whole phrase, with the live word highlighted."""
    spoken = [w for w in words if w.get("start") is not None and w.get("end") is not None]
    if not spoken:
        return ""

    events: list[str] = []
    for index in range(0, len(spoken), _WORDS_PER_PHRASE):
        phrase = spoken[index:index + _WORDS_PER_PHRASE]
        cleaned = [_ass_escape(w["word"].strip()) for w in phrase]
        for position, word in enumerate(phrase):
            global_index = index + position
            start = max(0.0, float(word["start"]))
            end = min(seconds, float(word["end"]))
            if end <= start:
                end = start + 0.12

            # Hold until the next word starts, whether that word is in this phrase
            # or the next one. This is what keeps captions on screen through the
            # pauses in normal speech.
            if global_index + 1 < len(spoken):
                next_start = float(spoken[global_index + 1]["start"])
                end = max(end, min(next_start, end + _MAX_HOLD_SECONDS))
            else:
                end = min(seconds, end + _TAIL_SECONDS)
            end = min(end, seconds)

            rendered = []
            for other, text in enumerate(cleaned):
                if other == position:
                    # \r resets to the style default afterwards; without it the
                    # colour bleeds across every following word on the line.
                    rendered.append(f"{{\\c{_ACTIVE_COLOUR}\\fscx112\\fscy112}}{text}{{\\r}}")
                else:
                    rendered.append(text)
            events.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(rendered)}\n"
            )
    return "".join(events)


def _write_captions(words: list[dict], seconds: float, handle: str, out_path: Path,
                    style: str = "karaoke") -> str:
    import app  # imported here: app.py imports routes at the bottom, so a top-level
                # import from this module would close a cycle at startup.

    if style not in CAPTION_STYLES:
        raise TwinError(f"unknown caption style {style!r} — choices: {', '.join(CAPTION_STYLES)}")

    if style == "block":
        cues = app.build_cues(words, 0.0, seconds)
        return app.write_ass(cues, out_path, handle=handle or None, duration=seconds,
                             video_w=WIDTH, video_h=HEIGHT)

    events = _karaoke_events(words, seconds)
    if not events:
        # No usable word timings — fall back rather than ship a silent-looking video.
        cues = app.build_cues(words, 0.0, seconds)
        return app.write_ass(cues, out_path, handle=handle or None, duration=seconds,
                             video_w=WIDTH, video_h=HEIGHT)

    # The header is app.write_ass's, reused deliberately: "Key: Value" with a COLON
    # in [Script Info], or libass ignores PlayRes and the captions render huge.
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\n"
        f"PlayResY: {HEIGHT}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Arial,96,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,340,1\n"
        "Style: Handle,Arial,46,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,90,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    body = ""
    if handle:
        body += f"Dialogue: 0,0:00:00.00,{_ass_time(seconds)},Handle,,0,0,0,,{_ass_escape(handle)}\n"
    Path(out_path).write_text(header + body + events, encoding="utf-8")
    return str(out_path)


def _ass_for_filter(path: str) -> str:
    # ffmpeg's subtitles filter on Windows wants forward slashes and an escaped
    # drive colon, or it reads "C" as a protocol and fails.
    return str(path).replace("\\", "/").replace(":", "\\:")


def _run_ffmpeg(cmd: list[str], what: str) -> None:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as error:
        raise TwinError("ffmpeg is not on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise TwinError(f"{what} took over an hour and was stopped") from error
    if result.returncode != 0:
        raise TwinError(f"{what} failed: {(result.stderr or '')[-600:]}")


def build(
    presenter_path: str,
    audio_path: str,
    out_path: str,
    handle: str = "",
    screen_path: str = "",
    layout: str = "full",
    words: list[dict] | None = None,
    caption_style: str = "karaoke",
) -> dict:
    """Render the finished vertical video. Returns the artifacts it produced."""
    if layout not in LAYOUTS:
        raise TwinError(f"unknown layout {layout!r} — choices: {', '.join(LAYOUTS)}")
    if caption_style not in CAPTION_STYLES:
        raise TwinError(
            f"unknown caption style {caption_style!r} — choices: {', '.join(CAPTION_STYLES)}"
        )
    if layout in ("split", "pip") and not screen_path:
        raise TwinError(f"the {layout} layout needs a screen recording, and none was given")

    narration = media.probe(audio_path)
    seconds = narration["seconds"]
    if seconds <= 0:
        raise TwinError("the narration has no length")

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    stem = Path(out_path).stem

    if words is None:
        words = align_words(audio_path)
    ass_path = WORK_DIR / f"{stem}.ass"
    _write_captions(words, seconds, handle, ass_path, style=caption_style)
    subtitles = f"subtitles='{_ass_for_filter(str(ass_path))}'"

    inputs = ["-i", str(presenter_path), "-i", str(audio_path)]
    if layout == "full":
        filter_complex = (
            f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,{subtitles}[v]"
        )
    else:
        # The screen recording is looped/trimmed to the narration first, so both
        # tracks are exactly the same length before they are combined.
        screen_fitted = WORK_DIR / f"{stem}_screen.mp4"
        media.loop_video_to_length(screen_path, seconds, str(screen_fitted), WIDTH, HEIGHT)
        inputs = ["-i", str(presenter_path), "-i", str(screen_fitted), "-i", str(audio_path)]

        if layout == "split":
            # Screen gets the top 1120px, presenter the bottom 800px. The screen is
            # fitted (not cropped) because cropping a screen recording cuts off UI.
            filter_complex = (
                f"color=c=#0d1117:s={WIDTH}x{HEIGHT}:d={seconds:.3f}[base];"
                f"[1:v]scale={WIDTH}:1120:force_original_aspect_ratio=decrease,setsar=1[scr];"
                f"[0:v]scale={WIDTH}:800:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:800,setsar=1[pres];"
                f"[base][scr]overlay=(W-w)/2:(1120-h)/2:shortest=0[top];"
                f"[top][pres]overlay=0:1120[stacked];"
                f"[stacked]{subtitles}[v]"
            )
        else:  # pip
            pip_w, margin = 380, 48
            filter_complex = (
                f"[1:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},setsar=1,boxblur=28:2[bg];"
                f"[1:v]scale={WIDTH}:-2:force_original_aspect_ratio=decrease,setsar=1[scr];"
                f"[bg][scr]overlay=(W-w)/2:(H-h)/2[framed];"
                f"[0:v]scale={pip_w}:{pip_w}:force_original_aspect_ratio=increase,"
                f"crop={pip_w}:{pip_w},setsar=1,"
                f"pad={pip_w + 8}:{pip_w + 8}:4:4:color=#42d392[pip];"
                f"[framed][pip]overlay={margin}:H-h-{margin + 420}[composed];"
                f"[composed]{subtitles}[v]"
            )

    audio_index = 1 if layout == "full" else 2
    _run_ffmpeg(
        ["ffmpeg", "-y", *inputs,
         "-filter_complex", filter_complex,
         "-map", "[v]", "-map", f"{audio_index}:a",
         # -14 LUFS is what the platforms normalise to, so matching it here means
         # they leave the audio alone instead of re-compressing it.
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         # 48 kHz explicitly: loudnorm runs its filter graph at 192 kHz and, left
         # alone, hands the encoder a 96 kHz stream. That is a valid file that no
         # social platform wants, and every one of them would re-encode it.
         "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-r", "30",
         "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(out_path)],
        "rendering the video",
    )

    if not Path(out_path).exists():
        raise TwinError("ffmpeg reported success but wrote no file")
    return {"output_path": str(out_path), "seconds": seconds, "words": words, "captions": str(ass_path)}


def qc(output_path: str) -> tuple[bool, str]:
    """The same deterministic gate app.qc_clip applies to a clipped video.

    Twin videos run longer than clips, so the duration ceiling is raised — but the
    dimension and stream checks are identical, deliberately, so a twin video and a
    clipped video are held to one standard.
    """
    path = Path(output_path)
    if not path.exists() or path.stat().st_size < 10_000:
        return False, "output missing or too small"
    try:
        info = media.probe(str(path))
    except TwinError as error:
        return False, str(error)
    if not info["has_video"]:
        return False, "no video stream"
    if not info["has_audio"]:
        return False, "no audio stream"
    if (info["width"], info["height"]) != (WIDTH, HEIGHT):
        return False, f"wrong dimensions {info['width']}x{info['height']}"
    if not (3 <= info["seconds"] <= 600):
        return False, f"duration out of range ({info['seconds']:.1f}s)"
    return True, "ok"
