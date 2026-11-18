"""Metadata adapter; profile-aware browser jobs do the actual retrieval."""
from app.core.config import get_settings
from app.integrations.base.adapter import BankAdapter, Capability, ConnectionMethod, ConnectionStatus
from app.integrations.registry import register


@register
class ConfiguredBrowserAdapter(BankAdapter):
    code = "browser_automation"
    name = "Bank Browser"
    connection_method = ConnectionMethod.BROWSER
    capabilities = frozenset({Capability.STATEMENTS})

    def is_configured(self):
        return get_settings().browser_automation_enabled

    def check_connection(self, ctx):
        return ConnectionStatus(False, "requires_auth", "Complete authentication in the official bank browser")
