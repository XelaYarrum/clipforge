"""The three pages Alexander actually touches: Accounts, Channel, Add-by-URL.

Kept out of app.py because app.py is already the pipeline; this is just the parts
with a human on the other end.
"""

from __future__ import annotations

import html

NAV = """
<p class="lead">
  <a href="/">Sources</a> &nbsp;·&nbsp;
  <a href="/channel">Channel</a> &nbsp;·&nbsp;
  <a href="/accounts">Accounts</a>
</p>
"""


def _dot(ok: bool, warn: bool = False) -> str:
    colour = "#42d392" if ok else ("#f0b429" if warn else "#6b7789")
    label = "connected" if ok else ("needs your click" if warn else "not set up")
    return f'<span style="color:{colour};font-weight:700;">● {label}</span>'


def accounts_page(statuses: list[dict], hosting_ready: bool, error: str = "") -> str:
    rows = []
    for st in statuses:
        platform = st["platform"]
        name = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}[platform]

        if st["ready"]:
            state = _dot(True)
            action = f'<form method="post" action="/accounts/{platform}/disconnect" style="display:block"><button class="wide" style="background:#39475c;color:#edf2f7">Disconnect</button></form>'
        elif st["has_client"]:
            state = _dot(False, warn=True)
            action = f'<a href="/oauth/{platform}/start"><button type="button">Connect {name}</button></a>'
        else:
            state = _dot(False)
            action = f'<span class="muted">Add the details below first</span>'

        missing = ""
        if st["missing"]:
            missing = f'<div class="muted" style="margin-top:6px">Still needs: {html.escape(", ".join(st["missing"]))}</div>'

        rows.append(
            f"<tr><td><strong>{name}</strong>{missing}</td><td>{state}</td><td>{action}</td></tr>"
        )

    error_html = (
        f'<div class="notice" style="border-left-color:#f05252">{html.escape(error)}</div>'
        if error
        else ""
    )

    ig_note = (
        ""
        if hosting_ready
        else '<div class="notice" style="border-left-color:#f0b429">'
        "<strong>Instagram also needs somewhere to put a clip for a minute.</strong><br>"
        "Instagram won't accept a video file — it insists on fetching the clip from a public "
        "web address. Until storage is set up below, Instagram posts will wait rather than fail. "
        "Nothing else is affected.</div>"
    )

    return f"""
<h1>Accounts</h1>
{NAV}
{error_html}
<p class="lead">Each platform needs its details once, then one Connect click. After that
ClipForge posts on its own and never asks again.</p>

<section>
  <table>
    <tr><th>Platform</th><th>Status</th><th></th></tr>
    {''.join(rows)}
  </table>
</section>

<section>
  <h2 style="margin-top:0;font-size:1.15rem">YouTube</h2>
  <p class="muted">Download the OAuth client JSON from Google Cloud Console
  (APIs &amp; Services → Credentials → Create credentials → OAuth client ID → Desktop app),
  then paste the whole file's contents here.</p>
  <form method="post" action="/accounts/youtube">
    <label class="wide">Contents of the downloaded .json file
      <textarea name="raw_json" placeholder='{{"installed": {{"client_id": "...", ...}}}}' required></textarea>
    </label>
    <button class="wide" type="submit">Save YouTube details</button>
  </form>
</section>

<section>
  <h2 style="margin-top:0;font-size:1.15rem">TikTok</h2>
  <p class="muted">From developers.tiktok.com → Manage apps → your app. The app needs the
  Content Posting API product and the video.publish scope. Redirect URI must be exactly:
  <code>http://127.0.0.1:8000/oauth/tiktok/callback</code></p>
  <form method="post" action="/accounts/tiktok">
    <label>Client key<input name="client_key" required></label>
    <label>Client secret<input name="client_secret" type="password" required></label>
    <button class="wide" type="submit">Save TikTok details</button>
  </form>
</section>

<section>
  <h2 style="margin-top:0;font-size:1.15rem">Instagram</h2>
  <p class="muted">From developers.facebook.com → your app. The Instagram account must be a
  Business account (not Creator) linked to a Facebook Page. Redirect URI must be exactly:
  <code>http://127.0.0.1:8000/oauth/instagram/callback</code></p>
  <form method="post" action="/accounts/instagram">
    <label>App ID<input name="app_id" required></label>
    <label>App secret<input name="app_secret" type="password" required></label>
    <label class="wide">Instagram user ID<input name="ig_user_id" required></label>
    <button class="wide" type="submit">Save Instagram details</button>
  </form>
</section>

<section>
  <h2 style="margin-top:0;font-size:1.15rem">Clip storage (Instagram only) {_dot(hosting_ready)}</h2>
  {ig_note}
  <p class="muted">Any S3-compatible storage works — Cloudflare R2 and Backblaze B2 are the
  cheap ones. A clip goes up, Instagram grabs it, and ClipForge deletes it straight after.</p>
  <form method="post" action="/accounts/hosting">
    <label>Endpoint URL<input name="endpoint_url" placeholder="https://<id>.r2.cloudflarestorage.com" required></label>
    <label>Bucket<input name="bucket" required></label>
    <label>Access key<input name="access_key" required></label>
    <label>Secret key<input name="secret_key" type="password" required></label>
    <label class="wide">Public base URL<input name="public_base" placeholder="https://pub-....r2.dev" required></label>
    <button class="wide" type="submit">Save storage details</button>
  </form>
</section>
"""


def channel_page(profile: dict, saved: bool = False) -> str:
    v = {k: html.escape(profile.get(k) or "") for k in profile}
    note = '<div class="notice">Saved. New clips will be picked and captioned with this in mind.</div>' if saved else ""
    return f"""
<h1>Channel</h1>
{NAV}
{note}
<p class="lead">This is how ClipForge knows what your channel is. It decides which moments
get clipped, so the more honest it is, the better the picks. Leave it blank and it just
guesses at what's generally interesting.</p>

<section>
  <form method="post" action="/channel">
    <label class="wide">Your handle (shown on every clip)
      <input name="handle" value="{v.get('handle','')}" placeholder="@yourchannel"></label>
    <label class="wide">What is this channel about?
      <textarea name="niche" placeholder="e.g. long-form interviews with fighters, cut down to the moments where they say something they regret">{v.get('niche','')}</textarea></label>
    <label class="wide">Who is watching?
      <textarea name="audience" placeholder="e.g. 18-30 year old men who already follow the sport and know the names">{v.get('audience','')}</textarea></label>
    <label>Tone
      <input name="tone" value="{v.get('tone','')}" placeholder="e.g. blunt, no fluff"></label>
    <label>Extra hashtags
      <input name="extra_hashtags" value="{v.get('extra_hashtags','')}" placeholder="#mma #interview"></label>
    <label class="wide">Never clip anything about
      <textarea name="avoid" placeholder="e.g. politics, anything about his family">{v.get('avoid','')}</textarea></label>
    <button class="wide" type="submit">Save channel</button>
  </form>
</section>
"""
