"""Provider notifications. Disabled unless a shared secret is configured.

The notification body is treated as an untrusted hint only: consent/session state is always re-read
from the provider API before anything changes. No bank data from the payload is stored.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Query, Request

from app.api.deps import DB
from app.core.config import get_settings
from app.core.errors import NotFound, Unauthorized
from app.core.logging import get_logger
from app.services.connections import handle_aa_notification

router = APIRouter(tags=["webhooks"])
log = get_logger(__name__)


@router.post("/webhooks/aa/setu", summary="Setu Account Aggregator notifications (consent / session status)")
async def setu_aa_notification(request: Request, db: DB, token: str = Query("", max_length=200)) -> dict:
    secret = get_settings().setu_aa_webhook_token
    if not secret:
        raise NotFound("Not found")
    if not hmac.compare_digest(token.encode(), secret.encode()):
        raise Unauthorized("Invalid notification token")
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = handle_aa_notification(db, payload)
    log.info("aa notification", extra={"ctx": {"type": str(payload.get("type", ""))[:60],
                                               "handled": result.get("handled")}})
    return {"ok": True}
