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
echo ClipForge autonomous pipeline is running.
echo It will transcribe -^> find clips -^> render every source, then re-check every 5 minutes.
echo Everything runs locally and free. Keep this window open. Press Ctrl+C to stop.
echo Finished clips land in data\clips\
echo.
call .venv\Scripts\python.exe run.py
