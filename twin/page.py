"""The Twin page — everything the digital twin needs, on one screen.

Written to be answerable rather than informative. Every section either shows a
thing that is done or gives the one action that finishes it, and the status
strip at the top says what is still standing between him and a finished video
instead of making him work it out from four green dots.

Style matches pages.py so the app stays one app.
"""

from __future__ import annotations

import html

NAV = """
<p class="lead">
  <a href="/">Sources</a> &nbsp;·&nbsp;
  <a href="/twin">Twin</a> &nbsp;·&nbsp;
  <a href="/channel">Channel</a> &nbsp;·&nbsp;
  <a href="/accounts">Accounts</a>
</p>
"""

EXTRA_CSS = """
<style>
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  .card { background:#0f141c; border:1px solid #2a3444; border-radius:9px; padding:14px 16px; }
  .pill { display:inline-block; font-size:.72rem; padding:2px 8px; border-radius:99px;
          border:1px solid #39475c; color:#aab5c4; margin-left:6px; }
  .pill.free { color:#42d392; border-color:#2b6b52; }
  .pill.paid { color:#f0b429; border-color:#7a5a1a; }
  .pill.on   { color:#07140d; background:#42d392; border-color:#42d392; font-weight:700; }
  .blocking { background:#25191a; border-left:4px solid #f05252; padding:14px 16px;
              border-radius:6px; margin:22px 0; }
  .blocking ul { margin:8px 0 0; padding-left:20px; }
  .stagerow td { font-size:.9rem; }
  .steps { counter-reset:s; list-style:none; padding-left:0; }
  .steps li { counter-increment:s; margin:0 0 10px; padding-left:34px; position:relative; }
  .steps li::before { content:counter(s); position:absolute; left:0; top:0; width:24px; height:24px;
    border-radius:50%; background:#42d392; color:#07140d; font-weight:700; font-size:.8rem;
    display:grid; place-items:center; }
  @media (max-width:650px) { .grid2 { grid-template-columns:1fr; } }
</style>
"""


def _esc(value) -> str:
    return html.escape(str(value or ""))


def _provider_card(provider: dict, kind: str, pinned: str | None) -> str:
    cost = "free" if provider["cost"] == "free" else "paid"
    pills = f'<span class="pill {cost}">{provider["cost"]}</span>'
    pills += f'<span class="pill">{provider["runs"]}</span>'
    if pinned == provider["name"]:
        pills += '<span class="pill on">in use</span>'

    if provider["ready"]:
        state = f'<div style="color:#42d392;font-weight:600">Ready</div>'
    else:
        items = "".join(f"<li>{_esc(m)}</li>" for m in provider["missing"])
        state = f'<div style="color:#f0b429;font-weight:600">Not set up</div><ul style="margin:6px 0 0;padding-left:18px;color:#aab5c4;font-size:.86rem">{items}</ul>'

    detail = f'<div class="muted" style="font-size:.82rem;margin-top:8px">{_esc(provider["detail"])}</div>' if provider["detail"] else ""
    use_button = ""
    if provider["ready"] and pinned != provider["name"]:
        use_button = (
            f'<form method="post" action="/twin/provider" style="display:block;margin-top:10px">'
            f'<input type="hidden" name="kind" value="{kind}">'
            f'<input type="hidden" name="name" value="{_esc(provider["name"])}">'
            f'<button type="submit" style="background:#39475c;color:#edf2f7">Use this one</button></form>'
        )

    return f"""
<div class="card">
  <div><strong>{_esc(provider["label"])}</strong>{pills}</div>
  <div class="muted" style="font-size:.86rem;margin:6px 0 10px">{_esc(provider["note"])}</div>
  {state}{detail}{use_button}
</div>"""


