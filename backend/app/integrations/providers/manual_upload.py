"""Manual statement upload channel — works with any bank worldwide.

It has no remote capabilities: statements arrive via the upload API and are
processed by the same pipeline as downloaded statements.
"""
from __future__ import annotations

from app.integrations.base.adapter import ConnectionMethod, BankAdapter, ConnectionContext, ConnectionStatus
from app.integrations.registry import register


@register
class ManualUploadAdapter(BankAdapter):
    code = "manual_upload"
    name = "Statement Upload"
    capabilities = frozenset()
    connection_method = ConnectionMethod.MANUAL

    def environment(self) -> str:
        return "local"

    def check_connection(self, ctx: ConnectionContext) -> ConnectionStatus:
        return ConnectionStatus(True, "active", "Manual upload channel — no remote connection required")
