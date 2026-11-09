"""A single-use link that lets one person sign up while sign-up is closed."""
from odbms import Model
from pydantic import field_validator


class Invite(Model):
    token_hash: str
    """SHA-256 of the token; the token itself is shown once and never stored."""
    email: str = ''
    """If set, only this address may use the invite."""
    role: str = 'member'
    created_by: str = ''
    expires_at: int = 0
    """Unix time."""
    used_at: int | None = None
    used_by: str = ''

    @field_validator('email', 'created_by', 'used_by', 'role', mode='before')
    @classmethod
    def _text(cls, value):
        return '' if value is None else str(value)
