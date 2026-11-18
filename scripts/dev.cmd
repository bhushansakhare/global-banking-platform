@echo off
REM Development: backend on :8000 and Vite (hot reload) on :5173 in two windows.
start "Meridian API" cmd /k "cd /d %~dp0..\backend && .venv\Scripts\python.exe main.py"
start "Meridian Web" cmd /k "cd /d %~dp0..\frontend && npm run dev"
echo Web app: http://127.0.0.1:5173    API docs: http://127.0.0.1:8000/api/docs
