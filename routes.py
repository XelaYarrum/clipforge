"""The human-facing routes: Channel (context), Accounts (credentials + connect),
and pulling a source straight from a URL.

Imported at the BOTTOM of app.py, so `from app import ...` here is resolved by then.
Everything in this file has the owner on the other end of it, which is why the
failures are sentences rather than status codes.
"""

from __future__ import annotations

import html
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import pages
import profile as channel_profile
from app import (
    CLIP_POOL,
    MEDIA_DIR,
    app,
    db_connection,
    page,
    redirect_with_error,
)
from post import credentials as post_credentials
from post import hosting as post_hosting
from post import oauth as post_oauth
from sources import puller


# ------------------------------------------------------------------ channel


@app.get("/channel", response_class=HTMLResponse)
def channel_get(request: Request) -> HTMLResponse:
    saved = request.query_params.get("saved") == "1"
    return page(pages.channel_page(channel_profile.load(db_connection), saved))


@app.post("/channel")
def channel_post(
    handle: str = Form(""),
    niche: str = Form(""),
    audience: str = Form(""),
    tone: str = Form(""),
    extra_hashtags: str = Form(""),
    avoid: str = Form(""),
) -> RedirectResponse:
    channel_profile.save(
        db_connection,
        {
            "handle": handle,
            "niche": niche,
            "audience": audience,
            "tone": tone,
            "extra_hashtags": extra_hashtags,
            "avoid": avoid,
        },
    )
    return RedirectResponse(url="/channel?saved=1", status_code=303)


# ------------------------------------------------------------------ accounts


@app.get("/accounts", response_class=HTMLResponse)
def accounts_get(request: Request) -> HTMLResponse:
    return page(
        pages.accounts_page(
            post_credentials.all_status(),
            post_hosting.is_configured(),
            request.query_params.get("error", ""),
        )
    )


def _accounts_error(message: str) -> RedirectResponse:
    return RedirectResponse(url="/accounts?" + urlencode({"error": message}), status_code=303)


@app.post("/accounts/youtube")
def save_youtube(raw_json: str = Form(...)) -> RedirectResponse:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return _accounts_error(
            "That didn't look like the JSON file. Paste the whole contents of the downloaded file."
        )
    if not (parsed.get("installed") or parsed.get("web")):
        return _accounts_error(
            'That JSON is not an OAuth client file - it should start with "installed" or "web".'
        )
    post_credentials.save_client("youtube", parsed)
    return RedirectResponse(url="/accounts", status_code=303)


@app.post("/accounts/tiktok")
def save_tiktok(client_key: str = Form(...), client_secret: str = Form(...)) -> RedirectResponse:
    post_credentials.save_client(
        "tiktok", {"client_key": client_key.strip(), "client_secret": client_secret.strip()}
    )
    return RedirectResponse(url="/accounts", status_code=303)


@app.post("/accounts/instagram")
def save_instagram(
    app_id: str = Form(...), app_secret: str = Form(...), ig_user_id: str = Form(...)
) -> RedirectResponse:
    post_credentials.save_client(
        "instagram",
        {
            "app_id": app_id.strip(),
            "app_secret": app_secret.strip(),
            "ig_user_id": ig_user_id.strip(),
        },
    )
    return RedirectResponse(url="/accounts", status_code=303)


