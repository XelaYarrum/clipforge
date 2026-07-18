CLIPFORGE
=========

A local, rights-first short-form clipping system for YouTube Shorts,
TikTok and Instagram Reels. It runs on this PC, for free, offline.


READ THIS FIRST
---------------
Open "START HERE.txt". It is the whole list of what you need to do.
Everything else in this folder is machinery.


WHAT IT DOES
------------
Give it a video link you're licensed to use, and on its own it will:

  - download it
  - transcribe it locally (your video never leaves this PC)
  - read the transcript and pick the strongest self-contained moments,
    scored against what YOUR channel is about
  - cut each one to a vertical 1080x1920 clip with burned-in captions
    and your handle
  - reject its own bad renders before they count as done
  - write a title and caption tuned to each platform's rules
  - queue it for YouTube, TikTok and Instagram, respecting each
    platform's real posting limits

All of that works today. The one thing it won't do until you connect
the accounts is actually press send - see START HERE.txt.


THE THREE BUTTONS
-----------------
  START_CLIPFORGE.bat ... open the dashboard (127.0.0.1:8000)
  RUN_PIPELINE.bat ...... let it clip everything, on its own, forever
  CHECK_EVERYTHING.bat .. run every test and print PASS or FAIL


RIGHTS
------
Every source needs a permission or licence record before it enters the
pipeline. That's deliberate and it isn't skippable. Only add footage
you own or are clearly licensed to repurpose - that judgement is yours,
not the software's.


FOR WHOEVER PICKS THIS UP NEXT
------------------------------
STATUS.txt is the technical log: what's built, what's verified, what's
next, and the decisions behind it. Read it before resuming.
