@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo Setting up ClipForge for the first time...
  python -m venv .venv
  call .venv\Scripts\python.exe -m pip install --upgrade pip
  call .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo.
echo ClipForge is starting. Keep this window open while you use it.
echo Open http://127.0.0.1:8000 in your browser.
call .venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

