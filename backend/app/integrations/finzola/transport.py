"""Fixed HTTPS endpoints, public-IP pinning, TLS hostname verification and bounded JSON."""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

GATEWAY = "https://ajlzldkyskdedjbeiazs.supabase.co/functions/v1/api-gateway"
RECEIVER = "https://ajlzldkyskdedjbeiazs.supabase.co/functions/v1/external-expense-webhook"
MAX_BYTES = 4 * 1024 * 1024


class RemoteError(Exception):
    def __init__(self, message, *, transient=False, auth=False):
        super().__init__(message)
        self.transient, self.auth = transient, auth


def request_json(method, url, headers, payload=None):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != urlsplit(GATEWAY).hostname or parts.port not in (None, 443):
        raise RemoteError("Destination is not the configured Finzola HTTPS service")
    try:
        addresses = [result[4][0] for result in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)]
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise RemoteError("Finzola hostname did not resolve exclusively to public addresses")
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(parts.hostname, timeout=15, context=context)
        connection.sock = context.wrap_socket(socket.create_connection((addresses[0], 443), timeout=15),
                                             server_hostname=parts.hostname)
        try:
            body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
            connection.request(method, parts.path, body=body,
                headers={**headers, "Accept": "application/json", "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise RemoteError("Finzola response exceeds the configured size limit")
            if response.status != 200:
                raise RemoteError(f"Finzola returned HTTP {response.status}",
                    transient=response.status in (408, 425, 429) or response.status >= 500,
                    auth=response.status in (401, 403))
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise RemoteError("Finzola returned invalid JSON") from None
        finally:
            connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException):
        raise RemoteError("Finzola network/TLS request failed", transient=True) from None


def fetch(domain, key):
    return request_json("GET", f"{GATEWAY}/{domain}", {"Authorization": f"Bearer {key}"})


def send(key, payload):
    response = request_json("POST", RECEIVER, {"X-API-Key": key}, payload)
    if not isinstance(response, dict) or response.get("ok") is not True or response.get("received") != payload["requestId"]:
        raise RemoteError("Receiver did not acknowledge the exact request ID")
    return {"ok": True, "received": response["received"]}