def _screen_section(recording: dict, windows: list[dict], recordings: list[dict],
                    can_record: bool, why_not: str) -> str:
    """Record-your-screen, so a demo doesn't mean opening a second program."""
    if not can_record:
        return f"""
<section>
  <h2 style="margin-top:0">Record your screen</h2>
  <p class="muted">{_esc(why_not)} You can still point at a file you recorded elsewhere
  when you make a video.</p>
</section>"""

    if recording.get("recording"):
        body = f"""
<div class="card" style="border-color:#7a2b2b;background:#201414">
  <div style="font-size:1.1rem;font-weight:700;color:#f8b4b4">● Recording — {recording["seconds"]:.0f}s</div>
  <div class="muted" style="margin:6px 0 12px">Capturing {_esc(recording["target"] or "the whole screen")}.
  Go and do the thing you want to show, then come back and stop it.</div>
  <form method="post" action="/twin/screen/stop">
    <button type="submit" style="background:#f05252;color:#fff">Stop recording</button>
  </form>
</div>"""
    else:
        options = ['<option value="desktop">The whole screen</option>']
        for window in windows:
            label = f'{window["title"][:70]} ({window["width"]}x{window["height"]})'
            options.append(f'<option value="{_esc(window["title"])}">{_esc(label)}</option>')
        note = recording.get("note", "")
        body = f"""
{f'<div class="muted" style="margin-bottom:10px">{_esc(note)}</div>' if note else ""}
<form method="post" action="/twin/screen/start">
  <label class="wide">What to record
    <select name="target">{"".join(options)}</select></label>
  <button class="wide" type="submit">Start recording</button>
</form>"""

    if recordings:
        rows = "".join(
            f'<tr><td><strong>{_esc(r["name"])}</strong><br>'
            f'<span class="muted">{r["seconds"]:.0f}s · {r["width"]}x{r["height"]}</span></td>'
            f'<td class="muted" style="font-size:.78rem">{_esc(r["path"])}</td></tr>'
            for r in recordings
        )
        have = f'<table style="margin-top:14px"><tr><th>Recording</th><th>Where it is</th></tr>{rows}</table>'
    else:
        have = '<p class="empty">Nothing recorded yet.</p>'

    return f"""
<section>
  <h2 style="margin-top:0">Record your screen</h2>
  <p class="muted">For a software demo the screen IS the video. Record it here and pick it
  below — no second program, no hunting for the file afterwards.</p>
  {body}
  {have}
</section>"""


def _series_section(kinds: dict, layouts: dict) -> str:
    return f"""
<section>
  <h2 style="margin-top:0">Make a week of videos at once</h2>
  <p class="muted">Give it one topic and it works out several genuinely different angles on it,
  then writes and queues a video for each. Typing one brief per video is the thing that caps how
  much you can put out.</p>
  <form method="post" action="/twin/series">
    <label class="wide">Topic
      <textarea name="topic" required placeholder="e.g. ClipForge — the clipping tool I built that runs on my own PC and costs nothing per video"></textarea></label>
    <label>How many<select name="count">
      <option value="3">3 videos</option>
      <option value="5" selected>5 videos</option>
      <option value="7">7 videos</option>
      <option value="10">10 videos</option>
    </select></label>
    <label>Kind<select name="kind">{"".join(
        f'<option value="{_esc(k)}">{_esc(v["label"])}</option>' for k, v in kinds.items())}</select></label>
    <label>Length<select name="target_seconds">
      <option value="30">About 30 seconds</option>
      <option value="45" selected>About 45 seconds</option>
      <option value="60">About 60 seconds</option>
    </select></label>
    <label>Layout<select name="layout">{"".join(
        f'<option value="{_esc(k)}">{_esc(v)}</option>' for k, v in layouts.items())}</select></label>
    <button class="wide" type="submit">Plan the angles and queue them all</button>
  </form>
</section>"""


