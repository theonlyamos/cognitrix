"""MCP servers people add from the web app or desktop app.

Each row belongs to the user who added it, or is shared (``owner_id`` None)
when an admin added it for everyone. Servers in the CLI's ``mcp.json`` stay
where they are and count as shared; see ``cognitrix/mcp/registry.py``.

``headers`` and ``env`` are JSON objects stored as encrypted text
(``cognitrix.common.secret_box``). They can hold tokens, so API responses only
ever show their keys. A header value may be ``{{secret:NAME}}``, filled from
the owner's saved sign-ins when the server connects.

Connected apps (Settings > Connected apps) are rows too: one per person and
app, with ``catalog_id`` set to the catalog entry (``cognitrix/mcp/catalog.py``)
and ``auth_type`` "oauth". ``oauth`` is a sealed JSON object with the client
registration and the tokens; see ``cognitrix/mcp/oauth.py``. ``status`` is
"connected", "needs_reauth" (the sign-in expired or was revoked) or "" (never
signed in, or signed out).

Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
"""
from odbms import Model
from pydantic import validator


class MCPServer(Model):
    name: str
    owner_id: str | None = None
    transport: str = 'http'
    description: str = ''
    url: str | None = None
    command: str | None = None
    args: list[str] = []
    env: str = ''
    """JSON object of environment variables for a stdio server."""
    headers: str = ''
    """JSON object of HTTP headers sent to an http/sse server."""
    timeout: int = 30
    enabled: bool = True
    auth_type: str | None = None
    """"none", "header" or "oauth". Empty on rows saved before sign-in existed."""
    catalog_id: str | None = None
    oauth: str | None = None
    """Sealed JSON: client registration, endpoints and tokens."""
    status: str | None = None
    account: str | None = None
    """Who the app is signed in as, e.g. an email address or GitHub login."""

    @validator('args', pre=True)
    def _coerce_null_list(cls, value):
        return [] if value is None else value
