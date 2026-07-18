# ClipForge

A local-first, rights-first autonomous clipping pipeline for YouTube Shorts, TikTok, and Instagram
Reels. Point it at licensed/original source video; it transcribes (GPU with CPU fallback), finds the
strongest moments, cuts vertical clips with captions, and schedules posts — end to end on your own
machine, free.

- **Pipeline**: source ingest → Whisper transcription → moment scoring → vertical crop + captions →
  scheduled posting (one-time OAuth per platform).
- **Dashboard**: local web UI (Flask) for accounts, channels, and add-by-URL.
- **Design**: everything runs locally; credentials never leave the machine; connectors stay unbuilt
  until the owner completes each platform's OAuth click-through (see `START HERE.txt`).

Status: pipeline complete end-to-end; posting live once per-platform OAuth is connected.
See `START HERE.txt` for the operator checklist and `STATUS.txt` for the honest current state.

MIT licensed.
