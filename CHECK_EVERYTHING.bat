@echo off
setlocal
cd /d "%~dp0"
echo Checking ClipForge. This touches nothing real - no accounts, no posting.
echo.
call .venv\Scripts\python.exe tests\run_all.py
echo.
pause
