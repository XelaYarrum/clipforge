"""Run every ClipForge check and print one verdict.

Double-click CHECK_EVERYTHING.bat instead of running this by hand.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("Posting limits + captions", "test_scheduler.py"),
    ("Posting, start to finish", "test_posting_e2e.py"),
    ("The dashboard pages", "test_dashboard.py"),
]

results = []
for label, script in SUITES:
    print("=" * 66)
    print(f"  {label}")
    print("=" * 66)
    proc = subprocess.run([sys.executable, str(HERE / script)], text=True)
    results.append((label, proc.returncode == 0))
    print()

print("=" * 66)
for label, ok in results:
    print(f"  {'PASS' if ok else 'FAIL'}   {label}")
print("=" * 66)

if all(ok for _, ok in results):
    print("\nEverything passed. ClipForge is working.\n")
    sys.exit(0)

print("\nSomething failed above. Nothing is broken on your accounts —\n"
      "this is ClipForge checking itself.\n")
sys.exit(1)
