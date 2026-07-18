from __future__ import annotations

import json
import hashlib
import html
import sqlite3
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "clipforge.db"
MEDIA_DIR = DATA_DIR / "media"
MODELS_DIR = DATA_DIR / "models"
CLIPS_DIR = DATA_DIR / "clips"
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
MAX_SOURCE_BYTES = 20 * 1024 * 1024 * 1024  # 20 GB

app = FastAPI(title="ClipForge")
TRANSCRIPTION_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-transcribe")
CLIP_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-clips")
RENDER_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-render")

# The pipeline's own "brain" runs on the FREE local fleet (Ollama), so a running
# ClipForge costs nothing and works offline.
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
SCORING_MODEL = "north-64k"
MIN_CLIP_SCORE = 60  # candidates below this are discarded


def db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def setup_database() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    MODELS_DIR.mkdir(exist_ok=True)
    CLIPS_DIR.mkdir(exist_ok=True)
    with closing(db_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_url TEXT,
                rights_type TEXT NOT NULL,
                rights_evidence TEXT NOT NULL,
                attribution TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL,
                model_name TEXT NOT NULL,
                detected_language TEXT,
                plain_text TEXT,
                segments_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS media_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL UNIQUE,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL UNIQUE,
                byte_size INTEGER NOT NULL,
                content_type TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS clip_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                start_sec REAL NOT NULL,
                end_sec REAL NOT NULL,
                hook TEXT,
                score INTEGER NOT NULL,
                reason TEXT,
                self_contained INTEGER,
                risk_flags TEXT,
                clip_text TEXT,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS renders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL UNIQUE,
                source_id INTEGER NOT NULL,
                output_path TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (candidate_id) REFERENCES clip_candidates(id),
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )
        connection.commit()


@app.on_event("startup")
def startup() -> None:
    setup_database()


