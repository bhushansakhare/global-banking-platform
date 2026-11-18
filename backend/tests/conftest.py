"""Test fixtures. Tests run against the dedicated PostgreSQL test database (never the main DB)."""
from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator

import pytest

# ---- configure environment BEFORE the app is imported ------------------------
from app.core.config import Settings, get_settings

_base = Settings()
if not _base.test_database_url:
    raise RuntimeError("TEST_DATABASE_URL must be configured in backend/.env to run tests")
if _base.test_database_url == _base.database_url:
    raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")
os.environ["DATABASE_URL"] = _base.test_database_url
os.environ["APP_ENV"] = "test"
os.environ["WORKER_ENABLED"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["ALLOW_DEMO"] = "true"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ["API_RATE_LIMIT_PER_MINUTE"] = "100000"
_tmp_storage = tempfile.mkdtemp(prefix="gbp-test-storage-")
os.environ["STORAGE_DIR"] = _tmp_storage
get_settings.cache_clear()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.ratelimit import limiter  # noqa: E402
from app.database.session import get_engine, get_sessionmaker, session_scope  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "Sup3rSecretPassw0rd"


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> Iterator[None]:
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    os.environ["ALEMBIC_DATABASE_URL"] = _base.test_database_url
    with get_engine().begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    yield
    get_engine().dispose()
    shutil.rmtree(_tmp_storage, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_db() -> Iterator[None]:
    yield
    tables = [
        "automation_job_logs", "automation_jobs", "automation_settings", "reconciliation_exceptions",
        "reconciliation_matches", "reconciliation_sessions", "statement_parsing_jobs", "transactions",
        "book_entries", "statements", "statement_files", "account_balances", "account_consents", "bank_accounts",
        "bank_connections", "notifications", "notification_preferences", "reports", "system_settings",
        "exchange_rates", "transaction_categories", "sessions", "users", "institutions", "role_permissions",
        "roles", "permissions", "bank_providers", "countries",
    ]
    with get_engine().begin() as c:
        c.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER trg_audit_logs_immutable"))
        c.execute(text("TRUNCATE audit_logs, " + ", ".join(tables) + ", organizations CASCADE"))
        c.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER trg_audit_logs_immutable"))
    limiter.reset()
    from app.integrations.providers.demo import _ledger
    _ledger.cache_clear()


@pytest.fixture
def db():
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app
    from app.services.seed import seed_demo_institutions, seed_reference_data

    with session_scope() as s:
        seed_reference_data(s)
        seed_demo_institutions(s)
    app = create_app(with_lifespan=False)
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def setup_owner(client: TestClient, email: str = "owner@example.com", currency: str = "GBP") -> dict:
    r = client.post("/api/v1/auth/setup", json={
        "organisation": "Acme Treasury", "full_name": "Olivia Owner", "email": email, "password": PASSWORD,
        "reporting_currency": currency, "timezone": "Europe/London", "country_code": "GB"})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def owner(client: TestClient) -> dict:
    data = setup_owner(client)
    client.headers["Authorization"] = f"Bearer {data['access_token']}"
    return data


def demo_institution_id(client: TestClient, ext: str = "DEMO_GB_NORTHWIND") -> str:
    r = client.get("/api/v1/institutions", params={"provider": "demo", "page_size": 50})
    assert r.status_code == 200, r.text
    return next(i["id"] for i in r.json()["items"] if i["external_id"] == ext)
