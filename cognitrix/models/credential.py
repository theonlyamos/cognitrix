"""Saved sign-ins: tokens, API keys and passwords agents may use by name.

The model never sees a value. It writes ``{{secret:NAME}}`` in a tool call
(or names the credential in the Http Request tool's ``credential`` field) and
the app fills the value in just before the request goes out, and only when the
request goes to one of the credential's ``allowed_hosts``. See
``cognitrix/credentials.py``.

``secret`` is a sealed JSON object (``cognitrix.common.secret_box``):

- kind ``token``: ``{"value": "..."}``, sent as ``Authorization: Bearer``
  unless ``header`` says another header name (``X-API-Key``...).
- kind ``basic``: ``{"username": "...", "password": "..."}``.
- kind ``oauth``: ``{"access_token": "...", "refresh_token": "...",
  "expires_at": 0, "token_url": "...", "client_id": "..."}``. Written by
  connected-app sign-in (planned), not by hand.

Persistence rule: instance ``save()`` once at creation, ``update_one`` after.
"""
from odbms import Model
from pydantic import validator

KINDS = ('token', 'basic', 'oauth')


class Credential(Model):
    owner_id: str
    name: str
    """What the model calls it, e.g. GITHUB_TOKEN. Unique per owner."""
    kind: str = 'token'
    description: str = ''
    """Shown to the model so it knows when to use it."""
    allowed_hosts: list[str] = []
    """Hosts the value may be sent to: ``api.github.com`` or ``*.example.com``."""
    header: str = ''
    """Header for kind ``token``; empty means ``Authorization: Bearer``."""
    secret: str = ''
    """Sealed JSON with the value(s); never leaves the server."""
    hint: str = ''
    """Last four characters, for the settings list."""
    last_used_at: str | None = None
    use_in_browser: bool = False
    """Agents may type it into sign-in pages on ``allowed_hosts`` in the
    browser on their own computer (cognitrix/computer/saved_sign_ins.py).
    That puts the value inside the sandbox, which the key proxy otherwise
    avoids, so it is off unless the person turns it on (Saved sign-ins, or by
    answering an agent's Ask For Sign-in for a website)."""

    @validator('use_in_browser', pre=True)
    def _coerce_flag(cls, value):
        # Rows saved before the field existed read back NULL (TEXT column).
        if value in (None, ''):
            return False
        if isinstance(value, str):
            return value.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(value)

    @validator('allowed_hosts', pre=True)
    def _coerce_null_list(cls, value):
        return [] if value is None else value
