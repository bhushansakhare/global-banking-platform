"""HTTP helper that maps transport/provider failures onto adapter errors."""
from __future__ import annotations

from typing import Any

import httpx

from app.core.ratelimit import provider_throttle
from app.integrations.base.adapter import (AuthenticationRequired, ConsentExpired, ProviderError,
                                           ProviderUnavailable, RateLimited)

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def request_json(provider: str, method: str, url: str, *, min_interval: float = 0.0,
                 client: httpx.Client | None = None, **kwargs: Any) -> Any:
    provider_throttle.wait(provider, min_interval)
    own = client is None
    c = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        resp = c.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ProviderUnavailable(f"{provider}: network error ({type(exc).__name__})") from exc
    finally:
        if own:
            c.close()
    if resp.status_code == 429:
        raise RateLimited(f"{provider}: rate limited", details={"retry_after": resp.headers.get("retry-after")})
    if resp.status_code >= 500:
        raise ProviderUnavailable(f"{provider}: upstream error {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    if resp.status_code in (401, 403):
        text = str(body).lower()
        if "expired" in text or "eua" in text or "consent" in text:
            raise ConsentExpired(f"{provider}: consent expired or revoked", details={"status": resp.status_code})
        raise AuthenticationRequired(f"{provider}: authentication rejected", details={"status": resp.status_code})
    if resp.status_code >= 400:
        raise ProviderError(f"{provider}: request failed ({resp.status_code})", details={"body": body})
    return body
