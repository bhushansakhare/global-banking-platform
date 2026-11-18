"""Content-addressed, tenant-segregated file storage for original statements and reports."""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound


def _root(kind: str) -> Path:
    root = (get_settings().storage_dir / kind).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_statement_file(tenant_id: uuid.UUID, content: bytes, ext: str) -> tuple[str, str]:
    """Returns (sha256, relative storage path). Idempotent for identical content."""
    digest = sha256_bytes(content)
    rel = Path(str(tenant_id)) / digest[:2] / f"{digest}.{ext or 'bin'}"
    path = _root("statements") / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)
    return digest, rel.as_posix()


def save_generated(kind: str, tenant_id: uuid.UUID, filename: str, content: bytes) -> str:
    rel = Path(str(tenant_id)) / filename
    path = _root(kind) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel.as_posix()


def resolve(kind: str, tenant_id: uuid.UUID, rel: str) -> Path:
    """Resolve a stored path, refusing traversal and cross-tenant access."""
    root = _root(kind)
    path = (root / rel).resolve()
    tenant_root = (root / str(tenant_id)).resolve()
    if tenant_root not in path.parents:
        raise Forbidden("File access denied")
    if not path.is_file():
        raise NotFound("Stored file is missing")
    return path


def verify_integrity(tenant_id: uuid.UUID, rel: str, expected_sha: str) -> bool:
    return sha256_bytes(resolve("statements", tenant_id, rel).read_bytes()) == expected_sha
