"""FastAPI application factory."""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes import accounts, auth, automation, institutions, misc, reconciliation, statements, transactions, \
    users, webhooks
from app.core.config import PROJECT_DIR, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import limiter
from app.database.session import get_engine, session_scope
from app.api.routes import browser_banking

log = get_logger("app")
AUTH_PATHS = ("/auth/login", "/auth/setup", "/auth/refresh")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.validate_for_startup()
    from app.services.seed import seed_reference_data, seed_demo_institutions
    with session_scope() as db:
        seed_reference_data(db)
        if s.demo_allowed:
            seed_demo_institutions(db)
    worker = scheduler = None
    if s.worker_enabled:
        from app.workers.worker import Worker
        worker = Worker()
        worker.start()
    if s.scheduler_enabled:
        from app.scheduler.scheduler import AutomationScheduler
        scheduler = AutomationScheduler()
        scheduler.start()
    log.info("application started", extra={"ctx": {"env": s.app_env, "worker": bool(worker),
                                                   "scheduler": bool(scheduler)}})
    try:
        yield
    finally:
        if scheduler:
            scheduler.stop()
        if worker:
            worker.stop()
        log.info("application stopped")


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    s = get_settings()
    configure_logging()
    app = FastAPI(title=s.app_name, version="1.0.0", lifespan=lifespan if with_lifespan else None,
                  docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json",
                  description="Read-only global banking intelligence, automation and reconciliation API. "
                              "This API never initiates payments or modifies bank accounts.")
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"])

    @app.middleware("http")
    async def security_and_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        ip = request.client.host if request.client else "unknown"
        path = request.url.path
        if path.startswith(s.api_prefix):
            if any(path.endswith(p) for p in AUTH_PATHS) and request.method == "POST":
                if not limiter.allow(f"auth:{ip}", s.auth_rate_limit_per_minute):
                    return JSONResponse(status_code=429, content={"error": {
                        "code": "rate_limited", "message": "Too many attempts. Please wait a minute.",
                        "details": None, "request_id": rid}})
            elif not limiter.allow(f"api:{ip}", s.api_rate_limit_per_minute):
                return JSONResponse(status_code=429, content={"error": {
                    "code": "rate_limited", "message": "Rate limit exceeded", "details": None, "request_id": rid}})
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if path.startswith(s.api_prefix):
            response.headers.setdefault("Cache-Control", "no-store")
        elif not path.startswith("/api/docs") and not path.startswith("/api/redoc"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' "
                "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; "
                "connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; "
                "frame-ancestors 'none'")
        if s.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path.startswith(s.api_prefix) and (time.perf_counter() - start) > 2:
            log.warning("slow request", extra={"ctx": {"path": path, "ms": int((time.perf_counter() - start) * 1000)}})
        return response

    api = APIRouter(prefix=s.api_prefix)
    for r in (auth.router, users.router, institutions.router, accounts.router, statements.router,
              transactions.router, reconciliation.router, automation.router, misc.router, webhooks.router,
              browser_banking.router):
        api.include_router(r)

    @api.get("/health", tags=["system"])
    def health() -> dict:
        db_ok = True
        try:
            with get_engine().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "database": db_ok, "version": app.version,
                "bank_browser": {"enabled": s.browser_automation_enabled, "mode": "headed",
                                 "verification_wait_seconds": s.browser_login_timeout_seconds}}

    app.include_router(api)

    # Serve the built React app (web + desktop) from the same origin when present.
    dist = Path(PROJECT_DIR / "frontend" / "dist")
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
