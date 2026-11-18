@echo off
REM Runs every quality gate. Exits non-zero on the first failure.
cd /d "%~dp0..\backend"
echo == Backend lint
.venv\Scripts\ruff.exe check app tests --select F,E9 || exit /b 1
echo == Backend tests (PostgreSQL test database)
.venv\Scripts\python.exe -m pytest -q || exit /b 1
cd /d "%~dp0..\frontend"
echo == Frontend typecheck
call npm run typecheck || exit /b 1
echo == Frontend lint
call npm run lint || exit /b 1
echo == Frontend tests
call npm test || exit /b 1
echo == Production build
call npm run build || exit /b 1
echo ALL CHECKS PASSED
