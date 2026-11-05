"""Long-lived API credentials for programmatic access.

A key belongs to a user and acts as that user, narrowed by ``scopes`` (what it
may do) and optional agent/team allowlists (which resources it may invoke —
allowlists constrain invoke paths only, not CRUD). Only the sha256 hash of the
secret is stored; the full secret and the webhook signing secret are returned
exactly once at creation.

Persistence rule: instance ``save()`` exactly once at creation. Every later
write (revoke, last_used_at) MUST be a partial ``APIKey.update_one`` —
``Model.save()`` writes the full row and would clobber concurrent writes.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from odbms import Model
from pydantic import validator

KEY_PREFIX = 'ctx_'
VALID_SCOPES = {'chat', 'run', 'read', 'write'}


def generate_secret() -> str:
    """Return a new full key secret (shown once, never stored)."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode('utf-8')).hexdigest()


def normalize_expiry(value: str | None) -> str | None:
    """Parse a client-supplied datetime into the naive-UTC format sqlite keeps.

    The odbms sqlite adapter rewrites any ``*_at`` string through
    ``fromisoformat`` to naive ``'%Y-%m-%d %H:%M:%S'`` on INSERT (but not on
    update_one) — normalizing here keeps every stored value one format and
    makes expiry comparisons TZ-correct.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.strftime('%Y-%m-%d %H:%M:%S')


class APIKey(Model):
    name: str
    """Human label chosen at creation"""

    user_id: str
    """Owner — the key acts as this user"""

    key_hash: str
    """sha256 hex of the full secret; the secret itself is never stored"""

    prefix: str
    """First characters of the secret, for display/identification"""

    scopes: list[str] = []
    """Subset of VALID_SCOPES"""

    allowed_agents: list[str] = []
    """Agent ids this key may invoke; empty = all"""

    allowed_teams: list[str] = []
    """Team ids this key may run; empty = all"""

    webhook_secret: str = ''
    """HMAC-SHA256 signing key for completion webhooks (server-side plaintext)"""

    rate_limit: int | None = None
    """Requests/minute override; None = server default"""

    expires_at: str | None = None
    """Naive-UTC 'YYYY-MM-DD HH:MM:SS'; None = never expires"""

    last_used_at: str | None = None
    """Stamped (throttled) on authenticated use"""

    revoked: bool = False
    """Soft-revoked keys are rejected but kept for audit"""

    @validator('scopes', 'allowed_agents', 'allowed_teams', pre=True)
    def _coerce_null_lists(cls, value):
        return [] if value is None else value

    @validator('revoked', pre=True)
    def _coerce_revoked(cls, value):
        return bool(value) if value is not None else False

    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        try:
            return datetime.fromisoformat(self.expires_at) <= now
        except ValueError:
            return False

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def agent_allowed(self, agent_id: str) -> bool:
        return not self.allowed_agents or agent_id in self.allowed_agents

    def team_allowed(self, team_id: str) -> bool:
        return not self.allowed_teams or team_id in self.allowed_teams