def page(content: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>ClipForge Source Library</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ background:#10131a; color:#edf2f7; margin:0; }}
    main {{ max-width:980px; margin:0 auto; padding:42px 20px 64px; }}
    h1 {{ font-size:2.15rem; margin:0 0 8px; }}
    .lead {{ color:#aeb9c9; margin:0 0 28px; }}
    .notice {{ background:#1a2631; border-left:4px solid #42d392; padding:14px 16px; border-radius:6px; margin:22px 0; }}
    section {{ background:#171c25; border:1px solid #2a3444; border-radius:12px; padding:24px; margin-top:22px; }}
    form {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    label {{ display:grid; gap:7px; font-size:.9rem; color:#c5d0de; }}
    .wide {{ grid-column:1/-1; }}
    input, select, textarea {{ box-sizing:border-box; width:100%; padding:11px; color:#eef4fb; background:#0f141c; border:1px solid #39475c; border-radius:7px; font:inherit; }}
    textarea {{ min-height:82px; resize:vertical; }}
    button {{ width:max-content; padding:11px 17px; border:0; border-radius:7px; background:#42d392; color:#07140d; font-weight:700; cursor:pointer; }}
    table {{ border-collapse:collapse; width:100%; margin-top:8px; }}
    th, td {{ padding:12px 8px; text-align:left; border-bottom:1px solid #2a3444; vertical-align:top; }}
    th {{ color:#9fadc0; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; }}
    .muted {{ color:#aab5c4; }}
    .empty {{ color:#aab5c4; padding:12px 0 2px; }}
    a {{ color:#6ee7b7; }}
    @media (max-width:650px) {{ form {{ grid-template-columns:1fr; }} .wide {{ grid-column:auto; }} table {{ display:block; overflow:auto; }} }}
  </style>
</head>
<body><main>{content}</main></body></html>"""
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    error_message = request.query_params.get("error", "")
    with closing(db_connection()) as connection:
        sources = connection.execute(
            """
            SELECT sources.*, media_files.original_filename, media_files.sha256,
                   media_files.byte_size, transcripts.status AS transcript_status,
                   transcripts.detected_language, transcripts.error_message,
                   (SELECT COUNT(*) FROM clip_candidates WHERE clip_candidates.source_id = sources.id) AS clip_count,
                   (SELECT status FROM renders WHERE renders.source_id = sources.id ORDER BY id DESC LIMIT 1) AS render_status,
                   (SELECT candidate_id FROM renders WHERE renders.source_id = sources.id AND status='done' ORDER BY id DESC LIMIT 1) AS rendered_candidate
            FROM sources
            LEFT JOIN media_files ON media_files.source_id = sources.id
            LEFT JOIN transcripts ON transcripts.source_id = sources.id
            ORDER BY sources.id DESC
            """
        ).fetchall()

    rows = "".join(
        f"""<tr>
<td><strong>{html.escape(source['title'])}</strong><br><span class=\"muted\">Added {html.escape(source['created_at'][:10])}</span></td>
<td>{html.escape(source['rights_type'])}<br><span class=\"muted\">{html.escape(source['rights_evidence'])}</span></td>
<td>{source_link(source['source_url'])}<br><span class=\"muted\">{html.escape(source['attribution'] or 'No attribution noted')}</span></td>
<td>{media_status(source)}</td>
<td>{transcript_status(source)}</td>
<td>{clip_status(source)}</td>
<td>{render_status_cell(source)}</td>
</tr>"""
        for source in sources
    )
    library = (
        f"<table><thead><tr><th>Source</th><th>Rights record</th><th>Location / credit</th><th>Local video</th><th>Transcript</th><th>Clips</th><th>Rendered clip</th></tr></thead><tbody>{rows}</tbody></table>"
        if rows
        else "<p class=\"empty\">No sources yet. Add only footage you own or are licensed to repurpose.</p>"
    )

    return page(
        f"""
<h1>ClipForge</h1>
<p class=\"lead\">Local short-form clipping workspace &nbsp;·&nbsp; <a href=\"/\">Sources</a> &nbsp;·&nbsp; <a href=\"/channel\">Channel</a> &nbsp;·&nbsp; <a href=\"/accounts\">Accounts</a></p>
<div class=\"notice\"><strong>Rights-first:</strong> Every source needs a clear permission or license record before it enters the clip pipeline.</div>
{f'<div class="notice"><strong>Upload issue:</strong> {html.escape(error_message)}</div>' if error_message else ''}
<section>
  <h2>Pull a source from a link</h2>
  <p class=\"muted\">Paste a video link and ClipForge downloads it here, then clips it. Only use links you own or are licensed to repurpose — the rights record below is still required.</p>
  <form method=\"post\" action=\"/sources/from-url\">
    <label class=\"wide\">Video link<input name=\"url\" required placeholder=\"https://www.youtube.com/watch?v=...\"></label>
    <label>Rights type<select name=\"rights_type\" required><option value=\"I own this\">I own this</option><option value=\"Written permission\">Written permission</option><option value=\"Commercial license\">Commercial license</option><option value=\"Creator agreement\">Creator agreement</option></select></label>
    <label>Proof of rights / permission<input name=\"rights_evidence\" required placeholder=\"Contract link, email date, or license ID\"></label>
    <button class=\"wide\" type=\"submit\">Pull this video</button>
  </form>
</section>
<section>
  <h2>Add an authorized source</h2>
  <form method=\"post\" action=\"/sources\">
    <label>Source title<input name=\"title\" required placeholder=\"Episode 42 — Founder interview\"></label>
    <label>Source URL (optional)<input name=\"source_url\" type=\"url\" placeholder=\"https://...\"></label>
    <label>Rights type<select name=\"rights_type\" required><option value=\"I own this\">I own this</option><option value=\"Written permission\">Written permission</option><option value=\"Commercial license\">Commercial license</option><option value=\"Creator agreement\">Creator agreement</option></select></label>
    <label>Proof of rights / permission<input name=\"rights_evidence\" required placeholder=\"Contract link, email date, or license ID\"></label>
    <label>Required attribution (optional)<input name=\"attribution\" placeholder=\"Credit: @creator\"></label>
    <label>Notes (optional)<input name=\"notes\" placeholder=\"Topic, sponsor restriction, etc.\"></label>
    <button class=\"wide\" type=\"submit\">Save authorized source</button>
  </form>
</section>
<section>
  <h2>Attach a local source video</h2>
  <p class=\"muted\">ClipForge copies the original into its local storage and records its SHA-256 fingerprint. It does not upload it anywhere.</p>
  {upload_form(sources)}
</section>
<section>
  <h2>Create a local transcript</h2>
  <p class=\"muted\">Uses the local Whisper model. On the first run it downloads the free base model once (about 140 MB), then your video stays on this PC.</p>
  {transcription_form(sources)}
</section>
<section>
  <h2>Find clip candidates</h2>
  <p class=\"muted\">A free local model reads the transcript, splits it into 20-90s windows, and keeps the strongest self-contained moments (scored {MIN_CLIP_SCORE}+). Nothing leaves this PC.</p>
  {clips_form(sources)}
</section>
<section>
  <h2>Render a vertical clip</h2>
  <p class=\"muted\">Cuts the top-scoring clip, reframes it to 1080×1920, burns captions from the transcript, and adds your handle — all with local ffmpeg. The result plays right here.</p>
  {render_form(sources)}
</section>
<section><h2>Source library ({len(sources)})</h2>{library}</section>
"""
    )


def source_link(url: str | None) -> str:
    if not url:
        return "<span class=\"muted\">Local / not linked</span>"
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}" target="_blank" rel="noreferrer">Open source</a>'


def media_status(source: sqlite3.Row) -> str:
    if not source["original_filename"]:
        return "<span class=\"muted\">No video attached</span>"
    filename = html.escape(source["original_filename"])
    fingerprint = html.escape(source["sha256"][:12])
    return f"<strong>{filename}</strong><br><span class=\"muted\">{format_bytes(source['byte_size'])} · SHA-256 {fingerprint}…</span>"


def format_bytes(byte_size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(byte_size)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{byte_size} B"


def upload_form(sources: list[sqlite3.Row]) -> str:
    eligible = [source for source in sources if not source["original_filename"]]
    if not eligible:
        return "<p class=\"empty\">Add an authorized source first, or all sources already have a local video attached.</p>"
    options = "".join(
        f'<option value="{source["id"]}">{html.escape(source["title"])}</option>'
        for source in eligible
    )
    return f"""
<form method=\"post\" action=\"/media\" enctype=\"multipart/form-data\">
  <label>Authorized source<select name=\"source_id\" required>{options}</select></label>
  <label>Video file<input name=\"video\" type=\"file\" required accept=\".mp4,.mov,.mkv,.webm,.m4v,video/*\"></label>
  <button class=\"wide\" type=\"submit\">Copy video into ClipForge</button>
</form>"""


@app.post("/sources")
def add_source(
    title: str = Form(...),
    source_url: str = Form(""),
    rights_type: str = Form(...),
    rights_evidence: str = Form(...),
    attribution: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    with closing(db_connection()) as connection:
        connection.execute(
            """
            INSERT INTO sources
            (title, source_url, rights_type, rights_evidence, attribution, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                source_url.strip(),
                rights_type.strip(),
                rights_evidence.strip(),
                attribution.strip(),
                notes.strip(),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/media")
def add_media(source_id: int = Form(...), video: UploadFile = File(...)) -> RedirectResponse:
    filename = Path(video.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_VIDEO_EXTENSIONS:
        return redirect_with_error("Choose an MP4, MOV, MKV, WebM, or M4V video file.")

    with closing(db_connection()) as connection:
        source = connection.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
        existing = connection.execute("SELECT id FROM media_files WHERE source_id = ?", (source_id,)).fetchone()
    if not source:
        raise HTTPException(status_code=404, detail="Source record not found.")
    if existing:
        return redirect_with_error("That source already has a local video.")

    stored_filename = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}_{token_hex(8)}{suffix}"
    destination = MEDIA_DIR / stored_filename
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with destination.open("xb") as output:
            while chunk := video.file.read(8 * 1024 * 1024):
                byte_size += len(chunk)
                if byte_size > MAX_SOURCE_BYTES:
                    raise ValueError("This video is over ClipForge's 20 GB local-source limit.")
                digest.update(chunk)
                output.write(chunk)
        with closing(db_connection()) as connection:
            connection.execute(
                """
                INSERT INTO media_files
                (source_id, original_filename, stored_filename, sha256, byte_size, content_type, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (source_id, filename, stored_filename, digest.hexdigest(), byte_size, video.content_type or "", datetime.now(UTC).isoformat()),
            )
            connection.commit()
    except (OSError, ValueError, sqlite3.IntegrityError) as error:
        destination.unlink(missing_ok=True)
        return redirect_with_error(str(error))
    finally:
        video.file.close()
    return RedirectResponse(url="/", status_code=303)


def redirect_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(url="/?" + urlencode({"error": message}), status_code=303)


def transcript_status(source: sqlite3.Row) -> str:
    if not source["original_filename"]:
        return "<span class=\"muted\">Attach a video first</span>"
    status = source["transcript_status"]
    if status is None:
        return "<span class=\"muted\">Not transcribed</span>"
    if status == "processing":
        return "<span class=\"muted\">Transcribing…</span>"
    if status == "done":
        return f"<strong>Transcript ready</strong><br><span class=\"muted\">{html.escape(source['detected_language'] or '?')}</span>"
    if status == "error":
        return f"<span class=\"muted\">Error: {html.escape(source['error_message'] or 'unknown')}</span>"
    return f"<span class=\"muted\">{html.escape(str(status))}</span>"


def transcription_form(sources: list[sqlite3.Row]) -> str:
    eligible = [
        source for source in sources
        if source["original_filename"] and source["transcript_status"] in (None, "error")
    ]
    if not eligible:
        return "<p class=\"empty\">Attach a video to a source first, then you can transcribe it.</p>"
    options = "".join(
        f'<option value="{source["id"]}">{html.escape(source["title"])}</option>'
        for source in eligible
    )
    return f"""
<form method=\"post\" action=\"/transcripts\">
  <label>Authorized source<select name=\"source_id\" required>{options}</select></label>
  <button class=\"wide\" type=\"submit\">Create local transcript</button>
</form>"""


def _transcribe_with(device: str, compute_type: str, model_name: str, stored_path: str):
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(stored_path, word_timestamps=True, vad_filter=True)
    segments_list = []
    parts = []
    for seg in segments:  # lazy generator — real work (and any failure) happens here
        words = [{"start": w.start, "end": w.end, "word": w.word} for w in (seg.words or [])]
        segments_list.append({"start": seg.start, "end": seg.end, "text": seg.text, "words": words})
        parts.append(seg.text.strip())
    return info.language, " ".join(parts).strip(), segments_list


def run_transcription(source_id: int, stored_path: str, model_name: str) -> None:
    def now() -> str:
        return datetime.now(UTC).isoformat()

    try:
        try:
            language, plain_text, segments_list = _transcribe_with("cuda", "float16", model_name, stored_path)
        except Exception:
            language, plain_text, segments_list = _transcribe_with("cpu", "int8", model_name, stored_path)
        with closing(db_connection()) as connection:
            connection.execute(
                """
                UPDATE transcripts
                SET status='done', detected_language=?, plain_text=?, segments_json=?,
                    error_message=NULL, updated_at=?
                WHERE source_id=?
                """,
                (language, plain_text, json.dumps(segments_list), now(), source_id),
            )
            connection.commit()
    except Exception as error:  # noqa: BLE001 — record any failure so the row never sticks on 'processing'
        with closing(db_connection()) as connection:
            connection.execute(
                "UPDATE transcripts SET status='error', error_message=?, updated_at=? WHERE source_id=?",
                (str(error)[:500], now(), source_id),
            )
            connection.commit()


@app.post("/transcripts")
def start_transcription(source_id: int = Form(...)) -> RedirectResponse:
    with closing(db_connection()) as connection:
        row = connection.execute(
            "SELECT stored_filename FROM media_files WHERE source_id = ?", (source_id,)
        ).fetchone()
        if not row:
            return redirect_with_error("Attach a video to that source first.")
        stored_path = str(MEDIA_DIR / row["stored_filename"])
        model_name = "base"
        stamp = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO transcripts
            (source_id, status, model_name, detected_language, plain_text, segments_json,
             error_message, created_at, updated_at)
            VALUES (?, 'processing', ?, NULL, NULL, NULL, NULL, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                status='processing', model_name=excluded.model_name,
                error_message=NULL, updated_at=excluded.updated_at
            """,
            (source_id, model_name, stamp, stamp),
        )
        connection.commit()
    TRANSCRIPTION_POOL.submit(run_transcription, source_id, stored_path, model_name)
    return RedirectResponse(url="/", status_code=303)


def build_windows(segments, min_sec=20.0, max_sec=90.0, target_sec=45.0, step_segments=2):
    """Slide over whole segments, emitting 20-90s candidate windows near ~45s."""
    results = []
    n = len(segments)
    if not segments:
        return []
    for i in range(0, n, step_segments):
        window_start = segments[i]["start"]
        duration = 0.0
        j = i
        while j < n and duration < target_sec:
            duration = segments[j]["end"] - window_start
            if duration > max_sec:
                break
            j += 1
        if min_sec <= duration <= max_sec and j > i:
            text = " ".join(seg["text"].strip() for seg in segments[i:j])
            candidate = {"start_sec": round(window_start, 2), "end_sec": round(segments[j - 1]["end"], 2), "text": text}
            if not any(r["start_sec"] == candidate["start_sec"] and r["end_sec"] == candidate["end_sec"] for r in results):
                results.append(candidate)
    return results


SCORE_SYSTEM = (
    "You are a short-form clip scout for TikTok/Reels/Shorts. Judge one transcript "
    "window for viral short-form potential. A strong clip has a hook in the first 1-2 "
    "seconds, is a complete self-contained idea, and carries tension, surprise, insight, "
    "or emotion. Reply with ONLY this JSON object and nothing else: "
    '{"score": <0-100 integer>, "hook": "<punchy opening line>", '
    '"reason": "<one sentence>", "self_contained": <true|false>, '
    '"risk_flags": [<any of: "medical","financial","harassment","misinformation","copyright">]}'
)


def score_window(text: str, channel_context: str = "") -> dict | None:
    """Score one window with the free local model. Returns None on any failure."""
    system = SCORE_SYSTEM
    if channel_context:
        system = f"{SCORE_SYSTEM}\n\nABOUT THIS CHANNEL: {channel_context}"
    payload = {
        "model": SCORING_MODEL,
        "format": "json",
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": "WINDOW TEXT: " + text[:4000]},
        ],
    }
    request = urllib.request.Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(body["message"]["content"])
        return {
            "score": int(parsed.get("score", 0)),
            "hook": str(parsed.get("hook", "")).strip(),
            "reason": str(parsed.get("reason", "")).strip(),
            "self_contained": bool(parsed.get("self_contained", False)),
            "risk_flags": parsed.get("risk_flags", []) or [],
        }
    except Exception:
        return None


def find_clip_candidates(source_id: int) -> None:
    """Load a done transcript, window it, score each window on the free local model,
    and store candidates that clear MIN_CLIP_SCORE. Idempotent: clears prior candidates."""
    with closing(db_connection()) as connection:
        row = connection.execute(
            "SELECT segments_json, status FROM transcripts WHERE source_id = ?", (source_id,)
        ).fetchone()
    if not row or row["status"] != "done" or not row["segments_json"]:
        return
    segments = json.loads(row["segments_json"])
    windows = build_windows(segments)
    stamp = datetime.now(UTC).isoformat()

    # Score for THIS channel when a profile exists, rather than for general interest.
    import profile as channel_profile

    channel_context = channel_profile.scoring_context(channel_profile.load(db_connection))

    with closing(db_connection()) as connection:
        connection.execute("DELETE FROM clip_candidates WHERE source_id = ?", (source_id,))
        for window in windows:
            verdict = score_window(window["text"], channel_context)
            if not verdict or verdict["score"] < MIN_CLIP_SCORE:
                continue
            connection.execute(
                """
                INSERT INTO clip_candidates
                (source_id, start_sec, end_sec, hook, score, reason, self_contained,
                 risk_flags, clip_text, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?)
                """,
                (
                    source_id, window["start_sec"], window["end_sec"], verdict["hook"],
                    verdict["score"], verdict["reason"], 1 if verdict["self_contained"] else 0,
                    json.dumps(verdict["risk_flags"]), window["text"], stamp,
                ),
            )
        connection.commit()


def clips_form(sources: list[sqlite3.Row]) -> str:
    eligible = [s for s in sources if s["transcript_status"] == "done"]
    if not eligible:
        return "<p class=\"empty\">Transcribe a source first, then ClipForge can find clips in it.</p>"
    options = "".join(
        f'<option value="{s["id"]}">{html.escape(s["title"])}</option>' for s in eligible
    )
    return f"""
<form method=\"post\" action=\"/clips\">
  <label>Transcribed source<select name=\"source_id\" required>{options}</select></label>
  <button class=\"wide\" type=\"submit\">Find clip candidates (free local model)</button>
</form>"""


def clip_status(source: sqlite3.Row) -> str:
    count = source["clip_count"] if "clip_count" in source.keys() else 0
    if source["transcript_status"] != "done":
        return "<span class=\"muted\">—</span>"
    if not count:
        return "<span class=\"muted\">No clips yet</span>"
    return f"<strong>{count} clip{'s' if count != 1 else ''} found</strong>"


@app.post("/clips")
def start_clip_finder(source_id: int = Form(...)) -> RedirectResponse:
    CLIP_POOL.submit(find_clip_candidates, source_id)
    return RedirectResponse(url="/", status_code=303)


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:01}:{m:02}:{s:05.2f}"  # SS must be zero-padded for valid ASS timing


def _ass_escape(text: str) -> str:
    return text.replace("\n", "\\N").replace("{", "").replace("}", "").upper()


def write_ass(cues, out_path, handle=None, duration=None, video_w=1080, video_h=1920):
    """Styled ASS: big bottom captions (Cap) + an optional top handle (Handle).
    Everything renders through libass/directwrite — no drawtext, no fontconfig."""
    ass_content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_w}\n"
        f"PlayResY: {video_h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Arial,96,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,340,1\n"
        "Style: Handle,Arial,46,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,8,60,60,90,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    if handle:
        end = _ass_time(duration if duration else 3600)
        ass_content += f"Dialogue: 0,0:00:00.00,{end},Handle,,0,0,0,,{_ass_escape(handle)}\n"
    for cue in cues:
        ass_content += f"Dialogue: 0,{_ass_time(cue['start'])},{_ass_time(cue['end'])},Cap,,0,0,0,,{_ass_escape(cue['text'])}\n"
    Path(out_path).write_text(ass_content, encoding="utf-8")
    return str(out_path)


def build_render_cmd(src_path, start_sec, end_sec, ass_path, out_path):
    duration = end_sec - start_sec
    # ffmpeg subtitles filter on Windows: forward slashes + escape the drive colon.
    ass_path_filter = str(ass_path).replace("\\", "/").replace(":", "\\:")
    vf_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"subtitles='{ass_path_filter}'"
    )
    return [
        "ffmpeg", "-y",
        "-ss", str(start_sec), "-i", str(src_path), "-t", str(duration),
        "-vf", vf_filter,
        "-af", "loudnorm",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out_path),
    ]


def build_cues(words, clip_start, clip_end, group_size=4):
    """Group the clip's words into short caption cues, timed relative to clip start."""
    inside = [w for w in words if w.get("start") is not None and clip_start <= w["start"] < clip_end]
    cues = []
    for i in range(0, len(inside), group_size):
        chunk = inside[i:i + group_size]
        if not chunk:
            continue
        start = max(0.0, chunk[0]["start"] - clip_start)
        end = min(clip_end, chunk[-1]["end"]) - clip_start
        if end <= start:
            end = start + 0.4
        text = " ".join(w["word"].strip() for w in chunk).strip()
        if text:
            cues.append({"start": start, "end": end, "text": text})
    return cues


def render_clip(candidate_id: int) -> None:
    """Render one candidate to a 9:16 captioned MP4 on the free local toolchain (ffmpeg)."""
    def now() -> str:
        return datetime.now(UTC).isoformat()

    try:
        with closing(db_connection()) as connection:
            cand = connection.execute(
                "SELECT * FROM clip_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if not cand:
                return
            source_id = cand["source_id"]
            media = connection.execute(
                "SELECT stored_filename FROM media_files WHERE source_id = ?", (source_id,)
            ).fetchone()
            transcript = connection.execute(
                "SELECT segments_json FROM transcripts WHERE source_id = ?", (source_id,)
            ).fetchone()
            source = connection.execute("SELECT title FROM sources WHERE id = ?", (source_id,)).fetchone()
            connection.execute(
                """
                INSERT INTO renders (candidate_id, source_id, output_path, status, created_at, updated_at)
                VALUES (?, ?, NULL, 'rendering', ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET status='rendering', error_message=NULL, updated_at=excluded.updated_at
                """,
                (candidate_id, source_id, now(), now()),
            )
            connection.commit()
        if not media:
            raise RuntimeError("Source has no local video attached.")

        words = []
        if transcript and transcript["segments_json"]:
            for seg in json.loads(transcript["segments_json"]):
                words.extend(seg.get("words") or [])
        cues = build_cues(words, cand["start_sec"], cand["end_sec"])

        src_path = MEDIA_DIR / media["stored_filename"]
        ass_path = CLIPS_DIR / f"clip_{candidate_id}.ass"
        out_path = CLIPS_DIR / f"clip_{candidate_id}.mp4"
        handle = (source["title"] if source else "ClipForge")
        write_ass(cues, ass_path, handle=handle, duration=cand["end_sec"] - cand["start_sec"])
        cmd = build_render_cmd(src_path, cand["start_sec"], cand["end_sec"], ass_path, out_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 or not out_path.exists():
            raise RuntimeError((result.stderr or "ffmpeg failed")[-500:])

        passed, reason = qc_clip(str(out_path))
        if not passed:
            raise RuntimeError(f"QC failed: {reason}")

        with closing(db_connection()) as connection:
            connection.execute(
                "UPDATE renders SET status='done', output_path=?, error_message=NULL, updated_at=? WHERE candidate_id=?",
                (str(out_path), now(), candidate_id),
            )
            connection.commit()
    except Exception as error:  # noqa: BLE001 — record failure so status never sticks on 'rendering'
        with closing(db_connection()) as connection:
            connection.execute(
                "UPDATE renders SET status='error', error_message=?, updated_at=? WHERE candidate_id=?",
                (str(error)[:500], now(), candidate_id),
            )
            connection.commit()


def render_form(sources: list[sqlite3.Row]) -> str:
    eligible = [s for s in sources if (s["clip_count"] or 0) > 0]
    if not eligible:
        return "<p class=\"empty\">Find clip candidates first, then ClipForge can render the top one to a vertical video.</p>"
    options = "".join(
        f'<option value="{s["id"]}">{html.escape(s["title"])} ({s["clip_count"]} clips)</option>' for s in eligible
    )
    return f"""
<form method=\"post\" action=\"/render\">
  <label>Source with clips<select name=\"source_id\" required>{options}</select></label>
  <button class=\"wide\" type=\"submit\">Render top clip to 9:16 video</button>
</form>"""


def render_status_cell(source: sqlite3.Row) -> str:
    status = source["render_status"] if "render_status" in source.keys() else None
    if status is None:
        return "<span class=\"muted\">—</span>"
    if status == "rendering":
        return "<span class=\"muted\">Rendering…</span>"
    if status == "error":
        return "<span class=\"muted\">Render error</span>"
    candidate = source["rendered_candidate"]
    if status == "done" and candidate:
        return f'<a href="/clip/{candidate}" target="_blank">▶ Play clip</a>'
    return f"<span class=\"muted\">{html.escape(str(status))}</span>"


@app.get("/clip/{candidate_id}")
def serve_clip(candidate_id: int) -> FileResponse:
    path = CLIPS_DIR / f"clip_{candidate_id}.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Clip not found.")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)


def qc_clip(output_path: str) -> tuple[bool, str]:
    """Deterministic quality gate on a rendered clip. Returns (passed, reason)."""
    path = Path(output_path)
    if not path.exists() or path.stat().st_size < 10_000:
        return False, "output missing or too small"
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        info = json.loads(probe.stdout or "{}")
    except Exception as error:  # noqa: BLE001
        return False, f"probe failed: {error}"
    streams = info.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    if not has_video:
        return False, "no video stream"
    if not has_audio:
        return False, "no audio stream"
    if (vstream.get("width"), vstream.get("height")) != (1080, 1920):
        return False, f"wrong dimensions {vstream.get('width')}x{vstream.get('height')}"
    if not (3 <= duration <= 100):
        return False, f"duration out of range ({duration:.1f}s)"
    return True, "ok"


@app.post("/render")
def start_render(source_id: int = Form(...)) -> RedirectResponse:
    with closing(db_connection()) as connection:
        top = connection.execute(
            "SELECT id FROM clip_candidates WHERE source_id = ? ORDER BY score DESC LIMIT 1", (source_id,)
        ).fetchone()
    if not top:
        return redirect_with_error("Find clip candidates for that source first.")
    RENDER_POOL.submit(render_clip, top["id"])
    return RedirectResponse(url="/", status_code=303)


# The human-facing pages live in routes.py. Imported LAST so every name above
# (app, page, db_connection, ...) already exists when it binds its routes.
import routes  # noqa: E402,F401