def twin_page(
    readiness: dict,
    catalogue: dict,
    voices: list[dict],
    plates: list[dict],
    videos: list[dict],
    layouts: dict,
    kinds: dict,
    message: str = "",
    error: str = "",
    recording: dict | None = None,
    windows: list[dict] | None = None,
    recordings: list[dict] | None = None,
    can_record: bool = False,
    why_not_record: str = "",
) -> str:
    recording = recording or {"recording": False}
    windows = windows or []
    recordings = recordings or []
    blocking = ""
    if readiness["blocking"]:
        items = "".join(f"<li>{_esc(b)}</li>" for b in readiness["blocking"])
        blocking = (
            '<div class="blocking"><strong>Before this can make a video:</strong>'
            f"<ul>{items}</ul></div>"
        )
    else:
        blocking = ('<div class="notice"><strong>Everything it needs is here.</strong> '
                    "Write a brief below and it will produce a finished vertical video.</div>")

    notes = ""
    if message:
        notes += f'<div class="notice">{_esc(message)}</div>'
    if error:
        notes += f'<div class="blocking">{_esc(error)}</div>'

    # ---------------------------------------------------------------- voices
    # The "default" badge is built outside the f-string: this venv is Python 3.11,
    # where a backslash inside an f-string expression is a SyntaxError, and an
    # escaped quote is the easiest way to write one by accident.
    default_badge = '<span class="pill on">default</span>'

    if voices:
        rows = "".join(
            f'<tr><td><strong>{_esc(v["name"])}</strong>'
            f'{default_badge if v["is_default"] else ""}<br>'
            f'<span class="muted">{_esc(v["provider"])} · '
            f'{(v["reference_seconds"] or 0):.0f}s reference</span></td>'
            f'<td><a href="/twin/voice/{v["id"]}/sample" target="_blank">Hear it</a></td>'
            f'<td><form method="post" action="/twin/voice/{v["id"]}/default">'
            f'<button type="submit" style="background:#39475c;color:#edf2f7">Make default</button></form></td>'
            f'<td><form method="post" action="/twin/voice/{v["id"]}/delete">'
            f'<button type="submit" style="background:#3a2226;color:#f8b4b4">Remove</button></form></td></tr>'
            for v in voices
        )
        voice_table = f"<table><tr><th>Voice</th><th>Sample</th><th></th><th></th></tr>{rows}</table>"
    else:
        voice_table = '<p class="empty">No voice yet.</p>'

    # ---------------------------------------------------------------- plates
    if plates:
        rows = "".join(
            f'<tr><td><strong>{_esc(p["name"])}</strong>'
            f'{default_badge if p["is_default"] else ""}<br>'
            f'<span class="muted">{(p["seconds"] or 0):.0f}s · {p["width"]}x{p["height"]}</span></td>'
            f'<td><form method="post" action="/twin/plate/{p["id"]}/default">'
            f'<button type="submit" style="background:#39475c;color:#edf2f7">Make default</button></form></td>'
            f'<td><form method="post" action="/twin/plate/{p["id"]}/delete">'
            f'<button type="submit" style="background:#3a2226;color:#f8b4b4">Remove</button></form></td></tr>'
            for p in plates
        )
        plate_table = f"<table><tr><th>Footage</th><th></th><th></th></tr>{rows}</table>"
    else:
        plate_table = '<p class="empty">No footage yet.</p>'

    # ---------------------------------------------------------------- videos
    if videos:
        rows = []
        for v in videos:
            if v["status"] == "done":
                state = f'<a href="/twin/video/{v["id"]}/play" target="_blank">▶ Play</a>'
            elif v["status"] == "error":
                state = f'<span style="color:#f8b4b4">Stopped at {_esc(v["stage"])}</span><br><span class="muted" style="font-size:.8rem">{_esc(v["error_message"])}</span>'
            elif v["status"] == "building":
                state = f'<span class="muted">Working — {_esc(v["stage"])}…</span>'
            else:
                state = '<span class="muted">Waiting</span>'
            retry = (
                f'<form method="post" action="/twin/video/{v["id"]}/build">'
                f'<button type="submit" style="background:#39475c;color:#edf2f7">'
                f'{"Build" if v["status"] in ("queued", "error") else "Rebuild"}</button></form>'
            )
            title = _esc(v["script_title"] or "Untitled")
            subtitle = f'{_esc(v["script_kind"])} · {_esc((v["script_hook"] or "")[:70])}'
            rows.append(
                f'<tr class="stagerow"><td><strong>{title}</strong><br>'
                f'<span class="muted">{subtitle}</span></td>'
                f"<td>{state}</td><td>{retry}</td></tr>"
            )
        video_table = f"<table><tr><th>Video</th><th>Status</th><th></th></tr>{''.join(rows)}</table>"
    else:
        video_table = '<p class="empty">Nothing made yet.</p>'

    kind_options = "".join(
        f'<option value="{_esc(k)}">{_esc(v["label"])}</option>' for k, v in kinds.items()
    )
    layout_options = "".join(
        f'<option value="{_esc(k)}">{_esc(v)}</option>' for k, v in layouts.items()
    )
    # A dropdown of what he has actually recorded, not a box to type a path into.
    screen_options = '<option value="">None — just me on screen</option>' + "".join(
        f'<option value="{_esc(r["path"])}">{_esc(r["name"])} ({r["seconds"]:.0f}s)</option>'
        for r in recordings
    )

    voice_cards = "".join(_provider_card(p, "voice", catalogue["pinned"]["voice"]) for p in catalogue["voice"])
    face_cards = "".join(_provider_card(p, "face", catalogue["pinned"]["face"]) for p in catalogue["face"])

    fleet_line = ""
    write_role = readiness["fleet"]["roles"].get("write", {})
    if write_role.get("model"):
        fleet_line = f'<p class="muted">Scripts are written by <strong>{_esc(write_role["model"])}</strong>, running on this PC. No account, no per-word cost.</p>'
    elif write_role.get("error"):
        fleet_line = f'<p class="muted" style="color:#f0b429">{_esc(write_role["error"])}</p>'

    return f"""
{EXTRA_CSS}
<h1>Your Twin</h1>
{NAV}
<p class="lead">Your voice and your footage, driven by a script you did not have to
write or record. Everything on this page runs on this PC unless you deliberately
switch a piece to a paid service.</p>
{notes}
{blocking}

<section>
  <h2 style="margin-top:0">Make a video</h2>
  {fleet_line}
  <form method="post" action="/twin/create">
    <label class="wide">What is this video about?
      <textarea name="brief" required placeholder="e.g. Show ClipForge finding the best 45 seconds of a two hour podcast on its own, then posting it. The point is it runs while I sleep and costs nothing."></textarea></label>
    <label>Kind<select name="kind">{kind_options}</select></label>
    <label>Length<select name="target_seconds">
      <option value="30">About 30 seconds</option>
      <option value="45" selected>About 45 seconds</option>
      <option value="60">About 60 seconds</option>
      <option value="90">About 90 seconds</option>
    </select></label>
    <label>Layout<select name="layout">{layout_options}</select></label>
    <label>Captions<select name="caption_style">
      <option value="karaoke" selected>Word lights up as it's said</option>
      <option value="block">Plain blocks of words</option>
    </select></label>
    <label>Screen recording<select name="screen_path">{screen_options}</select></label>
    <button class="wide" type="submit">Write the script and queue it</button>
  </form>
</section>

{_series_section(kinds, layouts)}

<section>
  <h2 style="margin-top:0">Videos ({len(videos)})</h2>
  {video_table}
</section>

{_screen_section(recording, windows, recordings, can_record, why_not_record)}

<section>
  <h2 style="margin-top:0">Your voice</h2>
  <p class="muted">Record yourself reading anything for about twenty to thirty seconds, somewhere
  quiet, in your normal speaking voice. Any phone recording works. There is no training step and
  nothing is uploaded — the file stays in this folder.</p>
  {voice_table}
  <form method="post" action="/twin/voice" enctype="multipart/form-data" style="margin-top:16px">
    <label>Name this voice<input name="name" required placeholder="Alexander"></label>
    <label>Recording<input name="audio" type="file" required accept="audio/*,video/*"></label>
    <button class="wide" type="submit">Add this voice</button>
  </form>
</section>

<section>
  <h2 style="margin-top:0">Your footage</h2>
  <p class="muted">A short video of you talking — even twenty seconds on loop — or a single photo
  if you have not filmed anything yet. This is what makes the video <em>you</em> rather than a
  stock presenter.</p>
  {plate_table}
  <form method="post" action="/twin/plate" enctype="multipart/form-data" style="margin-top:16px">
    <label>Name it<input name="name" required placeholder="Desk, grey hoodie"></label>
    <label>Video or photo<input name="footage" type="file" required accept="video/*,image/*"></label>
    <button class="wide" type="submit">Add this footage</button>
  </form>
</section>

<section>
  <h2 style="margin-top:0">What is doing the work</h2>
  <p class="muted">Free local options are chosen automatically. A paid one is never selected on its
  own even if a key exists — you have to pick it here.</p>
  <h3 style="font-size:1rem;color:#c5d0de">Voice</h3>
  <div class="grid2">{voice_cards}</div>
  <h3 style="font-size:1rem;color:#c5d0de;margin-top:20px">Presenter</h3>
  <div class="grid2">{face_cards}</div>
</section>

<section>
  <h2 style="margin-top:0">Filming your footage once</h2>
  <p class="muted">Twenty minutes with a phone gives ClipForge everything it needs for good.
  You never have to film for a specific video again.</p>
  <ol class="steps">
    <li><strong>Sound matters more than picture.</strong> A quiet room with soft furnishings beats
    an expensive microphone in an echoey one. Phone at arm's length is fine.</li>
    <li><strong>Record twenty to thirty seconds of normal talking</strong> for the voice. Read
    anything. Your real speaking pace, not a presenter voice — the clone copies whatever you give it.</li>
    <li><strong>Then film silent presenter loops.</strong> Look at the lens, nod, gesture, listen,
    react. Twenty seconds each. These get looped under narration, so they must not have a beginning
    or an end.</li>
    <li><strong>Vary it three or four times</strong> — different shirt, different spot, sitting and
    standing. That is what stops every video looking identical.</li>
    <li><strong>Shoot vertical</strong>, and leave space above your head and around the lower third
    where the captions land.</li>
  </ol>
</section>
"""
