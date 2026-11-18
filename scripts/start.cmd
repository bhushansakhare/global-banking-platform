@echo off
REM Starts the API, background worker and daily scheduler, serving the built web app.
cd /d "%~dp0..\backend"
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup.ps1 first.
  exit /b 1
)
.venv\Scripts\alembic.exe upgrade head || exit /b 1
echo Meridian running at http://127.0.0.1:8000   (Ctrl+C to stop)
.venv\Scripts\python.exe main.py
