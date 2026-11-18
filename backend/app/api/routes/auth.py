from __future__ import annotations

import hmac

from fastapi import APIRouter, Request, Response

from app.api import serializers as S
from app.api.deps import DB, CurrentUser, client_ip
from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.security import new_csrf_token
from app.schemas.requests import ChangePasswordRequest, LoginRequest, SetupRequest
from app.services import auth as svc

router = APIRouter(prefix="/auth", tags=["auth"])
RT_COOKIE, CSRF_COOKIE = "gbp_rt", "gbp_csrf"


def _set_cookies(resp: Response, pair: svc.TokenPair) -> str:
    s = get_settings()
    max_age = s.refresh_token_days * 86400
    resp.set_cookie(RT_COOKIE, pair.refresh_token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                    samesite="strict", path=f"{s.api_prefix}/auth")
    csrf = new_csrf_token()
    resp.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure, samesite="strict",
                    path="/")
    return csrf


def _clear(resp: Response) -> None:
    s = get_settings()
    resp.delete_cookie(RT_COOKIE, path=f"{s.api_prefix}/auth")
    resp.delete_cookie(CSRF_COOKIE, path="/")


def _check_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise Forbidden("CSRF validation failed", code="csrf_failed")


def _token_body(pair: svc.TokenPair, csrf: str) -> dict:
    return {"access_token": pair.access_token, "token_type": "bearer", "expires_in": pair.expires_in,
            "csrf_token": csrf, "user": S.user(pair.user)}


@router.get("/setup-status", summary="Whether first-run setup is still required")
def setup_status(db: DB) -> dict:
    return {"setup_required": svc.setup_required(db), "demo_allowed": get_settings().demo_allowed,
            "environment": get_settings().app_env}


@router.post("/setup", status_code=201, summary="First-run setup: create organisation and owner")
def setup(body: SetupRequest, request: Request, response: Response, db: DB) -> dict:
    user = svc.initial_setup(db, organisation=body.organisation, full_name=body.full_name, email=body.email,
                             password=body.password, reporting_currency=body.reporting_currency,
                             timezone=body.timezone, country_code=body.country_code, ip=client_ip(request))
    db.commit()
    pair = svc.login(db, email=user.email, password=body.password, ip=client_ip(request),
                     ua=request.headers.get("user-agent"))
    return _token_body(pair, _set_cookies(response, pair))


@router.post("/login", summary="Sign in with email and password")
def login(body: LoginRequest, request: Request, response: Response, db: DB) -> dict:
    pair = svc.login(db, email=body.email, password=body.password, ip=client_ip(request),
                     ua=request.headers.get("user-agent"))
    return _token_body(pair, _set_cookies(response, pair))


@router.post("/refresh", summary="Rotate the refresh token and obtain a new access token")
def refresh(request: Request, response: Response, db: DB) -> dict:
    _check_csrf(request)
    token = request.cookies.get(RT_COOKIE)
    if not token:
        raise Unauthorized("No refresh token", code="invalid_refresh")
    try:
        pair = svc.refresh(db, refresh_token=token, ip=client_ip(request), ua=request.headers.get("user-agent"))
    except Unauthorized:
        _clear(response)
        raise
    return _token_body(pair, _set_cookies(response, pair))


@router.post("/logout", status_code=204, summary="Sign out and revoke the session family")
def logout(request: Request, response: Response, db: DB) -> Response:
    _check_csrf(request)
    svc.logout(db, refresh_token=request.cookies.get(RT_COOKIE), session_id=None, user_id=None, tenant_id=None)
    _clear(response)
    response.status_code = 204
    return response


@router.get("/me")
def me(p: CurrentUser) -> dict:
    org = p.user
    return {"user": S.user(org), "tenant_id": str(p.tenant_id)}


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordRequest, p: CurrentUser, response: Response, db: DB) -> Response:
    svc.change_password(db, user=p.user, current=body.current_password, new=body.new_password)
    _clear(response)
    response.status_code = 204
    return response
