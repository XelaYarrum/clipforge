"""The local model fleet — ClipForge's brain, and the reason it costs nothing to run.

Everything in ClipForge that needs a language model comes through here. Nothing
names a model by hand.

That rule exists because of a real failure in this codebase: app.py hardcoded
SCORING_MODEL = "north-64k", that model was later removed from the machine, and
because score_window() swallowed the exception the clip finder reported "no clips
found" forever instead of "the model you asked for isn't installed". A hardcoded
model name is a time bomb with the fuse hidden.

So: a job asks for a ROLE. This file asks Ollama what is actually installed right
now and picks the best match. If nothing matches, it raises with the list of what
IS installed, because that is the sentence that ends the debugging.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
TAGS_URL = f"{OLLAMA_HOST}/api/tags"
CHAT_URL = f"{OLLAMA_HOST}/api/chat"

CONFIG_PATH = Path(__file__).resolve().parent / "data" / "config" / "fleet.json"

# Ordered preference per role. The first one that is actually installed wins.
# Small models come first for the roles that run hundreds of times (scoring);
# bigger ones first for the roles that run once and want quality (writing).
ROLE_PREFERENCES = {
    "score": ["atlas:latest", "qwen3.5:9b", "atlas-local-9b:latest", "orion:latest"],
    "write": ["atlas:latest", "qwen3.5:9b", "helios:latest", "atlas-local-9b:latest", "orion:latest"],
}

# How long each role is allowed to take. Scoring runs per window, so it is short;
# writing runs once per video and is allowed to think.
ROLE_TIMEOUTS = {"score": 180, "write": 300}


class FleetError(RuntimeError):
    """Raised with a sentence a human can act on, never a bare stack trace."""


def _read_overrides() -> dict:
    """data/config/fleet.json lets a role be pinned without editing code."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def installed() -> list[str]:
    """Every model Ollama currently has. Empty list if Ollama is not running."""
    try:
        with urllib.request.urlopen(TAGS_URL, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    return [m.get("name", "") for m in body.get("models", []) if m.get("name")]


def resolve(role: str) -> str:
    """The model this role gets right now. Raises FleetError naming the alternatives."""
    if role not in ROLE_PREFERENCES:
        raise FleetError(f"unknown role {role!r} — roles are: {', '.join(ROLE_PREFERENCES)}")

    have = installed()
    if not have:
        raise FleetError(
            f"Ollama is not answering at {OLLAMA_HOST}. Start Ollama, then try again."
        )

    override = _read_overrides().get(role)
    if override:
        if override in have:
            return override
        raise FleetError(
            f"fleet.json pins role {role!r} to {override!r}, which is not installed. "
            f"Installed: {', '.join(have)}"
        )

    for candidate in ROLE_PREFERENCES[role]:
        if candidate in have:
            return candidate

    # Nothing preferred is present. Rather than fail, take any chat model that is
    # here — a working answer from an unexpected model beats a dead pipeline.
    fallback = next((m for m in have if "embed" not in m), "")
    if fallback:
        return fallback

    raise FleetError(
        f"No model on this machine can serve role {role!r}. "
        f"Installed: {', '.join(have) or 'nothing'}"
    )


def chat(role: str, system: str, user: str, json_mode: bool = False, temperature: float = 0.4) -> str:
    """One round trip. Returns the assistant's text, or raises FleetError saying why not."""
    model = resolve(role)
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["format"] = "json"

    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=ROLE_TIMEOUTS.get(role, 180)) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise FleetError(f"{model} refused the request ({error.code}): {detail}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FleetError(f"couldn't reach Ollama at {OLLAMA_HOST}: {error}") from error
    except json.JSONDecodeError as error:
        raise FleetError(f"{model} returned something that wasn't JSON") from error

    # done_reason is the difference between two faults that look identical from
    # the outside: 'length' means the input was too long, anything else means the
    # model or its template is wrong. Saying which one saves an hour.
    text = (body.get("message") or {}).get("content", "")
    if not text.strip():
        reason = body.get("done_reason", "unknown")
        if reason == "length":
            raise FleetError(f"{model} ran out of room before answering — shorten the input")
        raise FleetError(f"{model} returned nothing (done_reason: {reason})")
    return text


def chat_json(role: str, system: str, user: str, temperature: float = 0.2) -> dict:
    """chat() where the answer must parse as a JSON object."""
    raw = chat(role, system, user, json_mode=True, temperature=temperature)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FleetError(f"answer was not valid JSON: {raw[:200]}") from error
    if not isinstance(parsed, dict):
        raise FleetError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def status() -> dict:
    """What the fleet looks like right now — for the dashboard, not for logic."""
    have = installed()
    roles = {}
    for role in ROLE_PREFERENCES:
        try:
            roles[role] = {"model": resolve(role), "error": None}
        except FleetError as error:
            roles[role] = {"model": None, "error": str(error)}
    return {"host": OLLAMA_HOST, "installed": have, "roles": roles}
