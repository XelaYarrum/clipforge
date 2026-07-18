import argparse
import time
from contextlib import closing

import app
from post import runner as post_runner


def process_all(live_posting: bool = False):
    with closing(app.db_connection()) as c:
        sources = c.execute("SELECT id, title FROM sources").fetchall()

    for row in sources:
        sid = row["id"]
        title = row["title"]

        try:
            # STAGE 1 — transcript
            with closing(app.db_connection()) as c:
                media = c.execute(
                    "SELECT stored_filename FROM media_files WHERE source_id = ?",
                    (sid,),
                ).fetchone()

            if media is None:
                print(f"skip {title}: no video")
                continue

            stored_filename = media["stored_filename"]

            with closing(app.db_connection()) as c:
                transcript = c.execute(
                    "SELECT status FROM transcripts WHERE source_id = ?", (sid,)
                ).fetchone()

            if transcript is None or transcript["status"] != "done":
                print(f"transcribing {title}…")
                stored_path = str(app.MEDIA_DIR / stored_filename)
                app.run_transcription(sid, stored_path, "base")

                with closing(app.db_connection()) as c:
                    transcript = c.execute(
                        "SELECT status FROM transcripts WHERE source_id = ?",
                        (sid,),
                    ).fetchone()

                if transcript is None or transcript["status"] != "done":
                    print("  transcript failed")
                    continue

            # STAGE 2 — candidates
            with closing(app.db_connection()) as c:
                count = c.execute(
                    "SELECT COUNT(*) AS n FROM clip_candidates WHERE source_id = ?",
                    (sid,),
                ).fetchone()["n"]

            if count == 0:
                print(f"finding clips in {title}…")
                app.find_clip_candidates(sid)

                with closing(app.db_connection()) as c:
                    count = c.execute(
                        "SELECT COUNT(*) AS n FROM clip_candidates WHERE source_id = ?",
                        (sid,),
                    ).fetchone()["n"]

                if count == 0:
                    print("  no clips found")
                    continue

            # STAGE 3 — render
            with closing(app.db_connection()) as c:
                top = c.execute(
                    "SELECT id FROM clip_candidates WHERE source_id = ? ORDER BY score DESC LIMIT 1",
                    (sid,),
                ).fetchone()

            top_id = top["id"]

            with closing(app.db_connection()) as c:
                render = c.execute(
                    "SELECT status, output_path, error_message FROM renders WHERE candidate_id = ?",
                    (top_id,),
                ).fetchone()

            if render is None or render["status"] != "done":
                print(f"rendering top clip of {title}…")
                app.render_clip(top_id)

                with closing(app.db_connection()) as c:
                    render = c.execute(
                        "SELECT status, output_path, error_message FROM renders WHERE candidate_id = ?",
                        (top_id,),
                    ).fetchone()

                if render is not None and render["status"] == "done":
                    print(f"  DONE -> {render['output_path']}")
                else:
                    reason = render["error_message"] if render is not None else "no render row"
                    print(f"  render failed: {reason}")
            else:
                print(f"{title}: already rendered -> {render['output_path']}")

        except Exception as e:
            print(f"error processing {title}: {e}")

    # STAGE 4 — posting (mock unless --live-posting is passed)
    print("posting stage…")
    try:
        post_runner.process(live=live_posting)
    except Exception as e:
        print(f"error in posting stage: {e}")


def main():
    parser = argparse.ArgumentParser(description="ClipForge autonomous pipeline")
    parser.add_argument("--once", action="store_true", help="do a single pass then exit")
    parser.add_argument("--interval", type=int, default=300, help="seconds between passes (default: 300)")
    parser.add_argument(
        "--live-posting",
        action="store_true",
        help="actually post (needs the OAuth grant per platform); default is mock",
    )
    args = parser.parse_args()

    app.setup_database()
    mode = "LIVE posting" if args.live_posting else "MOCK posting"
    print(f"ClipForge autonomous pipeline — transcribe -> find -> render -> post ({mode})")

    if args.once:
        process_all(live_posting=args.live_posting)
        return

    while True:
        try:
            process_all(live_posting=args.live_posting)
        except KeyboardInterrupt:
            print("stopped.")
            break
        except Exception as e:
            print(f"error in pass: {e}")

        print(f"sleeping {args.interval}s…")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("stopped.")
            break


if __name__ == "__main__":
    main()
