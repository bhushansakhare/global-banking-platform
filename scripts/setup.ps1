# One-time setup for Windows (PowerShell). Idempotent: safe to re-run.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-PgSuperPassword <pw>] [-PgBin "C:\Program Files\PostgreSQL\17\bin"]
param(
  [string]$PgSuperUser = "postgres",
  [string]$PgSuperPassword = "",
  [string]$PgBin = "C:\Program Files\PostgreSQL\17\bin",
  [string]$PgHost = "127.0.0.1",
  [int]$PgPort = 5432
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

Write-Host "== Checking prerequisites" -ForegroundColor Cyan
python --version; node --version; npm --version
if (-not (Test-Path "$PgBin\psql.exe")) { throw "psql.exe not found in $PgBin. Pass -PgBin." }

Write-Host "== Python environment" -ForegroundColor Cyan
if (-not (Test-Path "$backend\.venv")) { python -m venv "$backend\.venv" }
& "$backend\.venv\Scripts\python.exe" -m pip install --upgrade pip -q
& "$backend\.venv\Scripts\python.exe" -m pip install -r "$backend\requirements-dev.txt" -q

if (-not (Test-Path "$backend\.env")) {
  Write-Host "== Creating database role, databases and backend\.env" -ForegroundColor Cyan
  if (-not $PgSuperPassword) { $sec = Read-Host "PostgreSQL superuser password" -AsSecureString
    $PgSuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)) }
  $env:PGPASSWORD = $PgSuperPassword
  $dbpw = -join ((48..57)+(65..90)+(97..122) | Get-Random -Count 24 | ForEach-Object {[char]$_})
  $exists = & "$PgBin\psql.exe" -h $PgHost -p $PgPort -U $PgSuperUser -tAc "select 1 from pg_roles where rolname='gbp_app'"
  if ($exists -eq "1") { & "$PgBin\psql.exe" -h $PgHost -p $PgPort -U $PgSuperUser -c "ALTER ROLE gbp_app PASSWORD '$dbpw';" | Out-Null }
  else { & "$PgBin\psql.exe" -h $PgHost -p $PgPort -U $PgSuperUser -c "CREATE ROLE gbp_app LOGIN PASSWORD '$dbpw';" | Out-Null }
  foreach ($db in @("gbp","gbp_test")) {
    $has = & "$PgBin\psql.exe" -h $PgHost -p $PgPort -U $PgSuperUser -tAc "select 1 from pg_database where datname='$db'"
    if ($has -ne "1") { & "$PgBin\psql.exe" -h $PgHost -p $PgPort -U $PgSuperUser -c "CREATE DATABASE $db OWNER gbp_app;" | Out-Null }
  }
  Remove-Item Env:PGPASSWORD
  $py = "$backend\.venv\Scripts\python.exe"
  $jwt = & $py -c "import secrets;print(secrets.token_urlsafe(48))"
  $key = & $py -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
  $envText = @"
APP_ENV=development
DATABASE_URL=postgresql+psycopg://gbp_app:$dbpw@${PgHost}:$PgPort/gbp
TEST_DATABASE_URL=postgresql+psycopg://gbp_app:$dbpw@${PgHost}:$PgPort/gbp_test
JWT_SECRET=$jwt
MASTER_ENCRYPTION_KEY=$key
ALLOW_DEMO=true
"@
  [IO.File]::WriteAllText("$backend\.env", $envText)
  Write-Host "   backend\.env written (keep it secret; back up MASTER_ENCRYPTION_KEY)" -ForegroundColor Yellow
}

Write-Host "== Database migrations" -ForegroundColor Cyan
Push-Location $backend; & ".\.venv\Scripts\alembic.exe" upgrade head; & ".\.venv\Scripts\python.exe" -m app.cli seed; Pop-Location

Write-Host "== Frontend" -ForegroundColor Cyan
Push-Location $frontend; npm install --no-audit --no-fund; npm run build; Pop-Location

Write-Host "`nDone. Start with:  scripts\start.cmd   then open http://127.0.0.1:8000" -ForegroundColor Green
