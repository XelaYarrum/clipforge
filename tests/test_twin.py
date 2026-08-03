"""The twin's checks — script cleaning, provider contracts, and the gates.

Deliberately includes cases that must FAIL. A provider that can never report
itself unavailable, or a quality gate that can never reject, is not a gate. Each
of those is asserted here against a known-bad input rather than only a known-good
one.

Nothing here writes into data/twin: CLIPFORGE_TWIN_DIR is redirected to a scratch
folder before the twin package is imported.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="clipforge-twin-test-"))
os.environ["CLIPFORGE_TWIN_DIR"] = str(SCRATCH)

from twin import compose, registry, script as script_writer  # noqa: E402
from twin.contract import Availability, FaceProvider, TwinError, VoiceProvider  # noqa: E402
from twin.face_footage import FootageFace  # noqa: E402
from twin.voice_chatterbox import _split_for_speech  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}: {detail}" if detail else name)


def expect_raises(name: str, exception_type, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exception_type:
        check(name, True)
        return
    except Exception as error:  # noqa: BLE001
        check(name, False, f"raised {type(error).__name__} instead of {exception_type.__name__}")
        return
    check(name, False, "did not raise at all")


# ---------------------------------------------------------------- the gate that must fire

def test_availability_cannot_lie() -> None:
    """A provider saying 'not ready' while naming nothing missing is a bug."""
    expect_raises(
        "Availability rejects not-ready-with-no-reason",
        TwinError, Availability, ready=False, missing=[],
    )
    ok = Availability(ready=False, missing=["a reason"])
    check("Availability accepts not-ready WITH a reason", ok.ready is False and len(ok.missing) == 1)
    check("Availability accepts ready with no reasons", Availability(ready=True).ready is True)


def test_contract_is_abstract() -> None:
    """The base classes must not silently no-op if a provider forgets a method."""
    expect_raises("VoiceProvider.speak is not implemented by default",
                  NotImplementedError, VoiceProvider().speak, "h", "t", "o")
    expect_raises("VoiceProvider.clone is not implemented by default",
                  NotImplementedError, VoiceProvider().clone, "n", "a")
    expect_raises("FaceProvider.render is not implemented by default",
                  NotImplementedError, FaceProvider().render, "p", "a", "o")


# ---------------------------------------------------------------- script cleaning

def test_clean_for_speech_removes_unspeakables() -> None:
    cases = [
        ("## Heading\nsome words", "#", "markdown heading"),
        ("- first point", "-", "bullet"),
        ("1. first point", "1.", "numbered list"),
        ("say this [pause] and this", "[", "stage direction"),
        ("this — that", "—", "em dash"),
        ("a; b", ";", "semicolon"),
        ("cats & dogs", "&", "ampersand"),
        ("**bold**", "*", "asterisk"),
    ]
    for raw, banned, label in cases:
        cleaned = script_writer.clean_for_speech(raw)
        check(f"clean_for_speech removes {label}", banned not in cleaned, repr(cleaned))

    check("clean_for_speech keeps the words from a numbered list",
          "first point" in script_writer.clean_for_speech("1. first point"))
    check("clean_for_speech turns & into the spoken word",
          "and" in script_writer.clean_for_speech("cats & dogs"))
    check("clean_for_speech keeps bracketed words rather than deleting them",
          "important" in script_writer.clean_for_speech("this (important) thing"))


def test_house_style_report_finds_and_misses_correctly() -> None:
    """Calibrated on a known-bad AND a known-good string, not only the bad one."""
    bad = "Let's dive in. This is a game changer that will unlock the future of work."
    found = script_writer.house_style_report(bad)
    check("house style flags a known-bad line", len(found) >= 3, str(found))

    good = "I built a thing that clips podcasts on its own. It runs while I sleep. Here it is."
    check("house style stays silent on a known-good line",
          script_writer.house_style_report(good) == [], str(script_writer.house_style_report(good)))

    check("house style catches an em dash specifically",
          "em dash" in script_writer.house_style_report("this — that"))


def test_estimate_seconds_is_sane() -> None:
    words = " ".join(["word"] * 150)
    seconds = script_writer.estimate_seconds(words)
    check("150 words estimates near 60 seconds", 55 <= seconds <= 65, f"{seconds}s")
    check("empty text estimates zero", script_writer.estimate_seconds("") == 0)


def test_write_rejects_bad_input() -> None:
    import fleet
    expect_raises("script.write rejects an unknown kind", fleet.FleetError,
                  script_writer.write, "a brief", "nonsense-kind")
    expect_raises("script.write rejects an empty brief", fleet.FleetError,
                  script_writer.write, "   ", "demo")


# ---------------------------------------------------------------- speech chunking

def test_split_for_speech() -> None:
    check("empty text yields no chunks", _split_for_speech("") == [])
    check("a short sentence is one chunk", len(_split_for_speech("Hello there.")) == 1)

    long_text = " ".join([f"This is sentence number {i}." for i in range(40)])
    chunks = _split_for_speech(long_text)
    check("a long script splits into several chunks", len(chunks) > 1, str(len(chunks)))
    check("no chunk exceeds the model's comfortable length",
          all(len(c) <= 300 for c in chunks), str([len(c) for c in chunks]))

    # Nothing may be silently dropped — every word in must be a word out.
    words_in = long_text.split()
    words_out = " ".join(chunks).split()
    check("chunking loses no words", len(words_in) == len(words_out),
          f"{len(words_in)} in, {len(words_out)} out")

    runon = "word " * 200
    check("a single over-long sentence is still split rather than dropped",
          len(_split_for_speech(runon)) > 1)


# ---------------------------------------------------------------- layouts and QC

def test_layout_validation() -> None:
    expect_raises("compose rejects an unknown layout", TwinError,
                  compose.build, "a.mp4", "b.wav", "c.mp4", layout="hologram")
    expect_raises("split layout without a screen recording is refused", TwinError,
                  compose.build, "a.mp4", "b.wav", "c.mp4", layout="split")
    expect_raises("pip layout without a screen recording is refused", TwinError,
                  compose.build, "a.mp4", "b.wav", "c.mp4", layout="pip")


def test_qc_rejects_bad_output() -> None:
    missing = SCRATCH / "nope.mp4"
    passed, reason = compose.qc(str(missing))
    check("QC rejects a missing file", passed is False, reason)

    tiny = SCRATCH / "tiny.mp4"
    tiny.write_bytes(b"0" * 100)
    passed, reason = compose.qc(str(tiny))
    check("QC rejects a file that is too small", passed is False, reason)


def test_footage_provider_is_honest() -> None:
    provider = FootageFace()
    state = provider.available()
    check("the footage provider is ready wherever ffmpeg is", state.ready is True, state.detail)
    check("the footage provider declares itself free", provider.COST == "free")
    check("the footage provider accepts both video and stills",
          set(provider.ACCEPTS) == {"video", "image"})
    expect_raises("the footage provider refuses a file that isn't there",
                  TwinError, provider.render, str(SCRATCH / "ghost.mp4"), str(SCRATCH / "g.wav"),
                  str(SCRATCH / "out.mp4"))


def test_registry_never_auto_selects_a_paid_provider() -> None:
    """The rule that protects him from a bill he did not agree to."""
    catalogue = registry.catalogue()
    paid = [p for p in catalogue["voice"] if p["cost"] == "paid"]
    check("a paid voice provider is wired and visible", len(paid) >= 1)

    if not catalogue["pinned"]["voice"]:
        try:
            chosen = registry.voice_provider()
            check("nothing paid is chosen automatically", chosen.COST == "free", chosen.NAME)
        except TwinError:
            check("nothing paid is chosen automatically", True)

    check("every provider describes what it costs and where it runs",
          all(p["cost"] in ("free", "paid") and p["runs"] in ("local", "cloud")
              for p in catalogue["voice"] + catalogue["face"]))
    check("every not-ready provider names something missing",
          all(p["ready"] or p["missing"] for p in catalogue["voice"] + catalogue["face"]))


def test_face_provider_falls_back() -> None:
    """Lip-sync failing on faceless footage must not lose the whole video.

    Found live 2026-08-03: installing LatentSync made it the preferred face
    provider, and the next render died with "Face not detected" because the plate
    was a test pattern. A screen recording or b-roll would have done the same.
    """
    from twin.face_footage import FootageFace

    faces = registry._all("face")
    check("more than one face provider exists to fall back to", len(faces) >= 2, str(len(faces)))

    lipsync = next((p for p in faces if p.NAME == "lipsync"), None)
    if lipsync is not None:
        fallback = registry.fallback_face_provider(lipsync)
        check("a failing lip-sync provider has somewhere to fall back to", fallback is not None)
        if fallback is not None:
            check("the fallback is not the provider that just failed", fallback.NAME != "lipsync")
            check("the fallback is free", fallback.COST == "free", fallback.COST)

    # The footage provider needs no face, which is what makes it a valid fallback.
    check("the footage provider accepts stills as well as video",
          "image" in FootageFace().ACCEPTS)

    # A provider must never fall back to itself, or a failure loops.
    footage = FootageFace()
    result = registry.fallback_face_provider(footage)
    check("a provider never falls back to itself",
          result is None or result.NAME != footage.NAME)


def test_karaoke_captions() -> None:
    """The word-level caption track: every word appears, exactly one is live."""
    words = [
        {"start": 0.0, "end": 0.4, "word": " Stop"},
        {"start": 0.4, "end": 0.9, "word": " editing"},
        {"start": 0.9, "end": 1.3, "word": " videos"},
        {"start": 1.3, "end": 1.8, "word": " yourself"},
        {"start": 1.8, "end": 2.4, "word": " today"},
    ]
    events = compose._karaoke_events(words, 3.0)
    lines = [ln for ln in events.splitlines() if ln.startswith("Dialogue:")]
    check("one caption event per spoken word", len(lines) == len(words), str(len(lines)))

    for line in lines:
        highlights = line.count("\\c" + compose._ACTIVE_COLOUR)
        check("exactly one word is highlighted at a time", highlights == 1, line[:90])
        check("the highlight is reset so colour doesn't bleed", "{\\r}" in line)

    upper = events.upper()
    for word in ("STOP", "EDITING", "VIDEOS", "YOURSELF", "TODAY"):
        check(f"the word {word} survives into the captions", word in upper)

    # Timings must advance, never run backwards — a caption that starts before the
    # previous one ended flickers.
    starts = [ln.split(",")[1] for ln in lines]
    check("caption timings move forwards", starts == sorted(starts), str(starts))

    check("no words means no events", compose._karaoke_events([], 3.0) == "")
    check("words with no timings are skipped rather than crashing",
          compose._karaoke_events([{"word": "hi", "start": None, "end": None}], 3.0) == "")

    # The colour must be byte-reversed for ASS. Getting this wrong is silent.
    check("the active colour is in ASS byte order, not hex RGB",
          compose._ACTIVE_COLOUR == "&H92D342&", compose._ACTIVE_COLOUR)

    expect_raises("compose rejects an unknown caption style", TwinError,
                  compose.build, "a.mp4", "b.wav", "c.mp4", caption_style="sparkles")


def test_screen_recorder_is_honest() -> None:
    from twin import screen

    ok, why = screen.available()
    check("screen recording reports a reason when it can't run", ok or bool(why))
    if not ok:
        return

    check("it names which chip is encoding", screen.encoder_name() in ("graphics card", "processor"))
    expect_raises("stopping when nothing is recording says so", TwinError, screen.stop)

    # The capture width cap: NVENC refuses over 4096 wide, and a 4480-wide desktop
    # would fail to encode at all without this.
    wide = screen._scale_filter("desktop")
    check("the capture filter is a real filter string", "=" in wide, wide)
    size = screen._capture_size("desktop")
    if size[0] > screen.MAX_CAPTURE_WIDTH:
        check("an over-wide desktop gets scaled down", wide.startswith("scale="), wide)
        target = int(wide.split("=")[1].split(":")[0])
        check("scaled within the encoder's limit", target <= screen.MAX_CAPTURE_WIDTH, str(target))
        height = int(wide.split(":")[1])
        check("scaled height is even, as h264 requires", height % 2 == 0, str(height))


def test_series_dedupes_angles() -> None:
    from twin import series

    import fleet
    expect_raises("a series needs a topic", fleet.FleetError, series.plan_angles, "  ", 3)
    expect_raises("a series of zero is refused", fleet.FleetError, series.plan_angles, "x", 0)
    expect_raises("a series bigger than the cap is refused", fleet.FleetError,
                  series.plan_angles, "x", series.MAX_PER_RUN + 1)

    # summarise() must report the numbers separately rather than implying success.
    honest = series.summarise({"topic": "t", "asked_for": 5, "angles_planned": 3,
                               "queued": [{"video_id": 1}], "failed": [{"angle": "a"}]})
    check("the summary gives the count out of what was asked", "1 of the 5" in honest, honest)
    check("the summary admits a short plan", "3" in honest, honest)
    check("the summary admits failures", "failed" in honest, honest)

    clean = series.summarise({"topic": "t", "asked_for": 3, "angles_planned": 3,
                              "queued": [{"video_id": i} for i in range(3)], "failed": []})
    check("a clean run doesn't invent problems", "failed" not in clean, clean)


def test_post_queue_migration_keeps_existing_rows() -> None:
    """Upgrading a real database, not creating a fresh one.

    A fresh database gets the new schema directly, so a suite that only ever
    creates fresh databases proves nothing about HIS database, which already has
    a post_queue with the old shape and rows in it.
    """
    import sqlite3

    from post import scheduler

    db_path = SCRATCH / "migrate.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            render_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            title TEXT NOT NULL,
            description TEXT,
            post_id TEXT,
            not_before TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (render_id, platform)
        )
        """
    )
    connection.execute(
        "INSERT INTO post_queue (render_id, platform, status, title, created_at, updated_at)"
        " VALUES (7, 'youtube', 'posted', 'An older clip', 'x', 'y')"
    )
    connection.commit()

    scheduler.setup_post_tables(connection)

    columns = {r["name"] for r in connection.execute("PRAGMA table_info(post_queue)")}
    check("migration adds media_kind", "media_kind" in columns)

    rows = connection.execute("SELECT * FROM post_queue").fetchall()
    check("migration keeps the existing row", len(rows) == 1, str(len(rows)))
    check("the existing row is labelled a clip", rows[0]["media_kind"] == "clip")
    check("the existing row keeps its title", rows[0]["title"] == "An older clip")
    check("the existing row keeps its posted status", rows[0]["status"] == "posted")

    # The discriminating case: clip 7 and twin video 7 are different videos and
    # must both be queueable for the same platform. Under the old constraint the
    # second one would vanish into INSERT OR IGNORE without a word.
    scheduler.enqueue(connection, 7, "youtube", {"title": "A twin video", "description": ""},
                      media_kind="twin")
    both = connection.execute(
        "SELECT media_kind FROM post_queue WHERE render_id = 7 AND platform = 'youtube'"
    ).fetchall()
    check("a clip and a twin video with the same id can both queue",
          len(both) == 2, f"got {len(both)} row(s)")

    # Running it twice must not duplicate or destroy anything.
    scheduler.setup_post_tables(connection)
    check("migration is safe to run again",
          len(connection.execute("SELECT * FROM post_queue").fetchall()) == 2)

    expect_raises("enqueue refuses an unknown media kind", ValueError,
                  scheduler.enqueue, connection, 9, "youtube", {"title": "x"}, "hologram")
    connection.close()


