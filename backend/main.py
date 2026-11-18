"""Entry point: ``python main.py`` starts the API, worker and scheduler.

Command line utilities: ``python -m app.cli --help``.
"""
from __future__ import annotations

import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=False, proxy_headers=False, log_config=None)
