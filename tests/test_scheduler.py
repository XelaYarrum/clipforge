"""Verify piece 7's scheduler + metadata against known answers."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Relative to THIS file, never an absolute path. All three suites used to hardcode
# one machine's full path to the project. The folder was later moved, and every
# suite silently failed to import from then on — while CHECK_EVERYTHING.bat went on
# being the thing that says whether ClipForge works.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from post import scheduler, metadata
from post.scheduler import PACIFIC

fails = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n        got={got!r}\n        want={want!r}")
    if not ok:
        fails.append(name)


def fresh_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    scheduler.setup_post_tables(c)
    return c


def log(c, platform, when):
    c.execute(
        "INSERT INTO post_log (platform, post_id, posted_at) VALUES (?,?,?)",
        (platform, "x", when.astimezone(timezone.utc).isoformat()),
    )
    c.commit()


NOW = datetime(2026, 7, 15, 20, 0, 0, tzinfo=timezone.utc)

# --- TikTok rolling: 4 posts in window => allowed (known GOOD)
c = fresh_db()
for i in range(4):
    log(c, "tiktok", NOW - timedelta(hours=i + 1))
check("tiktok 4-in-window allowed", scheduler.can_post(c, "tiktok", NOW)[0], True)

# --- TikTok rolling: 5 posts in window => blocked (known BAD)
log(c, "tiktok", NOW - timedelta(hours=5))
allowed, nxt = scheduler.can_post(c, "tiktok", NOW)
check("tiktok 5-in-window blocked", allowed, False)
# oldest of the 5 landed 5h ago; its slot frees 24h after that => 19h from NOW
check("tiktok next eligible", nxt, NOW - timedelta(hours=5) + timedelta(hours=24))

# --- rolling window really rolls: posts older than 24h must not count
c = fresh_db()
for i in range(5):
    log(c, "tiktok", NOW - timedelta(hours=25 + i))  # all expired
check("tiktok expired posts dont count", scheduler.can_post(c, "tiktok", NOW)[0], True)

# --- Instagram cap 100
c = fresh_db()
for i in range(99):
    log(c, "instagram", NOW - timedelta(minutes=i + 1))
check("instagram 99 allowed", scheduler.can_post(c, "instagram", NOW)[0], True)
log(c, "instagram", NOW - timedelta(minutes=200))
check("instagram 100 blocked", scheduler.can_post(c, "instagram", NOW)[0], False)

# --- YouTube calendar-Pacific: NOW is 20:00 UTC = 13:00 Pacific same day.
c = fresh_db()
for i in range(5):
    log(c, "youtube", NOW - timedelta(hours=i))
check("youtube 5 today allowed", scheduler.can_post(c, "youtube", NOW)[0], True)
log(c, "youtube", NOW - timedelta(hours=6))
allowed, nxt = scheduler.can_post(c, "youtube", NOW)
check("youtube 6 today blocked", allowed, False)
check("youtube next reset is pacific midnight",
      nxt.astimezone(PACIFIC).strftime("%Y-%m-%d %H:%M"), "2026-07-16 00:00")

# --- the calendar/rolling distinction: 6 youtube posts 20h ago but YESTERDAY
# Pacific must NOT block today. NOW=13:00 Pacific Jul15; 20h earlier = 17:00 Pacific Jul14.
c = fresh_db()
for i in range(6):
    log(c, "youtube", NOW - timedelta(hours=20, minutes=i))
check("youtube yesterdays quota doesnt block today",
      scheduler.can_post(c, "youtube", NOW)[0], True)
# but the same 6 posts WOULD block a rolling-window platform
c2 = fresh_db()
for i in range(6):
    log(c2, "tiktok", NOW - timedelta(hours=20, minutes=i))
check("tiktok same timestamps DO block (rolling)",
      scheduler.can_post(c2, "tiktok", NOW)[0], False)

# --- metadata limits
m = metadata.build("youtube", "A short hook", "some clip text")
check("youtube title", m["title"], "A short hook")
check("youtube desc has #shorts", "#shorts" in m["description"], True)

long_hook = "x" * 500
m = metadata.build("youtube", long_hook, "body")
check("youtube title truncated to 100", len(m["title"]) <= 100, True)

m = metadata.build("tiktok", long_hook, "body")
check("tiktok title untruncated at 500 (limit 2200)", len(m["title"]), 500)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
