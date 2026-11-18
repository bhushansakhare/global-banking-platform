"""Structured JSON logging with sensitive-data redaction."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEYS = re.compile(
    r"(password|passwd|secret|token|otp|pin|cvv|authorization|cookie|api[_-]?key|credential|account_number|iban|mobile|phone|vua|customer_id|user_id|browser_identifier)",
    re.IGNORECASE,
)
# long digit runs (account / card numbers) and bearer tokens in free text
_DIGITS = re.compile(r"\b\d{8,19}\b")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.=]+", re.IGNORECASE)


def redact_text(text: str) -> str:
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _DIGITS.sub(lambda m: "••••" + m.group(0)[-4:], text)


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[…]"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SENSITIVE_KEYS.search(str(k)) else redact(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            data["ctx"] = redact(extra)
        if record.exc_info:
            data["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(data, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("uvicorn.access", "apscheduler.executors.default", "apscheduler.scheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_ctx(logger: logging.Logger, level: int, msg: str, **ctx: Any) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
