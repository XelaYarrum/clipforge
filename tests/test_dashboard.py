"""Drive the real dashboard end-to-end against a throwaway DB."""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, r".")

import app as clipforge

tmp = Path(tempfile.mkdtemp())
clipforge.DB_PATH = tmp / "t.db"
clipforge.setup_database()

# point credentials + hosting at the temp dir so the real config is untouched
from post import credentials, hosting, oauth
credentials.CRED_DIR = tmp / "creds"
hosting.CONFIG_PATH = tmp / "creds" / "hosting.json"

import routes  # binds the pages
from fastapi.testclient import TestClient

client = TestClient(clipforge.app)
fails = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- pages load
for path in ("/", "/channel", "/accounts"):
    r = client.get(path)
    check(f"GET {path} loads", r.status_code == 200, f"status {r.status_code}")

check("nav links to Channel + Accounts", "/accounts" in client.get("/").text)

# ---------------------------------------------------------------- accounts start empty
r = client.get("/accounts")
check("accounts shows 'not set up' before anything is added", "not set up" in r.text)
check("accounts warns Instagram needs storage", "fetching the clip from a public" in r.text)

# ---------------------------------------------------------------- channel round-trips
r = client.post("/channel", data={
    "handle": "@fightclips",
    "niche": "long-form fighter interviews cut to the honest moments",
    "audience": "people who already follow the sport",
    "tone": "blunt",
    "extra_hashtags": "mma, interview",
    "avoid": "politics",
}, follow_redirects=True)
check("saving the channel redirects back", r.status_code == 200)
check("channel remembers the handle", "@fightclips" in r.text)
check("channel remembers what to avoid", "politics" in r.text)

import profile as channel_profile
prof = channel_profile.load(clipforge.db_connection)
ctx = channel_profile.scoring_context(prof)
check("profile becomes scoring context for the clip finder", "fighter interviews" in ctx)
check("scoring context carries the avoid rule", "NEVER pick clips about: politics" in ctx)

from post import metadata
m = metadata.build("youtube", "A hook", "body text", prof)
check("metadata uses the channel handle", "@fightclips" in m["description"])
check("metadata carries the custom hashtags", "#mma" in m["description"] and "#interview" in m["description"])
check("metadata still forces #shorts for youtube", "#shorts" in m["description"])

# ---------------------------------------------------------------- credential validation
r = client.post("/accounts/youtube", data={"raw_json": "this is not json"}, follow_redirects=True)
# NB: the apostrophe renders as &#x27; — correct escaping, so match around it.
check("bad YouTube paste is rejected in plain words", "look like the JSON file" in r.text)

r = client.post("/accounts/youtube", data={"raw_json": '{"something": "else"}'}, follow_redirects=True)
check("valid JSON that isn't an OAuth file is still rejected", "not an OAuth client file" in r.text)

r = client.post("/accounts/youtube", data={
    "raw_json": '{"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "s3cret"}}'
}, follow_redirects=True)
check("a real-shaped YouTube client file is accepted", r.status_code == 200)
check("accounts now offers the Connect click", "Connect YouTube" in client.get("/accounts").text)

st = credentials.status("youtube")
check("youtube has client but no token yet", st["has_client"] and not st["has_token"])
check("youtube not 'ready' until connected", not st["ready"])

# ---------------------------------------------------------------- secrets never render
r = client.get("/accounts")
check("the client secret is NEVER shown on the page", "s3cret" not in r.text)

# ---------------------------------------------------------------- oauth url is real
client.post("/accounts/tiktok", data={"client_key": "KEY123", "client_secret": "SEK"})
url = oauth.authorize_url("tiktok")
check("tiktok authorize url points at tiktok", url.startswith("https://www.tiktok.com/v2/auth/authorize/"))
check("tiktok authorize url asks for video.publish", "video.publish" in url)
check("tiktok redirect_uri matches what he pastes into the portal",
      "127.0.0.1%3A8000%2Foauth%2Ftiktok%2Fcallback" in url)

yt_url = oauth.authorize_url("youtube")
check("youtube authorize url asks for offline access (so it can refresh)", "access_type=offline" in yt_url)
check("youtube authorize url asks for the upload scope", "youtube.upload" in yt_url)

# ---------------------------------------------------------------- csrf state
r = client.get("/oauth/tiktok/callback?code=x&state=forged", follow_redirects=True)
check("a forged callback state is refused", "That link expired" in r.text)

# ---------------------------------------------------------------- live connectors refuse cleanly
from post.connectors import get_connector
from post.live import NotConnected
for platform in ("youtube", "tiktok", "instagram"):
    try:
        get_connector(platform, live=True).upload("x.mp4", {"title": "t"})
        check(f"{platform} live refuses without a token", False, "it did not raise")
    except NotConnected as e:
        check(f"{platform} live refuses without a token", "not connected yet" in str(e))
    except Exception as e:
        check(f"{platform} live refuses without a token", False, f"wrong error: {type(e).__name__}: {e}")

# hosting unconfigured => a clear, actionable error, not a crash
try:
    hosting.publish("whatever.mp4")
    check("unconfigured hosting raises", False)
except hosting.HostingNotConfigured as e:
    check("unconfigured hosting explains itself", "public URL" in str(e))

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
