"""End-to-end: a finished render -> queued -> mock-posted on all 3 platforms.

Runs against a THROWAWAY database so nothing touches the real Source Library.
"""
import sys, tempfile, os, json
from pathlib import Path
from contextlib import closing

sys.path.insert(0, r".")

import app

# redirect the app at a temp DB + temp mock log BEFORE importing the post runner
tmpdir = Path(tempfile.mkdtemp())
app.DB_PATH = tmpdir / "test.db"

from post import runner as post_runner
from post import connectors, scheduler
connectors.MOCK_LOG = tmpdir / "mock_posts.log"

CLIP = str(Path(__file__).parent / "testclip.mp4")

app.setup_database()
with closing(app.db_connection()) as c:
    scheduler.setup_post_tables(c)
    c.execute(
        "INSERT INTO sources (id, title, source_url, rights_type, rights_evidence, created_at)"
        " VALUES (1,'Test Source','http://x','owned','test-only','2026-07-15')"
    )
    c.execute(
        "INSERT INTO clip_candidates (id, source_id, start_sec, end_sec, hook, score,"
        " clip_text, status, created_at)"
        " VALUES (1,1,0,30,'This one trick changed everything',85,"
        "'the full spoken text of the clip','candidate','2026-07-15')"
    )
    c.execute(
        "INSERT INTO renders (id, candidate_id, source_id, output_path, status,"
        " created_at, updated_at) VALUES (1,1,1,?, 'done','2026-07-15','2026-07-15')",
        (CLIP,),
    )
    c.commit()

print("--- pass 1: queue + post ---")
post_runner.process(live=False)

print("\n--- pass 2: must NOT double-post the same clip ---")
post_runner.process(live=False)

with closing(app.db_connection()) as c:
    rows = c.execute(
        "SELECT platform, status, post_id, title FROM post_queue ORDER BY platform"
    ).fetchall()
    logged = c.execute("SELECT COUNT(*) AS n FROM post_log").fetchone()["n"]

print("\n--- post_queue ---")
for r in rows:
    print(f"  {r['platform']:10} {r['status']:8} {r['post_id']}")
    print(f"             title: {r['title']}")

print(f"\npost_log rows: {logged}")

print("\n--- mock log file ---")
print(connectors.MOCK_LOG.read_text(encoding="utf-8").strip())

ok = (
    len(rows) == 3
    and all(r["status"] == "posted" for r in rows)
    and logged == 3  # exactly 3 — pass 2 must not have added more
)
# --- LIVE mode with nothing connected must WAIT, never mark the clip failed ---
print("\n--- pass 3: live mode, nothing connected -> must defer, not fail ---")
with closing(app.db_connection()) as c:
    c.execute("UPDATE post_queue SET status='pending', post_id=NULL, not_before=NULL")
    c.execute("DELETE FROM post_log")
    c.commit()

post_runner.process(live=True)

with closing(app.db_connection()) as c:
    states = [r["status"] for r in c.execute("SELECT status FROM post_queue").fetchall()]
    deferred = c.execute(
        "SELECT COUNT(*) AS n FROM post_queue WHERE not_before IS NOT NULL"
    ).fetchone()["n"]

print(f"  queue states after live attempt: {states}")
live_ok = all(s == "pending" for s in states) and deferred == 3
print("  " + ("PASS: all 3 waiting, none marked failed" if live_ok
              else "FAIL: an unconnected platform burned the clip"))
ok = ok and live_ok

print("\n" + ("E2E PASS" if ok else "E2E FAIL"))

print("\n--- live connector must refuse (no OAuth grant yet) ---")
from post.live import NotConnected
try:
    connectors.get_connector("tiktok", live=True).upload(CLIP, {"title": "x"})
    print("FAIL: live connector silently pretended to post")
    ok = False
except NotConnected as e:
    print(f"PASS: refused loudly -> {e}")

sys.exit(0 if ok else 1)
