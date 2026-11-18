"""File format and PDF-type detection by content (never by extension alone)."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO


@dataclass(frozen=True)
class Detection:
    format: str  # pdf|xlsx|xls|csv|unknown
    mime: str
    scanned_pdf: bool | None = None
    page_count: int | None = None
    encrypted: bool = False


def detect_format(content: bytes, filename: str = "") -> Detection:
    head = content[:8]
    if head.startswith(b"%PDF"):
        return _inspect_pdf(content)
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as zf:
                if any(n.startswith("xl/") for n in zf.namelist()):
                    return Detection("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except zipfile.BadZipFile:
            return Detection("unknown", "application/octet-stream")
        return Detection("unknown", "application/zip")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return Detection("xls", "application/vnd.ms-excel")
    sample = content[:4096]
    try:
        text = sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = sample.decode("cp1252")
        except UnicodeDecodeError:
            return Detection("unknown", "application/octet-stream")
    if "\x00" not in text and any(sep in text for sep in (",", ";", "\t", "|")):
        return Detection("csv", "text/csv")
    return Detection("unknown", "text/plain")


def _inspect_pdf(content: bytes) -> Detection:
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return Detection("unknown", "application/pdf")
    with doc:
        if doc.needs_pass:
            return Detection("pdf", "application/pdf", None, doc.page_count, encrypted=True)
        pages = min(doc.page_count, 5)
        chars = sum(len(doc[i].get_text("text").strip()) for i in range(pages))
        images = sum(len(doc[i].get_images()) for i in range(pages))
        scanned = chars < 30 * max(pages, 1) and images > 0
        return Detection("pdf", "application/pdf", scanned or (chars == 0), doc.page_count)