def test_no_root_file_shadows_a_stdlib_module() -> None:
    """The bug this exists to stop, found 2026-08-02.

    The project root is sys.path[0] whenever ClipForge is run from its own folder,
    which RUN_PIPELINE.bat does. A root file named profile.py therefore WON the
    import of Python's own `profile` module. Nothing here imports it directly, so
    it looked harmless for weeks — until torch imported cProfile, which imports
    `profile` internally, got the channel-profile file, and took the whole GPU
    stack down with "module 'profile' has no attribute 'run'".

    The lesson generalises past that one name, so the check does too.
    """
    import sysconfig

    stdlib = Path(sysconfig.get_paths()["stdlib"])
    stdlib_names = {p.stem for p in stdlib.glob("*.py")}
    stdlib_names |= {p.name for p in stdlib.iterdir() if p.is_dir() and (p / "__init__.py").exists()}

    ours = {p.stem for p in ROOT.glob("*.py")}
    collisions = sorted(ours & stdlib_names)
    check("no file in the project root shadows a standard library module",
          not collisions, f"shadowing: {collisions}")

    # Calibration: the check must be able to fire, or it proves nothing.
    check("the shadow check can detect a collision",
          "profile" in stdlib_names and "json" in stdlib_names)


def test_fleet_resolves_or_explains() -> None:
    import fleet
    expect_raises("fleet refuses an unknown role", fleet.FleetError, fleet.resolve, "astrology")
    state = fleet.status()
    check("fleet reports a host", bool(state["host"]))
    for role, info in state["roles"].items():
        check(f"role {role!r} either resolves or explains why not",
              bool(info["model"]) or bool(info["error"]))


def main() -> int:
    tests = [
        test_availability_cannot_lie,
        test_contract_is_abstract,
        test_clean_for_speech_removes_unspeakables,
        test_house_style_report_finds_and_misses_correctly,
        test_estimate_seconds_is_sane,
        test_write_rejects_bad_input,
        test_split_for_speech,
        test_layout_validation,
        test_qc_rejects_bad_output,
        test_footage_provider_is_honest,
        test_registry_never_auto_selects_a_paid_provider,
        test_face_provider_falls_back,
        test_karaoke_captions,
        test_screen_recorder_is_honest,
        test_series_dedupes_angles,
        test_post_queue_migration_keeps_existing_rows,
        test_no_root_file_shadows_a_stdlib_module,
        test_fleet_resolves_or_explains,
    ]
    try:
        for test in tests:
            test()
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)

    total = PASSED + len(FAILED)
    if FAILED:
        print(f"TWIN: {PASSED} of {total} checks passed, {len(FAILED)} FAILED")
        for failure in FAILED:
            print(f"  FAIL  {failure}")
        return 1
    print(f"TWIN: {PASSED} of {total} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
