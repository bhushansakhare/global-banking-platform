# Deployment (Windows, no Docker/WSL)

## Prerequisites

Windows 10+, Python 3.12, Node 24 / npm 11, PostgreSQL 17 (running), Git.
Desktop installer additionally needs Rust (MSVC toolchain), Visual Studio Build Tools "Desktop development
with C++", and WebView2 (preinstalled on Windows 11; bootstrapper bundled for Windows 10).

## Install

**PowerShell**
```powershell
cd C:\xampp\htdocs\global-banking-platform
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -PgBin "C:\Program Files\PostgreSQL\17\bin"
```
**CMD**
```cmd
cd /d C:\xampp\htdocs\global-banking-platform
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```
The script creates the virtualenv, a dedicated `gbp_app` role with a random password, the `gbp` and
`gbp_test` databases, `backend\.env` with fresh secrets, runs migrations and builds the frontend.

Manual equivalent:
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
copy ..\.env.example .env     # then edit
.\.venv\Scripts\alembic upgrade head
cd ..\frontend; npm install; npm run build
```

## Run

| Purpose | Command |
|---|---|
| Production-style (single process) | `scripts\start.cmd` → http://127.0.0.1:8000 |
| Development | `scripts\dev.cmd` (Vite :5173 proxies `/api` to :8000) |
| Separate worker | API with `WORKER_ENABLED=false`, then `.\.venv\Scripts\python -m app.cli worker` |

### Run as a Windows service

Use a service wrapper such as NSSM or WinSW pointing at
`backend\.venv\Scripts\python.exe main.py` with working directory `backend`, a dedicated service account,
and automatic restart. Put an HTTPS reverse proxy (IIS ARR, Caddy or nginx for Windows) in front if other
machines must reach it; set `COOKIE_SECURE=true` and add the public origin to `CORS_ORIGINS`.

## Desktop application

```powershell
winget install Rustlang.Rustup        # then: rustup default stable-msvc
cd desktop
npm install
npx tauri icon ..\frontend\public\icon-1024.png   # create icons/ from a 1024×1024 PNG
npm run build                                       # builds frontend, then MSI + NSIS installers
```
The installer contains the shell only; it starts the backend from `GBP_BACKEND_DIR` (or `..\backend`
in development). For a fully self-contained installer, copy the `backend` folder with its `.venv` into
Tauri resources (`bundle.resources`) — documented as an outstanding packaging step.

## Upgrades

`git pull` → `pip install -r requirements.txt` → `alembic upgrade head` → `npm run build` → restart.
Jobs interrupted by the restart are recovered automatically.

## Backups

`pg_dump -Fc -U gbp_app gbp > gbp.dump` plus the `storage\` folder and `backend\.env` (store the key
separately from the data backup).
