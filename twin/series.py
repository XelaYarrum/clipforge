"""One topic in, a week of videos out.

Writing a brief per video caps output at however many briefs he feels like typing.
Release velocity is the thing the whole plan rests on, so the brief itself has to
stop being the bottleneck.

This asks the local model for N genuinely different ANGLES on one topic first, and
only then writes a script per angle. Asking for five scripts in one go gets five
rewordings of the same script — the model anchors on its own first answer. Making
the angles a separate, explicit step is what produces five different videos.
"""

from __future__ import annotations

import re

import fleet

from . import pipeline, store
from .script import KINDS

MAX_PER_RUN = 10

ANGLES_SYSTEM = """You plan short-form video series. Given one topic, you produce genuinely DIFFERENT angles on it — not rewordings.

Two angles are different when they would open with different first sentences, interest different people, and could both exist on the same channel without one making the other redundant.

Useful ways to differ: the problem it removes vs the thing it is; what it costs vs what it does; the mistake most people make vs the right way; a specific number vs a story; who it is for vs who it is NOT for; what it replaces; what surprised the maker.

Do NOT produce: "part 1 / part 2" splits, "here are 5 tips" fragments, or the same claim with different adjectives.

Reply with ONLY this JSON object:
{"angles": [{"angle": "<one sentence naming what THIS video argues>", "why_different": "<what makes it not the others>"}]}"""


def plan_angles(topic: str, count: int, channel_context: str = "") -> list[dict]:
    """Ask for `count` distinct angles. Raises FleetError with a readable reason."""
    if not (topic or "").strip():
        raise fleet.FleetError("there's no topic to plan from")
    if not 1 <= count <= MAX_PER_RUN:
        raise fleet.FleetError(f"pick between 1 and {MAX_PER_RUN} videos, not {count}")

    system = ANGLES_SYSTEM
    if channel_context:
        system += f"\n\nABOUT THIS CHANNEL: {channel_context}"

    parsed = fleet.chat_json(
        "write", system,
        f"TOPIC: {topic.strip()}\n\nGive exactly {count} different angles.",
        temperature=0.85,  # higher than script writing: the job here is spread, not polish
    )
    raw = parsed.get("angles") or []
    if not isinstance(raw, list):
        raise fleet.FleetError("the planner didn't return a list of angles")

    angles: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("angle", ""))).strip()
        if not text:
            continue
        # Near-duplicate guard: the model is asked for difference and sometimes
        # produces the same sentence twice. Silently shipping both would make the
        # count a lie.
        fingerprint = " ".join(sorted(re.findall(r"[a-z]{4,}", text.lower())))[:180]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        angles.append({"angle": text, "why_different": str(item.get("why_different", "")).strip()})

    if not angles:
        raise fleet.FleetError("the planner returned no usable angles — try a more specific topic")
    return angles[:count]


def create_series(
    db,
    topic: str,
    count: int = 5,
    kind: str = "demo",
    target_seconds: int = 45,
    channel_context: str = "",
    voice_id: int | None = None,
    plate_id: int | None = None,
    screen_path: str = "",
) -> dict:
    """Plan the angles, then write and queue one video per angle.

    A script that fails to write does not stop the run — it is reported and the
    rest still get made. Four videos and one named failure beats zero videos.
    """
    if kind not in KINDS:
        raise fleet.FleetError(f"unknown kind {kind!r} — choices: {', '.join(KINDS)}")

    angles = plan_angles(topic, count, channel_context)
    made: list[dict] = []
    failed: list[dict] = []

    for angle in angles:
        brief = (
            f"{topic.strip()}\n\n"
            f"THIS video's angle, which it must stick to: {angle['angle']}"
        )
        try:
            video_id = pipeline.create(
                db, brief, kind, target_seconds,
                voice_id=voice_id, plate_id=plate_id,
                screen_path=screen_path, channel_context=channel_context,
            )
        except Exception as error:  # noqa: BLE001 — one bad script must not sink the batch
            failed.append({"angle": angle["angle"], "error": str(error)[:300]})
            continue

        row = store.get_video(db, video_id)
        script_row = store.get_script(db, row["script_id"]) if row else None
        made.append({
            "video_id": video_id,
            "angle": angle["angle"],
            "title": (script_row or {}).get("title", ""),
            "hook": (script_row or {}).get("hook", ""),
        })

    return {
        "topic": topic.strip(),
        "asked_for": count,
        "angles_planned": len(angles),
        "queued": made,
        "failed": failed,
    }


def summarise(result: dict) -> str:
    """One honest sentence about what a run produced.

    It reports asked-for, planned and queued separately, because those three
    numbers come apart — the planner can return four angles for a request of five,
    and a count with no denominator hides that.
    """
    queued = len(result["queued"])
    parts = [f"Queued {queued} of the {result['asked_for']} asked for"]
    if result["angles_planned"] < result["asked_for"]:
        parts.append(
            f"the planner only found {result['angles_planned']} genuinely different angles"
        )
    if result["failed"]:
        parts.append(f"{len(result['failed'])} script(s) failed to write")
    return ". ".join(parts) + "."
