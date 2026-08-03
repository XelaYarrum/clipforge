"""The script writer — a brief in, a spoken short-form script out.

Runs on the free local fleet through fleet.py, so writing a script costs nothing
and works with the internet off.

Two things here are deliberate and are the difference between a usable script and
slop:

1. The output is SPOKEN, not written. Models default to prose with subheadings and
   bullet points, which is unspeakable — literally: a voice model reads "1." aloud.
   The system prompt bans everything that only exists on a page.

2. The house style strips the tells. Em dashes, "in today's fast-paced world",
   "let's dive in", "game-changer", "unlock", "leverage", "it's not just X, it's Y".
   These go out under Alexander's name and voice, so anything that reads as
   machine-written costs him more than a weaker script would.
"""

from __future__ import annotations

import json
import re

import fleet

KINDS = {
    "demo": {
        "label": "Software demo",
        "guidance": (
            "This shows a piece of software the speaker built. Open on what it DOES for the "
            "viewer, not what it is. Name the annoying problem it removes in the first line. "
            "Walk through the one path that proves it works. End on where to get it."
        ),
    },
    "build": {
        "label": "Build showcase",
        "guidance": (
            "This shows something the speaker made. The interesting part is the decision or the "
            "constraint, not the feature list. Open on the surprising part. Say what it took. "
            "Do not oversell it."
        ),
    },
    "product": {
        "label": "Product / course ad",
        "guidance": (
            "This sells something. Lead with the outcome the buyer wants, not the contents. One "
            "concrete proof. One clear instruction at the end. No hype adjectives, no urgency "
            "theatre, no fake scarcity."
        ),
    },
    "news": {
        "label": "AI news take",
        "guidance": (
            "This reacts to a piece of AI news. Open with the claim, not the setup. Give the "
            "take in the first five seconds. Be specific about what actually changed rather than "
            "saying it changes everything."
        ),
    },
}

SYSTEM = """You write scripts that will be SPOKEN ALOUD by a text-to-speech voice and cut into a vertical short-form video. You are writing for the ear, never for the page.

HARD RULES — a script breaking any of these is unusable:
- Output ONLY words that should be said out loud. No headings, no bullet points, no numbered lists, no stage directions, no emoji, no markdown, no "[pause]", no speaker labels.
- No symbols a voice cannot read: no em dashes, no semicolons, no parentheses, no slashes, no ampersands. Write numbers the way they are said.
- Short sentences. Most under fifteen words. Vary the length so it does not drone.
- Plain spoken English. Contractions. The vocabulary of someone talking, not presenting.

BANNED PHRASES — these are the fingerprints of machine writing and they cost the speaker credibility:
"in today's world", "in an era where", "let's dive in", "let's break it down", "game changer", "game-changing", "unlock", "unleash", "leverage", "seamless", "revolutionary", "cutting-edge", "the future of", "imagine a world", "it's not just X, it's Y", "here's the thing", "buckle up", "that's a wrap", "stay tuned", "without further ado", "the possibilities are endless".

STRUCTURE:
- hook: one sentence, under twelve words, said in the first two seconds. It must make someone stop scrolling without lying about what follows.
- body: the script itself. Spoken continuously.
- call_to_action: one short sentence telling the viewer exactly one thing to do.

Reply with ONLY this JSON object:
{"title": "<under 60 characters>", "hook": "<one sentence>", "body": "<the spoken script>", "call_to_action": "<one sentence>"}"""

# 150 words per minute is a natural narration pace; the estimate drives how much
# presenter footage has to be looped, so it wants to be close rather than exact.
WORDS_PER_MINUTE = 150

_BANNED = [
    "in today's world", "in an era where", "let's dive in", "let's break it down",
    "game changer", "game-changing", "unlock", "unleash", "leverage", "seamless",
    "revolutionary", "cutting-edge", "the future of", "imagine a world",
    "here's the thing", "buckle up", "that's a wrap", "stay tuned",
    "without further ado", "the possibilities are endless",
]


def estimate_seconds(text: str) -> float:
    words = len(re.findall(r"\b[\w']+\b", text or ""))
    return round(words / WORDS_PER_MINUTE * 60, 1)


def clean_for_speech(text: str) -> str:
    """Strip what a voice model would read aloud as noise.

    This runs on every script regardless of which model wrote it, because the
    system prompt is an instruction and an instruction does not stop a side
    effect — only this does.
    """
    text = (text or "").strip()
    text = re.sub(r"[*_`#>]+", " ", text)                       # markdown
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"^\s*\d+[.)]\s*", "", text, flags=re.MULTILINE)  # numbered lists
    text = re.sub(r"\[[^\]]*\]", " ", text)                     # [stage directions]
    text = re.sub(r"\(([^)]*)\)", r"\1", text)                  # keep the words, drop the brackets
    text = text.replace("—", ", ").replace("–", ", ")           # em/en dash -> a spoken pause
    text = text.replace(";", ".").replace("&", " and ").replace("/", " or ")
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    return text.strip()


def house_style_report(text: str) -> list[str]:
    """Which banned phrases survived. Shown on the page; never silently swallowed."""
    lowered = (text or "").lower()
    found = [phrase for phrase in _BANNED if phrase in lowered]
    if "—" in (text or ""):
        found.append("em dash")
    return found


def write(brief: str, kind: str, channel_context: str = "", target_seconds: int = 45) -> dict:
    """Generate one script. Raises fleet.FleetError with a readable reason on failure."""
    if kind not in KINDS:
        raise fleet.FleetError(f"unknown script kind {kind!r} — choices: {', '.join(KINDS)}")
    if not (brief or "").strip():
        raise fleet.FleetError("there's no brief to write from")

    words = int(target_seconds / 60 * WORDS_PER_MINUTE)
    system = SYSTEM
    if channel_context:
        system += f"\n\nABOUT THIS CHANNEL: {channel_context}"

    user = (
        f"KIND: {KINDS[kind]['label']}\n"
        f"GUIDANCE: {KINDS[kind]['guidance']}\n"
        f"TARGET LENGTH: about {target_seconds} seconds, which is roughly {words} words of body.\n\n"
        f"BRIEF FROM THE SPEAKER:\n{brief.strip()}"
    )

    model = fleet.resolve("write")
    parsed = fleet.chat_json("write", system, user, temperature=0.7)

    body = clean_for_speech(str(parsed.get("body", "")))
    hook = clean_for_speech(str(parsed.get("hook", "")))
    cta = clean_for_speech(str(parsed.get("call_to_action", "")))
    title = str(parsed.get("title", "")).strip()[:60]

    if not body:
        raise fleet.FleetError(f"{model} returned a script with no body")

    # The hook is spoken first and is not part of the body, so it is prepended
    # here rather than at render time — the narration track must be the whole
    # thing in one piece or the timings drift.
    spoken = " ".join(part for part in (hook, body, cta) if part).strip()

    return {
        "title": title or hook[:60] or "Untitled",
        "hook": hook,
        "body": body,
        "call_to_action": cta,
        "spoken": spoken,
        "seconds_estimate": estimate_seconds(spoken),
        "model_used": model,
        "style_warnings": house_style_report(spoken),
    }


def spoken_text(script_row: dict) -> str:
    """Rebuild the full narration from a stored row, in the same order write() used."""
    parts = [script_row.get("hook"), script_row.get("body"), script_row.get("call_to_action")]
    return " ".join(p for p in parts if (p or "").strip()).strip()
