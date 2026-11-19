"""Environment-based application configuration."""
from __future__ import annotations

import base64
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Global Banking Intelligence Platform"
    app_env: str = Field(default="development", pattern="^(development|test|production)$")
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000

    database_url: str = "postgresql+psycopg://gbp_app:change-me@127.0.0.1:5432/gbp"
    database_echo: bool = False
    test_database_url: str = ""

    # Security
    jwt_secret: str = Field(default="", description="HS256 signing secret (min 32 chars)")
    jwt_issuer: str = "gbp"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    session_idle_minutes: int = 120
    master_encryption_key: str = Field(default="", description="base64 encoded 32-byte AES key")
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost"]
    cookie_secure: bool = False
    auth_rate_limit_per_minute: int = 20
    api_rate_limit_per_minute: int = 600

    # Storage
    storage_dir: Path = PROJECT_DIR / "storage"
    max_upload_mb: int = 25

    # Automation
    worker_enabled: bool = True
    scheduler_enabled: bool = True
    worker_concurrency: int = 4
    worker_poll_seconds: float = 2.0
    job_timeout_seconds: int = 900
    job_stale_seconds: int = 300
    browser_automation_enabled: bool = False
    browser_login_timeout_seconds: int = Field(default=600, ge=60, le=720)
    browser_channel: str = Field(default="msedge" if sys.platform == "win32" else "chromium",
                                 pattern="^(chromium|msedge|chrome)$")

    # Outgoing low-balance alerts. Credentials stay in the local environment.
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_from: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = Field(default="starttls", pattern="^(starttls|ssl)$")
    ocr_tessdata_dir: Path = BACKEND_DIR / "vendor" / "tessdata"

    # Demo
    allow_demo: bool = True

    # Optional third-party provider credentials (never committed)
    plaid_client_id: str = ""
    plaid_secret: str = ""
    plaid_env: str = "sandbox"
    gocardless_secret_id: str = ""
    gocardless_secret_key: str = ""

    # India — RBI Account Aggregator through Setu AA Gateway (FIU onboarding with Setu required)
    setu_aa_env: str = Field(default="sandbox", pattern="^(sandbox|production)$")
    setu_aa_base_url: str = ""            # default derived from setu_aa_env
    setu_aa_api_prefix: str = "/v2"
    setu_aa_client_id: str = ""
    setu_aa_client_secret: str = ""
    setu_aa_product_instance_id: str = ""
    setu_aa_token_url: str = ""           # optional: if set, a bearer token is obtained here (client_credentials)
    setu_aa_redirect_url: str = "http://127.0.0.1:8000/accounts"
    setu_aa_webhook_token: str = ""       # shared secret appended to the notification URL registered on Setu Bridge
    setu_aa_consent_months: int = 12
    setu_aa_history_days: int = 365

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def demo_allowed(self) -> bool:
        return self.allow_demo

    def encryption_key_bytes(self) -> bytes:
        if not self.master_encryption_key:
            raise RuntimeError("MASTER_ENCRYPTION_KEY is not configured")
        key = base64.b64decode(self.master_encryption_key)
        if len(key) != 32:
            raise RuntimeError("MASTER_ENCRYPTION_KEY must decode to exactly 32 bytes")
        return key

    def validate_for_startup(self) -> None:
        if len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be configured with at least 32 characters")
        self.encryption_key_bytes()
        if self.is_production and not self.cookie_secure:
            raise RuntimeError("COOKIE_SECURE must be true in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