@app.post("/accounts/hosting")
def save_hosting(
    endpoint_url: str = Form(...),
    bucket: str = Form(...),
    access_key: str = Form(...),
    secret_key: str = Form(...),
    public_base: str = Form(...),
) -> RedirectResponse:
    post_hosting.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    post_hosting.CONFIG_PATH.write_text(
        json.dumps(
            {
                "endpoint_url": endpoint_url.strip(),
                "bucket": bucket.strip(),
                "access_key": access_key.strip(),
                "secret_key": secret_key.strip(),
                "public_base": public_base.strip(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RedirectResponse(url="/accounts", status_code=303)


@app.post("/accounts/{platform}/disconnect")
def disconnect(platform: str) -> RedirectResponse:
    if platform not in post_credentials.REQUIREMENTS:
        raise HTTPException(status_code=404, detail="Unknown platform.")
    post_credentials.token_path(platform).unlink(missing_ok=True)
    return RedirectResponse(url="/accounts", status_code=303)


# ------------------------------------------------------------------ oauth


@app.get("/oauth/{platform}/start")
def oauth_start(platform: str) -> RedirectResponse:
    if platform not in post_credentials.REQUIREMENTS:
        raise HTTPException(status_code=404, detail="Unknown platform.")
    try:
        return RedirectResponse(url=post_oauth.authorize_url(platform), status_code=303)
    except Exception as error:
        return _accounts_error(str(error))


@app.get("/oauth/{platform}/callback", response_class=HTMLResponse)
def oauth_callback(platform: str, request: Request) -> HTMLResponse:
    if platform not in post_credentials.REQUIREMENTS:
        raise HTTPException(status_code=404, detail="Unknown platform.")

    params = request.query_params
    if params.get("error"):
        reason = params.get("error_description") or params.get("error") or ""
        return page(
            f"<h1>{html.escape(platform.title())} said no</h1>"
            f'<p class="lead">{html.escape(reason)}</p>'
            '<p><a href="/accounts">Back to Accounts</a></p>'
        )

    code = params.get("code")
    state = params.get("state", "")
    if not code:
        return page('<h1>Nothing came back</h1><p><a href="/accounts">Back to Accounts</a></p>')
    if not post_oauth.check_state(platform, state):
        # A mismatched state means this callback didn't come from a Connect click here.
        return page(
            "<h1>That link expired</h1>"
            '<p class="lead">Click Connect again - it has to be a fresh click.</p>'
            '<p><a href="/accounts">Back to Accounts</a></p>'
        )

    try:
        post_oauth.exchange_code(platform, code)
    except Exception as error:
        return page(
            f"<h1>Couldn't connect {html.escape(platform.title())}</h1>"
            f'<p class="lead">{html.escape(str(error))}</p>'
            '<p><a href="/accounts">Back to Accounts</a></p>'
        )

    return page(
        f"<h1>{html.escape(platform.title())} connected</h1>"
        '<div class="notice">That was the only time it needs you. '
        "ClipForge can post here on its own now.</div>"
        '<p><a href="/accounts">Back to Accounts</a></p>'
    )


# ------------------------------------------------------------------ pull by url


@app.post("/sources/from-url")
def add_source_from_url(
    url: str = Form(...),
    rights_type: str = Form(...),
    rights_evidence: str = Form(...),
) -> RedirectResponse:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return redirect_with_error("Paste a full video link starting with http.")

    try:
        info = puller.probe(url)
    except Exception as error:
        return redirect_with_error(f"Couldn't read that link: {str(error)[:200]}")

    with closing(db_connection()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO sources
            (title, source_url, rights_type, rights_evidence, attribution, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                info["title"],
                info["webpage_url"],
                rights_type.strip(),
                rights_evidence.strip(),
                info["uploader"],
                f"Pulled from URL. Duration {info['duration_sec']}s.",
                datetime.now(UTC).isoformat(),
            ),
        )
        source_id = cursor.lastrowid
        connection.commit()

    # Downloading is slow, so it runs on the pool and the page comes back now.
    CLIP_POOL.submit(_download_source, source_id, url)
    return RedirectResponse(url="/", status_code=303)


def _download_source(source_id: int, url: str) -> None:
    try:
        result = puller.download(url, str(MEDIA_DIR))
    except Exception:
        return
    stored = Path(result["path"]).name
    with closing(db_connection()) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO media_files
            (source_id, original_filename, stored_filename, sha256, byte_size, content_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                stored,
                stored,
                result["sha256"],
                result["byte_size"],
                "video/mp4",
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
