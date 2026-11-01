"""MCP servers people add from the web app or desktop app.

Each row belongs to the user who added it, or is shared (``owner_id`` None)
when an admin added it for everyone. Servers in the CLI's ``mcp.json`` stay
where they are and count as shared; see ``cognitrix/mcp/registry.py``.

``headers`` and ``env`` are JSON objects stored as text. They can hold tokens,
so API responses only ever show their keys.

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

    @validator('args', pre=True)
    def _coerce_null_list(cls, value):
        return [] if value is None else value
